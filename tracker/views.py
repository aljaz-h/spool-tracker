import calendar as calendar_stdlib
import logging
import secrets
import threading
from datetime import date, timedelta
from itertools import groupby

import django
import requests
from django.conf import settings as django_settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import selectors, tasks
from .integrations import simkl, trakt
from .models import ExternalAccount, MediaType, Profile, Title, WatchEvent, WatchList, WatchListItem

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
        "time_format_str": "H:i" if profile and profile.time_format == Profile.TimeFormat.H24 else "g:i A",
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
    profile = Profile.objects.filter(user=request.user).first()
    context = {"profile": profile}
    if profile is not None:
        context["watchlists"] = selectors.visible_lists(profile)
    return render(request, "tracker/lists.html", context)


def _get_visible_list_or_404(profile, list_id):
    watchlist = get_object_or_404(WatchList.objects.select_related("profile"), pk=list_id)
    if profile is None or not (watchlist.profile_id == profile.id or watchlist.is_shared):
        raise Http404
    return watchlist


@login_required
def list_detail(request, list_id):
    profile = Profile.objects.filter(user=request.user).first()
    watchlist = _get_visible_list_or_404(profile, list_id)
    context = {
        "profile": profile,
        "watchlist": watchlist,
        "can_edit": watchlist.can_edit(profile),
        "items": watchlist.items.select_related("title").prefetch_related("title__ratings"),
    }
    return render(request, "tracker/list_detail.html", context)


@login_required
@require_POST
def create_list(request):
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None:
        raise Http404
    name = request.POST.get("name", "").strip()
    if not name:
        messages.error(request, "List name is required.")
        return redirect("lists")
    watchlist = WatchList.objects.create(profile=profile, name=name, is_shared=bool(request.POST.get("is_shared")))
    return redirect("list_detail", list_id=watchlist.id)


@login_required
@require_POST
def delete_list(request, list_id):
    profile = Profile.objects.filter(user=request.user).first()
    watchlist = get_object_or_404(WatchList, pk=list_id)
    if profile is None or not watchlist.can_edit(profile):
        messages.error(request, "Only the creator of a list can delete it.")
        return redirect("list_detail", list_id=list_id)
    watchlist.delete()
    return redirect("lists")


def _render_list_items(request, watchlist):
    items = watchlist.items.select_related("title").prefetch_related("title__ratings")
    return render(
        request, "tracker/partials/list_detail_items.html", {"watchlist": watchlist, "can_edit": True, "items": items}
    )


@login_required
@require_POST
def add_to_list(request, list_id):
    profile = Profile.objects.filter(user=request.user).first()
    watchlist = get_object_or_404(WatchList, pk=list_id)
    if profile is None or not watchlist.can_edit(profile):
        raise Http404
    title = get_object_or_404(Title, pk=request.POST.get("title_id"))
    WatchListItem.objects.get_or_create(watchlist=watchlist, title=title)
    if request.headers.get("HX-Request"):
        return _render_list_items(request, watchlist)
    return redirect("list_detail", list_id=list_id)


@login_required
@require_POST
def remove_from_list(request, list_id):
    profile = Profile.objects.filter(user=request.user).first()
    watchlist = get_object_or_404(WatchList, pk=list_id)
    if profile is None or not watchlist.can_edit(profile):
        raise Http404
    WatchListItem.objects.filter(watchlist=watchlist, title_id=request.POST.get("title_id")).delete()
    if request.headers.get("HX-Request"):
        return _render_list_items(request, watchlist)
    return redirect("list_detail", list_id=list_id)


@login_required
def search_titles(request, list_id):
    profile = Profile.objects.filter(user=request.user).first()
    watchlist = get_object_or_404(WatchList, pk=list_id)
    if profile is None or not watchlist.can_edit(profile):
        raise Http404
    query = request.GET.get("q", "").strip()
    results = []
    if query:
        existing_ids = watchlist.items.values_list("title_id", flat=True)
        results = Title.objects.filter(name__icontains=query).exclude(pk__in=existing_ids)[:8]
    return render(request, "tracker/partials/title_search_results.html", {"results": results, "watchlist": watchlist})


GENRE_TYPES = {"movie": MediaType.MOVIE, "tv": MediaType.TV, "anime": MediaType.ANIME}


def _build_heatmap_grid(counts, year):
    """Mon-start week columns for a whole year, GitHub-contribution-graph
    style — ported from the mockup's renderHeatmap(), server-side."""
    start, end = date(year, 1, 1), date(year, 12, 31)
    cells = [None] * start.weekday()
    d = start
    while d <= end:
        c = counts.get(d, 0)
        level = 0 if c == 0 else 1 if c == 1 else 2 if c <= 3 else 3 if c <= 5 else 4
        cells.append({"date": d, "count": c, "level": level})
        d += timedelta(days=1)
    while len(cells) % 7:
        cells.append(None)
    weeks = [cells[i : i + 7] for i in range(0, len(cells), 7)]

    month_labels, last_month = [], None
    for week in weeks:
        first_real = next((c for c in week if c), None)
        if first_real and first_real["date"].month != last_month:
            last_month = first_real["date"].month
            month_labels.append(first_real["date"].strftime("%b"))
        else:
            month_labels.append(None)
    return weeks, month_labels


@login_required
def stats(request):
    profile = Profile.objects.filter(user=request.user).first()
    genre_type = request.GET.get("genre_type")
    genre_type = genre_type if genre_type in GENRE_TYPES else "movie"
    context = {"profile": profile, "genre_type": genre_type}
    if profile is not None:
        overview = selectors.stats_overview(profile)
        overview["split"]["movie_end"] = overview["split"]["movie_pct"]
        overview["split"]["tv_end"] = overview["split"]["movie_pct"] + overview["split"]["tv_pct"]
        context.update(overview)

        context["genre_breakdown"] = selectors.genre_breakdown(profile, GENRE_TYPES[genre_type])

        years = selectors.year_breakdown(profile, GENRE_TYPES[genre_type])
        max_count = max((y["count"] for y in years), default=0)
        for y in years:
            y["height_pct"] = max(6, round(y["count"] / max_count * 100)) if max_count else 6
        context["year_breakdown"] = years

        context["heatmap_years"] = selectors.heatmap_available_years(profile)
        year = context["heatmap_years"][0]
        context["heatmap_year"] = year
        context["heatmap_weeks"], context["heatmap_months"] = _build_heatmap_grid(
            selectors.heatmap_counts_by_day(profile, year), year
        )
        context["heatmap_active_days"] = sum(
            1 for w in context["heatmap_weeks"] for c in w if c and c["count"] > 0
        )
    return render(request, "tracker/stats.html", context)


@login_required
def stats_heatmap(request):
    profile = Profile.objects.filter(user=request.user).first()
    try:
        year = int(request.GET.get("year"))
    except (TypeError, ValueError):
        year = timezone.localdate().year

    context = {"heatmap_year": year, "heatmap_years": [], "heatmap_weeks": [], "heatmap_months": []}
    if profile is not None:
        context["heatmap_years"] = selectors.heatmap_available_years(profile)
        context["heatmap_weeks"], context["heatmap_months"] = _build_heatmap_grid(
            selectors.heatmap_counts_by_day(profile, year), year
        )
        context["heatmap_active_days"] = sum(
            1 for w in context["heatmap_weeks"] for c in w if c and c["count"] > 0
        )
    return render(request, "tracker/partials/stats_heatmap.html", context)


@login_required
def activity(request):
    # Hidden entirely (nav item + route) on single-profile instances,
    # not just an empty feed — spool-product-spec.md §5.
    if Profile.objects.count() <= 1:
        raise Http404
    return render(request, "tracker/activity.html", {"feed": selectors.activity_feed()})


@login_required
def settings_view(request):
    profile = Profile.objects.filter(user=request.user).first()
    connected_providers = set()
    if profile is not None:
        connected_providers = set(
            ExternalAccount.objects.filter(profile=profile).values_list("provider", flat=True)
        )
    db_engine = django_settings.DATABASES["default"]["ENGINE"].rsplit(".", 1)[-1]
    context = {
        "profile": profile,
        "profiles": Profile.objects.select_related("user").all(),
        "connected_providers": connected_providers,
        "django_version": ".".join(map(str, django.VERSION[:3])),
        "db_engine": db_engine,
        "debug": django_settings.DEBUG,
        "time_zone": django_settings.TIME_ZONE,
    }
    return render(request, "tracker/settings.html", context)


@login_required
@require_POST
def create_profile(request):
    username = request.POST.get("username", "").strip()
    password = request.POST.get("password", "")
    display_name = request.POST.get("display_name", "").strip()
    avatar_color = request.POST.get("avatar_color") or "#3a2a1c"
    if not username or not password or not display_name:
        messages.error(request, "Username, password, and display name are all required.")
        return redirect("settings")
    try:
        user = User.objects.create_user(username=username, password=password)
    except IntegrityError:
        messages.error(request, f'Username "{username}" is already taken.')
        return redirect("settings")
    Profile.objects.create(user=user, display_name=display_name, avatar_color=avatar_color)
    messages.success(request, f"Added profile for {display_name}.")
    return redirect("settings")


@login_required
@require_POST
def delete_profile(request, profile_id):
    profile = Profile.objects.filter(user=request.user).first()
    target = get_object_or_404(Profile, pk=profile_id)
    if profile is None or not profile.is_owner:
        messages.error(request, "Only the server owner can remove profiles.")
    elif target.id == profile.id:
        messages.error(request, "You can't remove your own profile.")
    else:
        target.user.delete()  # cascades to the Profile via the OneToOne FK
        messages.success(request, f"Removed {target.display_name}.")
    return redirect("settings")


@login_required
@require_POST
def save_appearance(request):
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None:
        raise Http404
    time_format = request.POST.get("time_format")
    if time_format in Profile.TimeFormat.values:
        profile.time_format = time_format
        profile.save(update_fields=["time_format"])
    return HttpResponse(status=204)


PROVIDER_MODULES = {"trakt": trakt, "simkl": simkl}
SYNC_TASKS = {"trakt": tasks.sync_trakt_history, "simkl": tasks.sync_simkl_history}


@login_required
def oauth_connect(request, provider):
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None:
        raise Http404
    client_id = getattr(django_settings, f"{provider.upper()}_CLIENT_ID", "")
    if not client_id:
        messages.error(
            request,
            f"{provider.title()} isn't configured on this server — set "
            f"{provider.upper()}_CLIENT_ID / {provider.upper()}_CLIENT_SECRET in the environment and restart.",
        )
        return redirect("settings")

    state = secrets.token_urlsafe(24)
    request.session[f"{provider}_oauth_state"] = state
    redirect_uri = request.build_absolute_uri(reverse(f"{provider}_callback"))
    return redirect(PROVIDER_MODULES[provider].authorize_url(redirect_uri, state))


@login_required
def oauth_callback(request, provider):
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None:
        raise Http404

    expected_state = request.session.pop(f"{provider}_oauth_state", None)
    if not expected_state or request.GET.get("state") != expected_state:
        messages.error(request, "That connection request expired or was invalid — please try connecting again.")
        return redirect("settings")

    code = request.GET.get("code")
    if not code:
        messages.error(request, f"{provider.title()} didn't return an authorization code.")
        return redirect("settings")

    redirect_uri = request.build_absolute_uri(reverse(f"{provider}_callback"))
    try:
        token_data = PROVIDER_MODULES[provider].exchange_code(code, redirect_uri)
    except requests.RequestException:
        messages.error(request, f"Couldn't complete the {provider.title()} connection — please try again.")
        return redirect("settings")

    expires_in = token_data.get("expires_in")
    ExternalAccount.objects.update_or_create(
        profile=profile,
        provider=provider,
        defaults={
            "access_token": token_data.get("access_token", ""),
            "refresh_token": token_data.get("refresh_token", ""),
            "token_expires_at": timezone.now() + timedelta(seconds=expires_in) if expires_in else None,
        },
    )
    # The connection itself (the ExternalAccount row above) must succeed
    # independently of the broker being reachable right now. Confirmed by
    # reproducing it locally: a down broker makes .apply_async() block for
    # ~16s even with retry=False and short socket timeouts configured
    # (redis-py's own internal retry-with-backoff sits underneath both),
    # so config alone doesn't bound this — a hard thread-join timeout
    # does. Worst case, a broker hiccup costs nothing worse than "today's
    # sync happens on the next daily beat run instead of immediately."
    _dispatch_sync_task_safely(SYNC_TASKS[provider], profile.id)
    messages.success(request, f"Connected to {provider.title()} — syncing your history now.")
    return redirect("settings")


def _dispatch_sync_task_safely(task, profile_id, timeout=2.0):
    def _dispatch():
        try:
            task.apply_async(args=[profile_id], retry=False)
        except Exception:
            logging.getLogger(__name__).exception("Background dispatch of %s failed", task.name)

    thread = threading.Thread(target=_dispatch, daemon=True)
    thread.start()
    thread.join(timeout=timeout)
    if thread.is_alive():
        logging.getLogger(__name__).warning(
            "Dispatch of %s did not complete within %ss; abandoning it for this request "
            "(the daily beat sync will still pick it up)",
            task.name,
            timeout,
        )


@login_required
@require_POST
def import_csv_stub(request):
    messages.info(request, "CSV import isn't available yet.")
    return redirect("settings")
