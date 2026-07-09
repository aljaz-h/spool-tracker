from django.conf import settings
from django.contrib import admin
from django.db import connection
from django.db.utils import OperationalError
from django.http import HttpResponse
from django.urls import include, path

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
    path("", include("tracker.urls")),
]
