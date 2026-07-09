import calendar as calendar_stdlib
from datetime import date, timedelta
from itertools import groupby

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import render
from django.utils import timezone

from . import selectors
from .models import MediaType, Profile, Title, WatchEvent

MOVIE_TV_TYPES = [MediaType.MOVIE, MediaType.TV]
LIBRARY_TABS = {"watching", "watchlist", "history"}
HISTORY_PAGE_SIZE = 24
HISTORY_PERIODS = {"today", "yesterday", "7", "30", "365"}


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


def _group_history_by_day(events):
    """Mirrors the mockup's groupHistByDate() — events must already be
    ordered by watched_at (either direction; date only moves monotonically
    along that ordering, so groupby's adjacency requirement still holds)."""
    groups = []
    for day, items in groupby(events, key=lambda e: timezone.localtime(e.watched_at).date()):
        items = list(items)
        movie_count = sum(1 for e in items if e.title.media_type == MediaType.MOVIE)
        groups.append(
            {"date": day, "items": items, "movie_count": movie_count, "episode_count": len(items) - movie_count}
        )
    return groups


@login_required
def history(request):
    profile = Profile.objects.filter(user=request.user).first()
    type_filter = request.GET.get("type", "all")
    period = request.GET.get("period", "all") if request.GET.get("period") in HISTORY_PERIODS else "all"
    sort = "old" if request.GET.get("sort") == "old" else "new"

    page_obj = None
    if profile is not None:
        events = WatchEvent.objects.filter(profile=profile).select_related("title", "episode")
        if type_filter in (MediaType.MOVIE, MediaType.TV, MediaType.ANIME):
            events = events.filter(title__media_type=type_filter)

        now = timezone.now()
        today_start = timezone.localtime(now).replace(hour=0, minute=0, second=0, microsecond=0)
        if period == "today":
            events = events.filter(watched_at__gte=today_start)
        elif period == "yesterday":
            events = events.filter(watched_at__gte=today_start - timedelta(days=1), watched_at__lt=today_start)
        elif period in ("7", "30", "365"):
            events = events.filter(watched_at__gte=now - timedelta(days=int(period)))

        events = events.order_by("-watched_at" if sort == "new" else "watched_at")
        page_obj = Paginator(events, HISTORY_PAGE_SIZE).get_page(request.GET.get("page"))

    context = {
        "profile": profile,
        "page_obj": page_obj,
        "day_groups": _group_history_by_day(page_obj.object_list) if page_obj else [],
        "type_filter": type_filter,
        "period": period,
        "sort": sort,
    }
    template = "tracker/partials/history_content.html" if request.headers.get("HX-Request") else "tracker/history.html"
    return render(request, template, context)


CALENDAR_TYPES = {"movie": MediaType.MOVIE, "tv": MediaType.TV, "anime": MediaType.ANIME}


@login_required
def calendar_view(request):
    profile = Profile.objects.filter(user=request.user).first()
    type_filter = request.GET.get("type", "all")
    source_filter = request.GET.get("source") if request.GET.get("source") in ("watching", "watchlist") else "all"
    media_type = CALENDAR_TYPES.get(type_filter)

    today = timezone.localdate()
    try:
        year, month = map(int, request.GET.get("month", "").split("-"))
        base_date = date(year, month, 1)
    except (ValueError, TypeError):
        base_date = today.replace(day=1)

    prev_month = (base_date - timedelta(days=1)).replace(day=1)
    next_month = (base_date.replace(day=28) + timedelta(days=4)).replace(day=1)

    context = {
        "profile": profile,
        "current_month_label": base_date.strftime("%B %Y"),
        "current_month_param": base_date.strftime("%Y-%m"),
        "prev_month_param": prev_month.strftime("%Y-%m"),
        "next_month_param": next_month.strftime("%Y-%m"),
        "prev_month_label": prev_month.strftime("%B"),
        "next_month_label": next_month.strftime("%B"),
        "type_filter": type_filter,
        "source_filter": source_filter,
        "today": today,
        "grid": [],
        "agenda": [],
    }
    if profile is not None:
        releases = list(selectors.calendar_releases(profile, media_type, source_filter))
        by_date = {}
        for rs in releases:
            by_date.setdefault(timezone.localtime(rs.release_date).date(), []).append(rs)

        weeks = calendar_stdlib.Calendar(firstweekday=0).monthdatescalendar(base_date.year, base_date.month)
        grid = []
        for week in weeks:
            row = []
            for d in week:
                day_items = by_date.get(d, [])
                row.append(
                    {
                        "date": d,
                        "in_month": d.month == base_date.month,
                        "is_today": d == today,
                        "items": day_items[:3],
                        "more_count": max(0, len(day_items) - 3),
                    }
                )
            grid.append(row)

        featured, queue = selectors.ready_to_watch_queue(profile)
        agenda_groups = [
            {"date": day, "items": list(items)}
            for day, items in groupby(releases, key=lambda r: timezone.localtime(r.release_date).date())
        ]
        context.update(
            {"grid": grid, "agenda_groups": agenda_groups, "featured": featured, "queue": queue}
        )

    hx_target = request.headers.get("HX-Target")
    if hx_target == "cal-main":
        template = "tracker/partials/calendar_main.html"
    elif request.headers.get("HX-Request"):
        template = "tracker/partials/calendar_body.html"
    else:
        template = "tracker/calendar.html"
    return render(request, template, context)


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
