
from __future__ import annotations

import math

from django.conf import settings

from .geo import MILES_PER_DEG_LAT, cumulative_miles, haversine, resample_by_distance
from .stations import GRID_DEG, NEIGHBOUR_OFFSETS, load_stations, station_grid


MAX_RANGE_MILES = 500.0
MPG = 10.0
TANK_GALLONS = MAX_RANGE_MILES / MPG  # 50

CORRIDOR_MILES = getattr(settings, "FUEL_CORRIDOR_MILES", 25.0)
CORRIDOR_FALLBACKS = getattr(settings, "FUEL_CORRIDOR_FALLBACKS", (25.0, 50.0, 100.0))
SAMPLE_SPACING_MILES = 5.0
ORIGIN_WINDOW_MILES = getattr(settings, "FUEL_ORIGIN_WINDOW_MILES", 50.0)
MIN_PURCHASE_GALLONS = getattr(settings, "FUEL_MIN_PURCHASE_GALLONS", 5.0)


class RouteInfeasible(Exception):
    """No legal fuelling plan exists for this route at this corridor width."""


def _cell(lat: float, lon: float) -> tuple[int, int]:
    return (math.floor(lat / GRID_DEG), math.floor(lon / GRID_DEG))


def stations_along_route(
    geometry: list[tuple[float, float]], cum: list[float], radius_miles: float
) -> list[dict]:
    stations = load_stations()
    grid = station_grid()
    sample_idx = resample_by_distance(geometry, cum, SAMPLE_SPACING_MILES)
    if not sample_idx:
        return []

    # A station within radius of the path is within radius + spacing/2 of a sample.
    scan_radius = radius_miles + SAMPLE_SPACING_MILES / 2 + 1.0
    scan_sq = scan_radius * scan_radius

    route_cells: dict[tuple[int, int], list[int]] = {}
    sample_pos: dict[int, int] = {}
    for pos, vi in enumerate(sample_idx):
        lat, lon = geometry[vi]
        route_cells.setdefault(_cell(lat, lon), []).append(vi)
        sample_pos[vi] = pos

    # Cheap reject: only stations sharing a cell neighbourhood with the route.
    candidates: set[int] = set()
    for r, c in route_cells:
        for dr, dc in NEIGHBOUR_OFFSETS:
            hits = grid.get((r + dr, c + dc))
            if hits:
                candidates.update(hits)

    found = []
    for si in candidates:
        s = stations[si]
        slat, slon, coslat = s["lat"], s["lon"], s["coslat"]

        best_sq, best_vi = scan_sq, -1
        srow, scol = _cell(slat, slon)
        for dr, dc in NEIGHBOUR_OFFSETS:
            for vi in route_cells.get((srow + dr, scol + dc), ()):
                rlat, rlon = geometry[vi]
                dy = (rlat - slat) * MILES_PER_DEG_LAT
                dx = (rlon - slon) * MILES_PER_DEG_LAT * coslat
                d_sq = dx * dx + dy * dy
                if d_sq < best_sq:
                    best_sq, best_vi = d_sq, vi
        if best_vi < 0:
            continue

        # Refine against every vertex between the neighbouring samples. The
        # argmin uses the same flat approximation -- over a span this short it
        # picks the identical vertex -- then the winner gets one exact haversine
        # so the reported detour is a true great-circle distance.
        pos = sample_pos[best_vi]
        lo = sample_idx[max(0, pos - 1)]
        hi = sample_idx[min(len(sample_idx) - 1, pos + 1)]
        near_sq, exact_vi = float("inf"), best_vi
        for vi in range(lo, hi + 1):
            rlat, rlon = geometry[vi]
            dy = (rlat - slat) * MILES_PER_DEG_LAT
            dx = (rlon - slon) * MILES_PER_DEG_LAT * coslat
            d_sq = dx * dx + dy * dy
            if d_sq < near_sq:
                near_sq, exact_vi = d_sq, vi
        exact_d = haversine(slat, slon, geometry[exact_vi][0], geometry[exact_vi][1])

        if exact_d <= radius_miles:
            found.append({**s, "mile": cum[exact_vi], "detour_miles": exact_d})

    found.sort(key=lambda x: x["mile"])
    return found


def _next_cheaper(points: list[dict]) -> list[int | None]:
   
    out: list[int | None] = [None] * len(points)
    stack: list[int] = []
    for i in range(len(points) - 1, -1, -1):
        price = points[i]["price"]
        while stack and points[stack[-1]]["price"] >= price:
            stack.pop()
        out[i] = stack[-1] if stack else None
        stack.append(i)
    return out


def _check_feasible(points: list[dict], total_miles: float) -> None:
    if not points or points[0]["mile"] > 1e-6:
        raise RouteInfeasible("no fuel station near the start of the route")
    for a, b in zip(points, points[1:]):
        gap = b["mile"] - a["mile"]
        if gap > MAX_RANGE_MILES:
            raise RouteInfeasible(
                f"{gap:.0f}-mile gap with no fuel between mile {a['mile']:.0f} "
                f"and mile {b['mile']:.0f}, which exceeds the {MAX_RANGE_MILES:.0f}-mile range"
            )
    tail = total_miles - points[-1]["mile"]
    if tail > MAX_RANGE_MILES:
        raise RouteInfeasible(
            f"last fuel stop is {tail:.0f} miles from the destination, "
            f"beyond the {MAX_RANGE_MILES:.0f}-mile range"
        )


def _solve(points: list[dict], total_miles: float) -> tuple[list[tuple[int, float]], float]:
   
    _check_feasible(points, total_miles)
    cheaper = _next_cheaper(points)

    fuel = 0.0  # miles of range currently in the tank
    prev_mile = 0.0
    cost = 0.0
    plan: list[tuple[int, float]] = []

    for i, s in enumerate(points):
        fuel -= s["mile"] - prev_mile
        prev_mile = s["mile"]
        if fuel < -1e-6:
            raise RouteInfeasible(f"ran dry before mile {s['mile']:.0f}")

        j = cheaper[i]
        if j is not None and points[j]["mile"] - s["mile"] <= MAX_RANGE_MILES:
            target = points[j]["mile"] - s["mile"]  # just reach the cheaper pump
        else:
            target = min(MAX_RANGE_MILES, total_miles - s["mile"])  # local minimum: fill

        buy = target - fuel
        if buy > 1e-9:
            gallons = buy / MPG
            cost += gallons * s["price"]
            fuel += buy
            plan.append((i, gallons))

    if fuel < (total_miles - prev_mile) - 1e-6:
        raise RouteInfeasible("cannot reach the destination from the last fuel stop")
    return plan, cost


def _suppress_micro_stops(
    points: list[dict], total_miles: float, min_gallons: float
) -> tuple[list[dict], list[tuple[int, float]], float, float]:
    
    plan, cost = _solve(points, total_miles)
    true_optimum = cost

    for _ in range(8):
        tiny = {i for i, gallons in plan if gallons < min_gallons and i != 0}
        if not tiny:
            break
        trial = [p for k, p in enumerate(points) if k not in tiny]
        try:
            trial_plan, trial_cost = _solve(trial, total_miles)
        except RouteInfeasible:
            break  # keep the last valid plan
        points, plan, cost = trial, trial_plan, trial_cost

    return points, plan, cost, true_optimum


def plan_route_fuel(
    geometry: list[tuple[float, float]],
    total_miles: float,
    corridor_miles: float | None = None,
) -> dict:
 
    cum = cumulative_miles(geometry, total_miles)

    radii = [corridor_miles] if corridor_miles else list(CORRIDOR_FALLBACKS)
    last_error: Exception | None = None

    for radius in radii:
        corridor = stations_along_route(geometry, cum, radius)
        if not corridor:
            last_error = RouteInfeasible(f"no truckstops within {radius:.0f} miles of the route")
            continue

        # The departure fill: cheapest station on the way out of town, at mile 0.
        window = [s for s in corridor if s["mile"] <= ORIGIN_WINDOW_MILES] or corridor[:1]
        origin = min(window, key=lambda s: s["price"])
        points = [{**origin, "mile": 0.0, "departure_fill": True}] + [
            s for s in corridor if s is not origin
        ]
        points.sort(key=lambda s: s["mile"])

        try:
            points, plan, cost, true_optimum = _suppress_micro_stops(
                points, total_miles, MIN_PURCHASE_GALLONS
            )
        except RouteInfeasible as exc:
            last_error = exc
            continue

        stops = []
        for order, (idx, gallons) in enumerate(plan, start=1):
            s = points[idx]
            stops.append({
                "order": order,
                "name": s["name"],
                "address": s["address"],
                "city": s["city"],
                "state": s["state"],
                "opis_id": s["opis_id"],
                "lat": s["lat"],
                "lon": s["lon"],
                "miles_from_start": round(s["mile"], 1),
                "detour_miles": round(s["detour_miles"], 1),
                "price_per_gallon": round(s["price"], 4),
                "gallons_purchased": round(gallons, 2),
                "cost_usd": round(gallons * s["price"], 2),
                "departure_fill": bool(s.get("departure_fill")),
            })

        return {
            "stops": stops,
            "total_gallons": round(total_miles / MPG, 2),
            "total_fuel_cost": round(cost, 2),
            "corridor_miles": radius,
            "candidate_stations": len(points),
            # Non-zero only when micro-stop suppression traded a little cost for
            # a usable plan; surfaced so the trade-off is auditable, not hidden.
            "optimality_gap_usd": round(cost - true_optimum, 2),
        }

    raise last_error or RouteInfeasible("could not plan fuel stops for this route")
