from __future__ import annotations

import polyline
import requests
from django.conf import settings
from django.core.cache import cache

OSRM_URL = getattr(settings, "OSRM_URL", "https://router.project-osrm.org/route/v1/driving")
OSRM_TIMEOUT = getattr(settings, "OSRM_TIMEOUT", 20)
ROUTE_CACHE_SECONDS = getattr(settings, "ROUTE_CACHE_SECONDS", 86_400)
METERS_PER_MILE = 1609.344


class RoutingError(Exception):
    """The routing provider could not be reached or returned something unusable."""


class RouteNotFound(RoutingError):
    """The provider was reached but no driving route exists between the points."""


def get_route(start: tuple[float, float], finish: tuple[float, float]) -> dict:
    """Driving route between two (lat, lon) points.

    Only the encoded polyline is cached, not the decoded vertex list -- decoding
    is a few milliseconds, while caching 34k tuples per route is not worth the
    memory.
    """
    key = f"osrm:{start[0]:.4f},{start[1]:.4f}:{finish[0]:.4f},{finish[1]:.4f}"
    hit = cache.get(key)
    if hit:
        return {**hit, "geometry": polyline.decode(hit["encoded_geometry"]), "api_calls": 0}

    coords = f"{start[1]},{start[0]};{finish[1]},{finish[0]}"
    try:
        resp = requests.get(
            f"{OSRM_URL}/{coords}",
            params={"overview": "full", "geometries": "polyline"},
            timeout=OSRM_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise RoutingError(f"routing provider unavailable: {exc}") from exc

    # OSRM reports "no route" as HTTP 400 with the reason in the JSON body, so
    # the body is parsed before the status is judged. Calling raise_for_status()
    # first would turn "these points aren't connected by road" into a 502.
    try:
        data = resp.json()
    except ValueError:
        data = None

    if isinstance(data, dict):
        code = data.get("code")
        # NoRoute: nothing drivable between them (e.g. Hawaii to the mainland).
        # NoSegment: a coordinate is nowhere near a road.
        if code in {"NoRoute", "NoSegment"}:
            raise RouteNotFound(
                "no drivable route exists between those two locations"
                if code == "NoRoute"
                else "one of those locations is not near a drivable road"
            )
        if code and code != "Ok":
            raise RoutingError(f"routing provider error: {code}")

    if not resp.ok:
        raise RoutingError(f"routing provider returned HTTP {resp.status_code}")
    if not isinstance(data, dict) or not data.get("routes"):
        raise RoutingError("routing provider returned a malformed response")

    route = data["routes"][0]
    encoded = route["geometry"]
    payload = {
        "distance_miles": route["distance"] / METERS_PER_MILE,
        "duration_hours": route["duration"] / 3600.0,
        "encoded_geometry": encoded,
    }
    cache.set(key, payload, ROUTE_CACHE_SECONDS)
    return {**payload, "geometry": polyline.decode(encoded), "api_calls": 1}
