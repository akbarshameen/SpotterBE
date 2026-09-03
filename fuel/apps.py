from django.apps import AppConfig
from django.conf import settings


class FuelConfig(AppConfig):
    name = "fuel"

    def ready(self):
        """Load the gazetteer and station index before the first request.

        Both are read-only and process-wide, so paying the ~0.5s once at boot
        keeps it off the critical path of whichever request happens to be first.
        Silently skipped if the data files have not been built yet, so that
        `manage.py build_station_index` can run on a fresh checkout.
        """
        if not getattr(settings, "FUEL_WARM_ON_STARTUP", True):
            return
        try:
            from .services.places import _tables
            from .services.stations import load_stations, station_grid

            _tables()
            load_stations()
            station_grid()
        except Exception:
            # Missing or half-built data files must not stop the process from
            # booting -- the endpoint reports the problem per-request instead.
            pass
