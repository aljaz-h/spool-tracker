from django.conf import settings
from django.contrib import admin
from django.db import connection
from django.db.utils import OperationalError
from django.http import HttpResponse
from django.urls import include, path, re_path
from django.views.static import serve as serve_static

from api.main import api


def healthz(request):
    """Actually probes the DB and Redis connections rather than just
    confirming the WSGI process is up — a process that's up but can't
    reach either dependency should fail the container healthcheck so
    orchestration (Docker Compose's `condition: service_healthy`) doesn't
    treat it as ready."""
    problems = []

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except OperationalError:
        problems.append("database")

    try:
        import redis

        redis.from_url(settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2).ping()
    except Exception:
        problems.append("redis")

    if problems:
        return HttpResponse(f"unhealthy: {', '.join(problems)}", status=503)
    return HttpResponse("ok")


urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz", healthz, name="healthz"),
    path("api/", api.urls),
    # User-uploaded media (currently just profile avatars) - served by
    # Django itself rather than whitenoise (that's collectstatic/versioned-
    # asset-only, wrong tool for content that changes at runtime) or an
    # external reverse proxy (this self-hosted stack has none of its own -
    # see docker-compose.yml - and can't assume one exists in front of it).
    # Not the most scalable way to serve files, but this app serves a
    # handful of small avatar images for a household, not public traffic
    # at scale - a reasonable trade-off over requiring extra infra.
    re_path(r"^media/(?P<path>.*)$", serve_static, {"document_root": settings.MEDIA_ROOT}),
    path("", include("tracker.urls")),
]

if settings.SILK_ENABLED:
    urlpatterns.append(path("silk/", include("silk.urls", namespace="silk")))
