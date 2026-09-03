
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get('SECRET_KEY', 'django-insecure-7zp_uf#!l&#9wg%ggd-f@@l6ba6@lor)52z&b-h9i$k$$)-ot@')

DEBUG = os.environ.get('DEBUG', 'True') == 'True'

ALLOWED_HOSTS = ['*']


# The API is stateless: no models, no sessions, no auth. Dropping the
# database-backed contrib apps removes a whole layer of startup work and
# means there is no database to provision or migrate.
INSTALLED_APPS = [
    'django.contrib.staticfiles',
    'rest_framework',
    'drf_spectacular',
    'fuel',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

REST_FRAMEWORK = {
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    # A public, read-only, stateless endpoint. Turning the auth layer off is
    # what lets django.contrib.auth stay uninstalled; UNAUTHENTICATED_USER must
    # be None or DRF will try to build an AnonymousUser on every request.
    'DEFAULT_AUTHENTICATION_CLASSES': [],
    'DEFAULT_PERMISSION_CLASSES': ['rest_framework.permissions.AllowAny'],
    'UNAUTHENTICATED_USER': None,
}

SPECTACULAR_SETTINGS = {
    'TITLE': 'Fuel Route Optimizer API',
    'DESCRIPTION': (
        'Finds the cost-optimal fuel stops along a US driving route for a '
        'vehicle with a 500-mile range doing 10 MPG.'
    ),
    'VERSION': '2.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
}

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


# No models, so no database is configured or needed.
DATABASES = {}

# In-process cache for resolved routes and geocoder fallbacks. A repeat request
# for the same pair of endpoints costs zero external API calls.
# Note: with multiple gunicorn workers this cache is per-worker. That is fine
# here -- it is a latency optimisation, not a correctness requirement. Point
# CACHE_URL at Redis if a shared cache is ever wanted.
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'fuel-route-cache',
        'TIMEOUT': 86_400,
        'OPTIONS': {'MAX_ENTRIES': 2_000},
    }
}

# --- Fuel planning -----------------------------------------------------------
# How far off the route a truckstop may sit and still count as a stop. Station
# coordinates are city-level, so this also absorbs a few miles of that error.
FUEL_CORRIDOR_MILES = float(os.environ.get('FUEL_CORRIDOR_MILES', 25))
# Widened only when a route would otherwise have an unfuellable gap.
FUEL_CORRIDOR_FALLBACKS = (FUEL_CORRIDOR_MILES, 50.0, 100.0)
# Window at the start of the route for the "fill before you leave" stop.
FUEL_ORIGIN_WINDOW_MILES = float(os.environ.get('FUEL_ORIGIN_WINDOW_MILES', 50))
# Purchases smaller than this are dropped and the plan re-solved; the raw
# optimum otherwise emits sub-gallon stops that are useless as instructions.
FUEL_MIN_PURCHASE_GALLONS = float(os.environ.get('FUEL_MIN_PURCHASE_GALLONS', 5))
# Load the gazetteer and station index at boot rather than on the first request.
FUEL_WARM_ON_STARTUP = os.environ.get('FUEL_WARM_ON_STARTUP', 'True') == 'True'

# --- External providers ------------------------------------------------------
OSRM_URL = os.environ.get('OSRM_URL', 'https://router.project-osrm.org/route/v1/driving')
OSRM_TIMEOUT = float(os.environ.get('OSRM_TIMEOUT', 20))
ROUTE_CACHE_SECONDS = int(os.environ.get('ROUTE_CACHE_SECONDS', 86_400))
GEOCODER_USER_AGENT = os.environ.get('GEOCODER_USER_AGENT', 'spotter-fuel-optimizer/2.0')


# Internationalization
# https://docs.djangoproject.com/en/5.2/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.2/howto/static-files/

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

# STATICFILES_STORAGE was removed in Django 5.1; setting it is silently ignored,
# so the backend goes here instead. The non-manifest variant is deliberate: the
# app serves no static assets of its own (Swagger UI and Leaflet both load from
# CDNs), and the manifest variant would make every request depend on
# collectstatic having run first.
STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': 'whitenoise.storage.CompressedStaticFilesStorage',
    },
}

# Default primary key field type
# https://docs.djangoproject.com/en/5.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
