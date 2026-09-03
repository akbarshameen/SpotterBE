"""QA check: cross-examine the bundled station coordinates against Nominatim.

The Census gazetteer and Nominatim are independent sources, so where they
disagree badly the bundled coordinate is probably wrong. This is how the
"Marion, IL resolved to a township 250 miles north" class of bug gets caught --
a state bounding-box check would not have, since the wrong point was still
inside Illinois.

    python manage.py verify_station_coords --sample 150

Rate-limited to 1 request/second per Nominatim's usage policy, so a 150-city
sample takes about three minutes.
"""
from __future__ import annotations

import json
import random
import time
import urllib.parse
import urllib.request

from django.core.management.base import BaseCommand

from fuel.services.geo import haversine
from fuel.services.stations import load_stations

NOMINATIM = "https://nominatim.openstreetmap.org/search"
UA = "spotter-fuel-optimizer/2.0 (coordinate QA; contact: repo owner)"


class Command(BaseCommand):
    help = "Sample-check bundled station coordinates against an independent geocoder."

    def add_arguments(self, parser):
        parser.add_argument("--sample", type=int, default=150,
                            help="How many stations to check (default 150).")
        parser.add_argument("--threshold", type=float, default=25.0,
                            help="Flag disagreements beyond this many miles (default 25).")
        parser.add_argument("--seed", type=int, default=20260904)

    def handle(self, *args, **opts):
        stations = load_stations()
        rng = random.Random(opts["seed"])
        sample = rng.sample(stations, min(opts["sample"], len(stations)))
        threshold = opts["threshold"]

        deltas: list[tuple[float, dict, tuple[float, float]]] = []
        unchecked = 0

        for i, s in enumerate(sample, 1):
            try:
                url = f"{NOMINATIM}?" + urllib.parse.urlencode(
                    {"q": f"{s['city']}, {s['state']}, USA", "format": "json",
                     "limit": 1, "countrycodes": "us"}
                )
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                res = json.loads(urllib.request.urlopen(req, timeout=30).read())
            except Exception as exc:
                self.stderr.write(f"  {s['city']}, {s['state']}: {type(exc).__name__} {exc}")
                unchecked += 1
                time.sleep(1.1)
                continue

            if not res:
                unchecked += 1
            else:
                ref = (float(res[0]["lat"]), float(res[0]["lon"]))
                deltas.append((haversine(s["lat"], s["lon"], ref[0], ref[1]), s, ref))
            time.sleep(1.1)
            if i % 25 == 0:
                self.stdout.write(f"    {i}/{len(sample)}")

        if not deltas:
            self.stderr.write(self.style.ERROR("nothing could be checked"))
            return

        deltas.sort(key=lambda d: -d[0])
        values = sorted(d[0] for d in deltas)
        n = len(values)
        flagged = [d for d in deltas if d[0] > threshold]

        self.stdout.write("")
        self.stdout.write(f"  checked          : {n} stations ({unchecked} unresolvable by reference)")
        self.stdout.write(f"  median disagreement: {values[n // 2]:.2f} mi")
        self.stdout.write(f"  90th percentile    : {values[int(n * 0.9)]:.2f} mi")
        self.stdout.write(f"  max                : {values[-1]:.2f} mi")
        style = self.style.SUCCESS if not flagged else self.style.WARNING
        self.stdout.write(style(f"  beyond {threshold:.0f} mi        : {len(flagged)} "
                                f"({100 * len(flagged) / n:.1f}%)"))
        for dist, s, ref in flagged[:20]:
            self.stdout.write(
                f"    {dist:7.1f} mi  {s['city']}, {s['state']:2}  "
                f"bundled=({s['lat']:.4f}, {s['lon']:.4f})  reference=({ref[0]:.4f}, {ref[1]:.4f})"
            )
