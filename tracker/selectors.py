"""Dashboard/Stats query helpers, kept out of views.py per
spool-django-handoff.md §5 ("compute in a model method or manager, not in
the template")."""

from datetime import timedelta

from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce, ExtractHour
from django.utils import timezone

from .models import Episode, MediaType, ReleaseSchedule, Title, WatchEvent, WatchList, WatchListItem, WatchProgress


def current_streak(profile):
    dates = set(WatchEvent.objects.filter(profile=profile).values_list("watched_at__date", flat=True))
    streak, day = 0, timezone.localdate()
    while day in dates:
        streak += 1
        day -= timedelta(days=1)
    return streak


def longest_streak(profile):
    dates = sorted(set(WatchEvent.objects.filter(profile=profile).values_list("watched_at__date", flat=True)))
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


def continue_watching(profile, media_types=None, limit=8):
    items = []
    qs = WatchProgress.objects.filter(profile=profile, status=WatchProgress.Status.WATCHING)
    if media_types:
        qs = qs.filter(title__media_type__in=media_types)
    qs = qs.select_related("title", "current_episode").order_by("-updated_at")
    if limit:
        qs = qs[:limit]
    for progress in qs:
        title = progress.title
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
                total_eps = Episode.objects.filter(title=title, season=ep.season).count()
                if total_eps:
                    percent = min(100, round(ep.episode / total_eps * 100))
                    caption = f"S{ep.season}E{ep.episode} of {total_eps}"
                else:
                    caption = f"S{ep.season}E{ep.episode}"
        items.append({"title": title, "percent": percent, "caption": caption})
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


def up_next(profile, limit=3):
    """Dashboard's "Up Next" card. Matches calendar_releases()'s default
    scope - any WatchProgress status, or plain watch history, not just
    WATCHING (see calendar_releases()'s docstring for why WATCHING alone
    isn't enough in practice). .distinct() is required here, unlike the
    old WatchProgress-only query: WatchProgress is at most one row per
    profile+title, but WatchEvent isn't (every episode watched is its own
    row), so joining through it can multiply-match the same
    ReleaseSchedule row once per watch event without it."""
    qs = (
        ReleaseSchedule.objects.filter(
            Q(title__watch_progress__profile=profile) | Q(title__watch_events__profile=profile),
            release_date__gte=timezone.now(),
        )
        .select_related("title", "episode")
        .order_by("release_date")
        .distinct()[:limit]
    )
    items = []
    for rs in qs:
        if rs.episode:
            caption = f"Season {rs.episode.season}, Episode {rs.episode.episode}"
        else:
            caption = rs.get_release_type_display()
        items.append({"title": rs.title, "caption": caption, "when": _when_label(rs.release_date)})
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
    return {
        "streak": current_streak(profile),
        "movies_this_year": movies_this_year,
        "shows_completed": shows_completed,
        # "217d 4h 3m" style, matching the Stats page's own watch-time
        # breakdown format (_format_duration) instead of a flat "7342h" -
        # the two pages showing the same kind of stat differently read as
        # inconsistent.
        "total_watch_time": _format_duration(total_minutes),
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


def stats_overview(profile):
    """Lifetime totals for the Stats page hero + donut — deliberately not
    year-scoped, unlike Dashboard's quick_stats()."""
    events = WatchEvent.objects.filter(profile=profile)
    total_minutes = (
        events.aggregate(total=Sum(Coalesce("episode__runtime_minutes", "title__runtime_minutes", 0)))["total"] or 0
    )
    total_hours = round(total_minutes / 60)

    cur, longest = current_streak(profile), longest_streak(profile)

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


def _format_duration(total_minutes):
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

    def bucket(events):
        result = {}
        combined_minutes = 0
        for media_type in [MediaType.MOVIE, MediaType.TV, MediaType.ANIME]:
            type_events = events.filter(title__media_type=media_type)
            minutes = (
                type_events.aggregate(
                    total=Sum(Coalesce("episode__runtime_minutes", "title__runtime_minutes", 0))
                )["total"]
                or 0
            )
            result[media_type] = {"duration": _format_duration(minutes), "count": type_events.count()}
            combined_minutes += minutes
        combined_hours = round(combined_minutes / 60)
        result["combined"] = {"hours": combined_hours, "days": round(combined_hours / 24, 1)}
        return result

    events = WatchEvent.objects.filter(profile=profile)
    return {
        "last_30_days": bucket(events.filter(watched_at__gte=timezone.now() - timedelta(days=30))),
        "all_time": bucket(events),
    }


def _format_duration_compact(total_minutes):
    """Single-unit duration for the genre chart's badges - "86d"/"18h"/
    "45m", picking the largest unit that's still >= 1. Distinct from
    _format_duration's multi-unit "Xd Xh Xm", which is too long to sit
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
        r["display"] = _format_duration_compact(r["value"]) if metric == "duration" else f"{r['value']} {unit}"
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
                "duration": _format_duration(minutes),
            }
        )

    peak_minutes = max((d["minutes"] for d in day_rows), default=0)
    for d in day_rows:
        d["height_pct"] = round(d["minutes"] / peak_minutes * 100) if peak_minutes else 0

    return {"days": day_rows, "peak_minutes": peak_minutes, "peak_duration": _format_duration(peak_minutes)}


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
        "average_duration": _format_duration(current_avg),
        "delta_positive": delta >= 0,
        "delta_label": f"{'+' if delta > 0 else '-'}{_format_duration(abs(delta))}" if delta else None,
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


def title_local_context(profile, title):
    """The title detail page's own-data half - watch/rating/list state -
    kept separate from the TMDB-sourced half (overview/cast/similar,
    fetched directly in the view) since this part never needs a network
    call and stays correct even without a TMDB_API_KEY configured."""
    progress = WatchProgress.objects.filter(profile=profile, title=title).select_related("current_episode").first()
    recent_events = list(
        WatchEvent.objects.filter(profile=profile, title=title).select_related("episode").order_by("-watched_at")[:10]
    )
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
    # Deliberately narrower than "has any recent_events" - a show with
    # only individual episodes watched (no whole-title mark) shouldn't
    # flip the header's primary Watched toggle green, which would falsely
    # claim the whole thing is done. Scoped to the plain (episode-less)
    # events that toggle itself owns (title_mark_watched/title_unmark_watched),
    # not the episode browser's own separate, always-append rewatch log.
    is_watched = WatchEvent.objects.filter(profile=profile, title=title, episode__isnull=True).exists()
    return {
        "progress": progress,
        "recent_events": recent_events,
        "latest_rating": latest_rating,
        "my_lists": my_lists,
        "in_list_ids": in_list_ids,
        "is_watched": is_watched,
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

    my_lists = list(WatchList.objects.filter(profile=profile).order_by("name"))
    list_membership = {tid: set() for tid in title_ids}
    for title_id, list_id in WatchListItem.objects.filter(
        watchlist__profile=profile, title_id__in=title_ids
    ).values_list("title_id", "watchlist_id"):
        list_membership[title_id].add(list_id)

    return {"watched_by_title": watched_by_title, "my_lists": my_lists, "list_membership": list_membership}


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

    Returns matched_title_by_key (Title or None - lets the template reuse
    poster_card_watched_button.html/poster_card_list_popover.html, the
    same partials a tracked title's own poster card uses, whenever a
    match exists) alongside the usual watched/list-membership dicts.
    """
    matched_title_by_key = {}
    for item in items:
        key = f"{item['media_type']}:{item['tmdb_id']}"
        matched_title_by_key[key] = Title.objects.filter(external_ids__tmdb=str(item["tmdb_id"])).first()

    title_ids = [t.pk for t in matched_title_by_key.values() if t is not None]
    watched_title_ids = set(
        WatchEvent.objects.filter(profile=profile, title_id__in=title_ids).values_list("title_id", flat=True).distinct()
    )
    list_membership_by_title = {}
    for title_id, list_id in WatchListItem.objects.filter(
        watchlist__profile=profile, title_id__in=title_ids
    ).values_list("title_id", "watchlist_id"):
        list_membership_by_title.setdefault(title_id, set()).add(list_id)

    discover_watched = {}
    discover_list_membership = {}
    for key, title in matched_title_by_key.items():
        discover_watched[key] = bool(title and title.pk in watched_title_ids)
        discover_list_membership[key] = list_membership_by_title.get(title.pk, set()) if title else set()

    return {
        "discover_title_by_key": matched_title_by_key,
        "discover_watched": discover_watched,
        "discover_list_membership": discover_list_membership,
    }
