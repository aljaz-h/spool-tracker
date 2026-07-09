from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import render

from . import selectors
from .models import MediaType, Profile, Title, WatchEvent

MOVIE_TV_TYPES = [MediaType.MOVIE, MediaType.TV]
LIBRARY_TABS = {"watching", "watchlist", "history"}


@login_required
def dashboard(request):
    profile = Profile.objects.filter(user=request.user).first()
    context = {"profile": profile}
    if profile is not None:
        context.update(
            {
                "continue_watching": selectors.continue_watching(profile),
                "up_next": selectors.up_next(profile),
                "recently_added": selectors.recently_added_to_lists(profile),
                "stats": selectors.quick_stats(profile),
            }
        )
    return render(request, "tracker/dashboard.html", context)


@login_required
def library(request, media_type, tab):
    if tab not in LIBRARY_TABS:
        raise Http404

    is_anime = media_type == "anime"
    base_types = [MediaType.ANIME] if is_anime else MOVIE_TV_TYPES

    type_filter = request.GET.get("type", "all")
    if not is_anime and type_filter in ("movie", "tv"):
        active_types = [type_filter]
    else:
        active_types = base_types
        type_filter = "all"

    profile = Profile.objects.filter(user=request.user).first()
    context = {
        "page_title": "Anime" if is_anime else "Movies & TV",
        "is_anime": is_anime,
        # The URL name ("movies_tv"/"anime") — distinct from the `media_type`
        # kwarg ("movie_tv"/"anime") the two path()s pass into this view.
        "library_url_name": "anime" if is_anime else "movies_tv",
        "tab": tab,
        "type_filter": type_filter,
        "profile": profile,
        "total_titles": Title.objects.filter(media_type__in=base_types).count(),
    }
    if profile is not None:
        if tab == "watching":
            context["watching"] = selectors.continue_watching(profile, media_types=active_types, limit=None)
        elif tab == "watchlist":
            context["watchlist_items"] = selectors.library_watchlist(profile, active_types)
        elif tab == "history":
            context["history_events"] = selectors.library_history(profile, active_types)
        context["total_episodes_logged"] = WatchEvent.objects.filter(
            profile=profile, title__media_type__in=base_types, episode__isnull=False
        ).count()
    return render(request, "tracker/library.html", context)


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
