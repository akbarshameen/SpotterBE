"""Request/response schemas for the route endpoint."""
from __future__ import annotations

from rest_framework import serializers


class RouteRequestSerializer(serializers.Serializer):
    start = serializers.CharField(
        max_length=200, trim_whitespace=True,
        help_text='Start location in the USA, e.g. "New York, NY" or "40.71,-74.01".',
    )
    finish = serializers.CharField(
        max_length=200, trim_whitespace=True,
        help_text='Finish location in the USA, e.g. "Los Angeles, CA".',
    )
    corridor_miles = serializers.FloatField(
        required=False, min_value=1.0, max_value=200.0,
        help_text="How far off-route a fuel stop may be. Defaults to 25 miles.",
    )

    def validate_start(self, value: str) -> str:
        return self._non_blank(value, "start")

    def validate_finish(self, value: str) -> str:
        return self._non_blank(value, "finish")

    @staticmethod
    def _non_blank(value: str, field: str) -> str:
        value = value.strip()
        if not value:
            raise serializers.ValidationError(f"{field} cannot be blank")
        return value


class FuelStopSerializer(serializers.Serializer):
    order = serializers.IntegerField()
    name = serializers.CharField()
    address = serializers.CharField(allow_blank=True)
    city = serializers.CharField()
    state = serializers.CharField()
    opis_id = serializers.CharField(allow_blank=True)
    lat = serializers.FloatField()
    lon = serializers.FloatField()
    miles_from_start = serializers.FloatField()
    detour_miles = serializers.FloatField(help_text="Straight-line distance from the route.")
    price_per_gallon = serializers.FloatField()
    gallons_purchased = serializers.FloatField()
    cost_usd = serializers.FloatField()
    departure_fill = serializers.BooleanField(
        help_text="True for the tank filled before leaving the origin."
    )


class LocationSerializer(serializers.Serializer):
    query = serializers.CharField()
    resolved = serializers.CharField()
    lat = serializers.FloatField()
    lon = serializers.FloatField()


class RouteInfoSerializer(serializers.Serializer):
    distance_miles = serializers.FloatField()
    duration_hours = serializers.FloatField()
    geometry = serializers.CharField(help_text="Encoded polyline (precision 5) of the road path.")
    bbox = serializers.ListField(child=serializers.FloatField(),
                                 help_text="[min_lat, min_lon, max_lat, max_lon]")
    map_url = serializers.CharField(help_text="Google Maps directions link including fuel stops.")


class RouteResponseSerializer(serializers.Serializer):
    start = LocationSerializer()
    finish = LocationSerializer()
    route = RouteInfoSerializer()
    fuel_stops = FuelStopSerializer(many=True)
    total_gallons = serializers.FloatField()
    total_fuel_cost_usd = serializers.FloatField()
    assumptions = serializers.DictField()
    meta = serializers.DictField()
