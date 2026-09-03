# Fuel Route Optimizer API

A Django REST API that plans the **cost-optimal** fuel stops along any US driving
route, for a vehicle with a 500-mile range doing 10 MPG.

```bash
curl -X POST http://localhost:8000/api/route/ -H "Content-Type: application/json" -d "{\"start\": \"New York, NY\", \"finish\": \"Los Angeles, CA\"}"
```

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/api/route/` | POST | Route + fuel plan as JSON |
| `/api/map/?start=...&finish=...` | GET | The same plan rendered on a Leaflet map |
| `/api/schema/swagger-ui/` | GET | Interactive API docs |

---

## How the exercise requirements are met

| Requirement | Where / how |
| --- | --- |
| Start + finish inside the USA | `POST /api/route/`, geocoded against bundled US-only data |
| Map of the route | Encoded polyline + bbox + Google Maps link in the JSON; `/api/map/` renders it |
| Optimal fuel-up locations | Provably optimal fill planner, see [The algorithm](#the-algorithm) |
| Multiple stops within a 500-mile range | Range enforced on true road miles; 13 stops on NYC to LA |
| Total money spent, at 10 MPG | `total_fuel_cost_usd`, with `total_gallons` = distance / 10 |
| Uses the supplied price CSV | `fuel/data/fuel_prices.csv`, geocoded offline into `stations.json` |
| Free map/routing API | OSRM (routing) and US Census Gazetteer (geocoding) — no keys, no billing |
| Latest stable Django | Django 5.2.17 LTS — see [Django version](#django-version) |
| Fast | ~130 ms warm, ~900 ms cold; the routing call dominates |
| Few routing API calls | **Exactly one**, and zero when cached. `meta.api_calls` reports it per request |

---

## The algorithm

Planning happens in two independent stages. Keeping them separate matters: it is
what stops the planner from choosing stops on one assumption and pricing them on
another.

### 1. Which stations are actually on this route

The price CSV gives a city and state per truckstop but no coordinates, so
coordinates are resolved **offline, once** (see [Data pipeline](#data-pipeline))
and committed. At request time:

1. The OSRM polyline (~35,000 vertices coast to coast) is walked in full to get
   cumulative road miles, then **rescaled to match OSRM's own reported
   distance**. Sampling the polyline instead understates NYC to LA by 77.6 miles
   (2.8%), which silently turns a 500-mile tank into 514 road miles.
2. The polyline is thinned to ~5-mile spacing for the corridor scan.
3. Of 3,813 stations, most are rejected by pure grid-cell set lookups — no
   distance arithmetic at all. Survivors are measured against route samples in
   their own cell neighbourhood, and the winner is refined against
   full-resolution vertices for a precise mile marker.

Stations within **25 miles** of the route qualify. On NYC to LA that leaves 364
candidates, computed in about 50 ms with no network access.

### 2. How much to buy at each

This is the classic *gas station problem*, and the optimal policy is short:

> At each station, if a **cheaper** station is within tank range, buy only
> enough fuel to reach it. Otherwise you are at a local price minimum, so
> **fill the tank** — or buy just enough to finish the trip, whichever is less.

Both branches are forced, which is why this is exactly optimal rather than
merely good. Finding "the nearest cheaper station ahead" for every station is a
single monotonic-stack pass, so the whole planner is **O(n)**.

The payoff is visible on NYC to LA at mile 1340, where AKAL Travel Center in
Waco, NE is the cheapest fuel for the next 500 miles. The planner buys the full
**50 gallons** there. A naive "drive to the cheapest station in range, then buy
just enough to reach the next one" greedy buys about 6 gallons and then pays
more three times over the following 300 miles.

`fuel/tests.py` verifies the greedy against an exhaustive dynamic program over
hundreds of randomised instances — that test is what justifies the word
"optimal".

### One deliberate deviation from the true optimum

The mathematical optimum sometimes buys a fraction of a gallon somewhere to
shave a fraction of a cent, which is a worse *answer* to "where should I fuel
up". Purchases under 5 gallons are therefore dropped and the plan re-solved. The
cost of that trade is reported in `meta.optimality_gap_usd` — **$0.38 on
NYC to LA**, or 0.04% — rather than hidden. Set `FUEL_MIN_PURCHASE_GALLONS=0`
for the raw optimum.

---

## Assumptions

These are stated in every response under `assumptions`, because they determine
every number returned.

- **Tank**: 500 miles of range = 50 gallons at 10 MPG.
- **Departure fill**: the vehicle leaves with a full tank, bought at the
  cheapest station within the first 50 route miles, modelled at mile 0. This is
  why `fuel_stops[0]` has `departure_fill: true` and `miles_from_start: 0.0`.
- **Arrives empty**, so `total_gallons` is exactly `distance / 10` and the sum
  of `gallons_purchased` across stops matches it. Both are asserted in the tests.
- **Detours are reported, not burned.** Each stop carries `detour_miles`
  (straight-line distance from the route), but those miles are excluded from
  fuel burn. Including them would break `total_gallons = distance / 10`, and
  measuring them properly would need one extra routing call *per stop*.
- **Prices** are the mean of each station's observations in the CSV, which lists
  many stations more than once with differing prices and no date column to order
  them by. Taking the minimum would bias every plan optimistically low.
- **Canadian truckstops are excluded** — the CSV contains 620 rows in AB, BC,
  MB, NB, NS, ON, QC, SK and YT, and the exercise is USA-only.

---

## Data pipeline

Run once; the outputs are committed so a clone needs no build step.

```bash
python manage.py build_station_index
```

1. Downloads two **US Census Gazetteer** bulk files (free, no key): *places*
   (32,333 cities/towns/CDPs) and *county subdivisions* (36,421 — chiefly New
   England towns, which are not Census "places").
2. Indexes them into 171,176 lookup keys. **Places always outrank county
   subdivisions**: rural townships are far larger in area than the cities they
   share a name with, so ranking the merged set by area put "Marion, IL" on a
   township near Rockford instead of the city on I-57, 250 miles south.
3. Resolves the CSV's 3,813 distinct US cities — **95.3% from bulk data**, the
   remaining ~180 from Nominatim at 1 request/second (about three minutes,
   cached across runs).
4. Writes `fuel/data/stations.json` (3,813 stations) and
   `fuel/data/us_places.json.gz` (2.3 MB).

**Coverage: 3,813 / 3,813 cities, with zero CSV rows dropped for want of
coordinates.**

Coordinates are city centroids, so a station's position is accurate to a few
miles rather than to the exact driveway — the 25-mile corridor is sized to
absorb that. To audit it:

```bash
python manage.py verify_station_coords --sample 150
```

That cross-checks a random sample against Nominatim, an independent source. It
exists because a state-bounding-box check would *not* have caught the Marion bug
— the wrong point was still inside Illinois.

Result on a 150-station sample:

| Metric | Value |
| --- | --- |
| Median disagreement | **0.52 mi** |
| 90th percentile | 3.97 mi |
| Beyond 25 mi | 4 (2.7%) |

All four outliers were checked by hand and are **errors in the reference, not in
the bundled data** — Nominatim returned a *county* where the CSV meant a town
(Warren, Hertford, Beaver and Blue Earth are all both). Each bundled coordinate
matches a Census town/city entry of 3–17 km², and each station's own `Address`
corroborates it: Warren, IN sits on `I-69, EXIT 278`, which runs through
Huntington County at the bundled point and nowhere near Warren County.

`us_places.json.gz` does double duty: it also geocodes the API's own `start` and
`finish` inputs, which is what keeps a request down to a single routing call.

---

## Response shape

```jsonc
{
  "start":  { "query": "New York, NY", "resolved": "New York, NY", "lat": 40.6627, "lon": -73.9387 },
  "finish": { "query": "Los Angeles, CA", "resolved": "Los Angeles, CA", "lat": 34.0194, "lon": -118.4108 },
  "route": {
    "distance_miles": 2810.5,
    "duration_hours": 50.34,
    "geometry": "...encoded polyline...",
    "bbox": [34.0194, -118.4108, 41.9057, -73.9387],
    "map_url": "https://www.google.com/maps/dir/..."
  },
  "fuel_stops": [
    {
      "order": 8,
      "name": "AKAL TRAVEL CENTER",
      "address": "I-80, EXIT 360",
      "city": "Waco", "state": "NE", "opis_id": "...",
      "lat": 40.9233, "lon": -97.4158,
      "miles_from_start": 1340.4,
      "detour_miles": 5.2,
      "price_per_gallon": 2.799,
      "gallons_purchased": 50.0,       // a full tank: cheapest for 500 miles
      "cost_usd": 139.95,
      "departure_fill": false
    }
  ],
  "total_gallons": 281.05,
  "total_fuel_cost_usd": 851.84,
  "assumptions": { "max_range_miles": 500.0, "miles_per_gallon": 10.0, "...": "..." },
  "meta": {
    "api_calls": 1,                    // 0 on a cache hit
    "elapsed_ms": 143.3,
    "candidate_stations_near_route": 364,
    "geocode_source": { "start": "gazetteer", "finish": "gazetteer" },
    "optimality_gap_usd": 0.38
  }
}
```

### Status codes

| Code | Meaning |
| --- | --- |
| 200 | Plan produced |
| 400 | Missing, blank or out-of-range input |
| 404 | A location is not in the USA, or no road connects the two |
| 422 | Route is real but unfuellable (e.g. Anchorage to Miami crosses Canada, where this dataset has no stations) |
| 502 | OSRM or Nominatim unreachable |

---

## Running locally

```bash
git clone https://github.com/akbarshameen/spotter-fuel-optimizer.git
cd spotter-fuel-optimizer
python -m venv venv
pip install -r requirements.txt
python manage.py runserver
```

Activate the virtualenv with `venv\Scripts\activate` on Windows, or
`source venv/bin/activate` on macOS and Linux.

The station data is committed, so no build step is needed — there is nothing to
migrate and no API keys to configure.

### Trying it without Postman

Swagger UI is the quickest way in, and the request body arrives pre-filled:

<http://127.0.0.1:8000/api/schema/swagger-ui/>

Expand `POST /api/route/`, click **Try it out**, then **Execute**. The map
preview needs nothing but a browser either:

<http://127.0.0.1:8000/api/map/?start=New+York,+NY&finish=Los+Angeles,+CA>

### Trying it with Postman

Import `postman_collection.json` from the repository root. It contains five
pre-filled requests — coast to coast, a trip that fits in one tank, the longest
route, and two error cases — each carrying assertions for the properties that
must hold: gallons equalling distance ÷ 10, no leg over 500 miles, every stop
inside the corridor, and at most one external API call.

```bash
python manage.py test fuel
```

39 tests, no network required — the routing provider is mocked and the optimizer
tests build their station lists by hand.

### Verifying the answer is actually optimal

On a coast-to-coast route nobody can tell by eye whether $851.84 is the best
possible number. So there is a scenario small enough to check with pencil and
paper:

```bash
python worked_example.py
```

Five stations on a 1,000-mile route. The script prints the answer worked out by
hand — including *why* each decision is forced — then the planner's actual
output beside it, then prices two plausible alternative strategies:

| Strategy | Cost |
| --- | --- |
| Top up to full at every station | $357.50 |
| Buy just enough to reach the next station | $328.00 |
| **This API** | **$302.50** |

All three buy the same 100 gallons (1,000 miles ÷ 10 MPG). The optimum wins
purely on *which* prices it pays: it fills the tank at $3.00 because nothing
cheaper is in range, and buys almost nothing at $3.50 because $2.50 is reachable.
The figures are pinned in `WorkedExampleTests` and cross-checked against an
exhaustive dynamic program.

### Demonstrating against a live server

```bash
python demo_api.py
```

Prints a five-route summary table, shows the API-call count dropping from 1 to 0
on a cached repeat, and dumps the full stop-by-stop breakdown for New York to
Los Angeles.

---

## Performance

Measured on New York to Los Angeles, 2,810 miles:

| Stage | Cold | Warm |
| --- | --- | --- |
| Geocode start + finish | 0 ms (bundled data) | 0 ms |
| OSRM routing call | ~800 ms | 0 ms (cached) |
| Corridor scan + fill planning | ~50 ms | ~50 ms |
| **Total** | **~900 ms** | **~130 ms** |

Choices behind that: the gazetteer and station grid load once at startup
(`AppConfig.ready`), not on the first request; routes are cached by rounded
coordinates; the corridor scan rejects most stations with set lookups before any
distance is computed; and the fill planner is O(n) via a monotonic stack.

The app is also stateless — no models, no database, no auth — so there is nothing
to migrate and no per-request session or user lookup.

---

## Tech stack

- Python 3.10, Django 5.2.17 LTS, Django REST Framework 3.18
- drf-spectacular (OpenAPI 3 / Swagger UI)
- [OSRM](https://project-osrm.org/) — routing, free, no key
- [US Census Gazetteer](https://www.census.gov/geographies/reference-files/time-series/geo/gazetteer-files.html) — geocoding, free, no key
- Nominatim — geocoding fallback only
- Leaflet + CARTO tiles for the map preview

### Django version

Django **6.1.1** is the latest stable release, but it requires Python 3.12 or
newer and this environment runs Python 3.10. The project therefore targets
**Django 5.2.17**, the newest release in the current LTS series (supported until
April 2028). Moving to 6.1 needs only a Python 3.12+ interpreter — no
application code changes.

---

## Known limitations

- Station coordinates are city centroids, accurate to a few miles rather than to
  the exact truckstop. The `Address` column (`"I-80, EXIT 360"`) could refine
  this further but would need per-station geocoding.
- `detour_miles` is straight-line, not driving distance, and is excluded from
  fuel burn.
- Prices are static, from the supplied CSV.
- Routes leaving the contiguous US mid-journey (Alaska via Canada) are correctly
  rejected as unfuellable rather than silently mispriced.
- The route cache is per-process; with multiple gunicorn workers each keeps its
  own. It is a latency optimisation, not a correctness requirement.
