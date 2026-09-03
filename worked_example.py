"""A hand-verifiable proof that the fuel planner returns the true optimum.

Run it with no server and no network:

    python worked_example.py

Why this exists: on a real coast-to-coast route nobody can tell by eye whether
$851.84 is optimal. So this uses a five-station route small enough to solve with
pencil and paper. The expected answer below is worked out by hand, hard-coded,
and then compared against what the planner actually produces. It also prices two
plausible alternative strategies, so the saving is visible rather than asserted.
"""
import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from fuel.services.optimizer import MAX_RANGE_MILES, MPG, _solve  # noqa: E402

TOTAL_MILES = 1000.0
TANK_GALLONS = MAX_RANGE_MILES / MPG

#                mile   price   label
SCENARIO = [(0, 4.00, "A"), (150, 3.00, "B"), (400, 3.50, "C"),
            (700, 2.50, "D"), (900, 3.80, "E")]

# ---------------------------------------------------------------------------
# The expected answer, derived by hand. Nothing below is computed by the code
# under test -- these are the numbers a human arrives at applying the rule
# "if a cheaper station is in range, buy only enough to reach it; otherwise
#  you are at a local price minimum, so fill up".
# ---------------------------------------------------------------------------
HAND_WORKED = [
    # label, gallons, price, cost, reasoning
    ("A", 15.0, 4.00, 60.00,
     "B is cheaper ($3.00) and only 150 mi away, so buy just 150 mi of fuel"),
    ("B", 50.0, 3.00, 150.00,
     "next cheaper is D at mile 700 -- 550 mi away, out of range -- so fill the tank"),
    ("C",  5.0, 3.50, 17.50,
     "D is cheaper and 300 mi away; 250 mi already in tank, so top up 50 mi"),
    ("D", 30.0, 2.50, 75.00,
     "nothing cheaper ahead; 300 mi left to run, so buy exactly that"),
    ("E",  0.0, 3.80, 0.00,
     "arrives with the 100 mi it needs -- buy nothing at the most expensive stop"),
]
EXPECTED_COST = 302.50
EXPECTED_GALLONS = 100.0


def station(mile, price, label):
    return {"mile": float(mile), "price": price, "name": label, "address": "",
            "city": label, "state": "XX", "opis_id": "0", "lat": 0.0, "lon": 0.0,
            "detour_miles": 0.0}


def rule(width=80):
    print("-" * width)


def strategy_minimum_fills(points):
    """Buy exactly enough at each station to reach the next one."""
    total = 0.0
    for i, s in enumerate(points):
        hop = (points[i + 1]["mile"] if i + 1 < len(points) else TOTAL_MILES) - s["mile"]
        total += (hop / MPG) * s["price"]
    return total


def strategy_always_fill(points):
    """Top the tank up at every station you pass."""
    fuel, prev, total = 0.0, 0.0, 0.0
    for s in points:
        fuel -= s["mile"] - prev
        prev = s["mile"]
        target = min(MAX_RANGE_MILES, TOTAL_MILES - s["mile"])
        buy = max(0.0, target - fuel)
        if buy > 0:
            total += (buy / MPG) * s["price"]
            fuel += buy
    return total


def main():
    points = [station(*row) for row in SCENARIO]

    print("=" * 80)
    print(" WORKED EXAMPLE -- is the answer actually optimal?")
    print("=" * 80)
    print()
    print(f"  Vehicle : {MAX_RANGE_MILES:.0f}-mile range at {MPG:.0f} MPG "
          f"= a {TANK_GALLONS:.0f}-gallon tank")
    print(f"  Route   : {TOTAL_MILES:,.0f} miles, five stations")
    print(f"  Model   : leaves with a full tank bought at mile 0, arrives empty")
    print()
    print("     station    mile    price/gal")
    for mile, price, label in SCENARIO:
        print(f"     {label:<7}  {mile:>6,}     ${price:.2f}")
    print()
    print(f"  Fuel burned is fixed at {TOTAL_MILES:,.0f} / {MPG:.0f} "
          f"= {EXPECTED_GALLONS:.0f} gallons no matter what you do.")
    print("  The only question is which prices you pay for those gallons.")
    print()

    rule()
    print(" EXPECTED, worked out by hand")
    rule()
    print()
    print(f"  {'at':<4} {'gallons':>8} {'$/gal':>7} {'cost':>9}   why")
    for label, gallons, price, cost, why in HAND_WORKED:
        print(f"  {label:<4} {gallons:>8.2f} {price:>7.2f} {cost:>9.2f}   {why}")
    print(f"  {'':<4} {EXPECTED_GALLONS:>8.2f} {'':>7} {EXPECTED_COST:>9.2f}   <-- expected total")
    print()

    rule()
    print(" ACTUAL, from the planner")
    rule()
    print()
    plan, cost = _solve(points, TOTAL_MILES)
    bought = {points[i]["name"]: gallons for i, gallons in plan}
    gallons_total = sum(gallons for _, gallons in plan)

    print(f"  {'at':<4} {'gallons':>8} {'$/gal':>7} {'cost':>9}   {'expected':>9}  match")
    ok = True
    for label, exp_gallons, price, exp_cost, _ in HAND_WORKED:
        got = bought.get(label, 0.0)
        got_cost = got * price
        same = abs(got - exp_gallons) < 0.01 and abs(got_cost - exp_cost) < 0.01
        ok = ok and same
        print(f"  {label:<4} {got:>8.2f} {price:>7.2f} {got_cost:>9.2f} "
              f"{exp_cost:>9.2f}  {'OK' if same else 'MISMATCH'}")
    print(f"  {'':<4} {gallons_total:>8.2f} {'':>7} {cost:>9.2f} {EXPECTED_COST:>9.2f}")
    print()

    totals_match = (abs(cost - EXPECTED_COST) < 0.01
                    and abs(gallons_total - EXPECTED_GALLONS) < 0.01)
    ok = ok and totals_match

    rule()
    print(" WHY IT BEATS THE OBVIOUS ALTERNATIVES")
    rule()
    print()
    naive_min = strategy_minimum_fills(points)
    naive_full = strategy_always_fill(points)
    print(f"  top up to full at every station              ${naive_full:>8.2f}")
    print(f"  buy just enough to reach the next station    ${naive_min:>8.2f}")
    print(f"  this API                                     ${cost:>8.2f}   <-- optimal")
    print()
    print(f"  saving vs. buy-just-enough : ${naive_min - cost:.2f} "
          f"({100 * (naive_min - cost) / naive_min:.1f}%)")
    print(f"  saving vs. always-fill     : ${naive_full - cost:.2f} "
          f"({100 * (naive_full - cost) / naive_full:.1f}%)")
    print()
    print("  All three buy the same 100 gallons. The optimum wins purely by")
    print("  buying them at better prices -- filling the tank at B ($3.00) because")
    print("  nothing cheaper is reachable, and buying almost nothing at C ($3.50)")
    print("  because D ($2.50) is within range.")
    print()

    rule()
    print(f" RESULT: {'PASS -- planner matches the hand-worked optimum' if ok else 'FAIL'}")
    rule()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
