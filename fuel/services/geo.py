"""Geodesic helpers used by the corridor search and the mile-marker maths."""
from __future__ import annotations

import math

EARTH_RADIUS_MILES = 3958.7613
MILES_PER_DEG_LAT = 69.055
DEG2RAD = math.pi / 180.0


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in miles."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(a))


def cumulative_miles(geometry: list[tuple[float, float]], total_miles: float | None = None) -> list[float]:
    """Road miles travelled at each polyline vertex.

    Two details matter here:

    * The whole polyline is walked, not a subsample. Sampling every 50th vertex
      and chording between them understates NYC->LA by 77.6 miles (2.8%), which
      silently turns a 500-mile tank range into 514 road miles.
    * The result is rescaled so the final value equals the router's own reported
      distance. Chord sums always undershoot slightly; rescaling pins the
      mile markers to real road miles.

    Because of that rescale, adjacent vertices (~0.1 miles apart) can use a flat
    equirectangular step instead of haversine -- far cheaper, and any systematic
    bias is divided out.
    """
    if len(geometry) < 2:
        return [0.0] * len(geometry)

    cum = [0.0]
    running = 0.0
    prev_lat, prev_lon = geometry[0]
    cos_prev = math.cos(prev_lat * DEG2RAD)
    sqrt = math.sqrt
    cos = math.cos
    for lat, lon in geometry[1:]:
        cos_cur = cos(lat * DEG2RAD)
        dy = (lat - prev_lat) * MILES_PER_DEG_LAT
        dx = (lon - prev_lon) * MILES_PER_DEG_LAT * 0.5 * (cos_prev + cos_cur)
        running += sqrt(dx * dx + dy * dy)
        cum.append(running)
        prev_lat, prev_lon, cos_prev = lat, lon, cos_cur

    if total_miles and running > 0:
        scale = total_miles / running
        cum = [c * scale for c in cum]
    return cum


def resample_by_distance(
    geometry: list[tuple[float, float]], cum: list[float], spacing_miles: float
) -> list[int]:
    """Indices of vertices roughly `spacing_miles` apart, always including the last.

    Used to thin the polyline for the corridor scan. A station within R miles of
    the path is within R + spacing/2 of one of these samples, so the scan radius
    is padded accordingly.
    """
    if not geometry:
        return []
    keep = [0]
    next_mark = spacing_miles
    for i, dist in enumerate(cum):
        if dist >= next_mark:
            keep.append(i)
            next_mark = dist + spacing_miles
    if keep[-1] != len(geometry) - 1:
        keep.append(len(geometry) - 1)
    return keep
