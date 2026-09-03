"""Truckstop dataset plus a spatial grid that makes corridor queries cheap.

Loaded and indexed once per process, so a request pays nothing for it.
"""
from __future__ import annotations

import json
import math
from functools import lru_cache

from .places import DATA_DIR

STATIONS_FILE = DATA_DIR / "stations.json"

# 1 degree of latitude is ~69 miles, so checking a cell plus its 8 neighbours
# always covers the 25-mile corridor with room to spare, even where longitude
# degrees are shortest (northern latitudes).
GRID_DEG = 1.0
MILES_PER_DEG_LAT = 69.055


def _cell(lat: float, lon: float) -> tuple[int, int]:
    return (math.floor(lat / GRID_DEG), math.floor(lon / GRID_DEG))


@lru_cache(maxsize=1)
def load_stations() -> list[dict]:
    """All geocoded truckstops, one per city, cheapest price per city."""
    if not STATIONS_FILE.exists():
        raise RuntimeError(
            f"{STATIONS_FILE.name} is missing. Build it with: "
            "python manage.py build_station_index"
        )
    stations = json.loads(STATIONS_FILE.read_text(encoding="utf-8"))
    for s in stations:
        # cached for the equirectangular distance approximation below
        s["coslat"] = math.cos(math.radians(s["lat"]))
    return stations


@lru_cache(maxsize=1)
def station_grid() -> dict[tuple[int, int], list[int]]:
    """Map of grid cell -> indices into load_stations()."""
    grid: dict[tuple[int, int], list[int]] = {}
    for i, s in enumerate(load_stations()):
        grid.setdefault(_cell(s["lat"], s["lon"]), []).append(i)
    return grid


# A cell plus its 8 neighbours, precomputed so the hot loop allocates nothing.
NEIGHBOUR_OFFSETS = tuple(
    (dr, dc) for dr in (-1, 0, 1) for dc in (-1, 0, 1)
)
