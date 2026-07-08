from django.http import HttpResponse
from django.urls import path


def scaffold_placeholder(request):
    return HttpResponse(
        "Spool scaffold is up. Pages land in later build steps."
    )


urlpatterns = [
    # Replaced by the real dashboard view in build step 5.
    path("", scaffold_placeholder, name="dashboard"),
]
