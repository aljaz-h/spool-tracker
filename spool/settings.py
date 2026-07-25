"""
Django settings for the Spool project.

All environment-specific configuration comes from environment variables
(12-factor) — see .env.example. Nothing here should differ between local
dev and the shipped Docker image.
"""

from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, False),
)
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY", default="django-insecure-dev-only-change-me")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])

# Trust the reverse proxy's X-Forwarded-Proto header (Nginx Proxy Manager,
# Traefik, nginx-proxy, and most others set this by default). Without it,
# request.build_absolute_uri() thinks every request is plain HTTP even when
# the proxy terminated real HTTPS in front of it - which silently breaks
# Trakt/Simkl's OAuth redirect_uri (it'd send http:// while https:// is
# what's registered) since gunicorn itself never sees TLS directly.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")


# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_celery_beat",
    "tracker",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "tracker.middleware.ForceCredentialChangeMiddleware",
    "tracker.middleware.ProfileTimezoneMiddleware",
    "tracker.middleware.LastSeenMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "spool.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "tracker.context_processors.active_profile",
                "tracker.context_processors.app_version",
            ],
        },
    },
]

WSGI_APPLICATION = "spool.wsgi.application"


# Database
# https://docs.djangoproject.com/en/5.1/ref/settings/#databases

DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
    )
}


# Password validation

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# Internationalization

LANGUAGE_CODE = "en-us"
TIME_ZONE = env("TIME_ZONE", default="UTC")
USE_I18N = True
USE_TZ = True


# Static & media files

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
# Only the compiled Tailwind output is collected — static/src is Tailwind's
# *input* file (it "@import"s the tailwindcss/daisyui packages, which aren't
# real files whitenoise can resolve) and must never be served or post-processed.
# The ("dist", ...) prefix form is required: STATICFILES_DIRS entries map
# directly onto STATIC_URL by default, so an unprefixed entry would serve
# app.css at /static/app.css instead of the /static/dist/app.css templates
# reference.
STATICFILES_DIRS = [
    ("dist", BASE_DIR / "static" / "dist"),
    ("vendor", BASE_DIR / "static" / "vendor"),
    ("img", BASE_DIR / "static" / "img"),
]
STORAGES = {
    # Explicit because setting STORAGES at all replaces Django's whole
    # default dict, not just the keys listed - without this, user-uploaded
    # files (e.g. avatar_image) have no "default" backend to resolve to.
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

MEDIA_URL = "media/"
MEDIA_ROOT = env("MEDIA_ROOT", default=str(BASE_DIR / "media"))

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# Auth redirects — Spool has no public signup; login is the entry point.

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"


# Redis / Celery

REDIS_URL = env("REDIS_URL", default="redis://localhost:6379/0")
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default=REDIS_URL)
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default=REDIS_URL)
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"
# Without these, a request-path .apply_async() call (e.g. the OAuth
# callback kicking off an immediate sync) hangs for a very long time if
# Redis is briefly unreachable — broker_connection_retry defaults to
# retrying the *connection* itself with backoff, which per-call
# retry=False does not override (that only skips retrying the publish
# once connected). Confirmed by reproducing the hang locally with no
# Redis running before adding this.
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = False
CELERY_BROKER_CONNECTION_RETRY = False
CELERY_BROKER_CONNECTION_TIMEOUT = 3
CELERY_BROKER_TRANSPORT_OPTIONS = {"socket_connect_timeout": 3, "socket_timeout": 3}

# Shared across gunicorn's multiple worker processes (LocMemCache, Django's
# default, is per-process - each worker would hit TMDB separately for the
# same trending/popular list). A separate Redis DB index from Celery's
# broker/backend (REDIS_URL, above) so the two don't share a keyspace.
# Explicit short socket timeouts - the Celery broker connection hang
# (CELERY_BROKER_CONNECTION_TIMEOUT, above) was caused by exactly this
# class of bug: redis-py's default connect behavior doesn't bound how long
# an unreachable Redis can stall a request. tmdb.py's _list_request() also
# wraps cache access in try/except as a second layer, since even a 2s
# stall on every single discovery-page request if Redis is down for a
# while is worth avoiding, not just bounding.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": env("CACHE_URL", default="redis://localhost:6379/2"),
        "OPTIONS": {"socket_connect_timeout": 2, "socket_timeout": 2},
    }
}


# Third-party API credentials (used by tracker/integrations, §6 of the addendum)

TRAKT_CLIENT_ID = env("TRAKT_CLIENT_ID", default="")
TRAKT_CLIENT_SECRET = env("TRAKT_CLIENT_SECRET", default="")
SIMKL_CLIENT_ID = env("SIMKL_CLIENT_ID", default="")
SIMKL_CLIENT_SECRET = env("SIMKL_CLIENT_SECRET", default="")
TMDB_API_KEY = env("TMDB_API_KEY", default="")
