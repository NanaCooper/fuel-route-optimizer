"""
Django settings for fuel_route project.

Configuration is read from environment variables (optionally via a `.env`
file in the project root). See `.env.example` for the available keys.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env')


def env_bool(name, default=False):
    value = os.getenv(name)
    return default if value is None else value.strip().lower() in {'1', 'true', 'yes', 'on'}


def env_number(name, default, cast=float):
    value = os.getenv(name)
    return cast(value) if value else default


SECRET_KEY = os.getenv(
    'DJANGO_SECRET_KEY',
    'django-insecure-dev-only-key-change-me-in-production-0123456789',
)
DEBUG = env_bool('DJANGO_DEBUG', True)
ALLOWED_HOSTS = [h.strip() for h in os.getenv('DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',') if h.strip()]


INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'routing',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'fuel_route.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'fuel_route.wsgi.application'


DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

# Routes (the only external call) are cached on disk, so repeat requests and the map page
# are served without hitting the routing API, even across restarts and worker processes.
# Swap for Redis/Memcached in production.
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.filebased.FileBasedCache',
        'LOCATION': os.getenv('ROUTE_CACHE_DIR', str(BASE_DIR / '.cache')),
        'TIMEOUT': env_number('ROUTE_CACHE_SECONDS', 60 * 60 * 24 * 7, int),
        'OPTIONS': {'MAX_ENTRIES': 5000},
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

REST_FRAMEWORK = {
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
        'rest_framework.renderers.BrowsableAPIRenderer',
    ],
    'DEFAULT_AUTHENTICATION_CLASSES': [],
    'DEFAULT_PERMISSION_CLASSES': [],
    'UNAUTHENTICATED_USER': None,
    'EXCEPTION_HANDLER': 'routing.exceptions.api_exception_handler',
}

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {'console': {'class': 'logging.StreamHandler'}},
    'loggers': {'routing': {'handlers': ['console'], 'level': os.getenv('LOG_LEVEL', 'INFO')}},
}


# --- Fuel route planner -----------------------------------------------------
OPENROUTESERVICE_API_KEY = os.getenv('OPENROUTESERVICE_API_KEY', '')
OPENROUTESERVICE_BASE_URL = os.getenv('OPENROUTESERVICE_BASE_URL', 'https://api.openrouteservice.org')
ROUTING_TIMEOUT_SECONDS = env_number('ROUTING_TIMEOUT_SECONDS', 20.0)
ROUTING_PROFILE = os.getenv('ROUTING_PROFILE', 'driving-car')

FUEL_PLANNER = {
    'VEHICLE_RANGE_MILES': env_number('VEHICLE_RANGE_MILES', 500.0),
    'MILES_PER_GALLON': env_number('MILES_PER_GALLON', 10.0),
    # Max distance a station may sit from the route to be considered "on route".
    'CORRIDOR_MILES': env_number('CORRIDOR_MILES', 10.0),
    # Starting empty, the first fill-up may be at most this far along the route.
    'ORIGIN_RESERVE_MILES': env_number('ORIGIN_RESERVE_MILES', 50.0),
    # Overhead charged per fuel stop when optimising (time, detour). 0 = pure cheapest fuel.
    'STOP_COST_USD': env_number('STOP_COST_USD', 5.0),
    # Spacing used when resampling the route polyline for station matching.
    'ROUTE_SAMPLE_MILES': env_number('ROUTE_SAMPLE_MILES', 1.0),
    # Max points in the route geometry returned to clients (keeps payload small).
    'MAX_RESPONSE_GEOMETRY_POINTS': env_number('MAX_RESPONSE_GEOMETRY_POINTS', 1500, int),
}
