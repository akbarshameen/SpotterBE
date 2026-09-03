"""US place-name geocoding from bundled Census Gazetteer data.

Serves two purposes:
  1. Assigns real coordinates to truckstops -- the price CSV has city/state but no lat/lon.
  2. Resolves user-supplied start/finish locations with no API call, which is what
     lets a request hit the routing provider exactly once.

The data file is generated offline by `manage.py build_station_index` and committed,
so nothing in this module touches the network.
"""
from __future__ import annotations

import gzip
import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PLACES_FILE = DATA_DIR / "us_places.json.gz"

# The price CSV includes Canadian truckstops; the exercise is USA-only.
CANADIAN_PROVINCES = frozenset(
    {"AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT"}
)

# Census appends a legal/statistical descriptor to every name: "Tomah city",
# "Big Cabin town", "Athens-Clarke County unified government (balance)".
# These are stripped when INDEXING so a plain "Tomah" query matches -- but never
# when QUERYING, because stripping a query would turn "Kansas City" into "Kansas".
_BALANCE = re.compile(r"\s*\(balance\)$", re.I)
_DESCRIPTOR = re.compile(
    r"\s+(?:cdp|city|town|village|borough|municipality|charter township|township|"
    r"comunidad|zona urbana|urban county|metro(?:politan)? government|"
    r"consolidated government|unified government|city and borough|town and village|"
    r"plantation|reservation|corporation|municipio|county|gore|purchase|grant|"
    r"location|precinct|district|division|unorganized territory)$",
    re.I,
)

STATE_ABBR = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "district of columbia": "DC", "florida": "FL", "georgia": "GA", "hawaii": "HI",
    "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY", "puerto rico": "PR",
}
VALID_STATES = frozenset(STATE_ABBR.values())


def normalize(name: str) -> str:
    """Fold a place name into a comparable key. Does not strip descriptors."""
    s = unicodedata.normalize("NFKD", name.strip().lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def index_keys(census_name: str) -> list[str]:
    """Every key a Census place should be indexed under."""
    base = _BALANCE.sub("", census_name.strip())
    keys = [normalize(base)]
    stripped = _DESCRIPTOR.sub("", base)
    if stripped != base:
        keys.append(normalize(stripped))
    if "-" in stripped:  # "Athens-Clarke County" also answers to "Athens"
        keys.append(normalize(stripped.split("-")[0]))
    # Space-stripped aliases reconcile the CSV's spelling with the Census's:
    # "MC DERMITT" vs "McDermitt", "Odonnell" vs "O'Donnell".
    keys += [k.replace(" ", "") for k in list(keys)]
    return [k for k in dict.fromkeys(keys) if k]


@lru_cache(maxsize=1)
def _tables() -> tuple[dict, dict]:
    """Load the committed gazetteer once per process."""
    if not PLACES_FILE.exists():
        raise RuntimeError(
            f"{PLACES_FILE.name} is missing. Build it with: "
            "python manage.py build_station_index"
        )
    with gzip.open(PLACES_FILE, "rt", encoding="utf-8") as fh:
        raw = json.load(fh)
    return raw["by_city_state"], raw["by_city"]


def lookup_city(city: str, state: str) -> tuple[float, float] | None:
    """Coordinates for a city within a specific state, or None."""
    by_city_state, _ = _tables()
    key = normalize(city)
    state = state.strip().upper()
    hit = by_city_state.get(f"{key}|{state}") or by_city_state.get(f"{key.replace(' ', '')}|{state}")
    return (hit[0], hit[1]) if hit else None


_COORD_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")


def _split_state(query: str) -> tuple[str, str | None]:
    """Peel a trailing state off a query: 'Denver, CO' / 'Denver Colorado'."""
    q = query.strip().rstrip(",")
    if "," in q:
        head, _, tail = q.rpartition(",")
        tail_norm = tail.strip().lower()
        abbr = tail.strip().upper()
        if abbr in VALID_STATES:
            return head.strip(), abbr
        if tail_norm in STATE_ABBR:
            return head.strip(), STATE_ABBR[tail_norm]
        return q, None
    words = q.split()
    if len(words) >= 2:
        if words[-1].upper() in VALID_STATES:
            return " ".join(words[:-1]), words[-1].upper()
        for n in (3, 2, 1):  # "new hampshire", "north dakota", "ohio"
            if len(words) > n:
                tail = " ".join(words[-n:]).lower()
                if tail in STATE_ABBR:
                    return " ".join(words[:-n]), STATE_ABBR[tail]
    return q, None


def geocode_local(query: str) -> tuple[tuple[float, float], str] | None:
    """Resolve a US location with zero network calls.

    Accepts "lat,lon", "City, ST", "City, State Name", or a bare "City".
    Returns ((lat, lon), resolved_label) or None if the gazetteer can't place it.
    """
    if not query or not query.strip():
        return None

    m = _COORD_RE.match(query)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return (lat, lon), f"{lat},{lon}"

    city, state = _split_state(query)
    if state:
        hit = lookup_city(city, state)
        if hit:
            return hit, f"{city.strip().title()}, {state}"
        return None  # state was explicit and wrong -- don't silently pick another

    _, by_city = _tables()
    key = normalize(city)
    hit = by_city.get(key) or by_city.get(key.replace(" ", ""))
    if hit:
        return (hit[0], hit[1]), f"{city.strip().title()}, {hit[2]}"
    return None
