"""Offline build step: give every truckstop in the price CSV real coordinates.

The price CSV has City/State but no lat/lon, so this resolves them once and
commits the result. Nothing at request time needs to geocode anything.

Sources, both free and key-less:
  * US Census Gazetteer bulk files (places + county subdivisions) -- ~96% of rows
  * Nominatim, rate-limited to 1 req/s, for the ~200 stragglers

Outputs:
  fuel/data/us_places.json  -- name -> coords, also used to geocode API input
  fuel/data/stations.json   -- deduped, geocoded truckstops ready to load
"""
from __future__ import annotations

import csv
import gzip
import io
import json
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from django.core.management.base import BaseCommand

from fuel.services.places import (
    CANADIAN_PROVINCES,
    DATA_DIR,
    index_keys,
    normalize,
)

CACHE_DIR = DATA_DIR / ".build_cache"
PRICES_CSV = DATA_DIR / "fuel_prices.csv"
PLACES_OUT = DATA_DIR / "us_places.json.gz"
STATIONS_OUT = DATA_DIR / "stations.json"

GAZ_YEAR = 2024
GAZ_FILES = {
    "places": f"{GAZ_YEAR}_Gaz_place_national.zip",
    "cousubs": f"{GAZ_YEAR}_Gaz_cousubs_national.zip",
}
GAZ_BASE = f"https://www2.census.gov/geo/docs/maps-data/data/gazetteer/{GAZ_YEAR}_Gazetteer"
UA = "spotter-fuel-optimizer/1.0 (build script; contact: repo owner)"
NOMINATIM = "https://nominatim.openstreetmap.org/search"


def _fetch(url: str, dest: Path, retries: int = 4) -> bytes:
    if dest.exists() and dest.stat().st_size > 100_000:
        return dest.read_bytes()
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
            raw = urllib.request.urlopen(req, timeout=120).read()
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(raw)
            return raw
        except Exception as exc:  # census.gov 404s intermittently
            last = exc
            time.sleep(2 + 2 * attempt)
    raise RuntimeError(f"could not download {url}: {last}")


def _read_gazetteer(raw: bytes) -> list[dict]:
    zf = zipfile.ZipFile(io.BytesIO(raw))
    text = zf.read(zf.namelist()[0]).decode("latin-1").splitlines()
    return [{k.strip(): v for k, v in row.items()} for row in csv.DictReader(text, delimiter="\t")]


class Command(BaseCommand):
    help = "Geocode the fuel-price CSV offline and write the committed data files."

    def add_arguments(self, parser):
        parser.add_argument(
            "--skip-nominatim",
            action="store_true",
            help="Use only the Census bulk files; leave unmatched cities unresolved.",
        )

    def handle(self, *args, **opts):
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

        # ---- 1. Census bulk gazetteer -------------------------------------
        # Tier order is load-bearing. "Places" (cities, towns, CDPs) are what a
        # postal city name usually refers to, and they always win a key.
        # "County subdivisions" are only consulted for names no place claims --
        # chiefly New England towns, which are not Census places.
        #
        # Merging the two tiers and ranking purely by land area is wrong: rural
        # townships are far larger than the cities they share a name with, so
        # e.g. "Marion, IL" resolved to a 116 km2 township near Rockford instead
        # of the city of Marion on I-57, 250 miles south.
        tiers: list[tuple[str, list[dict]]] = []
        for label, fname in GAZ_FILES.items():
            raw = _fetch(f"{GAZ_BASE}/{fname}", CACHE_DIR / fname)
            part = _read_gazetteer(raw)
            tiers.append((label, part))
            self.stdout.write(f"  {label:8} {len(part):>6} entries")

        def area(r: dict) -> float:
            try:
                return float(r.get("ALAND") or 0)
            except (TypeError, ValueError):
                return 0.0

        by_city_state: dict[str, list[float]] = {}
        by_city: dict[str, list] = {}
        for label, part in tiers:
            # Within a tier, the largest entry wins an ambiguous name, so a bare
            # "Springfield" lands on a substantial one rather than an arbitrary one.
            for r in sorted(part, key=area, reverse=True):
                state = (r.get("USPS") or "").strip().upper()
                name = (r.get("NAME") or "").strip()
                if not state or not name:
                    continue
                try:
                    lat, lon = float(r["INTPTLAT"]), float(r["INTPTLONG"])
                except (KeyError, ValueError):
                    continue
                lat, lon = round(lat, 4), round(lon, 4)
                for key in index_keys(name):
                    by_city_state.setdefault(f"{key}|{state}", [lat, lon])
                    by_city.setdefault(key, [lat, lon, state])
            self.stdout.write(
                f"    after {label:8} {len(by_city_state):>7} city+state keys"
            )
        self.stdout.write(
            self.style.SUCCESS(
                f"  gazetteer keys: {len(by_city_state)} city+state, {len(by_city)} city"
            )
        )

        # ---- 2. Resolve the price CSV ------------------------------------
        with open(PRICES_CSV, newline="", encoding="utf-8-sig") as fh:
            price_rows = list(csv.DictReader(fh))

        us_rows, dropped_ca = [], 0
        for r in price_rows:
            if (r["State"] or "").strip().upper() in CANADIAN_PROVINCES:
                dropped_ca += 1
            else:
                us_rows.append(r)
        self.stdout.write(f"  CSV: {len(price_rows)} rows, dropped {dropped_ca} Canadian, {len(us_rows)} US")

        wanted = {(r["City"].strip(), r["State"].strip().upper()) for r in us_rows}
        coords: dict[tuple[str, str], tuple[float, float]] = {}
        unresolved: list[tuple[str, str]] = []
        for city, state in sorted(wanted):
            key = normalize(city)
            # Same space-stripped fallback the runtime lookup uses, so the CSV's
            # "MC DERMITT" reconciles with the Census's "McDermitt".
            hit = by_city_state.get(f"{key}|{state}") or by_city_state.get(
                f"{key.replace(' ', '')}|{state}"
            )
            if hit:
                coords[(city, state)] = (hit[0], hit[1])
            else:
                unresolved.append((city, state))
        self.stdout.write(
            f"  matched {len(coords)}/{len(wanted)} cities from bulk data "
            f"({100 * len(coords) / len(wanted):.1f}%), {len(unresolved)} to geocode"
        )

        # ---- 3. Nominatim for the stragglers (cached across runs) --------
        nom_cache_path = CACHE_DIR / "nominatim.json"
        nom_cache: dict[str, list[float] | None] = {}
        if nom_cache_path.exists():
            nom_cache = json.loads(nom_cache_path.read_text(encoding="utf-8"))

        if unresolved and not opts["skip_nominatim"]:
            todo = [cs for cs in unresolved if f"{cs[0]}|{cs[1]}" not in nom_cache]
            self.stdout.write(f"  querying Nominatim for {len(todo)} cities at 1 req/s...")
            for i, (city, state) in enumerate(todo, 1):
                key = f"{city}|{state}"
                try:
                    url = f"{NOMINATIM}?" + urllib.parse.urlencode(
                        {"q": f"{city}, {state}, USA", "format": "json",
                         "limit": 1, "countrycodes": "us"}
                    )
                    req = urllib.request.Request(url, headers={"User-Agent": UA})
                    res = json.loads(urllib.request.urlopen(req, timeout=30).read())
                    nom_cache[key] = [float(res[0]["lat"]), float(res[0]["lon"])] if res else None
                except Exception as exc:
                    self.stderr.write(f"    {key}: {type(exc).__name__} {exc}")
                    nom_cache[key] = None
                time.sleep(1.1)  # Nominatim usage policy: max 1 req/s
                if i % 25 == 0:
                    nom_cache_path.write_text(json.dumps(nom_cache), encoding="utf-8")
                    self.stdout.write(f"    {i}/{len(todo)}")
            nom_cache_path.write_text(json.dumps(nom_cache), encoding="utf-8")

        still_missing = []
        for city, state in unresolved:
            hit = nom_cache.get(f"{city}|{state}")
            if hit:
                coords[(city, state)] = (hit[0], hit[1])
                # feed these back so API input geocoding benefits too
                by_city_state.setdefault(f"{normalize(city)}|{state}", [hit[0], hit[1]])
                by_city.setdefault(normalize(city), [hit[0], hit[1], state])
            else:
                still_missing.append((city, state))

        # ---- 4. Build the station list -----------------------------------
        # The CSV lists many stations more than once (same OPIS id, different
        # retail prices, no date column to order them by), so each station's
        # price is the mean of its observations -- the neutral estimator. Taking
        # the minimum instead would bias every plan optimistically low.
        observations: dict[tuple, dict] = {}
        skipped = 0
        for r in us_rows:
            city, state = r["City"].strip(), r["State"].strip().upper()
            pos = coords.get((city, state))
            if pos is None:
                skipped += 1
                continue
            try:
                price = float(r["Retail Price"])
            except (ValueError, KeyError, TypeError):
                skipped += 1
                continue
            opis = (r.get("OPIS Truckstop ID") or "").strip()
            name = r["Truckstop Name"].strip()
            key = (opis, name, city, state)
            entry = observations.get(key)
            if entry is None:
                observations[key] = {
                    "opis_id": opis,
                    "name": name,
                    "address": (r.get("Address") or "").strip(),
                    "city": city,
                    "state": state,
                    "prices": [price],
                    "lat": round(pos[0], 6),
                    "lon": round(pos[1], 6),
                }
            else:
                entry["prices"].append(price)

        duplicate_rows = len(us_rows) - skipped - len(observations)

        # One entry per city: at city-level coordinates two truckstops in the
        # same town are positionally identical, so only the cheaper can ever be
        # chosen. Keeping both would just slow the planner down.
        best: dict[tuple[str, str], dict] = {}
        for entry in observations.values():
            price = sum(entry["prices"]) / len(entry["prices"])
            record = {k: v for k, v in entry.items() if k != "prices"}
            record["price"] = round(price, 4)
            record["observations"] = len(entry["prices"])
            key = (entry["city"], entry["state"])
            if key not in best or record["price"] < best[key]["price"]:
                best[key] = record

        stations = sorted(best.values(), key=lambda s: (s["state"], s["city"]))
        STATIONS_OUT.write_text(json.dumps(stations, separators=(",", ":")), encoding="utf-8")
        with gzip.open(PLACES_OUT, "wt", encoding="utf-8", compresslevel=9) as fh:
            json.dump({"by_city_state": by_city_state, "by_city": by_city}, fh, separators=(",", ":"))

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"  stations.json  {len(stations)} stations  ({STATIONS_OUT.stat().st_size / 1e6:.2f} MB)"
        ))
        self.stdout.write(self.style.SUCCESS(
            f"  us_places.json {len(by_city_state)} keys  ({PLACES_OUT.stat().st_size / 1e6:.2f} MB)"
        ))
        self.stdout.write(
            f"  {len(observations)} distinct stations from {len(us_rows) - skipped} rows "
            f"({duplicate_rows} repeat observations averaged)"
        )
        if skipped:
            self.stdout.write(f"  skipped {skipped} CSV rows (no coords or unparseable price)")
        if still_missing:
            self.stdout.write(self.style.WARNING(f"  {len(still_missing)} cities unresolved:"))
            for city, state in still_missing[:20]:
                self.stdout.write(f"    {city}, {state}")
        prices = [s["price"] for s in stations]
        self.stdout.write(
            f"  price range ${min(prices):.3f} - ${max(prices):.3f}, "
            f"median ${sorted(prices)[len(prices) // 2]:.3f}"
        )
