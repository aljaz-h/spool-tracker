import calendar as calendar_stdlib
import logging
import os
import secrets
import threading
import uuid
from datetime import date, timedelta
from itertools import groupby

import django
import requests
from django.conf import settings as django_settings
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import csv_import, instance_config, rewatches, scheduling, selectors, tasks
from .integrations import simkl, tmdb, trakt
from .models import (
    ExternalAccount,
    InstanceConfig,
    MediaType,
    Profile,
    SyncLog,
    Title,
    WatchEvent,
    WatchList,
    WatchListItem,
    WatchProgress,
)

MOVIE_TV_TYPES = [MediaType.MOVIE, MediaType.TV]
HISTORY_PAGE_SIZE = 150
HISTORY_PERIODS = {"today", "yesterday", "7", "30", "365"}
DISCOVER_CATEGORIES = {"trending", "popular", "upcoming", "top_rated"}
# ISO 639-1 codes TMDB's with_original_language accepts - not exhaustive,
# just the languages common enough in a movie/TV catalog to be worth a
# dedicated dropdown entry instead of making everyone type a code.
DISCOVER_LANGUAGES = [
    ("en", "English"), ("ja", "Japanese"), ("ko", "Korean"), ("zh", "Chinese"),
    ("es", "Spanish"), ("fr", "French"), ("de", "German"), ("it", "Italian"),
    ("pt", "Portuguese"), ("hi", "Hindi"), ("ru", "Russian"), ("tr", "Turkish"),
    ("ar", "Arabic"), ("th", "Thai"), ("sv", "Swedish"), ("da", "Danish"),
    ("no", "Norwegian"), ("fi", "Finnish"), ("pl", "Polish"), ("nl", "Dutch"),
    ("cs", "Czech"), ("el", "Greek"), ("he", "Hebrew"), ("id", "Indonesian"),
    ("vi", "Vietnamese"), ("uk", "Ukrainian"), ("ro", "Romanian"), ("hu", "Hungarian"),
]


@login_required
def dashboard(request):
    profile = Profile.objects.filter(user=request.user).first()
    context = {"profile": profile}
    if profile is not None:
        stats = selectors.quick_stats(profile)
        context.update(
            {
                # No limit - this is now the full Watching list, not just a
                # "continue watching" teaser, since Movies & TV / Anime
                # becoming discovery pages means there's nowhere else for
                # in-progress tracking to live.
                "continue_watching": selectors.continue_watching(profile, limit=None),
                "watchlist_items": selectors.library_watchlist(profile, [MediaType.MOVIE, MediaType.TV, MediaType.ANIME]),
                "up_next": selectors.up_next(profile),
                "recently_added": selectors.recently_added_to_lists(profile),
                "stats": stats,
                "milestone": selectors.milestone_message(stats["streak"], stats["movies_this_year"]),
            }
        )
    return render(request, "tracker/dashboard.html", context)


@login_required
def profile_popup(request, profile_id):
    viewer = Profile.objects.filter(user=request.user).first()
    if viewer is None:
        raise Http404
    target = get_object_or_404(Profile, pk=profile_id)
    context = {
        "target": target,
        "stats": selectors.quick_stats(target),
        "recent_events": selectors.library_history(
            target, [MediaType.MOVIE, MediaType.TV, MediaType.ANIME], limit=8
        ),
    }
    return render(request, "tracker/partials/profile_popup.html", context)


def _discover_int_param(request, name):
    value = request.GET.get(name)
    return int(value) if value and value.lstrip("-").isdigit() else None


@login_required
def discover(request, media_type, category):
    """Movies & TV / Anime pages - browsing what's trending/popular/
    upcoming/top-rated on TMDB, with a genre/year/runtime/rating filter
    panel. Replaced the old Watching/Watchlist/History tabs (moved to the
    Dashboard) once this page became a discovery surface instead of a
    library view."""
    if category not in DISCOVER_CATEGORIES:
        raise Http404

    is_anime = media_type == "anime"
    tmdb_media_type = "tv" if is_anime else request.GET.get("type", "movie")
    if tmdb_media_type not in ("movie", "tv"):
        tmdb_media_type = "movie"

    genre_ids = [int(g) for g in request.GET.getlist("genre") if g.isdigit()]
    if is_anime:
        genre_ids = list({*genre_ids, tmdb.ANIMATION_GENRE_ID})

    filters = {
        "genre_ids": genre_ids,
        "year_from": _discover_int_param(request, "year_from"),
        "year_to": _discover_int_param(request, "year_to"),
        "runtime_from": _discover_int_param(request, "runtime_from"),
        "runtime_to": _discover_int_param(request, "runtime_to"),
        "rating_from": _discover_int_param(request, "rating_from"),
        "rating_to": _discover_int_param(request, "rating_to"),
        "original_language": request.GET.get("language") or None,
    }
    if is_anime:
        filters["origin_country"] = "JP"

    # TMDB refuses page requests beyond 500 regardless of total_pages.
    page_num = min(_discover_int_param(request, "page") or 1, 500)
    page = tmdb.discover(tmdb_media_type, category=category, page=page_num, **filters)

    query_without_page = request.GET.copy()
    query_without_page.pop("page", None)

    profile = Profile.objects.filter(user=request.user).first()
    context = {
        "profile": profile,
        "page_title": "Anime" if is_anime else "Movies & TV",
        "is_anime": is_anime,
        "library_url_name": "anime" if is_anime else "movies_tv",
        "category": category,
        "media_type": tmdb_media_type,
        "results": page["results"],
        "current_page": page_num,
        "total_pages": min(page["total_pages"], 500),
        "genres": tmdb.genres(tmdb_media_type),
        "selected_genres": set(genre_ids),
        "languages": DISCOVER_LANGUAGES,
        "base_query": query_without_page.urlencode(),
    }
    return render(request, "tracker/discover.html", context)


def _tmdb_media_type_for(title):
    """external_ids["tmdb_kind"] is the authoritative source (set at
    match time by trakt.py/simkl.py/csv_import.py) since anime is almost
    always matched against TMDB's tv catalog, not movie - title.media_type
    alone can't be trusted to pick the right TMDB endpoint."""
    return title.external_ids.get("tmdb_kind") or ("movie" if title.media_type == MediaType.MOVIE else "tv")


def _title_display(title, details):
    """Unifies the tracked (real Title row, TMDB details optional) and
    preview (no Title row, TMDB details required) cases into one flat set
    of display values, computed here rather than via template-side
    `|default:` chains - those still evaluate their fallback argument even
    when unused, and Django's test client's stricter template rendering
    raises on any attribute access against a None title, unlike normal
    (production) rendering, which just silently swallows it."""
    return {
        "display_name": (details or {}).get("name") or (title.name if title else "Untitled"),
        "display_year": (details or {}).get("year") or (title.year if title else None),
        "display_poster_url": (details or {}).get("poster_url") or (title.poster_url if title else None),
    }



@login_required
def title_detail(request, pk):
    """The click-through page for a title already in the local library -
    reachable from History, Dashboard, Calendar, and Lists, all of which
    already deal in real Title rows. Movies & TV / Anime discovery cards
    aren't backed by a Title row yet and go to title_preview instead."""
    title = get_object_or_404(Title, pk=pk)
    profile = Profile.objects.filter(user=request.user).first()

    tmdb_id = title.external_ids.get("tmdb")
    details = cast = similar = director = None
    watch_providers = []
    if tmdb_id:
        tmdb_media_type = _tmdb_media_type_for(title)
        details = tmdb.get_full_details(tmdb_media_type, tmdb_id)
        cast = tmdb.get_credits(tmdb_media_type, tmdb_id)
        similar = tmdb.get_similar(tmdb_media_type, tmdb_id)
        director = tmdb.get_director(tmdb_media_type, tmdb_id)
        watch_providers = tmdb.get_watch_providers(tmdb_media_type, tmdb_id)

    local_context = selectors.title_local_context(profile, title)
    context = {
        "profile": profile,
        "title": title,
        "poster_seed": title.pk,
        "details": details,
        "cast": cast or [],
        "similar": similar or [],
        "director": director,
        "watch_providers": watch_providers,
        "status_badge": tmdb.status_badge(details["status"]) if details else None,
        "is_preview": False,
        "preview_media_type": None,
        "preview_tmdb_id": None,
        **_title_display(title, details),
        **local_context,
    }
    return render(request, "tracker/title_detail.html", context)


@login_required
@require_POST
def title_mark_watched(request, pk):
    """The detail page's quick "mark as watched" action - a plain,
    episode-less WatchEvent (same shape History/the activity feed already
    render as "watched <title>" with no episode), since there was no
    manual "I watched this" action anywhere in the app before this page -
    everything else arrives via sync/import/CSV."""
    title = get_object_or_404(Title, pk=pk)
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None:
        raise Http404
    WatchEvent.objects.create(profile=profile, title=title, watched_at=timezone.now())
    rewatches.recompute_is_rewatch(profile, title, None)
    return redirect("title_detail", pk=pk)


@login_required
@require_POST
def title_rate(request, pk):
    """Updates the most recent watch's rating if one exists, otherwise
    rating implies a watch (same as clicking a star on Trakt logs the
    watch too) and creates one."""
    title = get_object_or_404(Title, pk=pk)
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None:
        raise Http404
    rating = request.POST.get("rating", "")
    if not (rating.isdigit() and 1 <= int(rating) <= 10):
        raise Http404
    rating = int(rating)
    latest_event = WatchEvent.objects.filter(profile=profile, title=title).order_by("-watched_at").first()
    if latest_event:
        latest_event.user_rating = rating
        latest_event.save(update_fields=["user_rating"])
    else:
        WatchEvent.objects.create(profile=profile, title=title, watched_at=timezone.now(), user_rating=rating)
        rewatches.recompute_is_rewatch(profile, title, None)
    return redirect("title_detail", pk=pk)


@login_required
def title_preview(request, media_type, tmdb_id):
    """The click-through page for a Movies & TV / Anime discovery card -
    not backed by a local Title row (the user may never have watched it),
    so this is read-only against TMDB directly, with a single "add to
    watchlist" action that's the only thing allowed to create the Title
    row. If a matching Title already exists (found this exact tmdb_id
    before, from a sync/import or an earlier watchlist-add here), that's
    the real page for it - redirect there instead of showing a second,
    library-blind copy of the same title."""
    if media_type not in ("movie", "tv"):
        raise Http404
    existing = Title.objects.filter(external_ids__tmdb=str(tmdb_id)).first()
    if existing is not None:
        return redirect("title_detail", pk=existing.pk)

    profile = Profile.objects.filter(user=request.user).first()
    details = tmdb.get_full_details(media_type, tmdb_id)
    if details is None:
        raise Http404
    context = {
        "profile": profile,
        "title": None,
        "poster_seed": tmdb_id,
        "details": details,
        "cast": tmdb.get_credits(media_type, tmdb_id),
        "similar": tmdb.get_similar(media_type, tmdb_id),
        "director": tmdb.get_director(media_type, tmdb_id),
        "watch_providers": tmdb.get_watch_providers(media_type, tmdb_id),
        "status_badge": tmdb.status_badge(details["status"]),
        "is_preview": True,
        "preview_media_type": media_type,
        "preview_tmdb_id": tmdb_id,
        **_title_display(None, details),
        "progress": None,
        "recent_events": [],
        "latest_rating": None,
        "my_lists": [],
        "in_list_ids": set(),
    }
    return render(request, "tracker/title_detail.html", context)


@login_required
@require_POST
def title_preview_add_to_watchlist(request, media_type, tmdb_id):
    """The preview page's one write action - get-or-create the Title from
    the TMDB id (same shape as trakt.py/simkl.py's own get-or-create, just
    keyed off an id we already have instead of a name+year search), then
    drop it on the profile's default "Watchlist" list (get-or-created by
    name, since WatchList has no is_default flag to key off instead) and
    hand off to the real detail page, where the fuller add-to-any-list UI
    lives."""
    if media_type not in ("movie", "tv"):
        raise Http404
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None:
        raise Http404

    title = Title.objects.filter(external_ids__tmdb=str(tmdb_id)).first()
    if title is None:
        details = tmdb.get_full_details(media_type, tmdb_id)
        if details is None:
            raise Http404
        media_type_for_title = MediaType.MOVIE if media_type == "movie" else MediaType.TV
        title = Title.objects.create(
            media_type=media_type_for_title,
            name=details["name"],
            year=int(details["year"]) if details["year"] else 0,
            poster_url=details["poster_url"] or "",
            external_ids={"tmdb": str(tmdb_id), "tmdb_kind": media_type},
        )

    watchlist, _ = WatchList.objects.get_or_create(profile=profile, name="Watchlist")
    WatchListItem.objects.get_or_create(watchlist=watchlist, title=title)
    return redirect("title_detail", pk=title.pk)


def _group_consecutive_episodes(events):
    """Collapses a run of consecutive (adjacent in the day's own order,
    same title) episode watches into one group card instead of N near-
    identical poster tiles - same idea as
    selectors._group_consecutive_watches uses for the Activity feed,
    just shaped for WatchEvent objects/template cards here instead of
    that feed's dict items. Movies and single episodes pass through
    unchanged."""
    grouped = []
    i = 0
    while i < len(events):
        event = events[i]
        if event.episode is None:
            grouped.append(event)
            i += 1
            continue
        run = [event]
        j = i + 1
        while j < len(events) and events[j].episode is not None and events[j].title_id == event.title_id:
            run.append(events[j])
            j += 1
        if len(run) > 1:
            episodes = [e.episode for e in run]
            first_by_ep = min(episodes, key=lambda e: (e.season, e.episode))
            last_by_ep = max(episodes, key=lambda e: (e.season, e.episode))
            grouped.append(
                {
                    "is_group": True,
                    "title": run[0].title,
                    "count": len(run),
                    "range_label": f"S{first_by_ep.season}E{first_by_ep.episode}–S{last_by_ep.season}E{last_by_ep.episode}",
                    "events": run,
                }
            )
        else:
            grouped.append(event)
        i = j
    return grouped


def _group_history_by_day(events):
    """Mirrors the mockup's groupHistByDate() — events must already be
    ordered by watched_at (either direction; date only moves monotonically
    along that ordering, so groupby's adjacency requirement still holds)."""
    groups = []
    for day, items in groupby(events, key=lambda e: timezone.localtime(e.watched_at).date()):
        items = list(items)
        movie_count = sum(1 for e in items if e.title.media_type == MediaType.MOVIE)
        groups.append(
            {
                "date": day,
                "items": _group_consecutive_episodes(items),
                "movie_count": movie_count,
                "episode_count": len(items) - movie_count,
            }
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


def _list_action_redirect(request, list_id):
    """add_to_list/remove_from_list default to list_detail, but the title
    detail page also posts to these (to add/remove itself from a list
    without a dedicated endpoint per action) and needs to land back on
    itself, not list_detail - "next" opts into that, validated against
    open-redirect the same way Django's own LoginView handles ?next=."""
    from django.utils.http import url_has_allowed_host_and_scheme

    next_url = request.POST.get("next")
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(next_url)
    return redirect("list_detail", list_id=list_id)


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
    return _list_action_redirect(request, list_id)


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
    return _list_action_redirect(request, list_id)


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

        context["watch_time_breakdown"] = selectors.watch_time_breakdown(profile)
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
    external_accounts = {}
    if profile is not None:
        external_accounts = {a.provider: a for a in ExternalAccount.objects.filter(profile=profile)}
    context = {
        "profile": profile,
        "connected_providers": external_accounts.keys(),
        "external_accounts": external_accounts,
    }
    return render(request, "tracker/settings.html", context)


@login_required
def admin_dashboard(request):
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None or not profile.is_owner:
        raise Http404
    db_engine = django_settings.DATABASES["default"]["ENGINE"].rsplit(".", 1)[-1]
    context = {
        "profile": profile,
        "profiles": Profile.objects.select_related("user").all(),
        "cfg": InstanceConfig.load(),
        "trakt_configured": bool(instance_config.get_trakt_credentials()[0]),
        "simkl_configured": bool(instance_config.get_simkl_credentials()[0]),
        "tmdb_configured": bool(instance_config.get_tmdb_api_key()),
        "django_version": ".".join(map(str, django.VERSION[:3])),
        "db_engine": db_engine,
        "debug": django_settings.DEBUG,
        "time_zone": django_settings.TIME_ZONE,
    }
    return render(request, "tracker/admin_dashboard.html", context)


@login_required
@require_POST
def save_instance_config(request):
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None or not profile.is_owner:
        raise Http404
    cfg = InstanceConfig.load()
    # Blank submitted value = "leave as-is", not "clear it" - the form never
    # re-renders an existing secret's real value (see admin_dashboard.html),
    # so a blank field only ever means the admin didn't type a replacement.
    for field in [
        "trakt_client_id",
        "trakt_client_secret",
        "simkl_client_id",
        "simkl_client_secret",
        "tmdb_api_key",
    ]:
        value = request.POST.get(field, "").strip()
        if value:
            setattr(cfg, field, value)
    cfg.save()
    messages.success(request, "Saved integration credentials.")
    return redirect("admin_dashboard")


SYNC_LOG_PAGE_SIZE = 50


@login_required
def sync_log(request):
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None or not profile.is_owner:
        raise Http404
    logs = SyncLog.objects.select_related("profile").all()
    paginator = Paginator(logs, SYNC_LOG_PAGE_SIZE)
    page = paginator.get_page(request.GET.get("page"))
    return render(request, "tracker/sync_log.html", {"profile": profile, "page": page})


@login_required
def change_credentials(request):
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None or not profile.must_change_credentials:
        return redirect("dashboard")

    if request.method == "POST":
        new_username = request.POST.get("username", "").strip()
        new_password = request.POST.get("password", "")
        confirm_password = request.POST.get("confirm_password", "")
        if not new_username or not new_password:
            messages.error(request, "Username and password are both required.")
        elif new_password != confirm_password:
            messages.error(request, "Passwords don't match.")
        elif len(new_password) < 8:
            messages.error(request, "Password must be at least 8 characters.")
        elif User.objects.filter(username=new_username).exclude(pk=request.user.pk).exists():
            messages.error(request, f'Username "{new_username}" is already taken.')
        else:
            request.user.username = new_username
            request.user.set_password(new_password)
            request.user.save()
            profile.must_change_credentials = False
            profile.save(update_fields=["must_change_credentials"])
            # Changing the password rotates the session auth hash - without
            # this the user would be immediately logged out by their own
            # password change and have to sign back in with the new one.
            update_session_auth_hash(request, request.user)
            messages.success(request, "Your username and password have been updated.")
            return redirect("dashboard")

    return render(request, "tracker/change_credentials.html", {"profile": profile})


# Same 14-color palette used to color Stats' genre legend - reused here so
# an avatar's color always comes from a set that's already proven to look
# good against the dark theme, rather than an open color picker.
AVATAR_COLOR_CHOICES = [
    "#e8a63c", "#3fa9a0", "#8b85d6", "#c0473a", "#5b8fd6", "#d67ab1", "#7fae5b",
    "#d6c14c", "#a67ac9", "#e08a4c", "#4ca6c9", "#9a9fb0", "#c9574c", "#5bc9a0",
]


@login_required
def my_profile(request):
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None:
        raise Http404

    if request.method == "POST" and request.POST.get("action") == "update_profile":
        display_name = request.POST.get("display_name", "").strip()
        avatar_color = request.POST.get("avatar_color", "").strip()
        if not display_name:
            messages.error(request, "Display name is required.")
        else:
            profile.display_name = display_name
            if avatar_color in AVATAR_COLOR_CHOICES:
                profile.avatar_color = avatar_color
            profile.save(update_fields=["display_name", "avatar_color"])
            messages.success(request, "Profile updated.")
        return redirect("my_profile")

    if request.method == "POST" and request.POST.get("action") == "change_password":
        current_password = request.POST.get("current_password", "")
        new_password = request.POST.get("new_password", "")
        confirm_password = request.POST.get("confirm_password", "")
        if not request.user.check_password(current_password):
            messages.error(request, "Current password is incorrect.")
        elif not new_password:
            messages.error(request, "New password is required.")
        elif new_password != confirm_password:
            messages.error(request, "New passwords don't match.")
        elif len(new_password) < 8:
            messages.error(request, "Password must be at least 8 characters.")
        else:
            request.user.set_password(new_password)
            request.user.save()
            # Same reason as change_credentials above - without this the
            # user's own password change immediately logs them out.
            update_session_auth_hash(request, request.user)
            messages.success(request, "Password changed.")
        return redirect("my_profile")

    return render(
        request, "tracker/my_profile.html", {"profile": profile, "avatar_colors": AVATAR_COLOR_CHOICES}
    )


@login_required
@require_POST
def create_profile(request):
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None or not profile.is_owner:
        raise Http404
    username = request.POST.get("username", "").strip()
    password = request.POST.get("password", "")
    display_name = request.POST.get("display_name", "").strip()
    avatar_color = request.POST.get("avatar_color") or "#3a2a1c"
    if not username or not password or not display_name:
        messages.error(request, "Username, password, and display name are all required.")
        return redirect("admin_dashboard")
    try:
        user = User.objects.create_user(username=username, password=password)
    except IntegrityError:
        messages.error(request, f'Username "{username}" is already taken.')
        return redirect("admin_dashboard")
    Profile.objects.create(user=user, display_name=display_name, avatar_color=avatar_color)
    messages.success(request, f"Added profile for {display_name}.")
    return redirect("admin_dashboard")


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
    return redirect("admin_dashboard")


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
    client_id, _ = instance_config.get_credentials(provider)
    if not client_id:
        messages.error(
            request,
            f"{provider.title()} isn't configured on this server — set it up under "
            f"Settings & Import → Admin, or via {provider.upper()}_CLIENT_ID / "
            f"{provider.upper()}_CLIENT_SECRET in the environment and restart.",
        )
        return redirect("settings")

    state = secrets.token_urlsafe(24)
    request.session[f"{provider}_oauth_state"] = state
    redirect_uri = request.build_absolute_uri(reverse(f"{provider}_callback"))
    return redirect(PROVIDER_MODULES[provider].authorize_url(redirect_uri, state, client_id))


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
    client_id, client_secret = instance_config.get_credentials(provider)
    try:
        token_data = PROVIDER_MODULES[provider].exchange_code(code, redirect_uri, client_id, client_secret)
    except requests.RequestException:
        messages.error(request, f"Couldn't complete the {provider.title()} connection — please try again.")
        return redirect("settings")

    expires_in = token_data.get("expires_in")
    account, _ = ExternalAccount.objects.update_or_create(
        profile=profile,
        provider=provider,
        defaults={
            "access_token": token_data.get("access_token", ""),
            "refresh_token": token_data.get("refresh_token", ""),
            "token_expires_at": timezone.now() + timedelta(seconds=expires_in) if expires_in else None,
        },
    )
    scheduling.ensure_periodic_task(account)
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


@login_required
@require_POST
def disconnect_provider(request, provider):
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None:
        raise Http404
    account = get_object_or_404(ExternalAccount, profile=profile, provider=provider)
    scheduling.remove_periodic_task(account)
    account.delete()
    # Titles/episodes/watch events that sync already created are left in
    # place - disconnecting stops future syncs, it isn't an "undo my import."
    messages.success(request, f"Disconnected {provider.title()}. Your imported history hasn't been removed.")
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
def save_sync_schedule(request, provider):
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None:
        raise Http404
    account = get_object_or_404(ExternalAccount, profile=profile, provider=provider)

    try:
        interval_days = max(1, min(30, int(request.POST.get("sync_interval_days", 1))))
    except ValueError:
        interval_days = 1
    try:
        hour_str, minute_str = request.POST.get("sync_time", "04:00").split(":")
        hour, minute = max(0, min(23, int(hour_str))), max(0, min(59, int(minute_str)))
    except (ValueError, AttributeError):
        hour, minute = 4, 0

    account.sync_interval_days = interval_days
    account.sync_hour = hour
    account.sync_minute = minute
    update_fields = ["sync_interval_days", "sync_hour", "sync_minute"]
    if provider == "trakt":
        account.import_lists = bool(request.POST.get("import_lists"))
        update_fields.append("import_lists")
    account.save(update_fields=update_fields)
    scheduling.ensure_periodic_task(account)
    messages.success(request, f"Updated the {provider.title()} sync schedule.")
    return redirect("settings")


CSV_IMPORT_DIR = os.path.join(django_settings.MEDIA_ROOT, "csv_imports")


def _discard_pending_csv_import(request):
    """Removes the temp file backing request.session['csv_import'], if any."""
    pending = request.session.pop("csv_import", None)
    if pending:
        try:
            os.remove(pending["path"])
        except OSError:
            pass


@login_required
@require_POST
def import_csv_upload(request):
    upload = request.FILES.get("csv_file")
    if not upload:
        messages.error(request, "Choose a CSV file first.")
        return redirect("settings")

    os.makedirs(CSV_IMPORT_DIR, exist_ok=True)
    path = os.path.join(CSV_IMPORT_DIR, f"{uuid.uuid4().hex}.csv")
    with open(path, "wb") as f:
        for chunk in upload.chunks():
            f.write(chunk)

    try:
        with open(path, "rb") as f:
            headers = csv_import.open_csv_reader(f).fieldnames or []
    except (OSError, UnicodeDecodeError):
        headers = []
    if not headers:
        os.remove(path)
        messages.error(request, f'"{upload.name}" doesn\'t look like a CSV file (no header row found).')
        return redirect("settings")

    _discard_pending_csv_import(request)
    request.session["csv_import"] = {
        "path": path,
        "filename": upload.name,
        "headers": headers,
        "mapping": csv_import.detect_mapping(headers),
    }
    return redirect("import_csv_preview")


@login_required
def import_csv_preview(request):
    pending = request.session.get("csv_import")
    if not pending:
        messages.error(request, "No CSV import in progress — upload a file to start.")
        return redirect("settings")

    with open(pending["path"], "rb") as f:
        reader = csv_import.open_csv_reader(f)
        sample_rows, sample_errors = csv_import.parse_rows(reader, pending["mapping"], limit=10)

    context = {
        "filename": pending["filename"],
        "headers": pending["headers"],
        "mapping": pending["mapping"],
        "fields": csv_import.FIELDS,
        "required_fields": csv_import.REQUIRED_FIELDS,
        "sample_rows": sample_rows,
        "sample_errors": sample_errors,
        "missing_required": [f for f in csv_import.REQUIRED_FIELDS if f not in pending["mapping"]],
    }
    return render(request, "tracker/import_csv_preview.html", context)


@login_required
@require_POST
def import_csv_remap(request):
    pending = request.session.get("csv_import")
    if not pending:
        return redirect("settings")
    mapping = {}
    for field in csv_import.FIELDS:
        header = request.POST.get(f"map_{field}", "")
        if header:
            mapping[field] = header
    pending["mapping"] = mapping
    request.session["csv_import"] = pending
    return redirect("import_csv_preview")


@login_required
@require_POST
def import_csv_cancel(request):
    _discard_pending_csv_import(request)
    messages.info(request, "CSV import cancelled.")
    return redirect("settings")


@login_required
@require_POST
def import_csv_commit(request):
    profile = Profile.objects.filter(user=request.user).first()
    pending = request.session.get("csv_import")
    if profile is None or not pending:
        return redirect("settings")

    missing_required = [f for f in csv_import.REQUIRED_FIELDS if f not in pending["mapping"]]
    if missing_required:
        messages.error(request, f"Map the required column(s) first: {', '.join(missing_required)}.")
        return redirect("import_csv_preview")

    with open(pending["path"], "rb") as f:
        reader = csv_import.open_csv_reader(f)
        rows, parse_errors = csv_import.parse_rows(reader, pending["mapping"])
    imported, skipped = csv_import.commit_rows(profile, rows)
    _discard_pending_csv_import(request)

    all_skipped = parse_errors + skipped
    request.session["csv_import_result"] = {
        "imported": imported,
        "skipped_count": len(all_skipped),
        "skipped": all_skipped[:50],
        "skipped_truncated": len(all_skipped) > 50,
    }
    return redirect("import_csv_result")


@login_required
def import_csv_result(request):
    result = request.session.pop("csv_import_result", None)
    if not result:
        return redirect("settings")
    return render(request, "tracker/import_csv_result.html", result)
