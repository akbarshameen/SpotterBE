
from __future__ import annotations

import time

from .geocoding import resolve
from .optimizer import MAX_RANGE_MILES, MPG, plan_route_fuel
from .routing import get_route

GOOGLE_MAPS_DIR = "https://www.google.com/maps/dir/"
MAX_MAP_WAYPOINTS = 10


def _bbox(geometry: list[tuple[float, float]]) -> list[float]:
    lats = [p[0] for p in geometry]
    lons = [p[1] for p in geometry]
    return [min(lats), min(lons), max(lats), max(lons)]


def google_maps_url(start, finish, stops) -> str:
    """Directions link with the fuel stops as waypoints.

    Google only reliably honours about ten waypoints, so longer plans are
    thinned evenly. The coordinates are real truckstop locations.
    """
    chosen = stops
    if len(stops) > MAX_MAP_WAYPOINTS:
        step = len(stops) / MAX_MAP_WAYPOINTS
        chosen = [stops[int(i * step)] for i in range(MAX_MAP_WAYPOINTS)]
    points = (
        [f"{start[0]:.6f},{start[1]:.6f}"]
        + [f"{s['lat']},{s['lon']}" for s in chosen]
        + [f"{finish[0]:.6f},{finish[1]:.6f}"]
    )
    return GOOGLE_MAPS_DIR + "/".join(points)


def build_plan(start_query: str, finish_query: str, corridor_miles: float | None = None) -> dict:
    """Resolve both endpoints, fetch the route, and plan the cheapest fuel stops.
    """
    started = time.perf_counter()

    #resolve calls hit bundled Census data on disk — zero network. 
    #So get_route on line 49 is the only external call in the system

    start = resolve(start_query)
    finish = resolve(finish_query)
    route = get_route(start.coords, finish.coords)
    fuel = plan_route_fuel(route["geometry"], route["distance_miles"], corridor_miles)

    api_calls = start.api_calls + finish.api_calls + route["api_calls"]
    stops = fuel["stops"]

    return {
        "start": {"query": start_query, "resolved": start.label,
                  "lat": start.lat, "lon": start.lon},
        "finish": {"query": finish_query, "resolved": finish.label,
                   "lat": finish.lat, "lon": finish.lon},
        "route": {
            "distance_miles": round(route["distance_miles"], 1),
            "duration_hours": round(route["duration_hours"], 2),
            "geometry": route["encoded_geometry"],
            "bbox": [round(v, 6) for v in _bbox(route["geometry"])],
            "map_url": google_maps_url(start.coords, finish.coords, stops),
        },
        "fuel_stops": stops,
        "total_gallons": fuel["total_gallons"],
        "total_fuel_cost_usd": fuel["total_fuel_cost"],
        "assumptions": {
            "max_range_miles": MAX_RANGE_MILES,
            "miles_per_gallon": MPG,
            "tank_gallons": MAX_RANGE_MILES / MPG,
            "starts_full_arrives_empty": True,
            "corridor_miles": fuel["corridor_miles"],
            "detour_miles_excluded_from_burn": True,
        },
        "meta": {
            "api_calls": api_calls,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "candidate_stations_near_route": fuel["candidate_stations"],
            "geocode_source": {"start": start.source, "finish": finish.source},
            "optimality_gap_usd": fuel["optimality_gap_usd"],
        },
    }
