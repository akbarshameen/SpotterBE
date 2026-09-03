"""HTTP layer: the JSON route endpoint and a rendered map preview."""
from __future__ import annotations

import json

from django.shortcuts import render
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import RouteRequestSerializer, RouteResponseSerializer
from .services.geocoding import GeocoderUnavailable, LocationNotFound
from .services.optimizer import RouteInfeasible
from .services.planner import build_plan
from .services.routing import RouteNotFound, RoutingError


def _error_response(exc: Exception) -> Response | None:
    """Map a planning failure onto an HTTP status, or None if unrecognised."""
    if isinstance(exc, LocationNotFound):
        return Response({"error": str(exc)}, status=status.HTTP_404_NOT_FOUND)
    if isinstance(exc, RouteNotFound):
        return Response({"error": str(exc)}, status=status.HTTP_404_NOT_FOUND)
    if isinstance(exc, GeocoderUnavailable):
        return Response({"error": f"geocoding unavailable: {exc}"},
                        status=status.HTTP_502_BAD_GATEWAY)
    if isinstance(exc, RoutingError):
        return Response({"error": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
    if isinstance(exc, RouteInfeasible):
        return Response({"error": f"cannot plan fuel for this route: {exc}"},
                        status=status.HTTP_422_UNPROCESSABLE_ENTITY)
    return None


class FuelRouteView(APIView):
    """Plan the cheapest fuel stops along a US driving route."""

    @extend_schema(
        summary="Cheapest fuel stops along a US driving route",
        description=(
            "Takes a start and finish inside the USA and returns the driving route, "
            "the cost-optimal fuel stops for a 500-mile-range vehicle doing 10 MPG, "
            "how many gallons to buy at each, and the total fuel spend.\n\n"
            "Start and finish are geocoded from bundled US Census data, so a request "
            "normally makes exactly one external routing call, and zero when the "
            "route is already cached. `meta.api_calls` reports the actual count."
        ),
        request=RouteRequestSerializer,
        responses={
            200: RouteResponseSerializer,
            400: {"description": "Invalid input"},
            404: {"description": "A location could not be resolved inside the USA"},
            422: {"description": "No legal fuelling plan exists for this route"},
            502: {"description": "Routing or geocoding provider unavailable"},
        },
        examples=[
            OpenApiExample("Coast to coast",
                           value={"start": "New York, NY", "finish": "Los Angeles, CA"},
                           request_only=True),
            OpenApiExample("Short hop",
                           value={"start": "Dallas, TX", "finish": "Houston, TX"},
                           request_only=True),
        ],
    )
    def post(self, request):
        form = RouteRequestSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        data = form.validated_data
        try:
            return Response(
                build_plan(data["start"], data["finish"], data.get("corridor_miles"))
            )
        except Exception as exc:
            handled = _error_response(exc)
            if handled is None:
                raise
            return handled


class RouteMapView(APIView):
    """Render the planned route and its fuel stops on a Leaflet map."""

    @extend_schema(
        summary="Rendered map of the route and its fuel stops",
        description="Same planning logic as the JSON endpoint, returned as an HTML page.",
        parameters=[
            OpenApiParameter("start", str, required=True, description='e.g. "New York, NY"'),
            OpenApiParameter("finish", str, required=True, description='e.g. "Los Angeles, CA"'),
            OpenApiParameter("corridor_miles", float, required=False),
        ],
        responses={200: {"type": "string", "format": "html"}},
    )
    def get(self, request):
        form = RouteRequestSerializer(data=request.query_params)
        if not form.is_valid():
            return Response(form.errors, status=status.HTTP_400_BAD_REQUEST)
        data = form.validated_data
        try:
            plan = build_plan(data["start"], data["finish"], data.get("corridor_miles"))
        except Exception as exc:
            handled = _error_response(exc)
            if handled is None:
                raise
            return handled
        return render(request, "fuel/map.html", {"plan_json": json.dumps(plan)})
