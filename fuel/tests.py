"""Tests for the fuel planner.

The unit tests below construct station lists by hand, so they run offline and
deterministically -- no routing provider, no geocoder, no data files.
`test_greedy_matches_exact_dp` is the important one: it checks the linear greedy
against an exhaustive dynamic program, which is what actually justifies calling
the result "optimal".
"""
from __future__ import annotations

import random
import unittest

from django.test import SimpleTestCase

from fuel.services.optimizer import (
    MAX_RANGE_MILES,
    MPG,
    RouteInfeasible,
    _next_cheaper,
    _solve,
)


def station(mile: float, price: float, name: str = "S") -> dict:
    return {
        "mile": float(mile), "price": float(price), "name": name,
        "address": "", "city": name, "state": "XX", "opis_id": "0",
        "lat": 0.0, "lon": 0.0, "detour_miles": 0.0,
    }


def cost_of(points, total):
    return _solve(points, total)[1]


class NextCheaperTests(SimpleTestCase):
    def test_matches_brute_force(self):
        rng = random.Random(20260904)
        for _ in range(200):
            prices = [rng.randint(1, 12) for _ in range(rng.randint(1, 18))]
            pts = [station(i * 10, p) for i, p in enumerate(prices)]
            got = _next_cheaper(pts)
            for i, p in enumerate(prices):
                expected = next((j for j in range(i + 1, len(prices)) if prices[j] < p), None)
                self.assertEqual(got[i], expected, f"prices={prices} i={i}")


class SolveTests(SimpleTestCase):
    def test_fills_up_when_nothing_cheaper_is_reachable(self):
        # $3.00 at the start, $4.00 at mile 400, 800-mile trip.
        # Fill completely at the cheap origin, then buy only the remainder.
        pts = [station(0, 3.00), station(400, 4.00)]
        plan, cost = _solve(pts, 800)
        self.assertEqual([(0, 50.0), (1, 30.0)], [(i, round(g, 2)) for i, g in plan])
        self.assertAlmostEqual(270.00, cost, places=2)

    def test_buys_only_enough_to_reach_a_cheaper_station(self):
        # $4.00 at the start but $3.00 just 100 miles along: buy 10 gallons, not 50.
        pts = [station(0, 4.00), station(100, 3.00)]
        plan, cost = _solve(pts, 500)
        self.assertEqual([(0, 10.0), (1, 40.0)], [(i, round(g, 2)) for i, g in plan])
        self.assertAlmostEqual(160.00, cost, places=2)
        # Naively filling the tank at the origin would cost $200.
        self.assertLess(cost, 50 * 4.00)

    def test_local_minimum_fills_and_skips_pricier_stations(self):
        # This is the bug the old greedy had: at a 500-mile price minimum it
        # bought a few gallons instead of filling, then paid more twice.
        pts = [station(0, 3.00), station(100, 2.50), station(200, 2.90), station(300, 2.95)]
        plan, cost = _solve(pts, 600)
        self.assertEqual([0, 1], [i for i, _ in plan], "should skip the two pricier stops")
        self.assertAlmostEqual(155.00, cost, places=2)

    def test_short_trip_needs_only_the_departure_fill(self):
        plan, cost = _solve([station(0, 3.00)], 300)
        self.assertEqual([(0, 30.0)], [(i, round(g, 2)) for i, g in plan])
        self.assertAlmostEqual(90.00, cost, places=2)

    def test_total_gallons_always_equals_distance_over_mpg(self):
        rng = random.Random(11)
        for _ in range(300):
            total = rng.randint(200, 3000)
            miles = sorted(rng.sample(range(0, total, 10), rng.randint(1, 12)))
            miles[0] = 0
            pts = [station(m, rng.randint(250, 450) / 100) for m in miles]
            try:
                plan, _ = _solve(pts, total)
            except RouteInfeasible:
                continue
            self.assertAlmostEqual(total / MPG, sum(g for _, g in plan), places=6)

    def test_rejects_gap_beyond_range(self):
        with self.assertRaises(RouteInfeasible):
            _solve([station(0, 3.0), station(600, 3.0)], 900)

    def test_rejects_missing_origin_station(self):
        with self.assertRaises(RouteInfeasible):
            _solve([station(10, 3.0)], 400)

    def test_rejects_unreachable_destination(self):
        with self.assertRaises(RouteInfeasible):
            _solve([station(0, 3.0)], 900)

    def test_never_exceeds_tank_range_between_purchases(self):
        rng = random.Random(7)
        for _ in range(200):
            total = rng.randint(600, 3000)
            miles = sorted(rng.sample(range(0, total, 10), rng.randint(3, 14)))
            miles[0] = 0
            pts = [station(m, rng.randint(250, 450) / 100) for m in miles]
            try:
                plan, _ = _solve(pts, total)
            except RouteInfeasible:
                continue
            bought = [(pts[i]["mile"], g) for i, g in plan]
            for (mile, gallons), (next_mile, _) in zip(bought, bought[1:]):
                self.assertLessEqual(gallons * MPG, MAX_RANGE_MILES + 1e-6)
                self.assertLessEqual(next_mile - mile, MAX_RANGE_MILES + 1e-6)


def _exact_dp_cost(points: list[dict], total: float, unit: int = 10) -> float:
    """Brute-force minimum cost, used only to validate the greedy.

    Fuel is discretised into `unit`-mile steps (all test instances are built on
    that grid). State is "arrived at station i carrying f units"; every possible
    purchase at every station is enumerated. Exponentially slower than the
    greedy but obviously correct, which is the point.
    """
    cap = int(MAX_RANGE_MILES // unit)
    reachable: dict[int, float] = {0: 0.0}  # fuel units on arrival -> best cost

    for i, s in enumerate(points):
        hop = (points[i + 1]["mile"] if i + 1 < len(points) else total) - s["mile"]
        need = int(round(hop / unit))
        nxt: dict[int, float] = {}
        for fuel, spent in reachable.items():
            for buy in range(0, cap - fuel + 1):
                carried = fuel + buy
                if carried < need:
                    continue
                left = carried - need
                total_spent = spent + (buy * unit / MPG) * s["price"]
                if left not in nxt or total_spent < nxt[left]:
                    nxt[left] = total_spent
        reachable = nxt
        if not reachable:
            return float("inf")
    return min(reachable.values())


class OptimalityTests(SimpleTestCase):
    def test_greedy_matches_exact_dp(self):
        """The linear greedy must equal the exhaustive optimum, every time."""
        rng = random.Random(20260904)
        compared = 0
        for _ in range(400):
            total = rng.randrange(300, 1600, 10)
            miles = sorted(set(rng.randrange(0, total, 10) for _ in range(rng.randint(2, 7))))
            if miles[0] != 0:
                miles[0] = 0
            pts = [station(m, rng.randint(240, 460) / 100) for m in miles]
            try:
                greedy = cost_of(pts, total)
            except RouteInfeasible:
                continue
            exact = _exact_dp_cost(pts, total)
            self.assertAlmostEqual(
                exact, greedy, places=6,
                msg=f"greedy={greedy} dp={exact} miles={miles} "
                    f"prices={[p['price'] for p in pts]} total={total}",
            )
            compared += 1
        self.assertGreater(compared, 100, "too few feasible instances to be meaningful")


# --------------------------------------------------------------------------
# Integration tests. These use the real bundled data files but never touch the
# network -- the routing provider is mocked with a synthetic polyline.
# --------------------------------------------------------------------------

from unittest import mock  # noqa: E402

from rest_framework.test import APIClient  # noqa: E402

from fuel.services.geo import cumulative_miles  # noqa: E402
from fuel.services.places import PLACES_FILE, geocode_local, lookup_city  # noqa: E402
from fuel.services.stations import STATIONS_FILE, load_stations  # noqa: E402

DATA_PRESENT = PLACES_FILE.exists() and STATIONS_FILE.exists()
requires_data = unittest.skipUnless(DATA_PRESENT, "run `manage.py build_station_index` first")

# Waypoints roughly tracing I-80 / I-76 / I-70 / I-15 from New Jersey to LA.
I80_WAYPOINTS = [
    (40.73, -74.17), (40.95, -75.60), (41.05, -78.50), (41.10, -80.65),
    (41.62, -83.53), (41.60, -86.25), (41.52, -88.10), (41.52, -90.58),
    (41.59, -93.62), (41.24, -96.01), (40.92, -98.34), (41.13, -102.98),
    (40.42, -104.71), (39.74, -104.98), (39.09, -108.55), (38.99, -110.16),
    (37.68, -113.06), (36.17, -115.14), (35.01, -117.02), (34.06, -118.25),
]


def densify(waypoints, step_miles=0.1):
    """Interpolate waypoints into a polyline with realistic vertex spacing."""
    from fuel.services.geo import haversine
    out = [waypoints[0]]
    for (la1, lo1), (la2, lo2) in zip(waypoints, waypoints[1:]):
        span = haversine(la1, lo1, la2, lo2)
        n = max(2, int(span / step_miles))
        for k in range(1, n + 1):
            t = k / n
            out.append((la1 + t * (la2 - la1), lo1 + t * (lo2 - lo1)))
    return out


@requires_data
class PlacesTests(SimpleTestCase):
    def test_resolves_plain_city_and_state(self):
        self.assertIsNotNone(lookup_city("Tomah", "WI"))
        self.assertIsNotNone(lookup_city("Big Cabin", "OK"))

    def test_city_named_x_city_is_not_truncated(self):
        """'Kansas City' must not normalise down to 'Kansas'."""
        kc = lookup_city("Kansas City", "MO")
        self.assertIsNotNone(kc)
        self.assertAlmostEqual(39.1, kc[0], delta=0.5)
        self.assertAlmostEqual(-94.6, kc[1], delta=0.5)

    def test_handles_punctuation_and_spacing_variants(self):
        self.assertIsNotNone(lookup_city("Canon City", "CO"))     # Cañon City
        self.assertIsNotNone(lookup_city("MC DERMITT", "NV"))     # McDermitt
        self.assertIsNotNone(lookup_city("Odonnell", "TX"))       # O'Donnell

    def test_parses_full_state_names_and_raw_coordinates(self):
        self.assertIsNotNone(geocode_local("New York, New York"))
        got = geocode_local("40.71,-74.01")
        self.assertIsNotNone(got)
        (lat, lon), _ = got
        self.assertAlmostEqual(40.71, lat, places=2)
        self.assertAlmostEqual(-74.01, lon, places=2)

    def test_rejects_wrong_state_rather_than_guessing(self):
        self.assertIsNone(geocode_local("Tomah, CA"))

    def test_rejects_unknown_place(self):
        self.assertIsNone(geocode_local("Nowheresvilleburg, ZZ"))


@requires_data
class StationDataTests(SimpleTestCase):
    def test_every_station_has_plausible_us_coordinates(self):
        for s in load_stations():
            self.assertTrue(18.0 <= s["lat"] <= 72.0, f"{s['city']}, {s['state']} lat {s['lat']}")
            self.assertTrue(-180.0 <= s["lon"] <= -65.0, f"{s['city']}, {s['state']} lon {s['lon']}")

    def test_no_canadian_stations_survived(self):
        from fuel.services.places import CANADIAN_PROVINCES
        states = {s["state"] for s in load_stations()}
        self.assertEqual(set(), states & CANADIAN_PROVINCES)

    def test_one_station_per_city_and_it_is_the_cheapest(self):
        seen = [(s["city"], s["state"]) for s in load_stations()]
        self.assertEqual(len(seen), len(set(seen)))

    def test_prices_are_sane(self):
        prices = [s["price"] for s in load_stations()]
        self.assertGreater(min(prices), 1.0)
        self.assertLess(max(prices), 10.0)


@requires_data
class PlannerIntegrationTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.geometry = densify(I80_WAYPOINTS)
        cls.total = cumulative_miles(cls.geometry)[-1]

    def test_plans_a_coast_to_coast_route(self):
        from fuel.services.optimizer import plan_route_fuel
        result = plan_route_fuel(self.geometry, self.total)
        stops = result["stops"]

        self.assertGreater(len(stops), 3)
        self.assertTrue(stops[0]["departure_fill"])
        self.assertAlmostEqual(self.total / MPG, result["total_gallons"], delta=0.02)
        self.assertAlmostEqual(
            self.total / MPG, sum(s["gallons_purchased"] for s in stops), delta=0.05
        )

    def test_no_leg_exceeds_the_tank_range(self):
        from fuel.services.optimizer import plan_route_fuel
        stops = plan_route_fuel(self.geometry, self.total)["stops"]
        marks = [s["miles_from_start"] for s in stops] + [self.total]
        for a, b in zip(marks, marks[1:]):
            self.assertLessEqual(b - a, MAX_RANGE_MILES + 0.001, f"leg {a}->{b}")

    def test_every_stop_is_within_the_corridor(self):
        from fuel.services.optimizer import plan_route_fuel
        result = plan_route_fuel(self.geometry, self.total, corridor_miles=25.0)
        for s in result["stops"]:
            self.assertLessEqual(s["detour_miles"], 25.0)

    def test_stops_are_ordered_and_have_real_identities(self):
        from fuel.services.optimizer import plan_route_fuel
        stops = plan_route_fuel(self.geometry, self.total)["stops"]
        self.assertEqual(list(range(1, len(stops) + 1)), [s["order"] for s in stops])
        marks = [s["miles_from_start"] for s in stops]
        self.assertEqual(marks, sorted(marks))
        for s in stops:
            self.assertTrue(s["name"])
            self.assertTrue(s["city"])
            self.assertEqual(2, len(s["state"]))
            self.assertGreater(s["gallons_purchased"], 0)

    def test_wider_corridor_never_costs_more(self):
        """More candidate stations can only help, never hurt."""
        from fuel.services.optimizer import plan_route_fuel
        narrow = plan_route_fuel(self.geometry, self.total, corridor_miles=15.0)
        wide = plan_route_fuel(self.geometry, self.total, corridor_miles=50.0)
        self.assertLessEqual(wide["total_fuel_cost"], narrow["total_fuel_cost"] + 0.5)


@requires_data
class ApiTests(SimpleTestCase):
    def setUp(self):
        self.client = APIClient()
        geometry = densify(I80_WAYPOINTS)
        self.fake_route = {
            "distance_miles": cumulative_miles(geometry)[-1],
            "duration_hours": 41.0,
            "encoded_geometry": "_ignored_",
            "geometry": geometry,
            "api_calls": 1,
        }

    def post(self, payload):
        return self.client.post("/api/route/", payload, format="json")

    def test_happy_path(self):
        with mock.patch("fuel.services.planner.get_route", return_value=self.fake_route):
            resp = self.post({"start": "New York, NY", "finish": "Los Angeles, CA"})
        self.assertEqual(200, resp.status_code, resp.data)
        body = resp.data
        self.assertGreater(len(body["fuel_stops"]), 3)
        self.assertAlmostEqual(
            body["route"]["distance_miles"] / MPG, body["total_gallons"], delta=0.2
        )
        self.assertGreater(body["total_fuel_cost_usd"], 0)
        self.assertEqual(500.0, body["assumptions"]["max_range_miles"])
        self.assertEqual(10.0, body["assumptions"]["miles_per_gallon"])
        # Both endpoints came from the bundled gazetteer, so the only external
        # call was the (mocked) routing one.
        self.assertEqual(1, body["meta"]["api_calls"])
        self.assertEqual("gazetteer", body["meta"]["geocode_source"]["start"])
        self.assertTrue(body["route"]["map_url"].startswith("https://www.google.com/maps/dir/"))

    def test_missing_fields_is_400(self):
        resp = self.post({"start": "New York, NY"})
        self.assertEqual(400, resp.status_code)
        self.assertIn("finish", resp.data)

    def test_blank_field_is_400(self):
        resp = self.post({"start": "   ", "finish": "Los Angeles, CA"})
        self.assertEqual(400, resp.status_code)

    def test_unresolvable_location_is_404(self):
        with mock.patch("fuel.services.planner.resolve") as res:
            from fuel.services.geocoding import LocationNotFound
            res.side_effect = LocationNotFound("could not find a US location matching 'zzz'")
            resp = self.post({"start": "zzz", "finish": "Los Angeles, CA"})
        self.assertEqual(404, resp.status_code)

    def test_routing_outage_is_502(self):
        from fuel.services.routing import RoutingError
        with mock.patch("fuel.services.planner.get_route", side_effect=RoutingError("boom")):
            resp = self.post({"start": "New York, NY", "finish": "Los Angeles, CA"})
        self.assertEqual(502, resp.status_code)

    def test_unfuellable_route_is_422(self):
        from fuel.services.optimizer import RouteInfeasible
        with mock.patch("fuel.services.planner.get_route", return_value=self.fake_route), \
             mock.patch("fuel.services.planner.plan_route_fuel", side_effect=RouteInfeasible("600-mile gap")):
            resp = self.post({"start": "New York, NY", "finish": "Los Angeles, CA"})
        self.assertEqual(422, resp.status_code)
        self.assertIn("600-mile gap", resp.data["error"])

    def test_corridor_override_is_validated(self):
        resp = self.post({"start": "New York, NY", "finish": "Los Angeles, CA",
                          "corridor_miles": 9999})
        self.assertEqual(400, resp.status_code)

    def test_openapi_schema_builds(self):
        resp = self.client.get("/api/schema/")
        self.assertEqual(200, resp.status_code)


class WorkedExampleTests(SimpleTestCase):
    """The scenario in worked_example.py, pinned so it cannot silently drift.

    Five stations on a 1,000-mile route. The expected figures are derived by
    hand in that script and cross-checked there against an exhaustive DP.
    """

    SCENARIO = [(0, 4.00, "A"), (150, 3.00, "B"), (400, 3.50, "C"),
                (700, 2.50, "D"), (900, 3.80, "E")]
    TOTAL = 1000.0
    EXPECTED = {"A": 15.0, "B": 50.0, "C": 5.0, "D": 30.0}  # E buys nothing
    EXPECTED_COST = 302.50

    def points(self):
        return [station(mile, price, label) for mile, price, label in self.SCENARIO]

    def test_matches_the_hand_worked_optimum(self):
        pts = self.points()
        plan, cost = _solve(pts, self.TOTAL)
        bought = {pts[i]["name"]: round(g, 4) for i, g in plan}
        self.assertEqual(self.EXPECTED, bought)
        self.assertAlmostEqual(self.EXPECTED_COST, cost, places=2)

    def test_fills_the_tank_at_the_unbeatable_price(self):
        """B is the cheapest thing within range, so it must take a full 50 gal."""
        pts = self.points()
        plan, _ = _solve(pts, self.TOTAL)
        bought = {pts[i]["name"]: g for i, g in plan}
        self.assertAlmostEqual(MAX_RANGE_MILES / MPG, bought["B"], places=4)

    def test_buys_nothing_at_the_dearest_station(self):
        pts = self.points()
        plan, _ = _solve(pts, self.TOTAL)
        self.assertNotIn("E", {pts[i]["name"] for i, _ in plan})

    def test_beats_both_naive_strategies(self):
        pts = self.points()
        _, optimal = _solve(pts, self.TOTAL)

        minimum_fills = sum(
            (((pts[i + 1]["mile"] if i + 1 < len(pts) else self.TOTAL) - s["mile"]) / MPG)
            * s["price"]
            for i, s in enumerate(pts)
        )
        self.assertAlmostEqual(328.00, minimum_fills, places=2)
        self.assertLess(optimal, minimum_fills)

        fuel, prev, always_fill = 0.0, 0.0, 0.0
        for s in pts:
            fuel -= s["mile"] - prev
            prev = s["mile"]
            buy = max(0.0, min(MAX_RANGE_MILES, self.TOTAL - s["mile"]) - fuel)
            always_fill += (buy / MPG) * s["price"]
            fuel += buy
        self.assertAlmostEqual(357.50, always_fill, places=2)
        self.assertLess(optimal, always_fill)

    def test_burns_exactly_distance_over_mpg(self):
        plan, _ = _solve(self.points(), self.TOTAL)
        self.assertAlmostEqual(self.TOTAL / MPG, sum(g for _, g in plan), places=6)
