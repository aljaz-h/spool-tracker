from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import render

from .models import Profile


@login_required
def dashboard(request):
    # Real content lands in build step 5 — this step is the base
    # template/nav shell only.
    return render(request, "tracker/dashboard.html")


@login_required
def library(request, media_type, tab):
    return render(
        request,
        "tracker/coming_soon.html",
        {"page_title": "Anime" if media_type == "anime" else "Movies & TV"},
    )


@login_required
def history(request):
    return render(request, "tracker/coming_soon.html", {"page_title": "History"})


@login_required
def calendar_view(request):
    return render(request, "tracker/coming_soon.html", {"page_title": "Calendar"})


@login_required
def lists(request):
    return render(request, "tracker/coming_soon.html", {"page_title": "Lists"})


@login_required
def stats(request):
    return render(request, "tracker/coming_soon.html", {"page_title": "Stats"})


@login_required
def activity(request):
    # Real feed + full edge-case handling lands in the polish/empty-states
    # step — this guard just keeps the URL consistent with the nav item
    # being hidden on single-profile instances (spool-product-spec.md §5).
    if Profile.objects.count() <= 1:
        raise Http404
    return render(request, "tracker/coming_soon.html", {"page_title": "Activity"})


@login_required
def settings_view(request):
    return render(request, "tracker/coming_soon.html", {"page_title": "Settings & Import"})
