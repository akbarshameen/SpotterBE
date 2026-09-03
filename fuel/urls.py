from django.urls import path

from .views import FuelRouteView, RouteMapView

urlpatterns = [
    path("route/", FuelRouteView.as_view(), name="fuel-route"),
    path("map/", RouteMapView.as_view(), name="fuel-route-map"),
]
