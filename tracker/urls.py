from django.shortcuts import render
from django.urls import path


def scaffold_placeholder(request):
    # Temporary theme sanity-check page for build step 2 — replaced by the
    # real dashboard view (with base template + sidebar nav) in step 4/5.
    return render(request, "theme_preview.html")


urlpatterns = [
    path("", scaffold_placeholder, name="dashboard"),
]
