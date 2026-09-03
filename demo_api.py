"""Manual smoke script against a running server -- handy for a walkthrough.

    python manage.py runserver
    python demo_api.py

Not part of the test suite (`python manage.py test fuel` covers that offline).
This one deliberately hits the real OSRM endpoint so the API-call count and
warm-vs-cold latency are visible.
"""
import sys
import time

import requests

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"

ROUTES = [
    ("New York, NY", "Los Angeles, CA"),
    ("Chicago, IL", "Houston, TX"),
    ("Seattle, WA", "Miami, FL"),
    ("Boston, MA", "Denver, CO"),
    ("Dallas, TX", "Houston, TX"),
]


def plan(start, finish):
    began = time.perf_counter()
    resp = requests.post(
        f"{BASE}/api/route/", json={"start": start, "finish": finish}, timeout=90
    )
    return resp, (time.perf_counter() - began) * 1000


def main():
    header = f"{'route':34} {'miles':>7} {'stops':>6} {'gallons':>8} {'cost':>10} {'api':>4} {'ms':>7}"
    print(header)
    print("-" * len(header))

    for start, finish in ROUTES:
        resp, elapsed = plan(start, finish)
        if resp.status_code != 200:
            print(f"{start + ' -> ' + finish:34} HTTP {resp.status_code}: {resp.text[:70]}")
            continue
        d = resp.json()
        print(
            f"{start + ' -> ' + finish:34} {d['route']['distance_miles']:7.0f} "
            f"{len(d['fuel_stops']):6} {d['total_gallons']:8.1f} "
            f"${d['total_fuel_cost_usd']:9,.2f} {d['meta']['api_calls']:4} {elapsed:7.0f}"
        )

    print()
    print("Cache check -- the same route again should report 0 API calls:")
    for attempt in (1, 2):
        resp, elapsed = plan(*ROUTES[0])
        d = resp.json()
        print(f"  attempt {attempt}: {d['meta']['api_calls']} api calls, {elapsed:.0f} ms")

    print()
    print(f"Detail for {ROUTES[0][0]} -> {ROUTES[0][1]}:")
    d = plan(*ROUTES[0])[0].json()
    print(f"  {'#':>2} {'station':30} {'where':22} {'mile':>7} {'off':>5} {'$/gal':>6} {'gal':>7} {'cost':>9}")
    for s in d["fuel_stops"]:
        where = f"{s['city']}, {s['state']}"
        print(
            f"  {s['order']:>2} {s['name'][:29]:30} {where:22} {s['miles_from_start']:7.1f} "
            f"{s['detour_miles']:5.1f} {s['price_per_gallon']:6.3f} "
            f"{s['gallons_purchased']:7.2f} {s['cost_usd']:9.2f}"
        )
    print(f"  total: {d['total_gallons']} gallons, ${d['total_fuel_cost_usd']}")
    print(f"  map:   {BASE}/api/map/?start={ROUTES[0][0]}&finish={ROUTES[0][1]}")


if __name__ == "__main__":
    main()
