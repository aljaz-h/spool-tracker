import calendar as calendar_stdlib
import csv
import json
import logging
import os
import secrets
import threading
import uuid
import zipfile
import zoneinfo
from datetime import date, timedelta
from io import StringIO
from itertools import groupby

import django
import requests
from django.conf import settings as django_settings
from django.contrib import messages
from django.contrib.auth import logout, update_session_auth_hash
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, Max
from django.db.models.functions import TruncDate
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import (
    completion,
    crypto,
    csv_import,
    instance_config,
    ratelimit,
    recommendations,
    rewatches,
    scheduling,
    selectors,
    tasks,
)
from .integrations import gemini, jikan, nuvio, simkl, tmdb, trakt
from .models import (
    AVATAR_COLOR_CHOICES,
    AdminAuditLogEntry,
    DataLog,
    Episode,
    ExternalAccount,
    ExternalRating,
    InstanceConfig,
    MediaType,
    Notification,
    NuvioConnection,
    Profile,
    Recommendation,
    Title,
    WatchEvent,
    WatchList,
    WatchListItem,
    WatchProgress,
    attach_genres,
)

MOVIE_TV_TYPES = [MediaType.MOVIE, MediaType.TV]
HISTORY_PAGE_SIZE = 150  # rows per page for the most/least-watched title list - one row is already one tile there, no day-grouping involved
HISTORY_DATES_PER_PAGE = 10  # dates per page for the default day-grouped History view - see _paginate_history_by_day
LOGS_PAGE_SIZE = 20  # rows per page for Settings' Logs tab - see selectors.combined_logs
HISTORY_PERIODS = {"today", "yesterday", "7", "30", "365"}
# "most_watched"/"least_watched" switch History from its usual day-grouped
# listing to a title-grouped one, ordered by how many WatchEvents (plays -
# every logged episode counts on its own, same convention history_group_tile.html's
# own per-day ×N badge already uses, not the poster card's more conservative
# "full rewatch" figure) exist for each title within the current filters -
# see _history_context.
HISTORY_SORTS = {"new", "old", "most_watched", "least_watched"}
DISCOVER_CATEGORIES = {"trending", "popular", "upcoming", "top_rated"}
# Movie-only, and deliberately not composable with the filter panel (see
# tmdb.collections()'s own docstring for why) - excluded from Anime's own
# category set in discover() below rather than living in DISCOVER_CATEGORIES
# itself, so it's still one shared "is this a valid category" set to check.
COLLECTIONS_CATEGORY = "collections"
# Turned off for now (not enough distinct collections surfaced yet to feel
# worth a permanent nav tab) without ripping out the feature - flip back to
# True to restore the tab and re-enable the category/detail routes. Every
# other piece (tmdb.collections()/get_collection_details(), the view
# branches, the templates, the tests) is untouched and ready to go.
COLLECTIONS_ENABLED = False
# Turned off for now (replaced on Dashboard by "Start watching"/"Recently
# watched"/"Social Activity") without ripping the feature out -
# selectors.because_you_watched() and its TMDB call are simply skipped
# while this is False. Flip back to True to restore the row.
DASHBOARD_BECAUSE_YOU_WATCHED_ENABLED = False
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
# tmdb.AVAILABILITY_CHOICES' keys, paired with their filter-panel labels.
DISCOVER_AVAILABILITY_LABELS = [
    ("streaming", "Streaming now"),
    ("digital", "All digital releases"),
]

# Settings → Appearance's personal Timezone dropdown (Profile.timezone,
# activated per-request by middleware.ProfileTimezoneMiddleware). Sourced
# from the stdlib rather than a hand-maintained list - filtered to
# region/city zones only (excludes bare "UTC" and the fixed-offset
# "Etc/GMT+N" entries), which is what every other app's timezone picker
# shows.
PROFILE_TIMEZONES = sorted(
    tz for tz in zoneinfo.available_timezones() if "/" in tz and not tz.startswith("Etc/")
)


@login_required
def dashboard(request):
    profile = Profile.objects.filter(user=request.user).first()
    context = {"profile": profile}
    if profile is not None:
        stats = selectors.quick_stats(profile)
        # No limit - this is now the full Watching list, not just a
        # "continue watching" teaser, since Movies & TV / Anime becoming
        # discovery pages means there's nowhere else for in-progress
        # tracking to live.
        continue_watching = selectors.continue_watching(profile, limit=None)
        # Newest-first (selectors._visible_watchlist_items' own ordering),
        # capped to one un-scrollable row (Dashboard redesign) - the full
        # list still lives at "See all lists" (lists.html), so this only
        # ever needs enough rows to fill the widest realistic viewport,
        # not the whole Watchlist. watchlist_count keeps the header's
        # "(295)" showing the true total even though only a handful render.
        watchlist_qs = selectors.library_watchlist(profile, [MediaType.MOVIE, MediaType.TV, MediaType.ANIME])
        watchlist_count = watchlist_qs.count()
        watchlist_items = list(watchlist_qs[:12])
        because_you_watched = (
            selectors.because_you_watched(profile) if DASHBOARD_BECAUSE_YOU_WATCHED_ENABLED else None
        )
        media_types = [MediaType.MOVIE, MediaType.TV, MediaType.ANIME]
        start_watching = selectors.start_watching(profile, media_types)
        # Recently Watched uses its own watch_event_card.html (episode
        # stills, no watched-toggle/list-popover action bar - those cards
        # are about what already happened, not library management), so it
        # doesn't need to feed poster_action_context below. Social Activity
        # is back to poster_card.html (normal vertical poster + full action
        # bar), so its titles do need to be in there like everything else.
        recently_watched = selectors.recently_watched(profile, media_types)
        social_activity = selectors.social_activity(profile)
        all_titles = (
            [item["title"] for item in continue_watching]
            + [item.title for item in watchlist_items]
            + start_watching
            + [event.title for event in social_activity]
        )
        context.update(
            {
                "continue_watching": continue_watching,
                "watchlist_items": watchlist_items,
                "watchlist_count": watchlist_count,
                "start_watching": start_watching,
                "recently_watched": recently_watched,
                "social_activity": social_activity,
                "up_next": selectors.up_next(profile, limit=4),
                "stats": stats,
                "milestone": selectors.milestone_message(stats["streak"], stats["movies_this_year"]),
                "because_you_watched": because_you_watched,
                "my_lists": list(WatchList.objects.filter(profile=profile).order_by("name")),
                "featured_lists": selectors.featured_lists(),
                **_recommendations_context(profile),
                **selectors.poster_action_context(profile, all_titles),
                **selectors.discover_action_context(
                    profile, because_you_watched["results"] if because_you_watched else []
                ),
            }
        )
    return render(request, "tracker/dashboard.html", context)


@login_required
@require_POST
def recommend(request):
    """A free-text mood plus this profile's own watch history/genre taste,
    handed to Gemini. Bring-your-own-key (Settings), optional and per
    profile - every failure mode (no key, bad key, Gemini unreachable)
    renders the same partial with a plain-language error instead of a
    500, since this is a nice-to-have, not something that should be able
    to break whatever page embeds it."""
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None:
        raise Http404
    mood = request.POST.get("mood", "").strip()
    if not mood:
        return render(request, "tracker/partials/recommendation_result.html", {"error": "Type what you're in the mood for first."})
    if not profile.gemini_api_key:
        return render(
            request,
            "tracker/partials/recommendation_result.html",
            {"error": "Add a free Gemini API key in Settings to turn this on."},
        )
    prompt = gemini.build_recommendation_prompt(profile, mood)
    reply = gemini.generate(profile.gemini_api_key, prompt)
    if reply is None:
        return render(
            request,
            "tracker/partials/recommendation_result.html",
            {"error": "Couldn't reach Gemini - check your API key in Settings, or try again in a moment."},
        )
    return render(request, "tracker/partials/recommendation_result.html", {"reply": reply})


@login_required
def profile_popup(request, profile_id):
    viewer = Profile.objects.filter(user=request.user).first()
    if viewer is None:
        raise Http404
    target = get_object_or_404(Profile, pk=profile_id)
    overview = selectors.stats_overview(target)
    context = {
        "target": target,
        **overview,
        "watch_time_breakdown": selectors.watch_time_breakdown(target),
        "top_genres": selectors.top_genres(target, limit=3),
        "recent_events": selectors.library_history(
            target, [MediaType.MOVIE, MediaType.TV, MediaType.ANIME], limit=8
        ),
    }
    return render(request, "tracker/partials/profile_popup.html", context)


def _discover_int_param(request, name):
    value = request.GET.get(name)
    return int(value) if value and value.lstrip("-").isdigit() else None


def _apply_display_modes(items, discover_watched, discover_list_membership, profile, watched_display, watchlisted_display):
    """Stamps each discover_tile.html item dict with a "display_mode" key
    ("show"/"dim"/"hide") per the Filters panel's Display toggle (a
    persisted Profile preference, not a GET param - see discover.html)
    - watched comes from discover_watched, watchlisted from
    membership in *the* auto-managed Watchlist specifically (not any
    custom list - same distinction completion.py's sync_watchlist_removal
    already makes via WatchList.is_watchlist). When a title matches both a
    watched and a watchlisted rule that disagree, hide wins over dim wins
    over show - either rule alone asking to hide/dim isn't something the
    other rule being milder should silently override."""
    if watched_display == "show" and watchlisted_display == "show":
        for item in items:
            item["display_mode"] = "show"
        return
    watchlist_id = (
        WatchList.objects.filter(profile=profile, is_watchlist=True).values_list("id", flat=True).first()
    )
    for item in items:
        key = f"{item['media_type']}:{item['tmdb_id']}"
        modes = []
        if discover_watched.get(key):
            modes.append(watched_display)
        if watchlist_id and watchlist_id in discover_list_membership.get(key, set()):
            modes.append(watchlisted_display)
        item["display_mode"] = "hide" if "hide" in modes else "dim" if "dim" in modes else "show"


def _collections_view(request):
    """Movies & TV's "Collections" tab - movie franchises (John Wick,
    Indiana Jones, ...), movie-only and not composable with the filter
    panel/pagination the other categories share (see tmdb.collections()'s
    own docstring for why: there's nothing in TMDB's API to filter or
    paginate here, just a single best-effort derived list)."""
    profile = Profile.objects.filter(user=request.user).first()
    context = {
        "profile": profile,
        "page_title": "Movies & TV",
        "is_anime": False,
        "library_url_name": "movies_tv",
        "category": COLLECTIONS_CATEGORY,
        "is_collections": True,
        "media_type": "movie",
        "results": tmdb.collections(),
        "current_page": 1,
        "total_pages": 1,
        "my_lists": [],
    }
    return render(request, "tracker/discover.html", context)


@login_required
def discover(request, media_type, category):
    """Movies & TV / Anime pages - browsing what's trending/popular/
    upcoming/top-rated on TMDB, with a genre/year/runtime/rating filter
    panel. Replaced the old Watching/Watchlist/History tabs (moved to the
    Dashboard) once this page became a discovery surface instead of a
    library view."""
    is_anime = media_type == "anime"
    if category == COLLECTIONS_CATEGORY:
        if is_anime or not COLLECTIONS_ENABLED:
            raise Http404
        return _collections_view(request)
    if category not in DISCOVER_CATEGORIES:
        raise Http404

    tmdb_media_type = "tv" if is_anime else request.GET.get("type", "movie")
    if tmdb_media_type not in ("movie", "tv"):
        tmdb_media_type = "movie"

    genre_ids = [int(g) for g in request.GET.getlist("genre") if g.isdigit()]
    if is_anime:
        genre_ids = list({*genre_ids, tmdb.ANIMATION_GENRE_ID})

    profile = Profile.objects.filter(user=request.user).first()
    # request.GET.get's own default only kicks in when the key is missing
    # entirely - an explicit ?language= (including "" for "Any language",
    # deliberately chosen after landing here with a preferred_language
    # default already applied) always wins over the profile's preference.
    default_language = profile.preferred_language if profile else ""
    # Movies-only - TMDB's /discover/tv has no certification filter param
    # at all (see tmdb.MOVIE_CERTIFICATIONS), so a ?certification= carried
    # over from a Movies search is simply dropped rather than erroring
    # once the type toggle switches to TV.
    selected_certification = request.GET.get("certification", "") if tmdb_media_type == "movie" else ""
    if selected_certification not in tmdb.MOVIE_CERTIFICATIONS:
        selected_certification = ""
    # TV-only - the mirror image of certification above (see tmdb.TV_STATUSES).
    selected_status = request.GET.get("status", "") if tmdb_media_type == "tv" else ""
    if selected_status not in tmdb.TV_STATUSES:
        selected_status = ""
    selected_availability = request.GET.get("availability", "")
    if selected_availability not in tmdb.AVAILABILITY_CHOICES:
        selected_availability = ""
    filters = {
        "genre_ids": genre_ids,
        "year_from": _discover_int_param(request, "year_from"),
        "year_to": _discover_int_param(request, "year_to"),
        "runtime_from": _discover_int_param(request, "runtime_from"),
        "runtime_to": _discover_int_param(request, "runtime_to"),
        "rating_from": _discover_int_param(request, "rating_from"),
        "rating_to": _discover_int_param(request, "rating_to"),
        "original_language": request.GET.get("language", default_language) or None,
        "certification": selected_certification or None,
        "status": selected_status or None,
        "availability": selected_availability or None,
    }
    if is_anime:
        filters["origin_country"] = "JP"

    # "Display" preference - Show/Dim/Hide for titles already watched or on
    # the watchlist. A persisted per-profile rendering preference (Settings
    # → Preferences), not a TMDB query param or a shareable filter -
    # applied below after TMDB's own results come back.
    watched_display = profile.discover_watched_display if profile else Profile.DiscoverDisplay.SHOW
    watchlisted_display = profile.discover_watchlisted_display if profile else Profile.DiscoverDisplay.SHOW

    # TMDB refuses page requests beyond 500 regardless of total_pages.
    page_num = min(_discover_int_param(request, "page") or 1, 500)
    page = tmdb.discover(tmdb_media_type, category=category, page=page_num, **filters)

    query_without_page = request.GET.copy()
    query_without_page.pop("page", None)

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
        "selected_language": filters["original_language"] or "",
        "certifications": tmdb.MOVIE_CERTIFICATIONS,
        "selected_certification": selected_certification,
        "tv_statuses": list(tmdb.TV_STATUSES),
        "selected_status": selected_status,
        "availability_choices": DISCOVER_AVAILABILITY_LABELS,
        "selected_availability": selected_availability,
        "watched_display": watched_display,
        "watchlisted_display": watchlisted_display,
        "base_query": query_without_page.urlencode(),
        "my_lists": list(WatchList.objects.filter(profile=profile).order_by("name")) if profile else [],
        "collections_enabled": COLLECTIONS_ENABLED,
    }
    if profile is not None:
        context.update(selectors.discover_action_context(profile, page["results"]))
        _apply_display_modes(
            page["results"], context["discover_watched"], context["discover_list_membership"],
            profile, watched_display, watchlisted_display,
        )
    return render(request, "tracker/discover.html", context)


@login_required
def collection_detail(request, collection_id):
    """A single franchise's movies (John Wick 1-4, ...) - reached by
    clicking a tile on the Collections tab. Read-only against TMDB, same
    as title_preview; the movies within it are themselves ordinary
    discover_tile.html previews, so watching/listing one works exactly
    like it does everywhere else that renders that partial."""
    if not COLLECTIONS_ENABLED:
        raise Http404
    collection = tmdb.get_collection_details(collection_id)
    if collection is None:
        raise Http404
    profile = Profile.objects.filter(user=request.user).first()
    context = {
        "profile": profile,
        "collection": collection,
        "results": collection["parts"],
        "my_lists": list(WatchList.objects.filter(profile=profile).order_by("name")) if profile else [],
    }
    if profile is not None:
        context.update(selectors.discover_action_context(profile, collection["parts"]))
    return render(request, "tracker/collection_detail.html", context)


SEARCH_LIBRARY_LIMIT = 24

# "all" plus every value Title.media_type/a normalized TMDB result's own
# category (see _search_result_category) can actually take - a bad/stale
# ?type= just falls back to "all" rather than raising or silently matching
# nothing.
SEARCH_TYPES = ("all", "movie", "tv", "anime")


def _search_result_category(result):
    """A normalized TMDB search result (tmdb.search()'s own shape) doesn't
    carry a MediaType-style category directly - "anime" isn't a real TMDB
    media_type, it's tv or movie with is_anime=True layered on top (see
    tmdb._normalize_result). Title.media_type already stores "anime" as
    its own value, so library results don't need this - only tmdb_results
    do."""
    return "anime" if result["is_anime"] else result["media_type"]


@login_required
def search(request):
    """The topbar search box's results page - two sections: titles already
    in the library (rendered as ordinary poster_card.html cards, full
    watched/list-picker actions) and everything else TMDB has for the
    query that isn't tracked yet (rendered as discover_tile.html preview
    cards, same as Movies & TV/Anime's own discovery grid). Library
    matching is a plain name__icontains scan - no ranking, no fuzzy
    matching - which is fine at personal-library scale (typo tolerance and
    year-qualified matching for the TMDB half live in tmdb.search itself).
    ?type= (all/movie/tv/anime) filters both sections the same way."""
    profile = Profile.objects.filter(user=request.user).first()
    query = request.GET.get("q", "").strip()
    selected_type = request.GET.get("type", "all")
    if selected_type not in SEARCH_TYPES:
        selected_type = "all"
    library_results = []
    tmdb_results = []
    context = {
        "profile": profile,
        "query": query,
        "selected_type": selected_type,
        "library_results": library_results,
        "tmdb_results": tmdb_results,
        "my_lists": list(WatchList.objects.filter(profile=profile).order_by("name")) if profile else [],
    }
    if query:
        library_qs = Title.objects.filter(name__icontains=query)
        if selected_type != "all":
            library_qs = library_qs.filter(media_type=selected_type)
        library_results = list(library_qs.order_by("name")[:SEARCH_LIBRARY_LIMIT])
        if profile is not None and library_results:
            context.update(selectors.poster_action_context(profile, library_results))
        raw_results = tmdb.search(query)["results"]
        # A title already in the library shouldn't also show up as a
        # "not tracked yet" TMDB preview card - same per-id
        # already-exists check title_preview itself does
        # (Title.objects.filter(external_ids__tmdb=str(tmdb_id),
        # external_ids__tmdb_kind=media_type) - the tmdb_kind half matters
        # here too, not just for title_preview's redirect: movie and tv
        # ids are separate TMDB namespaces, so without it a tv result
        # could get silently dropped from these results because an
        # unrelated movie happens to already be tracked under the same
        # numeric id). One query per result rather than a single __in
        # lookup deliberately - a JSONField key-transform's value
        # round-trips through SQLite's json_extract typed (a stored JSON
        # string like "42" comes back as the Python int 42, not "42"),
        # which silently breaks a str-vs-set-of-ids membership check
        # after the fact. Equality comparison against a str RHS, as used
        # here and everywhere else this pattern appears, doesn't hit that.
        tmdb_results = [
            r
            for r in raw_results
            if not Title.objects.filter(
                external_ids__tmdb=str(r["tmdb_id"]), external_ids__tmdb_kind=r["media_type"]
            ).exists()
            and (selected_type == "all" or _search_result_category(r) == selected_type)
        ]
        context["library_results"] = library_results
        context["tmdb_results"] = tmdb_results
    return render(request, "tracker/search.html", context)


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



def _parse_tmdb_date(value):
    """TMDB dates are always plain "YYYY-MM-DD" (no time component) -
    date.fromisoformat parses that exact shape directly, no strptime
    format string needed. None for missing/malformed input (TMDB does
    sometimes omit dates for not-yet-scheduled releases)."""
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _episode_release_label(air_date_str):
    """Countdown pill for an episode browser tile with a still-upcoming
    air date - "Today"/"Tomorrow", "In N days" under two weeks out,
    "In N weeks" under ~40 days, else "In N months" (the crossover sits
    where round(days/30) first reaches 1, so "In 1 month" is reachable
    rather than jumping straight from weeks to "In 2 months"). None once
    the date has passed (today counts as aired, not upcoming) or TMDB
    hasn't scheduled the episode yet at all (no air_date), which the
    template's `{% if %}` already treats as "no pill", not an error."""
    air_date = _parse_tmdb_date(air_date_str)
    if air_date is None:
        return None
    delta_days = (air_date - timezone.localdate()).days
    if delta_days < 0:
        return None
    if delta_days == 0:
        return "Today"
    if delta_days == 1:
        return "Tomorrow"
    if delta_days < 14:
        return f"In {delta_days} days"
    if delta_days < 40:
        weeks = delta_days // 7
        return f"In {weeks} week{'s' if weeks != 1 else ''}"
    months = round(delta_days / 30)
    return f"In {months} month{'s' if months != 1 else ''}"


def _release_info(details):
    """Release-date summary for the detail hero's metadata row - a movie
    has one release_date, but a show doesn't reduce to a single date the
    way a movie does (seasons can drop all at once or air weekly over
    months), so this shows the first-to-last-aired span once a show has
    stopped airing, or just the first-aired date while it's still airing
    (status_badge already carries Ongoing/Ended/Cancelled/etc, so this
    only needs to add the dates, not repeat the status).

    Returns {"prefix": str or None, "date": str} - "prefix" is the
    emphasized lead-in word ("Released"/"Releases"/"Premieres", template
    gives it its own bolder styling), None when the date needs no lead-in
    (an aired/airing show's own date range already reads fine on its
    own). Returns None outright when there's nothing worth showing (e.g.
    a movie TMDB has no release_date for at all, or a show with no known
    dates at all - status_badge alone covers that case)."""
    if not details:
        return None
    if details.get("media_type") == "movie":
        release_date = _parse_tmdb_date(details.get("release_date"))
        if release_date is None:
            return None
        verb = "Releases" if release_date > timezone.localdate() else "Released"
        return {"prefix": verb, "date": release_date.strftime("%b %d, %Y")}

    first_aired = _parse_tmdb_date(details.get("first_air_date"))
    last_aired = _parse_tmdb_date(details.get("last_air_date"))
    if first_aired and last_aired and last_aired != first_aired:
        return {"prefix": None, "date": f"{first_aired.strftime('%b %d, %Y')} – {last_aired.strftime('%b %d, %Y')}"}
    if first_aired:
        return {"prefix": None, "date": first_aired.strftime("%b %d, %Y")}
    next_episode = details.get("next_episode_to_air") or {}
    premiere = _parse_tmdb_date(next_episode.get("air_date"))
    if premiere:
        return {"prefix": "Premieres", "date": premiere.strftime("%b %d, %Y")}
    return None


def _star_fill(rating):
    """5 stars representing a 1-10 rating, 2 points each - each entry's
    "fill" (0/50/100) is precomputed here so the template just renders a
    clipped overlay instead of doing this arithmetic per star per request."""
    stars = []
    for i in range(1, 6):
        left, right = 2 * i - 1, 2 * i
        if not rating:
            fill = 0
        elif rating >= right:
            fill = 100
        elif rating >= left:
            fill = 50
        else:
            fill = 0
        stars.append({"left": left, "right": right, "fill": fill})
    return stars


def _resolve_mal_id(title):
    """Best-effort Jikan/MAL id resolution for an anime Title, matched by
    name/year and cached onto external_ids["mal"] once found so it's only
    ever looked up once - shared by the episode browser's filler overlay
    (_apply_anime_filler_flags) and the detail page's MAL score/Japanese
    title/studio enrichment (_anime_jikan_context) below."""
    mal_id = title.external_ids.get("mal")
    if mal_id is not None:
        return mal_id
    match = jikan.find_match(title.name, title.year)
    if match is None:
        return None
    mal_id = match["mal_id"]
    title.external_ids["mal"] = mal_id
    title.save(update_fields=["external_ids"])
    return mal_id


def _anime_jikan_context(title):
    """MAL score/Japanese title/studio/source-material for the detail
    hero - anime only, best-effort like every other jikan.py lookup: no
    match or Jikan unreachable just means these keys are absent, same
    "missing, not wrong" degrade as everywhere else Jikan is used. The
    MAL score is persisted as a real ExternalRating row (not just context)
    so it renders via the existing pill_badges.html alongside IMDb/RT/
    Trakt, instead of a bespoke one-off badge."""
    if title.media_type != MediaType.ANIME:
        return {}
    mal_id = _resolve_mal_id(title)
    if mal_id is None:
        return {}
    details = jikan.get_anime_details(mal_id)
    if not details:
        return {}
    if details.get("score") is not None:
        ExternalRating.objects.update_or_create(
            title=title, source=ExternalRating.Source.MAL, defaults={"score": str(details["score"])}
        )
    return {
        "mal_title_japanese": details.get("title_japanese"),
        "mal_studios": details.get("studios") or [],
        "mal_source": details.get("source"),
    }


def _apply_anime_filler_flags(title, episodes, season, tv_details):
    """Overlays Jikan's (MyAnimeList) per-episode filler/recap flags onto
    TMDB's season-relative episode list - TMDB has no filler data of its
    own. Best-effort like every other tmdb.py/jikan.py lookup: no MAL
    match, or Jikan unreachable/empty, just leaves every episode without
    a "filler"/"recap" key, which title_episodes.html's `{% if
    ep.filler %}` already treats as falsy - never an error, never a
    wrong badge, just no badge.

    TMDB's episode_number is season-relative; Jikan's is the show's whole
    absolute count (MAL doesn't split most shows into per-season
    entries), bridged by summing every earlier season's episode_count
    from tv_details (already fetched by the caller for season_ratings,
    no extra TMDB call needed here) - see the plan's note on why this
    doesn't hold for the (uncommon) anime MAL splits into separate
    per-season entries instead: those just silently get no badges for
    that season, not wrong ones."""
    mal_id = _resolve_mal_id(title)
    if mal_id is None:
        return

    filler_map = jikan.get_episode_filler_map(mal_id)
    if not filler_map:
        return

    offset = sum(
        s["episode_count"] or 0
        for s in (tv_details["seasons"] if tv_details else [])
        if s["season_number"] and s["season_number"] < season
    )
    for ep in episodes:
        flags = filler_map.get(offset + ep["episode_number"])
        if flags:
            ep["filler"] = flags["filler"]
            ep["recap"] = flags["recap"]


def _episode_panel_context(request, profile, title, tmdb_id, details, force_season=None):
    """Season/episode data for the title detail page's episode browser -
    shared by title_detail/title_episodes (a real, already-tracked Title)
    and title_preview/title_preview_episodes (no local Title row yet -
    title=None, tmdb_id passed in directly since there's no title.external_ids
    to read it from). Empty (no seasons) for movies and any show where
    TMDB doesn't report a season count. Watched-episode badges and the
    "resume where I left off" default season both need a real Title to
    look up WatchEvents against, so they're skipped (not an error) when
    title is None - a preview's episodes just all show as unwatched.
    force_season pins the season directly (used by the bulk mark-watched
    views, which already know which season they just acted on) instead
    of resolving it from request.GET/the profile's watch history.
    Each episode also carries release_in (see _episode_release_label) -
    a "In N days/weeks/months" countdown pill for one that hasn't aired
    yet, None once it has or TMDB has no air_date for it at all.

    season_ratings (dict {season_number: vote_average|None}) is TMDB's
    own per-season rating (get_tv_details' "seasons" list - one extra
    call, but zero per-season round-trips) for the season-picker
    dropdown to show next to every season, not just the selected one -
    a different figure from season_avg_rating below, which is the mean
    of the SELECTED season's own episodes' individual ratings."""
    context = {
        "seasons": [], "season": None, "episodes": [], "season_avg_rating": None,
        "season_ratings": {}, "season_total_runtime": None,
    }
    number_of_seasons = details["number_of_seasons"] if details else None
    if not number_of_seasons:
        return context
    context["seasons"] = list(range(1, number_of_seasons + 1))

    tv_details = tmdb.get_tv_details(tmdb_id) if tmdb_id else None
    if tv_details:
        context["season_ratings"] = {s["season_number"]: s.get("vote_average") or None for s in tv_details["seasons"]}

    if force_season is not None:
        season = force_season
    else:
        try:
            season = int(request.GET.get("season"))
        except (TypeError, ValueError):
            season = None
    if season not in context["seasons"]:
        season = selectors.default_season_for_title(profile, title) if profile and title else None
        if season not in context["seasons"]:
            season = context["seasons"][0]
    context["season"] = season

    season_data = tmdb.get_season_details(tmdb_id, season)
    episodes = season_data["episodes"] if season_data else []
    watched = selectors.watched_episode_numbers(profile, title, season) if profile and title else set()
    for ep in episodes:
        ep["watched"] = ep["episode_number"] in watched
        ep["release_in"] = _episode_release_label(ep.get("air_date"))
    if episodes:
        episodes[-1]["is_finale"] = True
    if title is not None and title.media_type == MediaType.ANIME and episodes:
        _apply_anime_filler_flags(title, episodes, season, tv_details)
    context["episodes"] = episodes
    rated = [ep["vote_average"] for ep in episodes if ep.get("vote_average")]
    context["season_avg_rating"] = round(sum(rated) / len(rated), 1) if rated else None
    runtimes = [ep["runtime"] for ep in episodes if ep.get("runtime")]
    context["season_total_runtime"] = selectors.format_duration(sum(runtimes)) if runtimes else None
    return context


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
    episode_context = {"seasons": [], "season": None, "episodes": [], "season_avg_rating": None, "season_ratings": {}}
    if tmdb_id:
        tmdb_media_type = tmdb.media_type_for(title)
        details = tmdb.get_full_details(tmdb_media_type, tmdb_id)
        cast = tmdb.get_credits(tmdb_media_type, tmdb_id)
        similar = tmdb.get_similar(tmdb_media_type, tmdb_id)
        director = tmdb.get_director(tmdb_media_type, tmdb_id)
        watch_providers = tmdb.get_watch_providers(tmdb_media_type, tmdb_id)
        episode_context = _episode_panel_context(request, profile, title, tmdb_id, details)

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
        "release_info": _release_info(details),
        "is_preview": False,
        "preview_media_type": None,
        "preview_tmdb_id": None,
        **_title_display(title, details),
        **local_context,
        **episode_context,
        **_recommend_context(profile, title),
        **_anime_jikan_context(title),
    }
    if profile is not None and similar:
        context.update(selectors.discover_action_context(profile, similar))
    context["star_fill"] = _star_fill(context["latest_rating"])
    return render(request, "tracker/title_detail.html", context)


def _recommend_context(profile, title):
    """The sidebar "Recommend to" card's context for a real (already
    materialized) title - shared by title_detail's own render and
    send_recommendation/title_preview_send_recommendation's re-render of
    just that card. Sorts every other profile into exactly one of:
    already watched it, already has a pending recommendation from this
    profile, or is a real candidate - so the card can show why a button
    isn't live instead of just hiding it."""
    if profile is None:
        return {"other_profiles": [], "already_watched_profile_ids": set(), "recommended_profile_ids": set()}
    other_profiles = list(Profile.objects.exclude(pk=profile.pk).order_by("display_name"))
    already_watched_profile_ids = set(
        WatchEvent.objects.filter(profile__in=other_profiles, title=title).values_list("profile_id", flat=True)
    )
    recommended_profile_ids = set(
        Recommendation.objects.filter(
            from_profile=profile, title=title, status=Recommendation.Status.PENDING
        ).values_list("to_profile_id", flat=True)
    )
    return {
        "other_profiles": other_profiles,
        "already_watched_profile_ids": already_watched_profile_ids,
        "recommended_profile_ids": recommended_profile_ids,
    }


def _preview_recommend_context(profile):
    """The sidebar "Recommend to" card's context for a not-yet-tracked
    preview title - with no local Title row yet, there's no possible
    WatchEvent/Recommendation history against it, so every other profile
    is always a live candidate. Recommending materializes the Title (see
    title_preview_send_recommendation), same as every other preview
    action (mark watched, add to list/watchlist)."""
    if profile is None:
        return {"other_profiles": [], "already_watched_profile_ids": set(), "recommended_profile_ids": set()}
    return {
        "other_profiles": list(Profile.objects.exclude(pk=profile.pk).order_by("display_name")),
        "already_watched_profile_ids": set(),
        "recommended_profile_ids": set(),
    }


@login_required
@require_POST
def send_recommendation(request, pk):
    """The sidebar "Recommend to" card's one action - one click sends, no
    confirmation or message field (deliberately kept simple). No-ops
    (doesn't create a row or notify) rather than erroring when the target
    has already watched it or already has one pending from this same
    profile - the re-rendered card just reflects that state instead of
    a dead-end button."""
    title = get_object_or_404(Title, pk=pk)
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None:
        raise Http404
    to_profile = get_object_or_404(Profile, pk=request.POST.get("to_profile_id"))
    if to_profile.pk == profile.pk:
        raise Http404
    recommendations.send(profile, to_profile, title)
    return render(
        request, "tracker/partials/recommend_card.html", {"title": title, **_recommend_context(profile, title)}
    )


@login_required
@require_POST
def title_preview_send_recommendation(request, media_type, tmdb_id):
    """The preview page's "Recommend to" card - previously the only way
    to recommend a title was to first add it to a watchlist (which
    materializes the Title), even though recommending has nothing to do
    with your own watchlist. Materializes the Title itself (same as every
    other preview action) then behaves exactly like send_recommendation
    from then on, including notifying the recipient - the re-rendered
    card points any further clicks at the real endpoint via the now-
    materialized title.pk (see recommend_card.html)."""
    if media_type not in ("movie", "tv"):
        raise Http404
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None:
        raise Http404
    to_profile = get_object_or_404(Profile, pk=request.POST.get("to_profile_id"))
    if to_profile.pk == profile.pk:
        raise Http404
    title = _get_or_create_preview_title(media_type, tmdb_id)
    if title is None:
        raise Http404
    recommendations.send(profile, to_profile, title)
    return render(
        request,
        "tracker/partials/recommend_card.html",
        {"title": title, "is_preview": False, **_recommend_context(profile, title)},
    )


def _received_recommendations(profile):
    return list(
        Recommendation.objects.filter(to_profile=profile, status=Recommendation.Status.PENDING)
        .select_related("from_profile", "title")
        .order_by("-created_at")
    )


def _recommendations_context(profile):
    """Dashboard's "Recommended to you" card and its two HTMX actions
    (dismiss, add-to-watchlist) all re-render the same partial off this -
    watchlisted_title_ids lets the card show "Added" instead of the
    add-to-watchlist button for a title already queued, without a second
    round trip per row."""
    received = _received_recommendations(profile)
    watchlisted_title_ids = set(
        WatchListItem.objects.filter(
            watchlist__profile=profile,
            watchlist__is_watchlist=True,
            title_id__in=[rec.title_id for rec in received],
        ).values_list("title_id", flat=True)
    )
    return {"received_recommendations": received, "watchlisted_title_ids": watchlisted_title_ids}


@login_required
@require_POST
def dismiss_recommendation(request, pk):
    """Removes a recommendation from the Dashboard's "Recommended to you"
    card without it counting as watched - re-recommending it later still
    works (the unique constraint only guards *pending* rows)."""
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None:
        raise Http404
    rec = get_object_or_404(Recommendation, pk=pk, to_profile=profile)
    rec.status = Recommendation.Status.DISMISSED
    rec.save(update_fields=["status"])
    return render(request, "tracker/partials/dashboard_recommendations.html", _recommendations_context(profile))


@login_required
@require_POST
def add_recommendation_to_watchlist(request, pk):
    """The "Recommended to you" card's quick-add - queues the title without
    resolving the recommendation itself, same as recommendations.py's
    docstring describes for any other route onto the Watchlist. It only
    leaves the pending feed once actually watched
    (recommendations.mark_title_watched, triggered from every "mark
    watched" path), so it keeps nudging until the title's actually seen,
    not just queued."""
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None:
        raise Http404
    rec = get_object_or_404(Recommendation, pk=pk, to_profile=profile)
    watchlist, _ = WatchList.objects.get_or_create(profile=profile, name="Watchlist", defaults={"is_watchlist": True})
    WatchListItem.objects.get_or_create(watchlist=watchlist, title=rec.title)
    return render(request, "tracker/partials/dashboard_recommendations.html", _recommendations_context(profile))


@login_required
def title_episodes(request, pk):
    """Re-renders just the episode browser (#episodes-panel) for a season
    switch - the season <select>'s own hx-get target, mirroring the
    Stats heatmap's year-select/#heatmap-panel pattern. title_preview_episodes
    is this same idea for a not-yet-tracked preview title."""
    title = get_object_or_404(Title, pk=pk)
    profile = Profile.objects.filter(user=request.user).first()
    context = {
        "title": title,
        "seasons": [],
        "season": None,
        "episodes": [],
        "season_avg_rating": None,
        "season_ratings": {},
        "preview_tmdb_id": None,
    }
    tmdb_id = title.external_ids.get("tmdb")
    if tmdb_id:
        details = tmdb.get_full_details(tmdb.media_type_for(title), tmdb_id)
        context.update(_episode_panel_context(request, profile, title, tmdb_id, details))
    return render(request, "tracker/partials/title_episodes.html", context)


@login_required
def title_preview_episodes(request, media_type, tmdb_id):
    """title_episodes' counterpart for a not-yet-tracked preview title -
    same season-switch endpoint shape, just keyed by (media_type, tmdb_id)
    since there's no local Title row/pk yet. Lets the episode browser's
    season <select> work on the preview page the same way it does on a
    real title's own page."""
    if media_type not in ("movie", "tv"):
        raise Http404
    profile = Profile.objects.filter(user=request.user).first()
    context = {
        "title": None,
        "seasons": [],
        "season": None,
        "episodes": [],
        "season_avg_rating": None,
        "season_ratings": {},
        "preview_media_type": media_type,
        "preview_tmdb_id": tmdb_id,
    }
    details = tmdb.get_full_details(media_type, tmdb_id)
    if details:
        context.update(_episode_panel_context(request, profile, None, tmdb_id, details))
    return render(request, "tracker/partials/title_episodes.html", context)


def _watched_button_template(request):
    """The watched-button popover fragment (title_mark_watched/
    title_unmark_watched/title_unmark_last_watched's HX-Request branches)
    is shared by two different trigger styles - the compact poster-card
    checkmark (grids) and title_detail's own header "Watched" pill - each
    with its own wrapper markup/positioning but the exact same menu
    panel (see watched_menu_panel.html). The menu's own POST buttons
    target "closest div.relative" (whichever wrapper is actually
    present), so HTMX's resolved HX-Target header tells us which one
    that was - the detail page's wrapper/bare-button ids are both
    prefixed "watched-*-detail-" specifically so this can tell them
    apart (the bare-button prefix matters too, for the very first
    "+ Mark as Watched" click before there's a popover wrapper at all)."""
    hx_target = request.headers.get("HX-Target", "")
    if hx_target.startswith("watched-popover-detail-") or hx_target.startswith("watched-btn-detail-"):
        return "tracker/partials/title_detail_watched_button.html"
    return "tracker/partials/poster_card_watched_button.html"


@login_required
@require_POST
def title_mark_watched(request, pk):
    """The quick "mark as watched" action, used by both the detail page's
    own header button (unwatched state) and the poster card action bar's
    watched button (HTMX, everywhere a poster card renders) - a plain,
    episode-less WatchEvent (same shape History/the activity feed already
    render as "watched <title>" with no episode). Always creates a new
    WatchEvent, a second click logs a rewatch - there's still no
    "unwatch" *here*; that's title_unmark_watched/title_unmark_last_watched,
    deliberately separate endpoints (see title_local_context's is_watched,
    and the poster card's own watched-button popover once selectors.title_watched
    is true) not a toggle baked into this one. The poster card's own watched button
    keeps this exact always-append behavior even once a title is watched
    (rewatch logging, now offered as an explicit popover menu item rather
    than a bare second click - see poster_card_watched_button.html) - only
    the detail page's dedicated header control gained real
    watched/unwatched toggle semantics."""
    title = get_object_or_404(Title, pk=pk)
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None:
        raise Http404
    WatchEvent.objects.create(profile=profile, title=title, watched_at=timezone.now())
    rewatches.recompute_is_rewatch(profile, title, None)
    completion.sync_watchlist_removal(profile, title)
    recommendations.mark_title_watched(profile, title)
    if request.headers.get("HX-Request"):
        watch_count = selectors.plain_watch_count(profile, title)
        return render(
            request,
            _watched_button_template(request),
            {"title": title, "watched": True, "watch_count": watch_count},
        )
    return redirect("title_detail", pk=pk)


@login_required
@require_POST
def title_unmark_watched(request, pk):
    """The detail page's own header "Watched" control, once already
    watched - a genuine toggle, unlike title_mark_watched's other
    callers (the poster card action bar, the episode browser), which
    always log a fresh rewatch and never unmark. Also reused by the
    poster card watched-button popover's "Remove all watched history"
    action (HX-Request branch below), which needed the exact same
    behavior. Removes only the plain (episode-less) watch marks that
    same header button creates - a show's per-episode history from the
    episode browser is a separate, untouched concern. The header form
    confirms client-side first (see confirmModal() in title_detail.html);
    the popover's own menu item carries its own hx-confirm instead."""
    title = get_object_or_404(Title, pk=pk)
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None:
        raise Http404
    WatchEvent.objects.filter(profile=profile, title=title, episode__isnull=True).delete()
    if request.headers.get("HX-Request"):
        return render(
            request,
            _watched_button_template(request),
            {"title": title, "watched": selectors.title_watched(profile, title), "watch_count": 0},
        )
    return redirect("title_detail", pk=pk)


@login_required
@require_POST
def title_unmark_last_watched(request, pk):
    """The poster card watched-button popover's "Remove last watched" -
    undoes a single play instead of title_unmark_watched's "clear
    everything," letting a title logged several times step back one play
    at a time. Only reachable via the popover, which requires a
    WatchEvent to exist (see selectors.title_watched) but not necessarily
    a *plain* one (e.g. a show watched only through the episode browser) -
    so there may be nothing to delete here in practice, a no-op is
    harmless either way."""
    title = get_object_or_404(Title, pk=pk)
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None:
        raise Http404
    last = (
        WatchEvent.objects.filter(profile=profile, title=title, episode__isnull=True)
        .order_by("-watched_at")
        .first()
    )
    if last is not None:
        last.delete()
    watch_count = selectors.plain_watch_count(profile, title)
    if request.headers.get("HX-Request"):
        return render(
            request,
            _watched_button_template(request),
            {"title": title, "watched": selectors.title_watched(profile, title), "watch_count": watch_count},
        )
    return redirect("title_detail", pk=pk)


@login_required
@require_POST
def episode_mark_watched(request, pk, season, episode_number):
    """The episode browser's per-episode watched button - materializes the
    local Episode row (sync/import may already have created one for this
    exact season/episode) with its TMDB name, then behaves like
    title_mark_watched: always a new WatchEvent, no "unwatch", a second
    click logs a rewatch. Always an HTMX fragment - this button only ever
    appears inside title_episodes.html."""
    title = get_object_or_404(Title, pk=pk)
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None:
        raise Http404
    ep_name = ""
    tmdb_id = title.external_ids.get("tmdb")
    if tmdb_id:
        season_data = tmdb.get_season_details(tmdb_id, season)
        if season_data:
            ep_name = next(
                (e["name"] for e in season_data["episodes"] if e["episode_number"] == episode_number), ""
            )
    episode, _ = Episode.objects.get_or_create(
        title=title, season=season, episode=episode_number, defaults={"name": ep_name}
    )
    WatchEvent.objects.create(profile=profile, title=title, episode=episode, watched_at=timezone.now())
    rewatches.recompute_is_rewatch(profile, title, episode)
    completion.sync_show_completion(profile, title)
    completion.sync_watchlist_removal(profile, title)
    recommendations.mark_title_watched(profile, title)
    return render(
        request,
        "tracker/partials/episode_watched_button.html",
        {
            "title": title,
            "season": season,
            "episode_number": episode_number,
            "watched": True,
            "id_suffix": request.POST.get("id_suffix", ""),
        },
    )


def _mark_episodes_watched_bulk(profile, title, episode_specs):
    """Shared by title_mark_season_watched/title_mark_all_seasons_watched -
    episode_specs is an iterable of (season, episode_number, name) for
    every episode TMDB reports across whatever season(s) are being
    marked. Unlike episode_mark_watched's own single-tile button (always
    logs a fresh play, even on a title already fully watched), this only
    creates a WatchEvent for an episode with zero existing ones - "catch
    my watched status up to reality" rather than "log a rewatch of
    everything," which is what a season/whole-show bulk action actually
    means. Each newly-created event is a genuine first watch, so there's
    nothing for rewatches.recompute_is_rewatch to do (its default
    is_rewatch=False is already correct) - skipped entirely, unlike
    csv_import.py's own bulk path, which (unlike this one) can import an
    out-of-order historical watch that legitimately needs recomputing.
    Returns how many new plays were logged."""
    seasons_touched = {season for season, _, _ in episode_specs}
    already_watched = set(
        WatchEvent.objects.filter(
            profile=profile, title=title, episode__isnull=False, episode__season__in=seasons_touched
        ).values_list("episode__season", "episode__episode")
    )
    now = timezone.now()
    created = 0
    for season, episode_number, name in episode_specs:
        if (season, episode_number) in already_watched:
            continue
        episode, _ = Episode.objects.get_or_create(
            title=title, season=season, episode=episode_number, defaults={"name": name}
        )
        WatchEvent.objects.create(profile=profile, title=title, episode=episode, watched_at=now)
        created += 1
    if created:
        completion.sync_show_completion(profile, title)
        completion.sync_watchlist_removal(profile, title)
        recommendations.mark_title_watched(profile, title)
    return created


@login_required
@require_POST
def title_mark_season_watched(request, pk, season):
    """The episode browser's "Mark season watched" action - catches up
    every episode TMDB reports for this one season the profile hasn't
    already logged a play for, in a single request. Re-renders the
    episode panel pinned to this same season (force_season) rather than
    whatever _episode_panel_context would otherwise resolve from
    request.GET, which this POST doesn't carry."""
    title = get_object_or_404(Title, pk=pk)
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None:
        raise Http404
    tmdb_id = title.external_ids.get("tmdb")
    details = None
    if tmdb_id:
        season_data = tmdb.get_season_details(tmdb_id, season)
        episodes = season_data["episodes"] if season_data else []
        _mark_episodes_watched_bulk(
            profile, title, [(season, ep["episode_number"], ep.get("name") or "") for ep in episodes]
        )
        details = tmdb.get_full_details(tmdb.media_type_for(title), tmdb_id)
    context = {
        "title": title,
        "seasons": [],
        "season": None,
        "episodes": [],
        "season_avg_rating": None,
        "season_ratings": {},
        "preview_tmdb_id": None,
    }
    if tmdb_id:
        context.update(_episode_panel_context(request, profile, title, tmdb_id, details, force_season=season))
    return render(request, "tracker/partials/title_episodes.html", context)


@login_required
@require_POST
def title_mark_all_seasons_watched(request, pk):
    """The episode browser's "Mark all seasons watched" action -
    episode_mark_watched/title_mark_season_watched's whole-show version.
    One tmdb.get_season_details call per season (same N-calls-per-show
    pattern completion.py's own _backfill_episode_runtimes already
    uses) to get real episode names for every season, not just a
    TMDB-reported episode count with blank names - worth the extra
    calls since this is a deliberate, infrequent catch-up action, not a
    hot path."""
    title = get_object_or_404(Title, pk=pk)
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None:
        raise Http404
    tmdb_id = title.external_ids.get("tmdb")
    context = {
        "title": title,
        "seasons": [],
        "season": None,
        "episodes": [],
        "season_avg_rating": None,
        "season_ratings": {},
        "preview_tmdb_id": None,
    }
    if tmdb_id:
        details = tmdb.get_full_details(tmdb.media_type_for(title), tmdb_id)
        number_of_seasons = details["number_of_seasons"] if details else 0
        episode_specs = []
        for season in range(1, (number_of_seasons or 0) + 1):
            season_data = tmdb.get_season_details(tmdb_id, season)
            if season_data:
                episode_specs.extend(
                    (season, ep["episode_number"], ep.get("name") or "") for ep in season_data["episodes"]
                )
        _mark_episodes_watched_bulk(profile, title, episode_specs)
        context.update(_episode_panel_context(request, profile, title, tmdb_id, details))
    return render(request, "tracker/partials/title_episodes.html", context)


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
        recommendations.mark_title_watched(profile, title)
    completion.sync_watchlist_removal(profile, title)
    return redirect("title_detail", pk=pk)


@login_required
def title_preview(request, media_type, tmdb_id):
    """The click-through page for a Movies & TV / Anime discovery card -
    not backed by a local Title row (the user may never have watched it),
    so this is read-only against TMDB directly - marking watched or
    adding to any list are the only actions allowed to create the Title
    row. If a matching Title already exists (found this exact tmdb_id
    before, from a sync/import or an earlier watchlist-add here), that's
    the real page for it - redirect there instead of showing a second,
    library-blind copy of the same title."""
    if media_type not in ("movie", "tv"):
        raise Http404
    # tmdb_kind constrains this to the same TMDB catalog media_type came
    # from - movie and tv ids are separate TMDB namespaces, so omitting
    # this could redirect to a same-numbered but unrelated title from the
    # other catalog (see discover_action_context's own docstring for a
    # live case this exact gap caused).
    existing = Title.objects.filter(external_ids__tmdb=str(tmdb_id), external_ids__tmdb_kind=media_type).first()
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
        "release_info": _release_info(details),
        "is_preview": True,
        "preview_media_type": media_type,
        "preview_tmdb_id": tmdb_id,
        **_title_display(None, details),
        "progress": None,
        "recent_events": [],
        "latest_rating": None,
        # Drives this page's own "Lists" card (every chip starts unfilled -
        # there's no local Title yet, so in_list_ids is always empty here;
        # see title_preview_add_to_list) as well as the "similar" grid's
        # discover_tile.html includes below, whose own list-picker popovers
        # are for those (also not-yet-tracked) titles.
        "my_lists": list(WatchList.objects.filter(profile=profile).order_by("name")) if profile else [],
        "in_list_ids": set(),
        **_preview_recommend_context(profile),
        **_episode_panel_context(request, profile, None, tmdb_id, details),
    }
    if profile is not None and context["similar"]:
        context.update(selectors.discover_action_context(profile, context["similar"]))
    return render(request, "tracker/title_detail.html", context)


_EMPTY_DISCOVER_CONTEXT = {
    "discover_title_by_key": {},
    "discover_watched": {},
    "discover_watch_count": {},
    "discover_list_membership": {},
}


def _person_age(birthday_date, deathday_date):
    """Years as of today, or as of deathday when the person has one
    ("age at death" per the spec) - accounts for whether their birthday
    has occurred yet within the end year. Takes already-parsed dates
    (see _parse_tmdb_date) - person_detail parses birthday/deathday once
    and reuses the same date objects for this and for the template's own
    |date: formatting, rather than parsing the raw strings twice."""
    if birthday_date is None:
        return None
    end = deathday_date or timezone.localdate()
    return end.year - birthday_date.year - ((end.month, end.day) < (birthday_date.month, birthday_date.day))


@login_required
def person_detail(request, person_id):
    """The click-through page for a cast/director credit on any movie,
    TV, or anime title page (title_detail.html's Cast row, shared by
    title_detail and title_preview, so this covers not-yet-tracked
    preview pages too) - bio/photo/filmography straight from TMDB, plus
    household watch stats (selectors.person_personal_stats) TMDB has no
    concept of. Read-only against TMDB, same as title_preview - no local
    row is ever created for a person."""
    details = tmdb.get_person_details(person_id)
    if details is None:
        raise Http404
    credits = tmdb.get_person_credits(person_id)
    profile = Profile.objects.filter(user=request.user).first()

    # Deduped once across all three departments - a hyphenate (e.g.
    # someone who both acted in and directed a film) would otherwise get
    # counted twice in the personal-stats totals below, even though the
    # filmography display itself deliberately still lists it under both
    # of their department sections.
    deduped_items = {}
    for department in ("acting", "directing", "writing"):
        for item in credits[department]:
            deduped_items.setdefault(item["tmdb_id"], item)
    items = list(deduped_items.values())

    action_context = (
        selectors.discover_action_context(profile, items) if profile and items else _EMPTY_DISCOVER_CONTEXT
    )
    stats = selectors.person_personal_stats(profile, items, action_context) if profile else None

    sections = {}
    for department in ("acting", "directing", "writing"):
        section_items = list(credits[department])
        for item in section_items:
            item["watched"] = action_context["discover_watched"].get(f"{item['media_type']}:{item['tmdb_id']}", False)
        # Two-pass stable sort (not one combined key) so "newest first"
        # holds *within* each watched-status group rather than being
        # overridden by it - Python's sort is stable, so the first pass's
        # date order survives the second pass's watched/unwatched split.
        section_items.sort(key=lambda i: i["release_date"] or "", reverse=True)
        section_items.sort(key=lambda i: not i["watched"])
        sections[department] = section_items

    department_labels = {"acting": "Acting", "directing": "Directing", "writing": "Writing"}
    filmography_sections = [
        {"key": department, "label": department_labels[department], "items": sections[department]}
        for department in ("acting", "directing", "writing")
        if sections[department]
    ]
    # "Known for" (primary) is whichever section has the most credits, not
    # just TMDB's own single-guess known_for_department or display order -
    # a person could be primarily a director with only a couple of small
    # acting credits, and "known for acting" would read wrong for them.
    # "also" lists every other non-empty section, so a hyphenate reads as
    # "Known for Acting - also Directing, Writing" per the spec's "or
    # multiple if applicable".
    by_credit_count = sorted(filmography_sections, key=lambda s: -len(s["items"]))
    known_for_primary = by_credit_count[0]["label"] if by_credit_count else details.get("known_for_department")
    known_for_secondary = [s["label"] for s in by_credit_count[1:]]

    birthday_date = _parse_tmdb_date(details.get("birthday"))
    deathday_date = _parse_tmdb_date(details.get("deathday"))

    # The stats card's inline figures - built as an ordered list (not
    # separate always-present context keys) so the template can lay them
    # out identically regardless of which ones this particular person
    # actually has data for. The credit-count callouts only cover
    # secondary departments - the primary one is already named in
    # "Known for", so repeating its count here would be redundant.
    highlight_stats = []
    if stats:
        highlight_stats.append(
            {
                "value": f"{stats['watched_count']} of {stats['total_count']}", "label": "watched",
                "info": f"Limited to their {tmdb.CREDIT_CAP} most notable credits per category.",
            }
        )
        if stats["total_watch_time"]:
            highlight_stats.append({"value": stats["total_watch_time"], "label": "total watch time", "info": None})
        if stats["avg_rating"]:
            highlight_stats.append(
                {"value": f"★ {stats['avg_rating']}/10", "label": "your average rating", "info": None}
            )
    for section in by_credit_count:
        if section["label"] != known_for_primary:
            count = len(section["items"])
            noun = "credit" if count == 1 else "credits"
            highlight_stats.append(
                {"value": str(count), "label": f"{section['label'].lower()} {noun}", "info": None}
            )

    context = {
        "profile": profile,
        "person": details,
        "known_for_primary": known_for_primary,
        "known_for_secondary": known_for_secondary,
        "birthday_date": birthday_date,
        "deathday_date": deathday_date,
        "age": _person_age(birthday_date, deathday_date),
        "stats": stats,
        "highlight_stats": highlight_stats,
        "filmography_sections": filmography_sections,
        # discover_tile.html's own list-picker popover needs this regardless
        # of whether a given tile has a matched local Title yet - same
        # context key title_detail's "similar" grid and title_preview both
        # already provide it under.
        "my_lists": list(WatchList.objects.filter(profile=profile).order_by("name")) if profile else [],
        **action_context,
    }
    return render(request, "tracker/person_detail.html", context)


def _get_or_create_preview_title(media_type, tmdb_id):
    """get-or-create the local Title for a TMDB preview id (same shape as
    trakt.py/simkl.py's own get-or-create, just keyed off an id we already
    have instead of a name+year search) - shared by every action a
    not-yet-tracked discover/preview card can trigger (watchlist-add,
    mark watched, add to any list), so a title only ever gets materialized
    once regardless of which action the user clicks first. Returns None
    if TMDB has nothing for this id.

    Matches tmdb_kind alongside tmdb id (see discover_action_context's
    docstring) - without it, materializing e.g. a tv preview whose id
    happens to collide with an unrelated movie's would silently reuse
    that movie's Title row instead of creating the real one."""
    title = Title.objects.filter(external_ids__tmdb=str(tmdb_id), external_ids__tmdb_kind=media_type).first()
    if title is not None:
        return title
    details = tmdb.get_full_details(media_type, tmdb_id)
    if details is None:
        return None
    media_type_for_title = MediaType.MOVIE if media_type == "movie" else MediaType.TV
    title = Title.objects.create(
        media_type=media_type_for_title,
        name=details["name"],
        year=int(details["year"]) if details["year"] else 0,
        poster_url=details["poster_url"] or "",
        external_ids={"tmdb": str(tmdb_id), "tmdb_kind": media_type},
    )
    attach_genres(title, details["genres"])
    return title


@login_required
@require_POST
def title_preview_add_to_watchlist(request, media_type, tmdb_id):
    """The preview page's one write action - materialize the Title, then
    drop it on the profile's auto-managed Watchlist (get-or-created by
    name, flagged is_watchlist=True on creation so completion.py's
    sync_watchlist_removal can find it later) and hand off to the real
    detail page, where the fuller add-to-any-list UI lives."""
    if media_type not in ("movie", "tv"):
        raise Http404
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None:
        raise Http404

    title = _get_or_create_preview_title(media_type, tmdb_id)
    if title is None:
        raise Http404

    watchlist, _ = WatchList.objects.get_or_create(
        profile=profile, name="Watchlist", defaults={"is_watchlist": True}
    )
    WatchListItem.objects.get_or_create(watchlist=watchlist, title=title)
    return redirect("title_detail", pk=title.pk)


@login_required
@require_POST
def title_preview_mark_watched(request, media_type, tmdb_id):
    """The Discover grid's watched button (HTMX, no local Title row yet)
    AND the preview page's own header "Mark as Watched" button (a plain
    form post - previously the preview page only offered "Add to
    Watchlist", with no independent way to log a watch for something
    you'd already seen elsewhere) - materializes the title (see
    _get_or_create_preview_title), then behaves exactly like
    title_mark_watched from then on. HTMX gets the fragment back in
    place; a plain post redirects to the real detail page, same as
    title_preview_add_to_watchlist."""
    if media_type not in ("movie", "tv"):
        raise Http404
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None:
        raise Http404
    title = _get_or_create_preview_title(media_type, tmdb_id)
    if title is None:
        raise Http404
    WatchEvent.objects.create(profile=profile, title=title, watched_at=timezone.now())
    rewatches.recompute_is_rewatch(profile, title, None)
    completion.sync_watchlist_removal(profile, title)
    recommendations.mark_title_watched(profile, title)
    if request.headers.get("HX-Request"):
        watch_count = selectors.plain_watch_count(profile, title)
        return render(
            request,
            "tracker/partials/poster_card_watched_button.html",
            {"title": title, "watched": True, "watch_count": watch_count},
        )
    return redirect("title_detail", pk=title.pk)


@login_required
@require_POST
def title_preview_add_to_list(request, media_type, tmdb_id, list_id):
    """The Discover grid's list-picker popover's first click on a
    not-yet-tracked title - materializes it, adds it to the chosen list,
    then hands back the *standard* poster_card_list_popover.html fragment
    (now keyed to a real title.pk). Every subsequent click in that same
    popover flows through the ordinary add_to_list/remove_from_list
    endpoints - this one only ever needs to handle "add", never "remove",
    since a title that didn't exist a moment ago can't already be on any
    list yet.

    Also the preview page's own "Lists" card (a plain, non-HTMX form,
    same as the rest of that card) - not just the Discover grid's HTMX
    popover - so a non-HTMX request instead redirects to the now-real
    title_detail page, same as title_preview_add_to_watchlist already
    does."""
    if media_type not in ("movie", "tv"):
        raise Http404
    profile = Profile.objects.filter(user=request.user).first()
    watchlist = get_object_or_404(WatchList, pk=list_id)
    if profile is None or not watchlist.can_edit(profile):
        raise Http404
    title = _get_or_create_preview_title(media_type, tmdb_id)
    if title is None:
        raise Http404
    WatchListItem.objects.get_or_create(watchlist=watchlist, title=title)
    if not request.headers.get("HX-Request"):
        return redirect("title_detail", pk=title.pk)
    return _render_poster_actions(request, profile, title)


def _build_episode_group(title, run):
    """The group-card dict shape shared by _group_consecutive_episodes
    (a fresh grouping of a day's events) and history_delete_episode
    (rebuilding one group after removing a single episode from it) -
    only min/max-by-episode and a sum, neither of which cares about
    run's own ordering."""
    episodes = [e.episode for e in run]
    first_by_ep = min(episodes, key=lambda e: (e.season, e.episode))
    last_by_ep = max(episodes, key=lambda e: (e.season, e.episode))
    total_minutes = sum((e.episode.runtime_minutes or e.title.runtime_minutes or 0) for e in run)
    return {
        "is_group": True,
        "title": title,
        "count": len(run),
        "range_label": f"S{first_by_ep.season}E{first_by_ep.episode}–S{last_by_ep.season}E{last_by_ep.episode}",
        "total_duration": selectors.format_duration(total_minutes) if total_minutes else None,
        "events": run,
        "timeline_events": sorted(run, key=lambda e: e.watched_at),
        # Nuvio syncs write a whole session's episodes in one batch, so in
        # practice a group is either all-Nuvio or none - "any" rather than
        # "all" just means a mixed group (e.g. one episode later re-logged
        # manually) still gets flagged instead of silently losing the marker.
        "has_nuvio_source": any(e.source == WatchEvent.Source.NUVIO for e in run),
    }


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
        grouped.append(_build_episode_group(run[0].title, run) if len(run) > 1 else event)
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
        # Same episode-then-title runtime fallback _build_episode_group
        # already uses for a single group's own total - here summed
        # across the whole day (movies included) instead of one run.
        total_minutes = sum((e.episode.runtime_minutes if e.episode else None) or e.title.runtime_minutes or 0 for e in items)
        groups.append(
            {
                "date": day,
                "items": _group_consecutive_episodes(items),
                "movie_count": movie_count,
                "episode_count": len(items) - movie_count,
                "total_duration": selectors.format_duration(total_minutes) if total_minutes else None,
            }
        )
    return groups


def _paginate_history_by_day(events, page_number, descending):
    """Paginates History by calendar date, not by tile or raw WatchEvent
    row - a "page" is HISTORY_DATES_PER_PAGE dates' worth of history,
    however many tiles that turns out to be (a heavy binge day next to
    several quiet ones is still just as many *dates*, so pages stay
    predictable in a way row/tile counts weren't - see CHANGELOG for the
    tile-starved-page bug this replaced).

    Two queries instead of loading every matching WatchEvent into memory
    to group-then-paginate: first the distinct watched dates (one row
    per day, not per event - cheap even across years of history) to
    figure out which page's worth of *dates* to show, then only the
    events actually falling on those specific dates. events is the
    filtered-but-unordered queryset (type/period/title/search already
    applied by the caller) - ordering is set here rather than by the
    caller, since a SELECT DISTINCT's ORDER BY must be one of the
    selected expressions (the distinct-dates query selects the
    TruncDate'd day, not the raw watched_at the final event fetch orders
    by)."""
    order_prefix = "-" if descending else ""
    dates_qs = (
        events.annotate(day=TruncDate("watched_at"))
        .values_list("day", flat=True)
        .distinct()
        .order_by(f"{order_prefix}day")
    )
    date_page = Paginator(dates_qs, HISTORY_DATES_PER_PAGE).get_page(page_number)
    page_dates = list(date_page.object_list)
    page_events = (
        events.filter(watched_at__date__in=page_dates)
        .select_related("title", "episode")
        .order_by(f"{order_prefix}watched_at")
    )
    return date_page, _group_history_by_day(list(page_events))


def _time_format_str(profile):
    return "H:i" if profile and profile.time_format == Profile.TimeFormat.H24 else "g:i A"


def _history_context(request, profile):
    type_filter = request.GET.get("type", "all")
    period = request.GET.get("period", "all") if request.GET.get("period") in HISTORY_PERIODS else "all"
    sort = request.GET.get("sort") if request.GET.get("sort") in HISTORY_SORTS else "new"
    query = request.GET.get("q", "").strip()
    # Set from the poster card watched-button popover's "View history
    # plays" link (?title=<pk>) - narrows History down to just that one
    # title instead of the profile's whole history. title_filter is the
    # actual Title (not just the id) so history.html can show what it's
    # filtered to and offer a way to clear it.
    title_id = request.GET.get("title", "")
    title_filter = Title.objects.filter(pk=title_id).first() if title_id.isdigit() else None
    grouped_by_watch_count = sort in ("most_watched", "least_watched")

    page_obj = None
    day_groups = []
    title_rows = []
    if profile is not None:
        events = WatchEvent.objects.filter(profile=profile)
        if type_filter in (MediaType.MOVIE, MediaType.TV, MediaType.ANIME):
            events = events.filter(title__media_type=type_filter)
        if title_filter is not None:
            events = events.filter(title=title_filter)
        if query:
            events = events.filter(title__name__icontains=query)

        now = timezone.now()
        today_start = timezone.localtime(now).replace(hour=0, minute=0, second=0, microsecond=0)
        if period == "today":
            events = events.filter(watched_at__gte=today_start)
        elif period == "yesterday":
            events = events.filter(watched_at__gte=today_start - timedelta(days=1), watched_at__lt=today_start)
        elif period in ("7", "30", "365"):
            events = events.filter(watched_at__gte=now - timedelta(days=int(period)))

        if grouped_by_watch_count:
            rows = (
                events.values("title_id")
                .annotate(watch_count=Count("id"), last_watched_at=Max("watched_at"))
                .order_by("-watch_count" if sort == "most_watched" else "watch_count", "-last_watched_at")
            )
            page_obj = Paginator(rows, HISTORY_PAGE_SIZE).get_page(request.GET.get("page"))
            titles_by_id = Title.objects.in_bulk([r["title_id"] for r in page_obj.object_list])
            title_rows = [
                {"title": titles_by_id[r["title_id"]], "watch_count": r["watch_count"], "last_watched_at": r["last_watched_at"]}
                for r in page_obj.object_list
                if r["title_id"] in titles_by_id
            ]
        else:
            page_obj, day_groups = _paginate_history_by_day(events, request.GET.get("page"), descending=sort == "new")

    query_without_page = request.GET.copy()
    query_without_page.pop("page", None)

    return {
        "profile": profile,
        "page_obj": page_obj,
        "day_groups": day_groups,
        "title_rows": title_rows,
        "grouped_by_watch_count": grouped_by_watch_count,
        "type_filter": type_filter,
        "period": period,
        "sort": sort,
        "query": query,
        "title_filter": title_filter,
        "base_query": query_without_page.urlencode(),
        "time_format_str": _time_format_str(profile),
    }


@login_required
def history(request, profile_id=None):
    """profile_id is only present on member_history's URL - viewing
    another household profile's history read-only (no bulk-select/delete,
    see history.html's is_own_history gate). history_bulk_delete always
    operates on the request's own profile regardless, so it's harmless
    even if that gate were somehow bypassed.

    Two different HTMX targets hit this same view: the toolbar's own
    form/the drawer's Period+Sort selects (type/search/period/sort
    changes) target #history-page and need the toolbar re-rendered too -
    otherwise the Filters button's own active-filter dot and the type
    toggle's checked state go stale after a change, since only the
    results below them would otherwise be swapped. Pagination/bulk-
    delete (which never change type/period/sort) target #history-content
    directly and only need the results themselves back."""
    target, is_own = _resolve_stats_profile(request, profile_id)
    context = _history_context(request, target)
    context["is_own_history"] = is_own
    context["history_base_url"] = reverse("history") if is_own or target is None else reverse("member_history", args=[target.pk])
    hx_target = request.headers.get("HX-Target") or ""
    if hx_target == "history-page":
        template = "tracker/partials/history_toolbar_and_content.html"
    elif request.headers.get("HX-Request"):
        template = "tracker/partials/history_content.html"
    else:
        template = "tracker/history.html"
    return render(request, template, context)


@login_required
@require_POST
def history_bulk_delete(request):
    """Deletes multiple WatchEvents at once from History's multi-select
    bar. Checkbox values are either a single event id or (for a collapsed
    binge-group tile) a comma-joined list of every event id in that group,
    so a plain `request.POST.getlist` needs a further comma-split/flatten
    before filtering - see history_group_tile.html's checkbox `value`."""
    profile = Profile.objects.filter(user=request.user).first()
    if profile is not None:
        event_ids = {
            int(part)
            for raw in request.POST.getlist("event_ids")
            for part in raw.split(",")
            if part.strip().isdigit()
        }
        WatchEvent.objects.filter(profile=profile, pk__in=event_ids).delete()
    context = _history_context(request, profile)
    return render(request, "tracker/partials/history_content.html", context)


@login_required
@require_POST
def history_delete_episode(request, event_id):
    """The binge-group tile's per-episode delete dropdown - removes one
    episode from an existing group and re-renders just that group
    (shrunk, degraded to a single tile if only one episode is left, or
    removed entirely if none are), rather than recomputing the whole
    day's grouping from scratch. remaining_ids (the group's other event
    ids, from the tile's own last render - see history_group_tile.html)
    is enough to do that: deleting from the middle of an already-
    contiguous run can only ever shrink it, never re-merge it with a
    different title's run, so there's nothing else to recompute."""
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None:
        raise Http404
    event = get_object_or_404(WatchEvent, pk=event_id, profile=profile)
    title = event.title
    event.delete()

    remaining_ids = {int(part) for part in request.POST.get("remaining_ids", "").split(",") if part.strip().isdigit()}
    remaining = list(
        WatchEvent.objects.filter(pk__in=remaining_ids, profile=profile)
        .select_related("title", "episode")
        .order_by("watched_at")
    )
    if not remaining:
        return HttpResponse(status=200)
    context = {"time_format_str": _time_format_str(profile)}
    if len(remaining) == 1:
        context["event"] = remaining[0]
        return render(request, "tracker/partials/history_tile.html", context)
    context["group"] = _build_episode_group(title, remaining)
    return render(request, "tracker/partials/history_group_tile.html", context)


@login_required
@require_POST
def history_delete_group(request):
    """The binge-group tile's top-right delete button - removes every
    episode in the group in one action, unlike the count badge's dropdown
    which removes one at a time. The confirm dialog (built client-side in
    history_group_tile.html, showing the play count) is the only guard
    against an accidental one-click wipe of a whole binge, so this view
    trusts it and just deletes - same trust level history_bulk_delete
    already gives its own multi-select confirm. event_ids is the same
    comma-joined format history_bulk_delete/history_delete_episode's
    remaining_ids already use."""
    profile = Profile.objects.filter(user=request.user).first()
    if profile is not None:
        event_ids = {int(part) for part in request.POST.get("event_ids", "").split(",") if part.strip().isdigit()}
        WatchEvent.objects.filter(profile=profile, pk__in=event_ids).delete()
    return HttpResponse(status=200)


CALENDAR_TYPES = {"movie": MediaType.MOVIE, "tv": MediaType.TV, "anime": MediaType.ANIME}
# How far back the sidebar's agenda list looks for already-passed releases,
# so a weekly show doesn't just vanish from it the moment its release time
# ticks by - it stays visible for a while, same idea as History does for
# anything you've already watched.
AGENDA_LOOKBACK_DAYS = 30


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
        # The grid is scoped to the specific month being viewed (not just
        # "upcoming from now") so navigating to a past month still shows
        # its releases, instead of every past month rendering blank.
        month_start = timezone.make_aware(timezone.datetime.combine(base_date, timezone.datetime.min.time()))
        month_end = timezone.make_aware(timezone.datetime.combine(next_month, timezone.datetime.min.time()))
        month_releases = list(
            selectors.calendar_releases(profile, media_type, source_filter, start=month_start, end=month_end)
        )
        by_date = {}
        for rs in month_releases:
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
        agenda_releases = list(
            selectors.calendar_releases(
                profile, media_type, source_filter, start=timezone.now() - timedelta(days=AGENDA_LOOKBACK_DAYS)
            )
        )
        agenda_groups = [
            {"date": day, "items": list(items)}
            for day, items in groupby(agenda_releases, key=lambda r: timezone.localtime(r.release_date).date())
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


def _dispatch_release_sync_safely(timeout=2.0):
    """Same short-timeout background-thread dispatch as
    _dispatch_sync_task_safely, just for sync_release_schedules - which
    takes no per-profile argument (it's instance-wide, see its own
    docstring), so that helper's args=[profile_id] doesn't fit here."""
    def _dispatch():
        try:
            tasks.sync_release_schedules.apply_async(retry=False)
        except Exception:
            logging.getLogger(__name__).exception("Background dispatch of sync_release_schedules failed")

    thread = threading.Thread(target=_dispatch, daemon=True)
    thread.start()
    thread.join(timeout=timeout)
    if thread.is_alive():
        logging.getLogger(__name__).warning(
            "Dispatch of sync_release_schedules did not complete within %ss; abandoning it for this request "
            "(the nightly beat sync will still pick it up)",
            timeout,
        )


@login_required
@require_POST
def calendar_refresh_releases(request):
    """The Calendar page's manual refresh button - kicks off the same
    household-wide release sync the nightly beat job runs, instead of
    waiting for it. Fire-and-forget like every other manual "sync now"
    action (see _dispatch_sync_task_safely) - the button's own Alpine
    state gives the click immediate feedback, this response carries no
    fresh data of its own (the sync runs on a worker, not in this
    request), so there's nothing here worth rendering."""
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None:
        raise Http404
    _dispatch_release_sync_safely()
    return HttpResponse(status=204)


LIST_PERIODS = {"today", "yesterday", "7", "30", "365"}
LIST_SORTS = {"manual", "added_new", "added_old", "name", "year"}


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


def _list_detail_context(request, watchlist, profile):
    """type/period/sort mirror History's own filter shape (_history_context)
    but scoped to what makes sense for a list: period narrows by added_at
    (a list item has no watch date of its own), and sort adds a manual
    drag-order option on top of History's added/name/year choices. Drag-
    reordering (list_detail_items.html, views.reorder_list) is only offered
    when the view is fully unfiltered - narrowing to a subset makes
    "where does this item's new position put it" ambiguous relative to
    whatever's hidden, so can_reorder gates the drag handles off outside
    that state rather than trying to resolve it."""
    type_filter = request.GET.get("type", "all")
    period = request.GET.get("period", "all") if request.GET.get("period") in LIST_PERIODS else "all"
    sort = request.GET.get("sort") if request.GET.get("sort") in LIST_SORTS else "manual"

    items = watchlist.items.select_related("title").prefetch_related("title__ratings")
    if type_filter in (MediaType.MOVIE, MediaType.TV, MediaType.ANIME):
        items = items.filter(title__media_type=type_filter)

    now = timezone.now()
    today_start = timezone.localtime(now).replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "today":
        items = items.filter(added_at__gte=today_start)
    elif period == "yesterday":
        items = items.filter(added_at__gte=today_start - timedelta(days=1), added_at__lt=today_start)
    elif period in ("7", "30", "365"):
        items = items.filter(added_at__gte=now - timedelta(days=int(period)))

    sort_field = {"added_new": "-added_at", "added_old": "added_at", "name": "title__name", "year": "-title__year"}.get(sort)
    items = list(items.order_by(sort_field) if sort_field else items)

    return {
        "watchlist": watchlist,
        "can_edit": watchlist.can_edit(profile),
        "can_reorder": type_filter == "all" and period == "all" and sort == "manual",
        "items": items,
        "type_filter": type_filter,
        "period": period,
        "sort": sort,
        **selectors.poster_action_context(profile, [item.title for item in items]),
    }


@login_required
def list_detail(request, list_id):
    profile = Profile.objects.filter(user=request.user).first()
    watchlist = _get_visible_list_or_404(profile, list_id)
    context = _list_detail_context(request, watchlist, profile)
    context["total_count"] = watchlist.items.count()
    template = "tracker/partials/list_toolbar_and_items.html" if request.headers.get("HX-Request") else "tracker/list_detail.html"
    return render(request, template, context)


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


@login_required
@require_POST
def toggle_list_shared(request, list_id):
    """The list's own creator's call (can_edit, not is_owner - unlike
    is_featured, which is an owner-only curation power below). Un-sharing
    also clears is_featured: a private list can never appear in the
    Dashboard's Featured Lists rail anyway (selectors.featured_lists()
    requires both is_shared and is_featured), so leaving the flag set
    would just be a dangling no-op waiting to confuse whoever re-shares
    it later expecting it to still need featuring."""
    profile = Profile.objects.filter(user=request.user).first()
    watchlist = get_object_or_404(WatchList, pk=list_id)
    if profile is None or not watchlist.can_edit(profile):
        raise Http404
    watchlist.is_shared = not watchlist.is_shared
    if not watchlist.is_shared:
        watchlist.is_featured = False
    watchlist.save(update_fields=["is_shared", "is_featured"])
    return redirect("list_detail", list_id=watchlist.id)


@login_required
@require_POST
def toggle_list_featured(request, list_id):
    """Owner-only curation power (independent of watchlist.can_edit/who
    created the list) - surfaces a shared list in the Dashboard's Featured
    Lists rail for every profile, so that rail stays curated by whoever
    runs the instance rather than turning into every profile's own pins."""
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None or not profile.is_owner:
        raise Http404
    watchlist = get_object_or_404(WatchList, pk=list_id)
    watchlist.is_featured = not watchlist.is_featured
    watchlist.save(update_fields=["is_featured"])
    return redirect("lists")


def _render_list_items(request, watchlist, profile):
    """profile is the acting/viewing profile, not necessarily
    watchlist.profile - a shared list can be viewed/edited by other
    household profiles too (see _get_visible_list_or_404), and their own
    watched/list-membership state (not the list creator's) is what the
    re-rendered cards' action buttons need to reflect.

    Always the unfiltered/manual-order view (can_reorder=True) - add/
    remove/reorder don't carry the list_detail toolbar's current type/
    period/sort (those live in a GET query string, this is a POST), so
    they intentionally reset the view to the default rather than trying
    to thread filter state through every action. Not a regression: before
    list filtering existed, this was the only view there was."""
    items = list(watchlist.items.select_related("title").prefetch_related("title__ratings"))
    context = {
        "watchlist": watchlist,
        "can_edit": True,
        "can_reorder": True,
        "items": items,
        **selectors.poster_action_context(profile, [item.title for item in items]),
    }
    return render(request, "tracker/partials/list_detail_items.html", context)


def _render_poster_actions(request, profile, title):
    """The list-picker popover's own HTMX fragment - re-rendered in place
    after adding/removing title from a list, from any grid the popover
    lives in (not just the Lists detail page, which _render_list_items
    already covers)."""
    my_lists = list(WatchList.objects.filter(profile=profile).order_by("name"))
    in_list_ids = set(
        WatchListItem.objects.filter(watchlist__profile=profile, title=title).values_list("watchlist_id", flat=True)
    )
    return render(
        request,
        "tracker/partials/poster_card_list_popover.html",
        {"title": title, "my_lists": my_lists, "in_list_ids": in_list_ids},
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
    if not WatchListItem.objects.filter(watchlist=watchlist, title=title).exists():
        next_position = (WatchListItem.objects.filter(watchlist=watchlist).aggregate(Max("position"))["position__max"] or 0) + 1
        WatchListItem.objects.create(watchlist=watchlist, title=title, position=next_position)
    hx_target = request.headers.get("HX-Target") or ""
    if hx_target.startswith("list-popover-"):
        return _render_poster_actions(request, profile, title)
    if request.headers.get("HX-Request"):
        return _render_list_items(request, watchlist, profile)
    return _list_action_redirect(request, list_id)


@login_required
@require_POST
def remove_from_list(request, list_id):
    profile = Profile.objects.filter(user=request.user).first()
    watchlist = get_object_or_404(WatchList, pk=list_id)
    if profile is None or not watchlist.can_edit(profile):
        raise Http404
    title = get_object_or_404(Title, pk=request.POST.get("title_id"))
    WatchListItem.objects.filter(watchlist=watchlist, title=title).delete()
    hx_target = request.headers.get("HX-Target") or ""
    if hx_target.startswith("list-popover-"):
        return _render_poster_actions(request, profile, title)
    if request.headers.get("HX-Request"):
        return _render_list_items(request, watchlist, profile)
    return _list_action_redirect(request, list_id)


@login_required
@require_POST
def reorder_list(request, list_id):
    """Fired by list_detail_items.html's drag-and-drop on dragend - item_id
    is posted in the exact new DOM order, so position just becomes that
    order's index. The drag has already reordered the DOM client-side
    (window.flipReorder, live as you drag, not just on drop), so this is a
    pure persist - swap:'none' on the caller's htmx.ajax means the response
    body is discarded, there's nothing left for the server to hand back
    that the page doesn't already show. Only reachable when the list_detail
    view was unfiltered (can_reorder - see _list_detail_context), but
    re-checked here too rather than trusted from the client."""
    profile = Profile.objects.filter(user=request.user).first()
    watchlist = get_object_or_404(WatchList, pk=list_id)
    if profile is None or not watchlist.can_edit(profile):
        raise Http404
    item_ids = [int(v) for v in request.POST.getlist("item_id") if v.isdigit()]
    with transaction.atomic():
        for index, item_id in enumerate(item_ids):
            WatchListItem.objects.filter(pk=item_id, watchlist=watchlist).update(position=index)
    return HttpResponse(status=204)


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


def _resolve_stats_profile(request, profile_id):
    """Shared by stats/stats_heatmap/history's optional profile_id kwarg
    (only present on the member_* URLs) - any other household profile is
    viewable read-only, same no-extra-restriction convention as the
    profile popup this deep-links from. profile_id=None (the plain, own-
    profile URLs) preserves the pre-existing "not linked to a profile yet"
    friendly message instead of a 404, unlike the member_* URLs, which
    404 outright if the viewer themselves has no profile."""
    viewer = Profile.objects.filter(user=request.user).first()
    if profile_id is not None:
        if viewer is None:
            raise Http404
        target = get_object_or_404(Profile, pk=profile_id)
    else:
        target = viewer
    is_own = target is not None and viewer is not None and target.id == viewer.id
    return target, is_own


@login_required
def stats(request, profile_id=None):
    profile, is_own_stats = _resolve_stats_profile(request, profile_id)
    genre_type = request.GET.get("genre_type")
    genre_type = genre_type if genre_type in GENRE_TYPES else "movie"
    genre_metric = request.GET.get("genre_metric") if request.GET.get("genre_metric") == "duration" else "items"
    context = {
        "profile": profile,
        "is_own_stats": is_own_stats,
        "genre_type": genre_type,
        "genre_metric": genre_metric,
        "heatmap_base_url": reverse("stats_heatmap") if is_own_stats or profile is None else reverse("member_stats_heatmap", args=[profile.pk]),
        "history_url": reverse("history") if is_own_stats or profile is None else reverse("member_history", args=[profile.pk]),
    }
    if profile is not None:
        overview = selectors.stats_overview(profile)
        overview["split"]["movie_end"] = overview["split"]["movie_pct"]
        overview["split"]["tv_end"] = overview["split"]["movie_pct"] + overview["split"]["tv_pct"]
        context.update(overview)

        context["watch_time_breakdown"] = selectors.watch_time_breakdown(profile)
        genres = selectors.genre_breakdown(profile, GENRE_TYPES[genre_type], genre_metric)
        context["genre_breakdown"] = genres
        context["most_genre"] = genres[0] if genres else None
        context["least_genre"] = genres[-1] if genres else None
        context["daily_breakdown"] = selectors.daily_breakdown(profile)
        context["daily_average"] = selectors.daily_average(profile)
        context["peak_hours"] = selectors.peak_hours(profile)

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
def stats_heatmap(request, profile_id=None):
    profile, is_own_stats = _resolve_stats_profile(request, profile_id)
    try:
        year = int(request.GET.get("year"))
    except (TypeError, ValueError):
        year = timezone.localdate().year

    context = {
        "heatmap_year": year,
        "heatmap_years": [],
        "heatmap_weeks": [],
        "heatmap_months": [],
        "heatmap_base_url": reverse("stats_heatmap") if is_own_stats or profile is None else reverse("member_stats_heatmap", args=[profile.pk]),
    }
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


def _landing_page_url(page):
    """Resolves a Profile.LandingPage value to a real URL -
    movies_tv/anime need a category kwarg reverse() alone can't supply,
    so they're special-cased to the same trending category their own nav
    link points to; anything else reverses directly by name."""
    if page == Profile.LandingPage.MOVIES_TV:
        return reverse("movies_tv", args=["trending"])
    if page == Profile.LandingPage.ANIME:
        return reverse("anime", args=["trending"])
    if page in Profile.LandingPage.values:
        return reverse(page)
    return reverse("dashboard")


class SpoolLoginView(auth_views.LoginView):
    """Same as Django's own LoginView, just honors the signed-in profile's
    default_landing_page (Settings → Appearance) instead of always
    dashboard - only when no explicit ?next= was given/POSTed, same
    precedence Django's own LoginView already gives that a priority over
    its own default redirect. Also rate-limits POSTs per client IP -
    Django's auth views have no brute-force protection of their own."""

    template_name = "tracker/login.html"

    def post(self, request, *args, **kwargs):
        if ratelimit.is_rate_limited(request, "login", limit=10, window_seconds=300):
            return HttpResponse(
                "Too many login attempts. Please wait a few minutes and try again.", status=429
            )
        return super().post(request, *args, **kwargs)

    def get_default_redirect_url(self):
        profile = Profile.objects.filter(user=self.request.user).first()
        if profile is not None:
            return _landing_page_url(profile.default_landing_page)
        return super().get_default_redirect_url()


def _settings_page_context(request, profile):
    """Shared context for the merged Settings/My Profile/Admin Dashboard
    page - settings_view, my_profile, and admin_dashboard all render the
    same template off this, so every section's data needs to be present
    regardless of which of the three URLs was actually hit. The
    owner-only Admin sections' data is only computed when the profile
    actually is one, so a non-owner's response never carries it at all -
    the template's own {% if profile.is_owner %} then has real data to
    gate, not just markup hidden by CSS."""
    external_accounts = {a.provider: a for a in ExternalAccount.objects.filter(profile=profile)}
    other_owner_exists = Profile.objects.filter(user__is_superuser=True).exclude(pk=profile.pk).exists()
    context = {
        "profile": profile,
        "connected_providers": set(external_accounts.keys()),
        "external_accounts": external_accounts,
        # Nuvio's connect status is per-viewer, same as Trakt/Simkl's own
        # connect status above - not admin-only, so this lives outside
        # the `if profile.is_owner:` block below.
        "nuvio_connection": NuvioConnection.objects.filter(profile=profile).first(),
        "languages": DISCOVER_LANGUAGES,
        "landing_pages": Profile.LandingPage.choices,
        "trakt_configured": bool(instance_config.get_trakt_credentials()[0]),
        "simkl_configured": bool(instance_config.get_simkl_credentials()[0]),
        "timezones": PROFILE_TIMEZONES,
        "server_time_zone": django_settings.TIME_ZONE,
        "avatar_colors": AVATAR_COLOR_CHOICES,
        "can_delete_own_account": not profile.is_owner or other_owner_exists,
    }
    if profile.is_owner:
        db_engine = django_settings.DATABASES["default"]["ENGINE"].rsplit(".", 1)[-1]
        context.update(
            {
                "profiles": Profile.objects.select_related("user").all(),
                "cfg": InstanceConfig.load(),
                "tmdb_configured": bool(instance_config.get_tmdb_api_key()),
                "django_version": ".".join(map(str, django.VERSION[:3])),
                "db_engine": db_engine,
                "debug": django_settings.DEBUG,
                "time_zone": django_settings.TIME_ZONE,
                "audit_log": AdminAuditLogEntry.objects.select_related("actor")[:15],
                "logs_page": selectors.combined_logs(
                    request.GET.get("page"),
                    page_size=LOGS_PAGE_SIZE,
                    profile_id=request.GET.get("log_profile") or None,
                    oldest_first=request.GET.get("log_sort") == "oldest",
                ),
                "log_profile_id": request.GET.get("log_profile", ""),
                "log_sort": request.GET.get("log_sort", "newest"),
                "failure_streaks": selectors.sync_failure_streaks(),
            }
        )
    return context


@login_required
def settings_view(request):
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None:
        raise Http404
    return render(request, "tracker/settings.html", {**_settings_page_context(request, profile), "active_tab": "integrations"})


@login_required
def admin_dashboard(request):
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None or not profile.is_owner:
        raise Http404
    return render(request, "tracker/settings.html", {**_settings_page_context(request, profile), "active_tab": "profiles"})


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
    for field in ["trakt_client_id", "simkl_client_id"]:
        value = request.POST.get(field, "").strip()
        if value:
            setattr(cfg, field, value)
    # Secret fields are encrypted at rest (see InstanceConfig's own
    # get_*/set_* accessors) - set_* handles the encryption, so these
    # can't go through the same plain setattr loop as the client ids above.
    for field, setter in [
        ("trakt_client_secret", cfg.set_trakt_client_secret),
        ("simkl_client_secret", cfg.set_simkl_client_secret),
        ("tmdb_api_key", cfg.set_tmdb_api_key),
    ]:
        value = request.POST.get(field, "").strip()
        if value:
            setter(value)
    cfg.save()
    messages.success(request, "Saved integration credentials.")
    return redirect(f"{reverse('admin_dashboard')}?tab=server_integrations")


@login_required
@require_POST
def test_provider_credentials(request, provider):
    """Server Integrations' "Test connection" buttons - shares
    save_instance_config's own form (same fields; the button posts here
    instead via formaction) and the same blank-means-keep-existing
    convention: a field left blank tests whatever's already saved rather
    than nothing, so testing works both before and after hitting Save."""
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None or not profile.is_owner:
        raise Http404

    # str.title() would mangle "TMDB" to "Tmdb" - an explicit display name
    # per provider instead, same reasoning as get_provider_display() on
    # ExternalAccount.Provider elsewhere in this file.
    display_names = {"trakt": "Trakt", "simkl": "Simkl", "tmdb": "TMDB"}
    if provider not in display_names:
        raise Http404
    display_name = display_names[provider]

    if provider == "trakt":
        credential = request.POST.get("trakt_client_id", "").strip() or instance_config.get_trakt_credentials()[0]
        caveat = " (this only confirms the client ID is live - Trakt only checks the secret during the OAuth flow, not here)"
    elif provider == "simkl":
        credential = request.POST.get("simkl_client_id", "").strip() or instance_config.get_simkl_credentials()[0]
        caveat = " (best effort - Simkl's API beyond OAuth isn't fully documented, see Server Integrations)"
    else:
        credential = request.POST.get("tmdb_api_key", "").strip() or instance_config.get_tmdb_api_key()
        caveat = ""

    if not credential:
        messages.error(request, f"No {display_name} credentials to test - fill in a value first.")
        return redirect(f"{reverse('admin_dashboard')}?tab=server_integrations")

    try:
        if provider == "trakt":
            trakt.test_credentials(credential)
        elif provider == "simkl":
            simkl.test_credentials(credential)
        elif not tmdb.test_api_key(credential):
            raise requests.RequestException("TMDB reported the key as invalid")
    except requests.RequestException as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        reason = f"HTTP {status}" if status else str(e) or "couldn't reach the API"
        messages.error(request, f"{display_name} connection failed ({reason}).")
    else:
        messages.success(request, f"{display_name} connection succeeded{caveat}.")
    return redirect(f"{reverse('admin_dashboard')}?tab=server_integrations")


@login_required
@require_POST
def save_log_retention(request):
    """Logs tab's "Keep logs for N days" field - auto-saves on change,
    same hx-post/hx-trigger=change/204-no-content convention as
    save_privacy/save_appearance. Blank clears it back to "keep forever"
    (PositiveIntegerField's null, not 0 - 0 days would mean "delete
    everything nightly", not "don't prune", so blank has to map to None
    rather than being coerced to 0). Actual pruning happens nightly via
    tasks.prune_old_logs - saving this only changes what that task does
    on its next run, nothing is deleted immediately."""
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None or not profile.is_owner:
        raise Http404
    cfg = InstanceConfig.load()
    raw = request.POST.get("log_retention_days", "").strip()
    if not raw:
        cfg.log_retention_days = None
    else:
        try:
            cfg.log_retention_days = max(1, min(3650, int(raw)))
        except ValueError:
            return HttpResponse(status=204)
    cfg.save(update_fields=["log_retention_days"])
    return HttpResponse(status=204)


@login_required
def sync_log(request):
    """Moved into Settings' Logs tab - kept as a redirect so old
    bookmarks/links to this URL still land somewhere useful."""
    return redirect(f"{reverse('settings')}?tab=logs")


@login_required
def change_credentials(request):
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None or not profile.must_change_credentials:
        return redirect("dashboard")

    if request.method == "POST":
        if ratelimit.is_rate_limited(request, "change_credentials", limit=10, window_seconds=300):
            messages.error(request, "Too many attempts. Please wait a few minutes and try again.")
            return render(request, "tracker/change_credentials.html", {"profile": profile})
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


MAX_AVATAR_IMAGE_SIZE = 5 * 1024 * 1024  # 5MB - a profile picture, not a photo album


def _is_valid_image_upload(uploaded_file):
    """Confirms the uploaded bytes actually decode as an image (Pillow),
    rather than trusting the browser-supplied filename/content-type -
    those are just client-side hints, not a security boundary. verify()
    consumes the file, so the caller needs to seek(0) before actually
    using it (for a save, a second read, etc.)."""
    from PIL import Image, UnidentifiedImageError

    try:
        Image.open(uploaded_file).verify()
    except (UnidentifiedImageError, OSError):
        return False
    return True


@login_required
def my_profile(request):
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None:
        raise Http404

    if request.method == "POST" and request.POST.get("action") == "update_profile":
        display_name = request.POST.get("display_name", "").strip()
        avatar_color = request.POST.get("avatar_color", "").strip()
        bio = request.POST.get("bio", "").strip()
        if not display_name:
            messages.error(request, "Display name is required.")
            return redirect("my_profile")

        profile.display_name = display_name
        profile.bio = bio[:160]
        if avatar_color in AVATAR_COLOR_CHOICES:
            profile.avatar_color = avatar_color
        update_fields = ["display_name", "avatar_color", "bio"]

        if request.POST.get("remove_photo"):
            profile.avatar_image.delete(save=False)
            update_fields.append("avatar_image")
        elif request.FILES.get("avatar_image"):
            uploaded = request.FILES["avatar_image"]
            if uploaded.size > MAX_AVATAR_IMAGE_SIZE:
                messages.error(request, "That image is too large — please use one under 5MB.")
                return redirect("my_profile")
            if not _is_valid_image_upload(uploaded):
                messages.error(request, "That file doesn't look like a valid image.")
                return redirect("my_profile")
            uploaded.seek(0)
            profile.avatar_image = uploaded
            update_fields.append("avatar_image")

        profile.save(update_fields=update_fields)
        messages.success(request, "Profile updated.")
        return redirect("my_profile")

    if request.method == "POST" and request.POST.get("action") == "change_password":
        if ratelimit.is_rate_limited(request, "change_password", limit=10, window_seconds=300):
            messages.error(request, "Too many attempts. Please wait a few minutes and try again.")
            return redirect("my_profile")
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

    return render(request, "tracker/settings.html", {**_settings_page_context(request, profile), "active_tab": "account"})


@login_required
@require_POST
def delete_own_account(request):
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None:
        raise Http404
    other_owner_exists = Profile.objects.filter(user__is_superuser=True).exclude(pk=profile.pk).exists()
    if profile.is_owner and not other_owner_exists:
        messages.error(request, "You're the only owner — promote another profile to owner first.")
        return redirect("my_profile")

    display_name = profile.display_name
    AdminAuditLogEntry.objects.create(
        actor=profile, action=AdminAuditLogEntry.Action.PROFILE_SELF_DELETED, target_display_name=display_name
    )
    user = request.user
    logout(request)
    user.delete()  # cascades to the Profile via the OneToOne FK
    return redirect("login")


@login_required
@require_POST
def create_profile(request):
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None or not profile.is_owner:
        raise Http404
    username = request.POST.get("username", "").strip()
    password = request.POST.get("password", "")
    display_name = request.POST.get("display_name", "").strip()
    avatar_color = request.POST.get("avatar_color", "").strip()
    if not username or not password or not display_name:
        messages.error(request, "Username, password, and display name are all required.")
        return redirect("admin_dashboard")
    try:
        user = User.objects.create_user(username=username, password=password)
    except IntegrityError:
        messages.error(request, f'Username "{username}" is already taken.')
        return redirect("admin_dashboard")
    # No avatar_color kwarg unless one was actually chosen - letting the
    # model's own default (a random, not-already-taken palette color)
    # apply is better than a hardcoded fallback every new profile would
    # otherwise share.
    create_kwargs = {"user": user, "display_name": display_name}
    if avatar_color in AVATAR_COLOR_CHOICES:
        create_kwargs["avatar_color"] = avatar_color
    Profile.objects.create(**create_kwargs)
    AdminAuditLogEntry.objects.create(
        actor=profile, action=AdminAuditLogEntry.Action.PROFILE_CREATED, target_display_name=display_name
    )
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
        target_display_name = target.display_name
        target.user.delete()  # cascades to the Profile via the OneToOne FK
        AdminAuditLogEntry.objects.create(
            actor=profile, action=AdminAuditLogEntry.Action.PROFILE_REMOVED, target_display_name=target_display_name
        )
        messages.success(request, f"Removed {target_display_name}.")
    return redirect("admin_dashboard")


@login_required
@require_POST
def promote_to_owner(request, profile_id):
    profile = Profile.objects.filter(user=request.user).first()
    target = get_object_or_404(Profile, pk=profile_id)
    if profile is None or not profile.is_owner:
        messages.error(request, "Only the server owner can promote profiles.")
    elif target.is_owner:
        messages.error(request, f"{target.display_name} is already an owner.")
    else:
        target.user.is_superuser = True
        target.user.save(update_fields=["is_superuser"])
        AdminAuditLogEntry.objects.create(
            actor=profile, action=AdminAuditLogEntry.Action.PROFILE_PROMOTED, target_display_name=target.display_name
        )
        messages.success(request, f"{target.display_name} is now an owner.")
    return redirect("admin_dashboard")


@login_required
@require_POST
def demote_from_owner(request, profile_id):
    profile = Profile.objects.filter(user=request.user).first()
    target = get_object_or_404(Profile, pk=profile_id)
    if profile is None or not profile.is_owner:
        messages.error(request, "Only the server owner can demote profiles.")
    elif target.id == profile.id:
        messages.error(request, "You can't demote yourself - have another owner do it instead.")
    elif not target.is_owner:
        messages.error(request, f"{target.display_name} is already a member.")
    else:
        target.user.is_superuser = False
        target.user.save(update_fields=["is_superuser"])
        AdminAuditLogEntry.objects.create(
            actor=profile, action=AdminAuditLogEntry.Action.PROFILE_DEMOTED, target_display_name=target.display_name
        )
        messages.success(request, f"{target.display_name} is now a member.")
    return redirect("admin_dashboard")


@login_required
@require_POST
def admin_reset_password(request, profile_id):
    """Owner-only "Reset password" in the Profiles tab, for when a member
    forgets theirs and can't get to their own Change Password form. No
    update_session_auth_hash call here, unlike change_credentials/
    my_profile's own password changes - those exist specifically so a
    user's *own* change doesn't log out their *own* session; here the
    target is someone else's account, so their other active sessions
    should (and, via Django's normal session auth-hash check, will)
    become invalid on their next request - the correct outcome for an
    owner-forced reset."""
    profile = Profile.objects.filter(user=request.user).first()
    target = get_object_or_404(Profile, pk=profile_id)
    new_password = request.POST.get("new_password", "")
    confirm_password = request.POST.get("confirm_password", "")
    if profile is None or not profile.is_owner:
        messages.error(request, "Only the server owner can reset passwords.")
    elif target.id == profile.id:
        messages.error(request, "Use “Change password” in your Account tab to change your own password.")
    elif not new_password or new_password != confirm_password:
        messages.error(request, "Passwords don't match.")
    elif len(new_password) < 8:
        messages.error(request, "Password must be at least 8 characters.")
    else:
        target.user.set_password(new_password)
        target.user.save(update_fields=["password"])
        AdminAuditLogEntry.objects.create(
            actor=profile,
            action=AdminAuditLogEntry.Action.PROFILE_PASSWORD_RESET,
            target_display_name=target.display_name,
        )
        messages.success(request, f"Reset {target.display_name}'s password.")
    return redirect("admin_dashboard")


@login_required
@require_POST
def run_merge_duplicates(request):
    """Maintenance tab - wraps management/commands/merge_duplicate_titles.py,
    which is pure DB queries with no external calls, so both preview and
    commit run inline rather than dispatching to Celery (unlike the three
    TMDB-touching backfills below). A preview doesn't change anything, so
    it doesn't write a DataLog row; a commit always does, even "no
    duplicates found" - that's still a completed run worth showing in the
    Logs tab."""
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None or not profile.is_owner:
        raise Http404

    commit = request.POST.get("mode") == "commit"
    buf = StringIO()
    call_command("merge_duplicate_titles", *(["--commit"] if commit else []), stdout=buf)
    output = buf.getvalue().strip()

    if commit:
        DataLog.objects.create(
            profile=profile,
            action=DataLog.Action.MERGE_DUPLICATES,
            status=DataLog.Status.SUCCESS,
            detail=(output.splitlines()[-1][:255] if output else ""),
        )
        messages.success(request, output or "Done.")
    else:
        messages.info(request, output or "No duplicate titles found.")
    return redirect(f"{reverse('admin_dashboard')}?tab=maintenance")


# (task, DataLog.Action, is_async) - "is_async" ones make one TMDB call per
# title with a deliberate throttle (see each command's own docstring) and
# can run well past a normal request's timeout for a real library, so they
# dispatch to Celery instead of running inline like backfill_rewatches.
MAINTENANCE_TASKS = {
    "backfill_posters": (tasks.run_backfill_posters, DataLog.Action.BACKFILL_POSTERS, True),
    "backfill_genres": (tasks.run_backfill_genres, DataLog.Action.BACKFILL_GENRES, True),
    "backfill_completion": (tasks.run_backfill_completion, DataLog.Action.BACKFILL_COMPLETION, True),
    "backfill_rewatches": (None, DataLog.Action.BACKFILL_REWATCHES, False),
}


@login_required
@require_POST
def run_maintenance_task(request, task_key):
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None or not profile.is_owner:
        raise Http404
    if task_key not in MAINTENANCE_TASKS:
        raise Http404
    task, action, is_async = MAINTENANCE_TASKS[task_key]

    if is_async:
        log = DataLog.objects.create(profile=profile, action=action, status=DataLog.Status.RUNNING)
        _dispatch_sync_task_safely(task, [log.id])
        messages.success(request, "Started — check the Logs tab in a bit.")
    else:
        buf = StringIO()
        call_command(task_key, stdout=buf)
        output = buf.getvalue().strip()
        DataLog.objects.create(
            profile=profile,
            action=action,
            status=DataLog.Status.SUCCESS,
            detail=(output.splitlines()[-1][:255] if output else ""),
        )
        messages.success(request, output or "Done.")
    return redirect(f"{reverse('admin_dashboard')}?tab=maintenance")


@login_required
@require_POST
def save_appearance(request):
    """One endpoint for every Appearance control (time format, default
    landing page, preferred language, Discover watched/watchlisted display)
    - each field only touches update_fields it actually received, so any
    single control's htmx submit (they each post independently, on change)
    leaves the others untouched."""
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None:
        raise Http404
    update_fields = []
    time_format = request.POST.get("time_format")
    if time_format in Profile.TimeFormat.values:
        profile.time_format = time_format
        update_fields.append("time_format")
    landing_page = request.POST.get("default_landing_page")
    if landing_page in Profile.LandingPage.values:
        profile.default_landing_page = landing_page
        update_fields.append("default_landing_page")
    if "preferred_language" in request.POST:
        language = request.POST.get("preferred_language", "")
        if language == "" or language in dict(DISCOVER_LANGUAGES):
            profile.preferred_language = language
            update_fields.append("preferred_language")
    if "timezone" in request.POST:
        tzname = request.POST.get("timezone", "")
        if tzname == "" or tzname in PROFILE_TIMEZONES:
            profile.timezone = tzname
            update_fields.append("timezone")
    watched_display = request.POST.get("discover_watched_display")
    if watched_display in Profile.DiscoverDisplay.values:
        profile.discover_watched_display = watched_display
        update_fields.append("discover_watched_display")
    watchlisted_display = request.POST.get("discover_watchlisted_display")
    if watchlisted_display in Profile.DiscoverDisplay.values:
        profile.discover_watchlisted_display = watchlisted_display
        update_fields.append("discover_watchlisted_display")
    if "gemini_api_key" in request.POST:
        profile.gemini_api_key = request.POST.get("gemini_api_key", "").strip()
        update_fields.append("gemini_api_key")
    if update_fields:
        profile.save(update_fields=update_fields)
    return HttpResponse(status=204)


@login_required
@require_POST
def save_privacy(request):
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None:
        raise Http404
    # Absent entirely from POST when unchecked - a plain HTML checkbox,
    # same convention as import_lists/is_shared elsewhere in this app.
    profile.share_activity = bool(request.POST.get("share_activity"))
    profile.save(update_fields=["share_activity"])
    return HttpResponse(status=204)


@login_required
@require_POST
def save_notifications(request):
    """All three toggles submit together (one form, hx-trigger=change) -
    same reasoning as save_appearance bundling its own fields, except
    here every field really is a plain checkbox so there's no per-field
    "was this key present at all" branching to do."""
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None:
        raise Http404
    profile.notify_new_releases = bool(request.POST.get("notify_new_releases"))
    profile.notify_upcoming_releases = bool(request.POST.get("notify_upcoming_releases"))
    profile.notify_sync_failures = bool(request.POST.get("notify_sync_failures"))
    profile.save(update_fields=["notify_new_releases", "notify_upcoming_releases", "notify_sync_failures"])
    return HttpResponse(status=204)


def _render_notifications_panel(request, profile):
    items = list(Notification.objects.filter(profile=profile).select_related("title")[:20])
    return render(request, "tracker/partials/notifications_panel.html", {"notifications": items})


@login_required
def notifications_panel(request):
    """The header bell's dropdown content - also included directly on
    first page load in base.html, same self-swapping-partial pattern as
    the Stats heatmap/episode browser (an hx-get back to this same view
    re-renders it after mark-as-read)."""
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None:
        raise Http404
    return _render_notifications_panel(request, profile)


@login_required
@require_POST
def mark_notification_read(request, pk):
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None:
        raise Http404
    notification = get_object_or_404(Notification, pk=pk, profile=profile)
    if not notification.read:
        notification.read = True
        notification.save(update_fields=["read"])
    return _render_notifications_panel(request, profile)


@login_required
@require_POST
def mark_all_notifications_read(request):
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None:
        raise Http404
    Notification.objects.filter(profile=profile, read=False).update(read=True)
    return _render_notifications_panel(request, profile)


@login_required
@require_POST
def clear_all_notifications(request):
    """The header bell's eraser button - deletes every one of this
    profile's notifications outright (not just marking them read, which
    mark_all_notifications_read already covers), same "empty the panel
    entirely" action Trakt/Simkl-style notification dropdowns offer."""
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None:
        raise Http404
    Notification.objects.filter(profile=profile).delete()
    return _render_notifications_panel(request, profile)


_CSV_FORMULA_TRIGGERS = ("=", "+", "-", "@")


def _csv_safe(value):
    """Neutralizes CSV/formula injection (OWASP's "CSV Injection") for a
    value about to be written to an exported CSV - a title name starting
    with =/+/-/@ is a formula trigger to Excel/Sheets/LibreOffice when
    this export is later opened as a spreadsheet, not just text to a
    plain viewer. A title is the only genuinely free-text field this
    export writes (it can arrive via CSV/JSON import, unlike the
    enum/numeric/date columns alongside it) - the ones most likely to
    carry an attacker-crafted string, since export_csv's own docstring's
    round-trip guarantee otherwise round-trips *any* title text straight
    from an import into this file, unmodified. Checked against the
    leading char after stripping whitespace, not value[0] directly - a
    leading space/tab before the trigger character still gets treated as
    a formula by some spreadsheet apps. Only prefixes when actually
    needed, so the overwhelming majority of titles are untouched and
    still round-trip through a re-import byte-for-byte - this only
    changes output for the rare title that already starts with one of
    these characters."""
    text = str(value)
    stripped = text.lstrip()
    if stripped and stripped[0] in _CSV_FORMULA_TRIGGERS:
        return "'" + text
    return value


@login_required
def export_csv(request):
    """Same column names csv_import.py's own COLUMN_ALIASES canonical
    keys use (title/media_type/year/season/episode/watched_at/rating), so
    a re-import of this exact file round-trips cleanly."""
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None:
        raise Http404
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="spool-export-{timezone.localdate().isoformat()}.csv"'
    writer = csv.writer(response)
    writer.writerow(["title", "media_type", "year", "season", "episode", "watched_at", "rating"])
    events = (
        WatchEvent.objects.filter(profile=profile)
        .select_related("title", "episode")
        .order_by("watched_at")
    )
    written = 0
    for event in events:
        writer.writerow(
            [
                _csv_safe(event.title.name),
                event.title.media_type,
                event.title.year or "",
                event.episode.season if event.episode else "",
                event.episode.episode if event.episode else "",
                event.watched_at.isoformat(),
                event.user_rating or "",
            ]
        )
        written += 1
    DataLog.objects.create(
        profile=profile, action=DataLog.Action.EXPORT, status=DataLog.Status.SUCCESS,
        item_count=written, detail="CSV",
    )
    return response


@login_required
def export_trakt_json(request):
    """Trakt's own /sync/history shape (see integrations/trakt.py's
    fetch_history/upsert_history_items, which consume exactly this shape
    the other direction) - movie/show ids are only included when this
    Title actually carries a trakt/tmdb id in external_ids, which won't
    be true for anything that only ever came in via CSV import."""
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None:
        raise Http404
    events = (
        WatchEvent.objects.filter(profile=profile)
        .select_related("title", "episode")
        .order_by("watched_at")
    )
    items = []
    for event in events:
        ids = {}
        trakt_id = event.title.external_ids.get("trakt")
        if trakt_id:
            ids["trakt"] = trakt_id
        tmdb_id = event.title.external_ids.get("tmdb")
        if tmdb_id:
            ids["tmdb"] = int(tmdb_id) if str(tmdb_id).isdigit() else tmdb_id
        if event.episode:
            items.append(
                {
                    "type": "episode",
                    "watched_at": event.watched_at.isoformat(),
                    "show": {"title": event.title.name, "year": event.title.year, "ids": ids},
                    "episode": {
                        "season": event.episode.season,
                        "number": event.episode.episode,
                        "title": event.episode.name,
                    },
                }
            )
        else:
            items.append(
                {
                    "type": "movie",
                    "watched_at": event.watched_at.isoformat(),
                    "movie": {"title": event.title.name, "year": event.title.year, "ids": ids},
                }
            )
    response = JsonResponse(items, safe=False, json_dumps_params={"indent": 2})
    filename = f"spool-trakt-export-{timezone.localdate().isoformat()}.json"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    DataLog.objects.create(
        profile=profile, action=DataLog.Action.EXPORT, status=DataLog.Status.SUCCESS,
        item_count=len(items), detail="Trakt JSON",
    )
    return response


PROVIDER_MODULES = {"trakt": trakt, "simkl": simkl}
SYNC_TASKS = {"trakt": tasks.sync_trakt_history, "simkl": tasks.sync_simkl_history, "nuvio": tasks.sync_nuvio_history}


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


CONNECT_ACTIONS = {"trakt": DataLog.Action.TRAKT_CONNECT, "simkl": DataLog.Action.SIMKL_CONNECT}


@login_required
def oauth_callback(request, provider):
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None:
        raise Http404
    connect_action = CONNECT_ACTIONS[provider]

    expected_state = request.session.pop(f"{provider}_oauth_state", None)
    if not expected_state or request.GET.get("state") != expected_state:
        DataLog.objects.create(
            profile=profile, action=connect_action, status=DataLog.Status.FAILED,
            error_message="Connection request expired or was invalid (state mismatch).",
        )
        messages.error(request, "That connection request expired or was invalid — please try connecting again.")
        return redirect("settings")

    code = request.GET.get("code")
    if not code:
        DataLog.objects.create(
            profile=profile, action=connect_action, status=DataLog.Status.FAILED,
            error_message=f"{provider.title()} didn't return an authorization code.",
        )
        messages.error(request, f"{provider.title()} didn't return an authorization code.")
        return redirect("settings")

    redirect_uri = request.build_absolute_uri(reverse(f"{provider}_callback"))
    client_id, client_secret = instance_config.get_credentials(provider)
    try:
        token_data = PROVIDER_MODULES[provider].exchange_code(code, redirect_uri, client_id, client_secret)
    except requests.RequestException as e:
        DataLog.objects.create(
            profile=profile, action=connect_action, status=DataLog.Status.FAILED,
            error_message=str(e)[:500],
        )
        messages.error(request, f"Couldn't complete the {provider.title()} connection — please try again.")
        return redirect("settings")

    expires_in = token_data.get("expires_in")
    # access_token/refresh_token are encrypted at rest (see
    # ExternalAccount's own set_access_token/set_refresh_token) - update_
    # or_create's defaults= can't call those, so this fetches-or-creates
    # first and sets them explicitly instead.
    account, _ = ExternalAccount.objects.get_or_create(profile=profile, provider=provider)
    account.set_access_token(token_data.get("access_token", ""))
    account.set_refresh_token(token_data.get("refresh_token", ""))
    account.token_expires_at = timezone.now() + timedelta(seconds=expires_in) if expires_in else None
    account.redirect_uri = redirect_uri
    account.save(update_fields=["encrypted_access_token", "encrypted_refresh_token", "token_expires_at", "redirect_uri"])
    scheduling.ensure_periodic_task(account)
    # The connection itself (the ExternalAccount row above) must succeed
    # independently of the broker being reachable right now. Confirmed by
    # reproducing it locally: a down broker makes .apply_async() block for
    # ~16s even with retry=False and short socket timeouts configured
    # (redis-py's own internal retry-with-backoff sits underneath both),
    # so config alone doesn't bound this — a hard thread-join timeout
    # does. Worst case, a broker hiccup costs nothing worse than "today's
    # sync happens on the next daily beat run instead of immediately."
    _dispatch_sync_task_safely(SYNC_TASKS[provider], [profile.id])
    DataLog.objects.create(profile=profile, action=connect_action, status=DataLog.Status.SUCCESS, detail="connected")
    messages.success(request, f"Connected to {provider.title()} — syncing your history now.")
    return redirect("settings")


def _finish_nuvio_connect(profile, email, refresh_token, nuvio_profile):
    """Creates/updates this profile's NuvioConnection, encrypts+saves the
    refresh token, (re)schedules the daily sync, and triggers an
    immediate first sync - the Nuvio-flow equivalent of oauth_callback's
    ExternalAccount.objects.update_or_create + scheduling.ensure_periodic_task
    + _dispatch_sync_task_safely block above. Shared by nuvio_connect_submit's
    single-profile happy path and nuvio_select_profile's multi-profile
    finish, so both end up in exactly the same state."""
    nuvio_profile_id = int(nuvio_profile.get("profile_index") or 0)
    connection, _ = NuvioConnection.objects.update_or_create(
        profile=profile,
        defaults={
            "email": email,
            "nuvio_profile_id": nuvio_profile_id,
            "nuvio_profile_name": nuvio_profile.get("name") or "",
        },
    )
    connection.set_refresh_token(refresh_token)
    connection.save(update_fields=["encrypted_refresh_token"])
    scheduling.ensure_periodic_task(connection)
    _dispatch_sync_task_safely(SYNC_TASKS["nuvio"], [profile.id])
    DataLog.objects.create(
        profile=profile, action=DataLog.Action.NUVIO_CONNECT, status=DataLog.Status.SUCCESS, detail="connected"
    )


@login_required
@require_POST
def nuvio_connect_submit(request):
    """Nuvio has no OAuth redirect flow - the connect form lives inline
    in Settings' Integrations panel and posts straight here. The
    password is used only for this one sign-in call and is never stored
    or put in the session; only the resulting refresh token is (see
    tracker/crypto.py). An account with more than one Nuvio profile
    (like Trakt slate) can't be resolved to one NuvioConnection yet, so
    it's routed to nuvio_select_profile instead of finishing here."""
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None:
        raise Http404

    email = request.POST.get("email", "").strip()
    password = request.POST.get("password", "")
    if not email or not password:
        messages.error(request, "Enter both your Nuvio email and password.")
        return redirect("settings")

    try:
        session, profiles = nuvio.authenticate(email, password)
    except requests.RequestException as e:
        DataLog.objects.create(
            profile=profile, action=DataLog.Action.NUVIO_CONNECT, status=DataLog.Status.FAILED,
            error_message=str(e)[:500],
        )
        messages.error(request, "Couldn't sign in to Nuvio — check your email and password and try again.")
        return redirect("settings")

    if not profiles:
        DataLog.objects.create(
            profile=profile, action=DataLog.Action.NUVIO_CONNECT, status=DataLog.Status.FAILED,
            error_message="Nuvio account has no profiles.",
        )
        messages.error(request, "That Nuvio account has no profiles to sync from.")
        return redirect("settings")

    if len(profiles) == 1:
        _finish_nuvio_connect(profile, email, session["refresh_token"], profiles[0])
        messages.success(request, "Connected to Nuvio — syncing your history now.")
        return redirect("settings")

    # Stashed encrypted, same as NuvioConnection.encrypted_refresh_token -
    # this is Django's server-side DB-backed session by default, but
    # encrypting here too costs nothing and doesn't depend on that.
    request.session["nuvio_connect_pending"] = {
        "email": email,
        "refresh_token": crypto.encrypt(session["refresh_token"]),
        "profiles": profiles,
    }
    return redirect("nuvio_select_profile")


@login_required
def nuvio_select_profile(request):
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None:
        raise Http404
    pending = request.session.get("nuvio_connect_pending")
    if not pending:
        messages.error(request, "That Nuvio connection request expired — please try connecting again.")
        return redirect("settings")

    if request.method == "POST":
        try:
            chosen_index = int(request.POST.get("profile_index", ""))
        except ValueError:
            chosen_index = None
        chosen = next(
            (p for p in pending["profiles"] if int(p.get("profile_index") or 0) == chosen_index), None
        )
        if chosen is None:
            messages.error(request, "Pick a Nuvio profile to continue.")
            return redirect("nuvio_select_profile")
        del request.session["nuvio_connect_pending"]
        _finish_nuvio_connect(profile, pending["email"], crypto.decrypt(pending["refresh_token"]), chosen)
        messages.success(request, "Connected to Nuvio — syncing your history now.")
        return redirect("settings")

    context = {
        "email": pending["email"],
        "profiles": [
            {
                "index": int(p.get("profile_index") or 0),
                "name": p.get("name") or f"Profile {int(p.get('profile_index') or 0)}",
            }
            for p in pending["profiles"]
        ],
    }
    return render(request, "tracker/nuvio_select_profile.html", context)


def _get_provider_account(profile, provider):
    """Returns the connection row for `provider` scoped to `profile` - a
    NuvioConnection for "nuvio" (see its duck-typed `provider` constant
    in models.py), an ExternalAccount for trakt/simkl. 404s if there
    isn't one, same as get_object_or_404 would - used by every view
    below that needs to look one up regardless of which kind it is."""
    if provider == "nuvio":
        return get_object_or_404(NuvioConnection, profile=profile)
    return get_object_or_404(ExternalAccount, profile=profile, provider=provider)


@login_required
@require_POST
def disconnect_provider(request, provider):
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None:
        raise Http404
    account = _get_provider_account(profile, provider)
    scheduling.remove_periodic_task(account)
    account.delete()
    # Titles/episodes/watch events that sync already created are left in
    # place - disconnecting stops future syncs, it isn't an "undo my import."
    messages.success(request, f"Disconnected {provider.title()}. Your imported history hasn't been removed.")
    return redirect("settings")


@login_required
@require_POST
def clear_watch_history(request):
    """Settings → Danger Zone. Deletes every WatchEvent (so every rating
    too, since those live on the event) and WatchProgress row for this
    profile - a full reset back to "never watched anything". Deliberately
    doesn't touch Titles/Episodes themselves (shared library data other
    profiles' own history may still reference) or this profile's own
    lists/watchlist - "history" and "what I've curated" are kept
    conceptually separate everywhere else in this app, and this is no
    exception."""
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None:
        raise Http404
    deleted, _ = WatchEvent.objects.filter(profile=profile).delete()
    WatchProgress.objects.filter(profile=profile).delete()
    messages.success(request, f"Cleared your watch history ({deleted} event{'s' if deleted != 1 else ''} removed).")
    return redirect("settings")


@login_required
@require_POST
def disconnect_and_wipe_provider(request, provider):
    """Same as disconnect_provider, plus actually removes this profile's
    own watch history for titles that provider is known to have matched -
    approximated by "has an external id for that provider" (external_ids
    has no per-WatchEvent provenance to key off instead), so a title also
    tracked another way loses its history here too if it happens to
    carry that provider's id. Only ever this profile's own WatchEvents -
    the Title/Episode rows, and any other profile's history against
    them, are untouched."""
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None:
        raise Http404
    account = _get_provider_account(profile, provider)
    scheduling.remove_periodic_task(account)
    account.delete()
    deleted, _ = WatchEvent.objects.filter(
        profile=profile, **{f"title__external_ids__{provider}__isnull": False}
    ).delete()
    messages.success(
        request,
        f"Disconnected {provider.title()} and removed {deleted} watch event{'s' if deleted != 1 else ''} "
        f"for titles matched via {provider.title()}.",
    )
    return redirect("settings")


def _dispatch_sync_task_safely(task, args, timeout=2.0):
    def _dispatch():
        try:
            task.apply_async(args=args, retry=False)
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
    account = _get_provider_account(profile, provider)

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


@login_required
@require_POST
def trigger_manual_sync(request, provider):
    """Settings & Import's "Sync now" button - dispatches the same Celery
    task the scheduled sync uses (see tasks.sync_all_connected_accounts),
    just for this one profile's account, right away instead of waiting
    for its next scheduled run. Doesn't touch sync_interval_days/hour/
    minute at all - the schedule keeps running independently of this.
    Uses the same broker-hiccup-safe dispatch as oauth_callback's own
    first-sync-on-connect, for the same reason (see its comment)."""
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None:
        raise Http404
    _get_provider_account(profile, provider)
    _dispatch_sync_task_safely(SYNC_TASKS[provider], [profile.id])
    messages.success(request, f"{provider.title()} sync started - check back in a moment.")
    return redirect("settings")


CSV_IMPORT_DIR = os.path.join(django_settings.MEDIA_ROOT, "csv_imports")
# A real Trakt "Export now" zip for a large, long-tracked library is
# still just plain-text JSON under the hood - a few MB at most (see
# _parse_pending_import's own note: ~0.1s to parse a 10k-row export).
# 50MB is generous headroom over that while still bounding how much an
# authenticated user can force the server to buffer to disk in one
# upload - request.FILES has no size cap of its own for multipart file
# uploads (DATA_UPLOAD_MAX_MEMORY_SIZE only governs non-file POST data).
MAX_CSV_IMPORT_SIZE = 50 * 1024 * 1024


def _discard_pending_csv_import(request):
    """Removes the temp file backing request.session['csv_import'], if any."""
    pending = request.session.pop("csv_import", None)
    if pending:
        try:
            os.remove(pending["path"])
        except OSError:
            pass


def _parse_pending_import(pending, limit=None):
    """Thin wrapper over csv_import.parse_file() using the session-stored
    path/kind/mapping - parsed fresh each call rather than cached (cheap:
    confirmed ~0.1s even for a 10k-row real Trakt export), the only real
    cost (TMDB lookups, DB writes) happens later in commit_rows()."""
    return csv_import.parse_file(pending["path"], pending["kind"], pending.get("mapping"), limit=limit)


@login_required
@require_POST
def import_csv_upload(request):
    upload = request.FILES.get("csv_file")
    if not upload:
        messages.error(request, "Choose a file first.")
        return redirect("settings")
    if upload.size > MAX_CSV_IMPORT_SIZE:
        messages.error(request, f'"{upload.name}" is too large — please upload a file under 50MB.')
        return redirect("settings")

    kind = csv_import.detect_kind(upload.name)
    if kind is None:
        messages.error(request, f'"{upload.name}" isn\'t a .csv, .json, or .zip file.')
        return redirect("settings")

    os.makedirs(CSV_IMPORT_DIR, exist_ok=True)
    path = os.path.join(CSV_IMPORT_DIR, f"{uuid.uuid4().hex}.{kind}")
    with open(path, "wb") as f:
        for chunk in upload.chunks():
            f.write(chunk)

    pending = {"path": path, "filename": upload.name, "kind": kind}

    if kind == "csv":
        try:
            with open(path, "rb") as f:
                headers = csv_import.open_csv_reader(f).fieldnames or []
        except (OSError, UnicodeDecodeError):
            headers = []
        if not headers:
            os.remove(path)
            messages.error(request, f'"{upload.name}" doesn\'t look like a CSV file (no header row found).')
            return redirect("settings")
        pending["headers"] = headers
        pending["mapping"] = csv_import.detect_mapping(headers)
    elif kind == "json":
        try:
            with open(path, "rb") as f:
                data = json.loads(f.read().decode("utf-8-sig"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            os.remove(path)
            messages.error(request, f'"{upload.name}" doesn\'t look like a valid JSON file.')
            return redirect("settings")
        rows, errors = csv_import.parse_json_rows(data, limit=1)
        if not rows and not errors:
            os.remove(path)
            messages.error(request, f'"{upload.name}" doesn\'t contain any recognizable watch history.')
            return redirect("settings")
    else:  # zip
        try:
            with zipfile.ZipFile(path):
                pass
        except zipfile.BadZipFile:
            os.remove(path)
            messages.error(request, f'"{upload.name}" doesn\'t look like a valid zip file.')
            return redirect("settings")
        rows, errors = csv_import.parse_zip_file(path, limit=1)
        if not rows and not errors:
            os.remove(path)
            messages.error(request, f'"{upload.name}" doesn\'t contain any recognizable watch history in its .csv/.json entries.')
            return redirect("settings")

    _discard_pending_csv_import(request)
    request.session["csv_import"] = pending
    return redirect("import_csv_preview")


@login_required
def import_csv_preview(request):
    pending = request.session.get("csv_import")
    if not pending:
        messages.error(request, "No import in progress — upload a file to start.")
        return redirect("settings")

    sample_rows, sample_errors = _parse_pending_import(pending, limit=10)
    kind = pending["kind"]

    context = {
        "filename": pending["filename"],
        "kind": kind,
        "headers": pending.get("headers", []),
        "mapping": pending.get("mapping", {}),
        "fields": csv_import.FIELDS,
        "required_fields": csv_import.REQUIRED_FIELDS,
        "sample_rows": sample_rows,
        "sample_errors": sample_errors,
        "missing_required": (
            [f for f in csv_import.REQUIRED_FIELDS if f not in pending["mapping"]] if kind == "csv" else []
        ),
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



# A real Trakt "Export now" zip can carry 10k+ watch events. Timed
# against that actual export: committing ~10,500 rows synchronously took
# ~85s with zero TMDB lookups involved (no API key configured in that
# test) - already well past any reasonable reverse-proxy/gunicorn
# request timeout, and worse with real TMDB lookups for new titles. 500
# rows is comfortably inside "finishes in a couple seconds" territory
# even accounting for TMDB calls, while still covering the vast majority
# of ordinary personal CSV/JSON exports synchronously (instant result
# page, no polling needed).
LARGE_IMPORT_ROW_THRESHOLD = 500


def _dispatch_import_task_safely(log, profile_id, path, kind, mapping, timeout=2.0):
    """Same pattern as _dispatch_sync_task_safely - dispatch on a
    background thread with a short timeout so a slow/unreachable Celery
    broker can't hang this request, just log and move on. Unlike sync,
    there's no daily beat job that'll pick this back up later if the
    dispatch itself fails - the DataLog row is left at RUNNING and the
    temp file stays on disk. Acceptable here: dispatch failure means the
    broker is down, which is a bigger problem than one stuck import."""
    def _dispatch():
        try:
            tasks.run_data_import.apply_async(args=[log.id, profile_id, path, kind, mapping], retry=False)
        except Exception:
            logging.getLogger(__name__).exception("Background dispatch of run_data_import failed")

    thread = threading.Thread(target=_dispatch, daemon=True)
    thread.start()
    thread.join(timeout=timeout)
    if thread.is_alive():
        logging.getLogger(__name__).warning("Dispatch of run_data_import did not complete within %ss", timeout)


@login_required
@require_POST
def import_csv_commit(request):
    profile = Profile.objects.filter(user=request.user).first()
    pending = request.session.get("csv_import")
    if profile is None or not pending:
        return redirect("settings")

    if pending["kind"] == "csv":
        missing_required = [f for f in csv_import.REQUIRED_FIELDS if f not in pending["mapping"]]
        if missing_required:
            messages.error(request, f"Map the required column(s) first: {', '.join(missing_required)}.")
            return redirect("import_csv_preview")

    rows, parse_errors = _parse_pending_import(pending)

    if len(rows) > LARGE_IMPORT_ROW_THRESHOLD:
        request.session.pop("csv_import", None)  # ownership of the temp file passes to the task below
        log = DataLog.objects.create(profile=profile, action=DataLog.Action.IMPORT, status=DataLog.Status.RUNNING)
        _dispatch_import_task_safely(log, profile.id, pending["path"], pending["kind"], pending.get("mapping"))
        messages.success(
            request,
            f"Import started in the background ({len(rows)} rows) — large files can take a few minutes. "
            "Check Settings → Logs for progress.",
        )
        return redirect(f"{reverse('settings')}?tab=logs")

    try:
        imported, skipped = csv_import.commit_rows(profile, rows)
    except Exception as e:
        DataLog.objects.create(
            profile=profile, action=DataLog.Action.IMPORT, status=DataLog.Status.FAILED,
            error_message=str(e)[:500],
        )
        _discard_pending_csv_import(request)
        raise
    _discard_pending_csv_import(request)

    all_skipped = parse_errors + skipped
    DataLog.objects.create(
        profile=profile, action=DataLog.Action.IMPORT, status=DataLog.Status.SUCCESS,
        item_count=imported, detail=f"{len(all_skipped)} skipped" if all_skipped else "",
    )
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
