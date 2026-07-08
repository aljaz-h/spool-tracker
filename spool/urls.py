from django.contrib import admin
from django.http import HttpResponse
from django.urls import include, path

from api.main import api


def healthz(request):
    return HttpResponse("ok")


urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz", healthz, name="healthz"),
    path("api/", api.urls),
    path("", include("tracker.urls")),
]
