"""Dashboard/Stats query helpers, kept out of views.py per
spool-django-handoff.md §5 ("compute in a model method or manager, not in
the template")."""

from datetime import timedelta

from django.db.models import Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import Episode, MediaType, ReleaseSchedule, WatchEvent, WatchListItem, WatchProgress


def current_streak(profile):
    dates = set(WatchEvent.objects.filter(profile=profile).values_list("watched_at__date", flat=True))
    streak, day = 0, timezone.localdate()
    while day in dates:
        streak += 1
        day -= timedelta(days=1)
    return streak


def continue_watching(profile, limit=8):
    items = []
    qs = (
        WatchProgress.objects.filter(profile=profile, status=WatchProgress.Status.WATCHING)
        .select_related("title", "current_episode")
        .order_by("-updated_at")[:limit]
    )
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


def recently_added_to_lists(profile, limit=3):
    return (
        WatchListItem.objects.filter(Q(watchlist__profile=profile) | Q(watchlist__is_shared=True))
        .select_related("title")
        .prefetch_related("title__ratings")
        .order_by("-added_at")[:limit]
    )
