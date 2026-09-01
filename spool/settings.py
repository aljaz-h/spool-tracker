"""
Django settings for the Spool project.

All environment-specific configuration comes from environment variables
(12-factor) — see .env.example. Nothing here should differ between local
dev and the shipped Docker image.
"""

import sys
from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, False),
)
environ.Env.read_env(BASE_DIR / ".env")

_INSECURE_SECRET_KEY_PLACEHOLDERS = {
    "django-insecure-dev-only-change-me",  # this module's own default, below
    "changeme",  # .env.example's own placeholder value
}

SECRET_KEY = env("DJANGO_SECRET_KEY", default="django-insecure-dev-only-change-me")
DEBUG = env("DEBUG")
# Fails at settings-import time - before manage.py migrate, gunicorn's own
# boot, or a Celery worker/beat process gets anywhere near serving a
# request or touching the database - rather than letting a forgotten or
# copy-pasted-but-unedited DJANGO_SECRET_KEY (docs/CONFIGURATION.md already
# documents it as required, with "no safe default") start the app up
# anyway, silently signing sessions/CSRF tokens/password-reset links with a
# key anyone can find in this repo's own source or .env.example. DEBUG-mode
# local dev is deliberately exempt - the placeholder default exists
# specifically so `manage.py runserver` works out of the box without a
# .env file at all (see docs/DEVELOPMENT.md).
if not DEBUG and SECRET_KEY in _INSECURE_SECRET_KEY_PLACEHOLDERS:
    raise ImproperlyConfigured(
        "DJANGO_SECRET_KEY is missing or still set to a placeholder value, and DEBUG=False. "
        "Generate a real secret (setup.sh/setup.ps1 do this automatically for a new install) "
        "and set DJANGO_SECRET_KEY in your .env - see docs/CONFIGURATION.md."
    )
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])

# Trust the reverse proxy's X-Forwarded-Proto header (Nginx Proxy Manager,
# Traefik, nginx-proxy, and most others set this by default). Without it,
# request.build_absolute_uri() thinks every request is plain HTTP even when
# the proxy terminated real HTTPS in front of it - which silently breaks
# Trakt/Simkl's OAuth redirect_uri (it'd send http:// while https:// is
# what's registered) since gunicorn itself never sees TLS directly.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Off by default, same reasoning as the transport-security flags above -
# request/SQL-query profiling has a real per-request cost and its own
# dashboard (/silk/), neither of which should be paid or exposed on every
# install just because it's installed. Meant to be flipped on temporarily
# to capture real production traffic (docs/CONFIGURATION.md), then back
# off - not left running permanently. INSTALLED_APPS/MIDDLEWARE below only
# add Silk's app/middleware when this is true, so a disabled install pays
# no per-request cost at all, not just a hidden one.
SILK_ENABLED = env.bool("SILK_ENABLED", default=False)


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
if SILK_ENABLED:
    INSTALLED_APPS.append("silk")

MIDDLEWARE = [
    # First, so it wraps every other middleware's response and compresses
    # last (Django runs response middleware bottom-to-top, so the first
    # entry in this list touches the response last, after everyone
    # else's HTML/JSON is finalized). WhiteNoise's own static-file
    # responses already come precompressed with Content-Encoding set, so
    # this is a no-op for those - it only ever applies to dynamic pages,
    # which WhiteNoise doesn't touch.
    "django.middleware.gzip.GZipMiddleware",
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
if SILK_ENABLED:
    # As close to the top of the list as possible (Silk's own recommendation)
    # so its timing covers nearly the whole request/response cycle, not just
    # what happens after the middleware ahead of it - only GZipMiddleware
    # stays ahead of it, since that one specifically needs to be outermost
    # (see its own comment above) to compress what Silk (and everything
    # else) already produced.
    MIDDLEWARE.insert(1, "silk.middleware.SilkyMiddleware")

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
# db and web are separate containers (docker-compose.yml) - without this,
# Django opens a brand new TCP connection (plus Postgres auth) on every
# single request instead of reusing one across a request's own lifetime
# and the next. CONN_HEALTH_CHECKS makes a request that picks up a pooled
# connection gone stale (Postgres restarted, a network blip) reconnect
# transparently instead of surfacing that as a request error.
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DB_CONN_MAX_AGE", default=60)
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True


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
# `manage.py test` has no real Redis to talk to in most dev/CI shells, and
# CACHE_URL only overrides the location above, not the backend - so every
# cache read/write across the ~1800-test suite was paying up to
# socket_connect_timeout (2s) before tmdb.py's own try/except degraded it,
# which dwarfed any test's own runtime.
#
# DummyCache, not LocMemCache - LocMemCache actually works (unlike the
# always-unreachable Redis it replaces here), which sounds like a strict
# improvement but isn't: it persists across every test in the same
# process, and plenty of integration tests across this suite reuse the
# same id (e.g. JikanGetAnimeDetailsTests' get_anime_details(269), called
# from several tests each mocking a different response) trusting that
# each call actually hits their own mock. A real, working cache silently
# hands a later test an earlier test's stale cached value instead
# (confirmed - this exact leak broke 3 Jikan tests the one time this was
# tried with LocMemCache). DummyCache never stores anything at all, so
# every read/write is an instant, allocation-free no-op - it reproduces
# the "caching is effectively off" behavior an unreachable Redis was
# already accidentally providing (just without the 2s timeout tax to get
# there), rather than genuinely turning caching on for a test suite that
# was never written with cross-test cache isolation in mind.
if "test" in sys.argv:
    CACHES["default"] = {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}


# Third-party API credentials (used by tracker/integrations, §6 of the addendum)

TRAKT_CLIENT_ID = env("TRAKT_CLIENT_ID", default="")
TRAKT_CLIENT_SECRET = env("TRAKT_CLIENT_SECRET", default="")
SIMKL_CLIENT_ID = env("SIMKL_CLIENT_ID", default="")
SIMKL_CLIENT_SECRET = env("SIMKL_CLIENT_SECRET", default="")
TMDB_API_KEY = env("TMDB_API_KEY", default="")
MDBLIST_API_KEY = env("MDBLIST_API_KEY", default="")

# MDBList supplementary-ratings tuning (tracker/tasks.py fetch_mdblist_ratings/
# _classify_next_refresh) - tunable without a code change since the right
# values depend on real usage/quota data the admin won't have until this has
# been running a while.
MDBLIST_NEWLY_RELEASED_DAYS = env.int("MDBLIST_NEWLY_RELEASED_DAYS", default=90)
MDBLIST_OBSCURE_VOTE_THRESHOLD = env.int("MDBLIST_OBSCURE_VOTE_THRESHOLD", default=500)
MDBLIST_REFRESH_UPCOMING_DAYS = env.int("MDBLIST_REFRESH_UPCOMING_DAYS", default=2)
MDBLIST_REFRESH_NEW_DAYS = env.int("MDBLIST_REFRESH_NEW_DAYS", default=2)
MDBLIST_REFRESH_OLDER_DAYS = env.int("MDBLIST_REFRESH_OLDER_DAYS", default=21)
MDBLIST_REFRESH_OBSCURE_DAYS = env.int("MDBLIST_REFRESH_OBSCURE_DAYS", default=10)
MDBLIST_REFRESH_NOT_FOUND_DAYS = env.int("MDBLIST_REFRESH_NOT_FOUND_DAYS", default=30)
# Free tier is 1,000 requests/day - pause a bit early (950) rather than
# racing the exact limit.
MDBLIST_DAILY_QUOTA = env.int("MDBLIST_DAILY_QUOTA", default=1000)
MDBLIST_QUOTA_PAUSE_AT = env.int("MDBLIST_QUOTA_PAUSE_AT", default=950)


# Transport security - all opt-in via env var, defaulting to Django's own
# (off) defaults, so upgrading an existing self-hosted instance never
# silently changes behavior. A self-hosted install may be plain HTTP on
# a local network with no reverse proxy at all, in which case forcing
# these on would make the app unreachable (a Secure cookie is never sent
# back over plain HTTP) - see docs/CONFIGURATION.md and SECURITY.md for
# when to turn each of these on. SECURE_PROXY_SSL_HEADER above already
# makes Django trust X-Forwarded-Proto for is_secure()/these checks, so
# they work correctly once a real TLS-terminating reverse proxy is in
# front of the app.
SESSION_COOKIE_SECURE = env.bool("SESSION_COOKIE_SECURE", default=False)
CSRF_COOKIE_SECURE = env.bool("CSRF_COOKIE_SECURE", default=False)
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=False)
# 0 (off) until an operator opts in - enabling HSTS on a domain that
# later needs to fall back to plain HTTP (e.g. TLS cert issue, moving
# behind a different proxy) locks out browsers that already cached the
# header for its max-age. include_subdomains/preload are separate flags
# since turning those on is a stronger, less reversible commitment than
# HSTS alone (preload lists are especially slow to undo).
SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", default=False)
SECURE_HSTS_PRELOAD = env.bool("SECURE_HSTS_PRELOAD", default=False)


# Logging - Django's own built-in default silently drops every request-
# handler exception (a bare 500) once DEBUG=False: its only handler for
# 'django.request' is mail_admins (dead weight without ADMINS/a working
# email backend configured, which nothing here sets up), and the default
# 'console' handler is filtered to DEBUG=True only - so a production 500
# left no trace anywhere, not even in `docker compose logs`, which is
# where an operator would actually look. This routes it to stdout
# instead (already captured by Docker's own log driver) regardless of
# DEBUG, without ever putting a traceback in front of an end user - that
# risk stays exactly DEBUG's job, untouched by this.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {"format": "[{asctime}] {levelname} {name}: {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "simple"},
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "django.request": {
            "handlers": ["console"],
            "level": "ERROR",
            "propagate": False,
        },
    },
}


# django-silk (request/SQL/Python profiling) - only meaningful when
# SILK_ENABLED actually added it to INSTALLED_APPS/MIDDLEWARE above; these
# settings are otherwise inert.
if SILK_ENABLED:
    # Gates /silk/ itself behind a real login + is_staff, not just "reachable
    # by anyone who knows the URL" - it exposes every recorded request's
    # full SQL (including parameter values) and, with the profiler on,
    # Python call stacks. bootstrap_admin's own admin user already gets
    # is_staff=True (same flag django.contrib.admin itself gates on), so
    # nothing extra needs configuring to use this as the one production
    # account allowed to view it.
    SILKY_AUTHENTICATION = True
    SILKY_AUTHORISATION = True
    SILKY_PERMISSIONS = lambda user: user.is_staff
    # cProfile-level Python profiling on top of Silk's own default SQL/
    # timing capture - "where is the time going" inside a slow view, not
    # just "how many queries did it run and how long did they take".
    # Real per-request overhead on top of the SQL capture, which is exactly
    # why this whole feature is opt-in rather than always-on (see
    # SILK_ENABLED's own comment above).
    SILKY_PYTHON_PROFILER = True
    SILKY_PYTHON_PROFILER_BINARY = True
