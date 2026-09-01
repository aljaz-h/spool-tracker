"""Dashboard/Stats query helpers, kept out of views.py per
spool-django-handoff.md §5 ("compute in a model method or manager, not in
the template")."""

from datetime import timedelta

from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce, ExtractDay, ExtractHour, ExtractMonth
from django.utils import timezone

from .models import (
    DataLog,
    Episode,
    MediaType,
    Profile,
    ReleaseSchedule,
    Title,
    WatchEvent,
    WatchList,
    WatchListItem,
    WatchProgress,
)

# Settings' Logs tab Action Type filter - each bucket is (key, label,
# matching DataLog.Action values). "sync" is special: None means "match
# SyncLog rows instead of DataLog" (see combined_logs below), since
# SyncLog has no "action" of its own to bucket - every row in it already
# is one kind of action (a sync run). "connect" deliberately groups all
# three provider-specific *_CONNECT actions into one pill, matching how
# the panel shows one "Connect" option, not three.
LOG_ACTION_TYPES = [
    ("sync", "Sync", None),
    ("connect", "Connect", [DataLog.Action.TRAKT_CONNECT, DataLog.Action.SIMKL_CONNECT, DataLog.Action.NUVIO_CONNECT]),
    ("disconnect", "Disconnect", [DataLog.Action.DISCONNECT]),
    ("import", "CSV Import", [DataLog.Action.IMPORT]),
    ("export", "CSV Export", [DataLog.Action.EXPORT]),
    ("merge_duplicates", "Merge Duplicates", [DataLog.Action.MERGE_DUPLICATES]),
    ("backfill_posters", "Backfill Posters", [DataLog.Action.BACKFILL_POSTERS]),
    ("backfill_genres", "Backfill Genres", [DataLog.Action.BACKFILL_GENRES]),
    ("backfill_completion", "Backfill Completion", [DataLog.Action.BACKFILL_COMPLETION]),
    ("backfill_rewatches", "Backfill Rewatches", [DataLog.Action.BACKFILL_REWATCHES]),
    ("reclassify_anime", "Reclassify Anime", [DataLog.Action.RECLASSIFY_ANIME]),
    ("release_sync", "Release Sync", [DataLog.Action.RELEASE_SYNC]),
    ("release_notifications", "Release Notifications", [DataLog.Action.RELEASE_NOTIFICATIONS]),
    ("watchlist_stale", "Watchlist Reminders", [DataLog.Action.WATCHLIST_STALE]),
    ("update_check", "Update Check", [DataLog.Action.UPDATE_CHECK]),
    ("log_retention", "Log Retention", [DataLog.Action.LOG_RETENTION]),
]
_LOG_ACTION_TYPE_VALUES = {key: values for key, _label, values in LOG_ACTION_TYPES}


def _distinct_watch_dates(profile):
    """One row per distinct watched date, deduped at the database level
    (.distinct()) rather than fetching one row per WatchEvent and
    deduping in Python via set() - a profile with years of near-daily
    watching has far fewer distinct days than events (confirmed: 25k
    events, ~1-1.5k distinct days in practice), so this cuts both the
    rows transferred and the Python-side work for both streak functions
    below."""
    return sorted(WatchEvent.objects.filter(profile=profile).values_list("watched_at__date", flat=True).distinct())


def _streak_from_dates(dates):
    """Counts backward from the most recent day that's still "alive" -
    today itself if it already has a watch, otherwise yesterday (today
    isn't over yet, so not having watched something *yet* today isn't a
    missed day). Anchoring unconditionally to today (the previous
    version) meant the streak reported 0 for anyone who simply hadn't
    watched anything yet on a given day, even with an unbroken run
    through yesterday - confirmed as a real user-reported bug, not just
    a hypothetical edge case. Only two genuinely missed days in a row
    (nothing today or yesterday) actually breaks the streak now."""
    date_set = set(dates)
    today = timezone.localdate()
    day = today if today in date_set else today - timedelta(days=1)
    current = 0
    while day in date_set:
        current += 1
        day -= timedelta(days=1)
    return current


def _longest_streak_from_dates(dates):
    if not dates:
        return 0
    longest = current = 1
    for prev, cur in zip(dates, dates[1:]):
        if (cur - prev).days == 1:
            current += 1
            longest = max(longest, current)
        else:
            current = 1
    return longest


def current_streak(profile):
    return _streak_from_dates(_distinct_watch_dates(profile))


def longest_streak(profile):
    return _longest_streak_from_dates(_distinct_watch_dates(profile))


def streaks(profile):
    """current_streak()/longest_streak() combined off a single query -
    quick_stats()/stats_overview() both need both values together and
    previously called the two functions separately, each re-fetching and
    re-deduping the same distinct-dates data."""
    dates = _distinct_watch_dates(profile)
    return _streak_from_dates(dates), _longest_streak_from_dates(dates)


def continue_watching(profile, media_types=None, limit=8):
    items = []
    qs = WatchProgress.objects.filter(profile=profile, status=WatchProgress.Status.WATCHING)
    if media_types:
        qs = qs.filter(title__media_type__in=media_types)
    qs = qs.select_related("title", "current_episode").order_by("-updated_at")
    if limit:
        qs = qs[:limit]
    progresses = list(qs)

    # One grouped query for every non-movie row's season episode-count,
    # instead of a Episode.objects.filter(...).count() per row - dashboard
    # calls this with limit=None (the full Watching list), so a per-row
    # query would scale with how many shows a profile has in progress.
    non_movie_title_ids = [p.title_id for p in progresses if p.title.media_type != MediaType.MOVIE]
    season_totals = {}
    if non_movie_title_ids:
        rows = (
            Episode.objects.filter(title_id__in=non_movie_title_ids)
            .values("title_id", "season")
            .annotate(total=Count("id"))
        )
        season_totals = {(r["title_id"], r["season"]): r["total"] for r in rows}

    for progress in progresses:
        title = progress.title
        season = episode_number = None
        if title.media_type == MediaType.MOVIE:
            total_seconds = (title.runtime_minutes or 0) * 60
            percent = min(100, round(progress.position_seconds / total_seconds * 100)) if total_seconds else 0
            if title.runtime_minutes:
                remaining = max(0, title.runtime_minutes - progress.position_seconds // 60)
                caption = f"{remaining} min left"
            else:
                caption = "In progress"
        else:
            ep = progress.current_episode
            percent, caption = 0, "In progress"
            if ep:
                season, episode_number = ep.season, ep.episode
                total_eps = season_totals.get((title.id, ep.season), 0)
                if total_eps:
                    percent = min(100, round(ep.episode / total_eps * 100))
                    caption = f"S{ep.season}E{ep.episode} of {total_eps}"
                else:
                    caption = f"S{ep.season}E{ep.episode}"
        # season/episode_number (None for a movie, or a show with no
        # current_episode yet) let poster_card.html deep-link straight to
        # this episode (?season=N#episode-N-M) instead of just the
        # title's own page - see title_episodes.html's own episode-card
        # ids and app.css's :target styling for the other half of this.
        items.append(
            {"title": title, "percent": percent, "caption": caption, "season": season, "episode_number": episode_number}
        )
    return items


def _when_label(release_date):
    d = timezone.localtime(release_date).date()
    delta = (d - timezone.localdate()).days
    if delta == 0:
        return "TODAY"
    if delta == 1:
        return "TOMORROW"
    if delta < 7:
        return d.strftime("%a").upper()
    return f"{d.strftime('%b').upper()} {d.day}"


def episode_range_caption(episodes):
    """Caption for 2+ episodes of the same title releasing on the same
    day (e.g. a full-season drop) - "Season 4, Episode 1-3" when the
    numbers are contiguous, a comma list when they aren't, and an
    explicit S/E per episode in the (rare) case they span more than one
    season. Not underscore-prefixed - notifications.generate_release_notifications
    reuses this too, so its release-drop notifications read the same way
    as the dashboard's own Up Next card."""
    episodes = sorted(episodes, key=lambda e: (e.season, e.episode))
    if len({e.season for e in episodes}) > 1:
        return ", ".join(f"S{e.season}E{e.episode}" for e in episodes)
    numbers = [e.episode for e in episodes]
    season = episodes[0].season
    if numbers == list(range(numbers[0], numbers[-1] + 1)):
        return f"Season {season}, Episode {numbers[0]}-{numbers[-1]}"
    return f"Season {season}, Episodes " + ", ".join(str(n) for n in numbers)


def up_next(profile, limit=3):
    """Dashboard's "Up Next" card. Matches calendar_releases()'s default
    scope - any WatchProgress status, or plain watch history, not just
    WATCHING (see calendar_releases()'s docstring for why WATCHING alone
    isn't enough in practice). .distinct() is required here, unlike the
    old WatchProgress-only query: WatchProgress is at most one row per
    profile+title, but WatchEvent isn't (every episode watched is its own
    row), so joining through it can multiply-match the same
    ReleaseSchedule row once per watch event without it.

    Multiple episodes of the same title releasing on the same calendar
    day (a full-season drop) are collapsed into a single card with a
    "xN" count and an episode-range caption, instead of one card per
    episode eating the whole limit - fetches limit * _FETCH_MULTIPLIER
    raw rows before grouping so a same-day batch doesn't starve later
    titles out of the final limit slots."""
    _FETCH_MULTIPLIER = 20
    qs = (
        ReleaseSchedule.objects.filter(
            Q(title__watch_progress__profile=profile) | Q(title__watch_events__profile=profile),
            release_date__gte=timezone.now(),
        )
        .select_related("title", "episode")
        .order_by("release_date")
        .distinct()[: limit * _FETCH_MULTIPLIER]
    )
    groups = []
    group_index = {}
    for rs in qs:
        key = (rs.title_id, timezone.localtime(rs.release_date).date())
        if key in group_index:
            groups[group_index[key]]["episodes"].append(rs.episode)
        else:
            group_index[key] = len(groups)
            groups.append(
                {
                    "title": rs.title,
                    "release_date": rs.release_date,
                    "release_type_display": rs.get_release_type_display(),
                    "episodes": [rs.episode] if rs.episode else [],
                }
            )

    items = []
    for group in groups[:limit]:
        episodes = group["episodes"]
        if len(episodes) > 1:
            caption = episode_range_caption(episodes)
        elif episodes:
            caption = f"Season {episodes[0].season}, Episode {episodes[0].episode}"
        else:
            caption = group["release_type_display"]
        items.append(
            {
                "title": group["title"],
                "caption": caption,
                "when": _when_label(group["release_date"]),
                "count": len(episodes) or 1,
            }
        )
    return items


def quick_stats(profile):
    year = timezone.localdate().year
    movies_this_year = WatchEvent.objects.filter(
        profile=profile, title__media_type=MediaType.MOVIE, watched_at__year=year
    ).count()
    shows_completed = WatchProgress.objects.filter(
        profile=profile,
        status=WatchProgress.Status.COMPLETED,
        title__media_type__in=[MediaType.TV, MediaType.ANIME],
    ).count()
    total_minutes = (
        WatchEvent.objects.filter(profile=profile).aggregate(
            total=Sum(Coalesce("episode__runtime_minutes", "title__runtime_minutes", 0))
        )["total"]
        or 0
    )
    # Dashboard's streak pill shows both side by side ("N day streak ·
    # longest N") - streaks() computes both off one shared query instead
    # of current_streak()/longest_streak() each re-fetching the same
    # distinct-dates data.
    streak, longest = streaks(profile)
    return {
        "streak": streak,
        "longest_streak": longest,
        "movies_this_year": movies_this_year,
        "shows_completed": shows_completed,
        # "217d 4h 3m" style, matching the Stats page's own watch-time
        # breakdown format (format_duration) instead of a flat "7342h" -
        # the two pages showing the same kind of stat differently read as
        # inconsistent.
        "total_watch_time": format_duration(total_minutes),
    }


# Checked for equality, not >=, so a milestone banner fires the one day
# it's actually reached rather than persisting on every visit afterward.
STREAK_MILESTONES = {
    7: "Seven days straight — that's a habit now.",
    30: "Thirty days in a row. This isn't a phase.",
    100: "A hundred days straight. Impressive commitment.",
    365: "A full year without missing a day. Legendary.",
}
MOVIE_COUNT_MILESTONES = {
    25: "25 movies this year already.",
    50: "50 movies this year — some people read books.",
    100: "100 movies this year. That's a full-time hobby.",
    200: "200 movies this year. At this point it's a lifestyle.",
}


def milestone_message(streak, movies_this_year):
    """A short celebratory line for the Dashboard when streak or
    movies_this_year lands exactly on a milestone - streak takes
    priority if both hit on the same day. None most days."""
    if streak in STREAK_MILESTONES:
        return STREAK_MILESTONES[streak]
    if movies_this_year in MOVIE_COUNT_MILESTONES:
        return MOVIE_COUNT_MILESTONES[movies_this_year]
    return None


def _visible_watchlist_items(profile, media_types=None):
    qs = WatchListItem.objects.filter(Q(watchlist__profile=profile) | Q(watchlist__is_shared=True))
    if media_types:
        qs = qs.filter(title__media_type__in=media_types)
    return qs.select_related("title").prefetch_related("title__ratings").order_by("-added_at").distinct()


def recently_added_to_lists(profile, limit=3):
    """Dashboard's "Recently added to lists" row - custom lists only
    (is_watchlist=False). The Watchlist carousel right above it on
    Dashboard already shows the newest Watchlist adds, so including
    Watchlist items here too just echoed the same few titles a second
    time in a shorter row instead of adding new information."""
    return _visible_watchlist_items(profile).exclude(watchlist__is_watchlist=True)[:limit]


def because_you_watched(profile, candidate_pool=3, limit=12):
    """Dashboard's personalized discovery row - TMDB's own
    "recommendations" for the most recently watched title that has a
    TMDB id (most watch history does, via Trakt/Simkl/CSV import's own
    TMDB matching, or a title added through a discover/preview/search
    card). Tries up to candidate_pool recent titles, newest first,
    stopping at the first one TMDB actually has recommendations for -
    an obscure title can have none, and one retry or two is worth it,
    but this deliberately doesn't keep trying indefinitely (each attempt
    is a real TMDB call) just to fill a Dashboard row. None if nothing
    qualifies (no TMDB-linked watch history yet, no TMDB_API_KEY
    configured, or every candidate came back empty) - the Dashboard
    just skips the row rather than showing an empty one."""
    from tracker.integrations import tmdb as tmdb_integration

    recent_titles = []
    seen_title_ids = set()
    for event in (
        WatchEvent.objects.filter(profile=profile, title__external_ids__tmdb__isnull=False)
        .select_related("title")
        .order_by("-watched_at")
    ):
        if event.title_id not in seen_title_ids:
            seen_title_ids.add(event.title_id)
            recent_titles.append(event.title)
        if len(recent_titles) >= candidate_pool:
            break

    for title in recent_titles:
        tmdb_id = title.external_ids.get("tmdb")
        if not tmdb_id:
            continue
        media_type = tmdb_integration.media_type_for(title)
        results = tmdb_integration.get_similar(media_type, tmdb_id, limit=limit)
        if results:
            return {"anchor_title": title, "results": results}
    return None


def for_you(profile, limit=12):
    """Dashboard's "For You" row - a discover() call scoped to this
    profile's own genre/provider/region preferences (Settings →
    Preferences), distinct from because_you_watched above (TMDB's
    "similar to X" recommendations) and from every other row on this
    page (all generic trending/popular). Movie-only for now, matching
    preferred_genre_ids' own movie-catalog scope (see its model field
    comment). None when the profile hasn't set a genre or provider
    preference yet - an unscoped discover() call would just be "popular
    movies again", which the Dashboard already shows elsewhere."""
    if not profile.preferred_genre_ids and not profile.preferred_provider_ids:
        return None
    from tracker.integrations import tmdb as tmdb_integration

    page = tmdb_integration.discover(
        "movie",
        category="popular",
        genre_ids=profile.preferred_genre_ids,
        watch_providers=profile.preferred_provider_ids,
        region=profile.preferred_region,
        page_size=1,
    )
    results = (page.get("results") or [])[:limit]
    return {"results": results} if results else None


def start_watching(profile, media_types, limit=12):
    """Dashboard's "Start watching" row - watchlist titles worth
    surfacing right now: a movie that recently released, a show that
    recently dropped a new episode/season, or anything currently
    trending on TMDB. Recency comes from ReleaseSchedule (already
    populated by the Trakt/Simkl calendar sync - up_next() reads the
    same model's future side, this reads its recent-past side);
    trending comes from a live TMDB call, intersected against the
    watchlist by tmdb id (that call is cached 6h by tmdb._list_request,
    so this isn't a per-request cost). Already-WATCHING titles are
    excluded - this row is about starting something, the "Watching" row
    above it already covers continuing one. Recently-released titles
    are listed before trending ones (a concrete "new episode dropped"
    beats a soft popularity signal), each bucket newest/most-popular
    first, capped to limit total."""
    from .integrations import tmdb as tmdb_integration

    watching_ids = set(
        WatchProgress.objects.filter(profile=profile, status=WatchProgress.Status.WATCHING).values_list(
            "title_id", flat=True
        )
    )
    watchlist_titles = {}
    for item in _visible_watchlist_items(profile, media_types):
        if item.title_id not in watching_ids:
            watchlist_titles[item.title_id] = item.title
    if not watchlist_titles:
        return []

    cutoff = timezone.now() - timedelta(days=30)
    recent_ids = list(
        ReleaseSchedule.objects.filter(
            title_id__in=watchlist_titles.keys(), release_date__gte=cutoff, release_date__lte=timezone.now()
        )
        .order_by("-release_date")
        .values_list("title_id", flat=True)
        .distinct()
    )

    trending_tmdb_ids = set()
    for media_type in ("movie", "tv"):
        for result in tmdb_integration.discover(media_type, category="trending").get("results", []):
            if result.get("tmdb_id") is not None:
                trending_tmdb_ids.add(str(result["tmdb_id"]))
    trending_ids = [
        title_id
        for title_id, title in watchlist_titles.items()
        if title.external_ids.get("tmdb") in trending_tmdb_ids
    ]

    ordered_ids = []
    for title_id in [*recent_ids, *trending_ids]:
        if title_id not in ordered_ids:
            ordered_ids.append(title_id)
    return [watchlist_titles[title_id] for title_id in ordered_ids[:limit]]


def _attach_watch_event_display(events):
    """Attaches .still_url (this specific episode's TMDB still, falling
    back to the title's own poster when there's no episode, no TMDB id,
    or TMDB has nothing for it) and .caption ("S1E4 · Name", or None for
    a movie) to each WatchEvent - shared by recently_watched()/
    social_activity() so both Dashboard rows render identically. Batches
    TMDB season lookups per distinct (tmdb_id, season) touched rather
    than per event, since a binge revisits the same season repeatedly
    and get_season_details is a real (if 6h-cached) TMDB call."""
    from .integrations import tmdb as tmdb_integration

    season_cache = {}
    for event in events:
        ep = event.episode
        if ep is None:
            event.still_url = event.title.poster_url or None
            event.caption = None
            continue
        tmdb_id = event.title.external_ids.get("tmdb")
        cache_key = (tmdb_id, ep.season)
        if tmdb_id and cache_key not in season_cache:
            season_cache[cache_key] = tmdb_integration.get_season_details(tmdb_id, ep.season)
        season_data = season_cache.get(cache_key) or {}
        still_url = None
        tmdb_name = None
        for ep_data in season_data.get("episodes") or []:
            if ep_data.get("episode_number") == ep.episode:
                still_url = ep_data.get("still_url")
                tmdb_name = ep_data.get("name")
                break
        event.still_url = still_url or event.title.poster_url or None
        # ep.name is only ever populated by the Trakt/Simkl calendar sync
        # (spool-handoff-addendum.md §1) - watch history imported from
        # elsewhere (CSV, older syncs) leaves it blank, so this falls back
        # to the name in the TMDB season data already fetched above for
        # the still image, rather than showing no episode name at all.
        name = ep.name or tmdb_name
        event.caption = f"S{ep.season}E{ep.episode}" + (f" · {name}" if name else "")


def recently_watched(profile, media_types, limit=12):
    """Dashboard's "Recently Watched" row - this profile's own last
    `limit` watch events, newest first - not deduped by title, so a
    3-episode binge shows as 3 separate cards, each with that episode's
    own still image via _attach_watch_event_display()."""
    events = list(
        WatchEvent.objects.filter(profile=profile, title__media_type__in=media_types)
        .select_related("title", "episode")
        .order_by("-watched_at")[:limit]
    )
    _attach_watch_event_display(events)
    return events


def social_activity(profile, limit=12):
    """Dashboard's "Social Activity" row - the last `limit` watch events
    from other profiles in the household (share_activity respecting,
    same privacy flag activity_feed() honors), newest first, not
    deduped, each carrying its own .profile so the card (a normal
    poster_card.html, not recently_watched()'s episode-still cards) can
    show who watched it via a pill over the poster - no still-image
    lookup needed here, so this skips _attach_watch_event_display()
    entirely rather than paying for TMDB calls a plain poster card
    would just ignore."""
    return list(
        WatchEvent.objects.filter(profile__share_activity=True)
        .exclude(profile=profile)
        .select_related("title", "episode", "profile")
        .prefetch_related("title__ratings")
        .order_by("-watched_at")[:limit]
    )


def on_this_day(profile, today=None, limit=8):
    """Dashboard's "On This Day" row - titles this profile watched on
    today's month/day in a previous year, newest year first. Bucketed on
    watched_at's LOCAL month/day (ExtractMonth/Day's tzinfo param, same
    approach as peak_hours' ExtractHour above), not the UTC date it's
    stored as - a household near a day boundary shouldn't see a card
    appear/vanish depending on which side of UTC midnight they're on.
    Deduped to one card per (title, year) - a same-day rewatch binge
    would otherwise repeat the same nostalgia moment multiple times."""
    today = today or timezone.localdate()
    events = (
        WatchEvent.objects.filter(profile=profile)
        .annotate(
            month=ExtractMonth("watched_at", tzinfo=timezone.get_current_timezone()),
            day=ExtractDay("watched_at", tzinfo=timezone.get_current_timezone()),
        )
        .filter(month=today.month, day=today.day)
        .exclude(watched_at__year=today.year)
        .select_related("title")
        .prefetch_related("title__ratings")
        .order_by("-watched_at")
    )
    seen_years = set()
    entries = []
    for event in events:
        year = timezone.localtime(event.watched_at).year
        key = (event.title_id, year)
        if key in seen_years:
            continue
        seen_years.add(key)
        entries.append({"title": event.title, "years_ago": today.year - year})
        if len(entries) >= limit:
            break
    return entries


def library_watchlist(profile, media_types):
    """Movies & TV / Anime 'Watchlist' tab — every title on any list visible
    to this profile (own + shared), scoped to the section's media types.
    There's no separate 'watchlist' model; the tab is a filtered view over
    WatchList/WatchListItem (spool-product-spec.md doesn't define a
    distinct concept for it)."""
    return _visible_watchlist_items(profile, media_types)


def library_history(profile, media_types, limit=50):
    """Per-section History tab — a simple recent-first list. Filtering/
    pagination/day-grouping is the combined /history/ page's job (build
    step 7); this stays intentionally simpler."""
    return (
        WatchEvent.objects.filter(profile=profile, title__media_type__in=media_types)
        .select_related("title", "episode")
        .order_by("-watched_at")[:limit]
    )


def calendar_releases(profile, media_type=None, source="all", start=None, end=None):
    """Releases for titles this profile is watching or has on any visible
    watchlist (own + shared, same visibility rule as everywhere else lists
    show up) — spool-handoff-addendum.md §1. The default "all" view also
    includes titles with ANY WatchProgress status (not just WATCHING - a
    finished show that later gets renewed should still surface its new
    season here) and, since nothing in this app actually sets WatchProgress
    to WATCHING (it's write-only from Django admin; real usage only ever
    reaches COMPLETED, via completion.py once every episode is watched),
    also anything with plain watch history at all - otherwise a show
    you're mid-way through via Trakt/Simkl/CSV import, with no
    WatchProgress row of any kind, would never qualify. The explicit
    source="watching" filter keeps its narrower, literal meaning on
    purpose - that's a deliberate user-facing filter choice, not the gap
    this broadening fixes.

    start/end scope the release_date window as [start, end) - omitted,
    this defaults to everything from now onward (the Calendar sidebar's
    "what's upcoming" agenda, which stays relative to the current moment
    no matter which month the grid is showing). The calendar grid instead
    passes the specific month being viewed: ReleaseSchedule rows are never
    pruned once their date passes, so a past month's releases are still in
    the database, they just weren't being queried for."""
    watching_q = Q(
        title__watch_progress__profile=profile, title__watch_progress__status=WatchProgress.Status.WATCHING
    )
    any_progress_q = Q(title__watch_progress__profile=profile) | Q(title__watch_events__profile=profile)
    watchlist_q = Q(title__watchlist_items__watchlist__profile=profile) | Q(
        title__watchlist_items__watchlist__is_shared=True
    )
    if source == "watching":
        scope_q = watching_q
    elif source == "watchlist":
        scope_q = watchlist_q
    else:
        scope_q = any_progress_q | watchlist_q

    date_q = Q(release_date__gte=start if start else timezone.now())
    if end:
        date_q &= Q(release_date__lt=end)

    qs = (
        ReleaseSchedule.objects.filter(scope_q, date_q)
        .select_related("title", "episode")
        .order_by("release_date")
        .distinct()
    )
    if media_type:
        qs = qs.filter(title__media_type=media_type)
    return qs


def sync_failure_streaks():
    """Sync Log's alert banner - flags each (profile, provider) whose most
    recent syncs are consecutively failed, so a dead integration reads as
    "here's what's wrong" instead of a wall of red rows someone has to
    notice and count themselves. Only looks at the newest 200 log rows
    total (not per-pair) - Sync Log is a low-volume household admin tool,
    not something that needs unbounded history scanned on every page
    load. RUNNING rows are excluded entirely (neither extend nor break a
    streak) since they're transient and say nothing about outcome yet.
    Returns dicts sorted oldest-streak-first (the most overdue problem
    first), each with the profile, provider, how many failures in a row,
    when the streak started, and whether the errors in it look
    auth-shaped (contain "401" or "Unauthorized") - the template uses
    that to decide whether a reconnect link actually makes sense, versus
    a generic "syncs are failing" note for e.g. a network blip."""
    from .models import SyncLog

    recent = (
        SyncLog.objects.select_related("profile")
        .exclude(status=SyncLog.Status.RUNNING)
        .order_by("-started_at")[:200]
    )

    by_pair = {}
    for log in recent:
        by_pair.setdefault((log.profile_id, log.provider), []).append(log)

    streaks = []
    for (profile_id, provider), logs in by_pair.items():
        streak = []
        for log in logs:
            if log.status != SyncLog.Status.FAILED:
                break
            streak.append(log)
        if len(streak) < 2:
            continue
        streaks.append(
            {
                "profile": streak[0].profile,
                "profile_id": profile_id,
                "provider": provider,
                "count": len(streak),
                "since": streak[-1].started_at,
                "looks_like_auth_failure": any(
                    "401" in log.error_message or "Unauthorized" in log.error_message for log in streak
                ),
            }
        )
    streaks.sort(key=lambda s: s["since"])
    return streaks


def combined_logs(
    page_number,
    page_size=50,
    cap=1000,
    profile_id=None,
    oldest_first=False,
    action_type=None,
    provider=None,
    status=None,
    date_from=None,
    date_to=None,
):
    """Settings → Logs. Merges SyncLog (recurring background Trakt/Simkl
    syncs) and DataLog (CSV import/export, connect attempts) into one
    chronological feed across every profile - admin-only, optionally
    narrowed to a single profile. Each table is capped at `cap` rows
    before merging in Python rather than a SQL UNION - a generous cap
    (unlike sync_failure_streaks()'s 200, which only ever needs to look
    at recent rows to detect a *current* streak) since this view's whole
    point is letting an admin page back through history, not just the
    latest activity. Filtering to one profile is applied before the cap,
    so a single user's full history stays reachable even once the
    all-profiles feed exceeds it. SyncLog's RUNNING rows are included
    deliberately, unlike sync_failure_streaks() - a stuck/long-running
    sync showing "running" is itself useful signal in a raw log view.

    Every filter below is applied to the querysets before the [:cap]
    slice, same reasoning as profile_id - the cap should mean "most
    recent cap rows matching the filter", not "cap first, then filter
    (possibly to nothing)". action_type/date_from/date_to are trusted to
    already be validated/parsed by the caller (see views._settings_page_
    context) since they arrive as free-text GET params a user could
    hand-edit."""
    from django.core.paginator import Paginator

    from .models import SyncLog

    sync_logs = SyncLog.objects.select_related("profile").order_by("-started_at")
    data_logs = DataLog.objects.select_related("profile").order_by("-created_at")
    if profile_id:
        sync_logs = sync_logs.filter(profile_id=profile_id)
        data_logs = data_logs.filter(profile_id=profile_id)
    if action_type == "sync":
        data_logs = data_logs.none()
    elif action_type in _LOG_ACTION_TYPE_VALUES:
        sync_logs = sync_logs.none()
        data_logs = data_logs.filter(action__in=_LOG_ACTION_TYPE_VALUES[action_type])
    if provider:
        sync_logs = sync_logs.filter(provider=provider)
        data_logs = data_logs.filter(provider=provider)
    if status:
        sync_logs = sync_logs.filter(status=status)
        data_logs = data_logs.filter(status=status)
    if date_from:
        sync_logs = sync_logs.filter(started_at__date__gte=date_from)
        data_logs = data_logs.filter(created_at__date__gte=date_from)
    if date_to:
        sync_logs = sync_logs.filter(started_at__date__lte=date_to)
        data_logs = data_logs.filter(created_at__date__lte=date_to)

    entries = [
        {
            "profile": log.profile,
            "action": f"Sync · {log.get_provider_display()}",
            "status": log.status,
            "item_count": log.item_count,
            "detail": "",
            "imported_titles": log.imported_titles,
            "imported_titles_more": max(0, (log.item_count or 0) - len(log.imported_titles)),
            "error_message": log.error_message,
            "timestamp": log.started_at,
            "duration_seconds": log.duration_seconds,
        }
        for log in sync_logs[:cap]
    ] + [
        {
            "profile": log.profile,
            "action": log.get_action_display(),
            "status": log.status,
            "item_count": log.item_count,
            "detail": log.detail,
            "imported_titles": log.imported_titles,
            "imported_titles_more": max(0, (log.item_count or 0) - len(log.imported_titles)),
            "error_message": log.error_message,
            "timestamp": log.created_at,
            "duration_seconds": None,
        }
        for log in data_logs[:cap]
    ]
    entries.sort(key=lambda e: e["timestamp"], reverse=not oldest_first)
    return Paginator(entries, page_size).get_page(page_number)


def titles_needing_release_sync():
    """Titles worth polling TMDB for upcoming releases - anything with a
    WatchProgress row (any status), watchlist membership, or plain watch
    history. The watch-history leg matters in practice: nothing in this
    app ever sets WatchProgress to WATCHING (see calendar_releases()'s
    docstring), so without it, a show a household is mid-way through via
    Trakt/Simkl/CSV import - with no WatchProgress row of any kind - would
    never get its upcoming episodes checked. Instance-wide, not
    profile-scoped: a release date is a fact about the Title, shared
    across every household profile that cares about it;
    calendar_releases()/up_next() do the per-profile filtering at read
    time. TMDB-id filtering happens per-title in Python inside
    release_sync.sync_title_releases, not here, matching the
    title.external_ids.get("tmdb") idiom already used in views.py."""
    return Title.objects.filter(
        Q(watch_progress__isnull=False) | Q(watchlist_items__isnull=False) | Q(watch_events__isnull=False)
    ).distinct()


def ready_to_watch_queue(profile, queue_size=3):
    """The Calendar sidebar's featured card — the next *already-released*
    episode for whatever this profile most recently progressed (not a
    future release; that's the agenda below it). Movies have no "next
    episode" concept, so they're excluded from consideration."""
    progress = (
        WatchProgress.objects.filter(profile=profile, status=WatchProgress.Status.WATCHING)
        .exclude(title__media_type=MediaType.MOVIE)
        .exclude(current_episode__isnull=True)
        .select_related("title", "current_episode")
        .order_by("-updated_at")
        .first()
    )
    if not progress:
        return None, []

    ep = progress.current_episode
    upcoming = list(
        Episode.objects.filter(title=progress.title, season=ep.season, episode__gt=ep.episode).order_by("episode")[
            :queue_size
        ]
    )
    if not upcoming:
        upcoming = list(
            Episode.objects.filter(title=progress.title, season__gt=ep.season).order_by("season", "episode")[
                :queue_size
            ]
        )
    if not upcoming:
        return None, []

    featured, rest = upcoming[0], upcoming[1:]
    return {"title": progress.title, "episode": featured}, [{"title": progress.title, "episode": e} for e in rest]


def visible_lists(profile):
    """Lists index — every list this profile can see (owned + shared),
    with just enough prefetched to render the cover collage + count
    without a query per card."""
    return (
        WatchList.objects.filter(Q(profile=profile) | Q(is_shared=True))
        .select_related("profile")
        .prefetch_related("items__title")
        .distinct()
    )


def featured_lists():
    """Dashboard's Featured Lists rail - owner-curated (views.
    toggle_list_featured), shown to every profile regardless of who
    created the list. is_shared is required alongside is_featured since
    featuring a private list wouldn't be visible to anyone else anyway."""
    return (
        WatchList.objects.filter(is_shared=True, is_featured=True)
        .select_related("profile")
        .prefetch_related("items__title")
        .order_by("name")
    )


def _days_and_hours_display(total_minutes):
    """(whole rounded day count, "Nh Mm" display string) for a duration -
    the Stats page's "Combined" rows lead with days (rounded to the
    nearest whole day - 430.6 rounds up to 431, not truncated down to
    430) and show the precise hour/minute figure alongside it in
    parentheses, the reverse of stats_overview()/watch_time_breakdown()'s
    older hours-first total_watch_hours/total_watch_days and combined
    "hours"/"days" keys (kept as-is - profile_popup.html still reads
    those directly)."""
    days = round(total_minutes / (24 * 60))
    hours, minutes = divmod(int(total_minutes), 60)
    hours_display = f"{hours}h {minutes}m" if minutes else f"{hours}h"
    return days, hours_display


def stats_overview(profile):
    """Lifetime totals for the Stats page hero + donut — deliberately not
    year-scoped, unlike Dashboard's quick_stats()."""
    events = WatchEvent.objects.filter(profile=profile)
    total_minutes = (
        events.aggregate(total=Sum(Coalesce("episode__runtime_minutes", "title__runtime_minutes", 0)))["total"] or 0
    )
    total_hours = round(total_minutes / 60)
    total_watch_days_rounded, total_watch_hours_display = _days_and_hours_display(total_minutes)

    cur, longest = streaks(profile)

    type_counts = dict(events.values_list("title__media_type").annotate(c=Count("id")).order_by())
    total_events = sum(type_counts.values())

    def pct(media_type):
        return round(type_counts.get(media_type, 0) / total_events * 100) if total_events else 0

    return {
        "current_streak": cur,
        "longest_streak": longest,
        "dial_pct": min(100, round(cur / longest * 100)) if longest else 0,
        "total_watch_hours": total_hours,
        "total_watch_days": round(total_hours / 24, 1),
        "total_watch_days_rounded": total_watch_days_rounded,
        "total_watch_hours_display": total_watch_hours_display,
        # "watched" (unique titles) vs "plays" (every watch, rewatches
        # included) - the same distinction Trakt/Simkl draw ("2,034 movies
        # (2,773 plays)"). movies_watched previously *was* the plays count
        # mislabeled as a movie count, which reads as "2,773 different
        # movies" when it's actually far fewer unique titles rewatched a
        # lot - confirmed against a real account where the two numbers
        # differed by 700+.
        "movies_watched": events.filter(title__media_type=MediaType.MOVIE).values("title_id").distinct().count(),
        "movies_plays": events.filter(title__media_type=MediaType.MOVIE).count(),
        "shows_completed": WatchProgress.objects.filter(
            profile=profile,
            status=WatchProgress.Status.COMPLETED,
            title__media_type__in=[MediaType.TV, MediaType.ANIME],
        ).count(),
        "episodes_logged": events.filter(episode__isnull=False).count(),
        "split": {
            "movie_pct": pct(MediaType.MOVIE),
            "tv_pct": pct(MediaType.TV),
            "anime_pct": pct(MediaType.ANIME),
        },
    }


def format_duration(total_minutes):
    """"2d 17h 58m" / "22h 21m" / "45m" - matches how Trakt/Simkl format
    their own watch-time breakdowns, which is why this exists separately
    from stats_overview's plain "{hours}h" figure."""
    days, rem = divmod(int(total_minutes), 24 * 60)
    hours, minutes = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if days or hours:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


def watch_time_breakdown(profile):
    """Per-type (Movies/TV/Anime) watch time + count, split into last-30-
    days and all-time buckets - the single combined "Total watch time"
    figure in stats_overview() doesn't break down by type or time window
    the way Trakt/Simkl's own stats pages do. Each bucket also carries
    its own "combined" total (hours/days, same shape and rounding as
    stats_overview()'s own all-time total_watch_hours/total_watch_days)
    so a "Combined" row can sit under Last 30 days the same way one
    already does under All time - the all-time template row still reads
    from stats_overview() directly rather than this one, to avoid two
    call sites computing what should be the identical lifetime figure."""

    media_types = [MediaType.MOVIE, MediaType.TV, MediaType.ANIME]

    def bucket(events):
        # One aggregate() call with a conditional Sum/Count per media
        # type, instead of the 3x2 separate queries a per-type loop would
        # issue - each call site (last_30_days/all_time) previously cost
        # 6 round trips for what's really one GROUP-BY-shaped question.
        agg_kwargs = {}
        for media_type in media_types:
            agg_kwargs[f"{media_type}_minutes"] = Sum(
                Coalesce("episode__runtime_minutes", "title__runtime_minutes", 0),
                filter=Q(title__media_type=media_type),
            )
            agg_kwargs[f"{media_type}_count"] = Count("id", filter=Q(title__media_type=media_type))
        totals = events.aggregate(**agg_kwargs)

        result = {}
        combined_minutes = 0
        for media_type in media_types:
            minutes = totals[f"{media_type}_minutes"] or 0
            result[media_type] = {"duration": format_duration(minutes), "count": totals[f"{media_type}_count"] or 0}
            combined_minutes += minutes
        combined_hours = round(combined_minutes / 60)
        days_rounded, hours_display = _days_and_hours_display(combined_minutes)
        result["combined"] = {
            "hours": combined_hours,
            "days": round(combined_hours / 24, 1),
            "days_rounded": days_rounded,
            "hours_display": hours_display,
        }
        return result

    events = WatchEvent.objects.filter(profile=profile)
    return {
        "last_30_days": bucket(events.filter(watched_at__gte=timezone.now() - timedelta(days=30))),
        "all_time": bucket(events),
    }


def format_duration_compact(total_minutes):
    """Single-unit duration for the genre chart's badges - "86d"/"18h"/
    "45m", picking the largest unit that's still >= 1. Distinct from
    format_duration's multi-unit "Xd Xh Xm", which is too long to sit
    inside a narrow genre-bar segment or a small MOST/LEAST badge."""
    days = int(total_minutes) // (24 * 60)
    if days:
        return f"{days}d"
    hours = int(total_minutes) // 60
    if hours:
        return f"{hours}h"
    return f"{int(total_minutes)}m"


def genre_breakdown(profile, media_type, metric="items"):
    """Per-genre breakdown for Stats' "Your top genres" panel (styled
    after Simkl's own genre chart) - by title/event count ("items") or
    total watch time ("duration"). Sorted descending, each genre's own
    share of the type's total as a rounded percentage - shares can be a
    point or two off summing to exactly 100, same rounding trade-off
    every other percentage breakdown in this app already makes."""
    qs = WatchEvent.objects.filter(profile=profile, title__media_type=media_type, title__genres__isnull=False)
    if metric == "duration":
        qs = (
            qs.values("title__genres__name")
            .annotate(value=Sum(Coalesce("episode__runtime_minutes", "title__runtime_minutes", 0)))
            .order_by("-value")
        )
    else:
        qs = qs.values("title__genres__name").annotate(value=Count("id")).order_by("-value")

    unit = "movies" if media_type == MediaType.MOVIE else "episodes"
    rows = [{"name": row["title__genres__name"], "value": row["value"]} for row in qs if row["value"]]
    total = sum(r["value"] for r in rows)
    for r in rows:
        r["pct"] = round(r["value"] / total * 100) if total else 0
        r["display"] = format_duration_compact(r["value"]) if metric == "duration" else f"{r['value']} {unit}"
    return rows


def top_genres(profile, limit=3):
    """Top N genres by watch count across every media type combined - the
    profile popup's compact chip row. Unlike genre_breakdown, which the
    full Stats page deliberately scopes to one media type at a time (with
    its own TV/Anime/Movies selector), the popup has no room for that
    selector, so this just combines everything into one ranking."""
    qs = (
        WatchEvent.objects.filter(profile=profile, title__genres__isnull=False)
        .values("title__genres__name")
        .annotate(value=Count("id"))
        .order_by("-value")[:limit]
    )
    return [{"name": row["title__genres__name"], "value": row["value"]} for row in qs if row["value"]]


def taste_compatibility(profile_a, profile_b):
    """Stats page's "Taste Compatibility" panel - how much two profiles'
    genre tastes overlap, plus which shared genre they lean on most.
    Jaccard similarity (shared genres / either profile's total distinct
    genres) rather than a raw watch-count comparison, so a household
    member with a much bigger watch history doesn't automatically look
    "less compatible" just by virtue of a larger denominator. Returns
    None when either profile has no genre history at all - "0%
    overlap" would misleadingly read as "opposite tastes" when it
    really just means "nothing to compare yet"."""
    counts_a = dict(
        WatchEvent.objects.filter(profile=profile_a, title__genres__isnull=False)
        .values("title__genres__name")
        .annotate(value=Count("id"))
        .values_list("title__genres__name", "value")
    )
    counts_b = dict(
        WatchEvent.objects.filter(profile=profile_b, title__genres__isnull=False)
        .values("title__genres__name")
        .annotate(value=Count("id"))
        .values_list("title__genres__name", "value")
    )
    genres_a, genres_b = set(counts_a), set(counts_b)
    if not genres_a or not genres_b:
        return None
    shared = genres_a & genres_b
    union = genres_a | genres_b
    top_shared_genre = max(shared, key=lambda g: counts_a[g] + counts_b[g]) if shared else None
    return {
        "profile": profile_b,
        "overlap_pct": round(len(shared) / len(union) * 100),
        "top_shared_genre": top_shared_genre,
    }


def year_breakdown(profile, media_type):
    qs = (
        WatchEvent.objects.filter(profile=profile, title__media_type=media_type)
        .values("title__year")
        .annotate(count=Count("id"))
        .order_by("title__year")
    )
    return [{"year": row["title__year"], "count": row["count"]} for row in qs]


def heatmap_available_years(profile):
    years = {d.year for d in WatchEvent.objects.filter(profile=profile).dates("watched_at", "year")}
    years.add(timezone.localdate().year)
    return sorted(years, reverse=True)


def heatmap_counts_by_day(profile, year):
    qs = (
        WatchEvent.objects.filter(profile=profile, watched_at__year=year)
        .values("watched_at__date")
        .annotate(count=Count("id"))
    )
    return {row["watched_at__date"]: row["count"] for row in qs}


_WEEKDAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def daily_breakdown(profile, days=7):
    """Per-day watch duration for the last `days` days (today inclusive) -
    the Stats page's Daily Breakdown bar chart. Today's own label reads
    "Today" rather than its weekday name, and each day's height_pct is
    relative to the window's own peak day (not a fixed scale), matching
    the mockup's "12h 24m" label floating above the tallest bar."""
    today = timezone.localdate()
    start = today - timedelta(days=days - 1)
    minutes_by_date = {
        row["watched_at__date"]: row["minutes"] or 0
        for row in (
            WatchEvent.objects.filter(profile=profile, watched_at__date__gte=start, watched_at__date__lte=today)
            .values("watched_at__date")
            .annotate(minutes=Sum(Coalesce("episode__runtime_minutes", "title__runtime_minutes", 0)))
        )
    }

    day_rows = []
    for i in range(days):
        d = start + timedelta(days=i)
        minutes = minutes_by_date.get(d, 0)
        day_rows.append(
            {
                "label": "Today" if d == today else _WEEKDAY_LABELS[d.weekday()],
                "date": d,
                "minutes": minutes,
                "duration": format_duration(minutes),
            }
        )

    peak_minutes = max((d["minutes"] for d in day_rows), default=0)
    for d in day_rows:
        d["height_pct"] = round(d["minutes"] / peak_minutes * 100) if peak_minutes else 0

    return {"days": day_rows, "peak_minutes": peak_minutes, "peak_duration": format_duration(peak_minutes)}


def daily_average(profile, days=7):
    """Average per-day watch time over the last `days` days, with a delta
    vs. the preceding period of the same length (e.g. "+9m" - this
    window's daily average is 9 minutes higher than last window's)."""
    today = timezone.localdate()

    def total_minutes(start, end):
        return (
            WatchEvent.objects.filter(profile=profile, watched_at__date__gte=start, watched_at__date__lte=end)
            .aggregate(total=Sum(Coalesce("episode__runtime_minutes", "title__runtime_minutes", 0)))["total"]
            or 0
        )

    current_start = today - timedelta(days=days - 1)
    previous_end = current_start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=days - 1)

    current_avg = total_minutes(current_start, today) / days
    previous_avg = total_minutes(previous_start, previous_end) / days
    delta = round(current_avg) - round(previous_avg)

    return {
        "average_duration": format_duration(current_avg),
        "delta_positive": delta >= 0,
        "delta_label": f"{'+' if delta > 0 else '-'}{format_duration(abs(delta))}" if delta else None,
    }


# (label, start_hour_inclusive, end_hour_exclusive) in the profile's local
# time - Night wraps past midnight (21:00-05:00), everything else doesn't.
_TIME_OF_DAY_BUCKETS = [
    ("Morning", 5, 12),
    ("Afternoon", 12, 17),
    ("Evening", 17, 21),
    ("Night", 21, 5),
]


def peak_hours(profile):
    """Lifetime distribution of watch events by time of day - which part
    of the day this household tends to watch in, for the Stats page's
    Peak Hours bars. Bucketed on watched_at's LOCAL hour (via ExtractHour's
    tzinfo param), not the UTC hour it's stored as - a single grouped
    query (at most 24 rows back) rather than fetching every WatchEvent."""
    counts_by_hour = dict(
        WatchEvent.objects.filter(profile=profile)
        .annotate(hour=ExtractHour("watched_at", tzinfo=timezone.get_current_timezone()))
        .values("hour")
        .annotate(count=Count("id"))
        .values_list("hour", "count")
    )

    counts = {label: 0 for label, _, _ in _TIME_OF_DAY_BUCKETS}
    for hour, count in counts_by_hour.items():
        for label, start, end in _TIME_OF_DAY_BUCKETS:
            in_range = start <= hour < end if start < end else (hour >= start or hour < end)
            if in_range:
                counts[label] += count
                break

    max_count = max(counts.values(), default=0)
    return [
        {"label": label, "count": counts[label], "pct": round(counts[label] / max_count * 100) if max_count else 0}
        for label, _, _ in _TIME_OF_DAY_BUCKETS
    ]


def activity_feed(limit=30):
    """What your household has been watching — merges two real, directly-
    derivable action types (watched/rated, added-to-list) across every
    profile. Only meaningful with >1 profile, which is exactly when the
    Activity page exists at all (spool-product-spec.md §5). Excludes any
    profile with share_activity=False (Settings → Privacy) entirely -
    not just muted, actually absent from the merged feed."""
    items = []
    for event in (
        WatchEvent.objects.filter(profile__share_activity=True)
        .select_related("profile", "title", "episode")
        .order_by("-watched_at")[:limit]
    ):
        items.append(
            {
                "profile": event.profile,
                "timestamp": event.watched_at,
                "kind": "rated" if event.user_rating else "watched",
                "title": event.title,
                "episode": event.episode,
                "rating": event.user_rating,
            }
        )
    for wli in (
        WatchListItem.objects.filter(watchlist__profile__share_activity=True)
        .select_related("watchlist__profile", "title")
        .order_by("-added_at")[:limit]
    ):
        items.append(
            {
                "profile": wli.watchlist.profile,
                "timestamp": wli.added_at,
                "kind": "added_to_list",
                "title": wli.title,
                "watchlist": wli.watchlist,
            }
        )
    items.sort(key=lambda i: i["timestamp"], reverse=True)
    return _group_consecutive_watches(items[:limit])


def _group_key(item):
    """What "run" an item could join, or None if this kind never groups.
    Episode watches group per (profile, title) - a show binge. Movie
    watches group per profile alone, not per title too - a movie marathon
    is almost always different films back to back, not the same one
    repeatedly. List-adds group per (profile, watchlist), not per title -
    the noisy case is bulk-adding many different titles to one list, same
    as the screenshot that prompted this. Ratings are always shown
    individually; each one is already a single meaningful entry."""
    if item["kind"] == "watched":
        if item["episode"] is not None:
            return ("episode", item["profile"].pk, item["title"].pk)
        return ("movie", item["profile"].pk)
    if item["kind"] == "added_to_list":
        return ("list", item["profile"].pk, item["watchlist"].pk)
    return None


MAX_GROUP_GAP = timedelta(hours=6)


def _group_consecutive_watches(items):
    """Collapses a run of consecutive (adjacent in the already-sorted feed,
    same _group_key, and no more than MAX_GROUP_GAP apart) events into one
    summary entry, so e.g. a 15-episode binge or a 13-title bulk list-add
    doesn't bury every other profile's activity under a wall of near-
    identical rows. The gap check matters as much as the key match: two
    real, hours-apart viewing sessions of the same show share the same
    _group_key (profile, title) but aren't the same sitting - without a
    cutoff they'd silently merge into one group whose single displayed
    timestamp is only honest for *some* of the episodes it claims to
    cover. Checked against the previous item in the run (a chain, not a
    span-from-the-start cap), so a long-but-continuous binge still stays
    one group as long as no single gap between consecutive episodes
    exceeds it."""
    grouped = []
    i = 0
    while i < len(items):
        item = items[i]
        key = _group_key(item)
        if key is None:
            grouped.append(item)
            i += 1
            continue
        run = [item]
        j = i + 1
        while (
            j < len(items)
            and _group_key(items[j]) == key
            and run[-1]["timestamp"] - items[j]["timestamp"] <= MAX_GROUP_GAP
        ):
            run.append(items[j])
            j += 1
        grouped.append(_build_group(key[0], run) if len(run) > 1 else item)
        i = j
    return grouped


def _build_group(group_type, run):
    """run is ordered newest-first (matches the feed's own sort)."""
    if group_type == "episode":
        episodes = [i["episode"] for i in run]
        first_by_ep = min(episodes, key=lambda e: (e.season, e.episode))
        last_by_ep = max(episodes, key=lambda e: (e.season, e.episode))
        range_label = f"S{first_by_ep.season}E{first_by_ep.episode}–S{last_by_ep.season}E{last_by_ep.episode}"
        return {
            "profile": run[0]["profile"],
            "timestamp": run[0]["timestamp"],
            "kind": "watched_group",
            "title": run[0]["title"],
            "count": len(run),
            "range_label": range_label,
            "episodes": run,
            "is_group": True,
        }
    if group_type == "movie":
        return {
            "profile": run[0]["profile"],
            "timestamp": run[0]["timestamp"],
            "kind": "watched_movies_group",
            "count": len(run),
            "movies": run,
            "is_group": True,
        }
    return {
        "profile": run[0]["profile"],
        "timestamp": run[0]["timestamp"],
        "kind": "added_to_list_group",
        "watchlist": run[0]["watchlist"],
        "count": len(run),
        "items": run,
        "is_group": True,
    }


def plain_watch_count(profile, title):
    """How many episode-less WatchEvents this profile has logged for this
    title - deliberately narrower than "has any watch event at all" - a
    show with only individual episodes watched (no whole-title mark)
    shouldn't count toward the header's primary Watched toggle/popover,
    which would falsely claim the whole thing is done. Scoped to the
    plain events title_mark_watched/title_unmark_watched/
    title_unmark_last_watched actually own, not the episode browser's
    own separate, always-append rewatch log. Shared by title_local_context
    (the detail page's single-title case, which only ever renders this
    button for movies - see title_detail.html) and _badge_watch_counts'
    own movie branch below."""
    return WatchEvent.objects.filter(profile=profile, title=title, episode__isnull=True).count()


def _badge_watch_counts(profile, titles):
    """Batched watched-button badge counts (the checkmark's ×N) for
    poster_action_context/discover_action_context - a movie's is its
    plain-event count (plain_watch_count, unchanged - also what the
    popover's "Remove last/all watched" actions themselves operate on).
    A show/anime has no equivalent single "watched" event (see
    title_mark_watched's movie-only gating in title_detail.html), so
    this instead takes the *minimum* watch count across every locally-
    known episode - an Episode row only exists once watched at least
    once (see episode_mark_watched), so this reads as "of the episodes
    you've engaged with, the least-rewatched one has been watched this
    many times." A DB-only approximation of "how many times have you
    watched this show start to finish" - the real thing would need
    TMDB's total episode count per title, a call this can't afford to
    make once per grid tile. Deliberately conservative: only partially
    rewatching a season keeps this low even if a few individual
    episodes were replayed many times. Falls back to the plain-event
    count for a show with no episode-level events at all (e.g.
    quick-marked via the plain checkmark instead of ever opening the
    episode browser), matching a movie's own behavior in that case."""
    title_ids = [t.pk for t in titles]
    plain_counts = dict(
        WatchEvent.objects.filter(profile=profile, title_id__in=title_ids, episode__isnull=True)
        .values("title_id")
        .annotate(n=Count("id"))
        .values_list("title_id", "n")
    )
    show_ids = [t.pk for t in titles if t.media_type != MediaType.MOVIE]
    episode_counts_by_title = {}
    for title_id, n in (
        WatchEvent.objects.filter(profile=profile, title_id__in=show_ids, episode__isnull=False)
        .values("title_id", "episode_id")
        .annotate(n=Count("id"))
        .values_list("title_id", "n")
    ):
        episode_counts_by_title.setdefault(title_id, []).append(n)
    return {
        t.pk: min(episode_counts_by_title[t.pk]) if t.pk in episode_counts_by_title else plain_counts.get(t.pk, 0)
        for t in titles
    }


def title_watched(profile, title):
    """Whether this profile has ANY WatchEvent for this title - plain or
    per-episode. Wider than plain_watch_count>0 on purpose: a show watched
    entirely through the episode browser (see title_mark_season_watched/
    title_mark_all_seasons_watched) never logs a plain event, but is still
    genuinely watched for the poster card's own checkmark (poster_action_context's
    watched_by_title answers the same question for a grid; this is the
    single-title shape the watched-button fragment views need)."""
    return WatchEvent.objects.filter(profile=profile, title=title).exists()


def title_watch_history_context(profile, title):
    """The title detail page's "Your history" card - status line + recent
    plays - factored out of title_local_context so views that only change
    watch state (episode_mark_watched and friends) can re-render just this
    slice for an out-of-band swap without title_local_context's other,
    unrelated queries (ratings, lists)."""
    progress = WatchProgress.objects.filter(profile=profile, title=title).select_related("current_episode").first()
    recent_events = list(
        WatchEvent.objects.filter(profile=profile, title=title).select_related("episode").order_by("-watched_at")[:10]
    )
    return {"progress": progress, "recent_events": recent_events}


def title_local_context(profile, title):
    """The title detail page's own-data half - watch/rating/list state -
    kept separate from the TMDB-sourced half (overview/cast/similar,
    fetched directly in the view) since this part never needs a network
    call and stays correct even without a TMDB_API_KEY configured."""
    latest_rating = (
        WatchEvent.objects.filter(profile=profile, title=title, user_rating__isnull=False)
        .order_by("-watched_at")
        .values_list("user_rating", flat=True)
        .first()
    )
    my_lists = list(WatchList.objects.filter(profile=profile).order_by("name"))
    in_list_ids = set(
        WatchListItem.objects.filter(watchlist__profile=profile, title=title).values_list("watchlist_id", flat=True)
    )
    watch_count = plain_watch_count(profile, title)
    return {
        **title_watch_history_context(profile, title),
        "latest_rating": latest_rating,
        "my_lists": my_lists,
        "in_list_ids": in_list_ids,
        "is_watched": watch_count > 0,
        "watch_count": watch_count,
    }


def default_season_for_title(profile, title):
    """Which season the title detail page's episode browser should open
    on by default - the highest season number the profile has any
    watched episode in (picking up where they left off), or None if
    they haven't watched any episode of this show yet, in which case the
    view falls back to season 1."""
    return (
        WatchEvent.objects.filter(profile=profile, title=title, episode__isnull=False)
        .order_by("-episode__season")
        .values_list("episode__season", flat=True)
        .first()
    )


def watched_episode_numbers(profile, title, season):
    """Episode numbers of the given season the profile has watched - for
    the episode browser's per-tile watched badge."""
    return set(
        WatchEvent.objects.filter(profile=profile, title=title, episode__season=season).values_list(
            "episode__episode", flat=True
        )
    )


def watched_episode_counts_by_season(profile, title):
    """{season_number: distinct episode count watched}, across every
    season of this show - the season picker's own per-card progress bar
    (views._episode_panel_context). One grouped query (same
    .values_list().annotate(Count()) idiom used elsewhere in this
    module, e.g. stats' type_counts) instead of one query per season.
    distinct=True on the Count so a rewatch (a second WatchEvent for the
    same episode) doesn't inflate the count past that season's own
    actual episode total."""
    return dict(
        WatchEvent.objects.filter(profile=profile, title=title, episode__isnull=False)
        .values_list("episode__season")
        .annotate(c=Count("episode", distinct=True))
        .order_by()
    )


def poster_action_context(profile, titles):
    """Batched version of title_local_context()'s watched/list-membership
    lookups, for a grid of poster cards (Dashboard's carousels, a list's
    item grid) - one query each instead of one-per-card, which title_local_context's
    single-title shape would turn into an N+1 if reused as-is here. Every
    title's pk is pre-seeded as a key in watched_by_title/list_membership
    (even with no matches), so poster_card.html's `|get_item:` lookups
    never hit a missing key."""
    title_ids = [t.pk for t in titles]
    watched_ids = set(
        WatchEvent.objects.filter(profile=profile, title_id__in=title_ids)
        .values_list("title_id", flat=True)
        .distinct()
    )
    watched_by_title = {tid: tid in watched_ids for tid in title_ids}

    # The checkmark's ×N badge - see _badge_watch_counts for what this
    # means for a show vs. a movie. Note the popover's own "Remove
    # last/all watched" actions only ever touch *plain* events
    # regardless of what this badge shows - for a show watched via the
    # episode browser those stay harmless no-ops, same as before this
    # badge existed for shows at all.
    watch_count_by_title = _badge_watch_counts(profile, titles)

    my_lists = list(WatchList.objects.filter(profile=profile).order_by("name"))
    list_membership = {tid: set() for tid in title_ids}
    for title_id, list_id in WatchListItem.objects.filter(
        watchlist__profile=profile, title_id__in=title_ids
    ).values_list("title_id", "watchlist_id"):
        list_membership[title_id].add(list_id)

    return {
        "watched_by_title": watched_by_title,
        "watch_count_by_title": watch_count_by_title,
        "my_lists": my_lists,
        "list_membership": list_membership,
    }


def discover_action_context(profile, items):
    """poster_action_context's counterpart for TMDB preview tiles
    (discover_tile.html - Movies & TV/Anime's grid, Dashboard's "Because
    you watched" row, title_detail's "similar" grid, search's TMDB-results
    section, a collection's movies) - these have no Title pk to key off,
    only a (media_type, tmdb_id) pair, and may or may not have a matching
    local Title row yet. Without this, a title already watched or listed
    (tracked previously, now reappearing on a Trending/Popular page or as
    a "similar" suggestion) always rendered as untracked - the whole
    reason this exists.

    One .filter(...).first() per item to find its local Title, not a
    batched __in - see views.search's own per-result TMDB dedupe check
    for why: a JSONField key-transform's value round-trips through
    SQLite's json_extract typed, silently breaking a str-vs-id membership
    check under __in. Bounded to a page's worth of items (~20-24), same
    as that existing per-result check.

    Matches on external_ids__tmdb_kind alongside external_ids__tmdb, not
    tmdb id alone - TMDB's movie and tv id numbering are separate
    namespaces, so an unrelated movie and tv show can share the same raw
    numeric id (confirmed live: a tv credit incorrectly matched, badged,
    and linked to an unrelated movie that happened to share its tmdb id
    before this check existed). tmdb_kind is set alongside tmdb on every
    Title that has one (trakt.py/simkl.py/nuvio.py/csv_import.py/
    _get_or_create_preview_title all pair them) - see media_type_for()'s
    own docstring for why tmdb_kind, not local media_type, is the
    authoritative "which TMDB catalog" signal (anime is tracked as
    media_type="anime" locally but matched via TMDB's tv catalog).

    Returns matched_title_by_key (Title or None - lets the template reuse
    poster_card_watched_button.html/poster_card_list_popover.html, the
    same partials a tracked title's own poster card uses, whenever a
    match exists) alongside the usual watched/list-membership dicts.

    Falls back to local media_type when tmdb_kind is missing entirely -
    titles matched/imported before tmdb_kind existed (or by any path that
    predates it) have a tmdb id but no tmdb_kind, and would otherwise
    never match here again, permanently showing as untracked on
    Trending/Popular/similar even though History shows them watched (a
    real reported case). Grouping TV and ANIME together for a "tv" item
    (not just an exact MediaType.TV match) keeps this safe against the
    exact bug tmdb_kind was added to prevent - a movie and a tv show
    sharing a raw numeric TMDB id - since both TV and ANIME only ever
    mean TMDB's tv catalog, never movie. Self-heals the match by writing
    the now-known tmdb_kind back, so this fallback is only ever needed
    once per title.
    """
    local_media_types_for_kind = {"movie": [MediaType.MOVIE], "tv": [MediaType.TV, MediaType.ANIME]}
    matched_title_by_key = {}
    for item in items:
        key = f"{item['media_type']}:{item['tmdb_id']}"
        match = Title.objects.filter(
            external_ids__tmdb=str(item["tmdb_id"]), external_ids__tmdb_kind=item["media_type"]
        ).first()
        if match is None:
            match = Title.objects.filter(
                external_ids__tmdb=str(item["tmdb_id"]),
                external_ids__tmdb_kind__isnull=True,
                media_type__in=local_media_types_for_kind.get(item["media_type"], []),
            ).first()
            if match is not None:
                match.external_ids = {**match.external_ids, "tmdb_kind": item["media_type"]}
                match.save(update_fields=["external_ids"])
        matched_title_by_key[key] = match

    matched_titles = [t for t in matched_title_by_key.values() if t is not None]
    title_ids = [t.pk for t in matched_titles]
    watched_title_ids = set(
        WatchEvent.objects.filter(profile=profile, title_id__in=title_ids).values_list("title_id", flat=True).distinct()
    )
    # The checkmark's ×N badge - same _badge_watch_counts poster_action_context
    # uses, see its own docstring for the movie/show distinction.
    badge_counts_by_title = _badge_watch_counts(profile, matched_titles)
    list_membership_by_title = {}
    for title_id, list_id in WatchListItem.objects.filter(
        watchlist__profile=profile, title_id__in=title_ids
    ).values_list("title_id", "watchlist_id"):
        list_membership_by_title.setdefault(title_id, set()).add(list_id)

    discover_watched = {}
    discover_watch_count = {}
    discover_list_membership = {}
    for key, title in matched_title_by_key.items():
        discover_watched[key] = bool(title and title.pk in watched_title_ids)
        discover_watch_count[key] = badge_counts_by_title.get(title.pk, 0) if title else 0
        discover_list_membership[key] = list_membership_by_title.get(title.pk, set()) if title else set()

    return {
        "discover_title_by_key": matched_title_by_key,
        "discover_watched": discover_watched,
        "discover_watch_count": discover_watch_count,
        "discover_list_membership": discover_list_membership,
    }


def person_personal_stats(profile, items, action_context):
    """The person detail page's "N of M watched"/average rating/watch
    time/co-watcher stats - computed against this household's own watch
    history, not anything TMDB provides. items is a person's credits,
    already deduped by tmdb_id across their Acting/Directing/Writing
    sections by the caller (views.person_detail) - a title the person
    both acted in and directed must only be counted once here (unlike
    the filmography display itself, which deliberately shows it in both
    of that person's department sections), or its watch time/rating
    would double-count. action_context is discover_action_context(profile,
    items)'s own return value - computed once by the caller (which also
    needs it to render each filmography section) and reused here instead
    of a second per-item Title lookup pass."""
    matched_titles = [t for t in action_context["discover_title_by_key"].values() if t is not None]
    matched_title_ids = [t.pk for t in matched_titles]

    watched_title_ids = set(
        WatchEvent.objects.filter(profile=profile, title_id__in=matched_title_ids)
        .values_list("title_id", flat=True)
        .distinct()
    )

    latest_ratings = []
    for title_id in watched_title_ids:
        rating = (
            WatchEvent.objects.filter(profile=profile, title_id=title_id, user_rating__isnull=False)
            .order_by("-watched_at")
            .values_list("user_rating", flat=True)
            .first()
        )
        if rating is not None:
            latest_ratings.append(rating)
    avg_rating = round(sum(latest_ratings) / len(latest_ratings), 1) if latest_ratings else None

    total_minutes = (
        WatchEvent.objects.filter(profile=profile, title_id__in=watched_title_ids)
        .aggregate(total=Sum(Coalesce("episode__runtime_minutes", "title__runtime_minutes", 0)))["total"]
        or 0
    )

    # share_activity-gated, same privacy convention social_activity() uses -
    # this stat exposes another profile's watch history, unlike
    # _recommend_context's unfiltered "other_profiles" (recommending
    # doesn't reveal what anyone's watched).
    co_watchers = []
    if watched_title_ids:
        for other in Profile.objects.exclude(pk=profile.pk).filter(share_activity=True).order_by("display_name"):
            their_watched_ids = set(
                WatchEvent.objects.filter(profile=other, title_id__in=watched_title_ids)
                .values_list("title_id", flat=True)
                .distinct()
            )
            overlap = len(watched_title_ids & their_watched_ids)
            if overlap:
                co_watchers.append({"profile": other, "count": overlap})

    return {
        "watched_count": len(watched_title_ids),
        "total_count": len(items),
        "avg_rating": avg_rating,
        "total_watch_time": format_duration(total_minutes) if total_minutes else None,
        "co_watchers": co_watchers,
    }
