"""Dashboard/Stats query helpers, kept out of views.py per
spool-django-handoff.md §5 ("compute in a model method or manager, not in
the template")."""

from datetime import timedelta

from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import Episode, MediaType, ReleaseSchedule, WatchEvent, WatchList, WatchListItem, WatchProgress


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
    qs = (
        ReleaseSchedule.objects.filter(
            title__watch_progress__profile=profile,
            title__watch_progress__status=WatchProgress.Status.WATCHING,
            release_date__gte=timezone.now(),
        )
        .select_related("title", "episode")
        .order_by("release_date")[:limit]
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
        "total_watch_hours": round(total_minutes / 60),
    }


def _visible_watchlist_items(profile, media_types=None):
    qs = WatchListItem.objects.filter(Q(watchlist__profile=profile) | Q(watchlist__is_shared=True))
    if media_types:
        qs = qs.filter(title__media_type__in=media_types)
    return qs.select_related("title").prefetch_related("title__ratings").order_by("-added_at").distinct()


def recently_added_to_lists(profile, limit=3):
    return _visible_watchlist_items(profile)[:limit]


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


def calendar_releases(profile, media_type=None, source="all"):
    """Upcoming releases for titles this profile is watching or has on any
    visible watchlist (own + shared, same visibility rule as everywhere
    else lists show up) — spool-handoff-addendum.md §1."""
    watching_q = Q(
        title__watch_progress__profile=profile, title__watch_progress__status=WatchProgress.Status.WATCHING
    )
    watchlist_q = Q(title__watchlist_items__watchlist__profile=profile) | Q(
        title__watchlist_items__watchlist__is_shared=True
    )
    if source == "watching":
        scope_q = watching_q
    elif source == "watchlist":
        scope_q = watchlist_q
    else:
        scope_q = watching_q | watchlist_q

    qs = (
        ReleaseSchedule.objects.filter(scope_q, release_date__gte=timezone.now())
        .select_related("title", "episode")
        .order_by("release_date")
        .distinct()
    )
    if media_type:
        qs = qs.filter(title__media_type=media_type)
    return qs


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
        "movies_watched": events.filter(title__media_type=MediaType.MOVIE).count(),
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


def genre_breakdown(profile, media_type):
    qs = (
        WatchEvent.objects.filter(profile=profile, title__media_type=media_type, title__genres__isnull=False)
        .values("title__genres__name")
        .annotate(count=Count("id"))
        .order_by("-count")
    )
    return [{"name": row["title__genres__name"], "count": row["count"]} for row in qs]


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
