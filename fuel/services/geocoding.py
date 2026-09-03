"""Turn a user-supplied location string into coordinates.

The bundled Census gazetteer answers almost everything with no network call,
which is what keeps a request down to a single routing call. Nominatim is only
consulted for input the gazetteer cannot place -- street addresses, ZIPs, typos.
"""
from __future__ import annotations

from dataclasses import dataclass

import requests
from django.conf import settings
from django.core.cache import cache

from .places import geocode_local

NOMINATIM_URL = getattr(settings, "NOMINATIM_URL", "https://nominatim.openstreetmap.org/search")
NOMINATIM_TIMEOUT = getattr(settings, "NOMINATIM_TIMEOUT", 10)
GEOCODE_CACHE_SECONDS = getattr(settings, "GEOCODE_CACHE_SECONDS", 86_400)
USER_AGENT = getattr(settings, "GEOCODER_USER_AGENT", "spotter-fuel-optimizer/2.0")


class LocationNotFound(Exception):
    """The location could not be resolved to a point inside the USA."""


class GeocoderUnavailable(Exception):
    """The fallback geocoder could not be reached."""


@dataclass(frozen=True)
class Resolved:
    lat: float
    lon: float
    label: str
    source: str      # "gazetteer" | "nominatim" | "cache"
    api_calls: int

    @property
    def coords(self) -> tuple[float, float]:
        return (self.lat, self.lon)


def resolve(query: str) -> Resolved:
    """Resolve a US location, preferring the offline gazetteer."""
    query = (query or "").strip()
    if not query:
        raise LocationNotFound("location is empty")

    local = geocode_local(query)
    if local:
        (lat, lon), label = local
        return Resolved(lat, lon, label, "gazetteer", 0)

    key = f"nominatim:{query.lower()}"
    hit = cache.get(key)
    if hit:
        return Resolved(hit[0], hit[1], hit[2], "cache", 0)

    try:
        resp = requests.get(
            NOMINATIM_URL,
            params={"q": query, "format": "json", "limit": 1, "countrycodes": "us"},
            headers={"User-Agent": USER_AGENT},
            timeout=NOMINATIM_TIMEOUT,
        )
        resp.raise_for_status()
        results = resp.json()
    except requests.RequestException as exc:
        raise GeocoderUnavailable(str(exc)) from exc
    except ValueError as exc:
        raise GeocoderUnavailable("malformed geocoder response") from exc

    if not results:
        raise LocationNotFound(f"could not find a US location matching {query!r}")

    top = results[0]
    lat, lon = float(top["lat"]), float(top["lon"])
    label = top.get("display_name", query)
    cache.set(key, (lat, lon, label), GEOCODE_CACHE_SECONDS)
    return Resolved(lat, lon, label, "nominatim", 1)
