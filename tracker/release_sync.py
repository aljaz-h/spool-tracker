"""Turns TMDB's per-title release info into ReleaseSchedule/Episode rows -
the sync job the ReleaseSchedule model's own docstring always assumed
would exist, but that never got built (see tracker/tasks.py's
sync_release_schedules, the Celery task that calls sync_title_releases in
a loop). Mirrors trakt.py/simkl.py's Episode.get_or_create idiom for
creating not-yet-aired episodes, and the same "a lookup failure never
blocks whatever it's attached to" philosophy tmdb.py's module docstring
states - a failure here just means this title's schedule doesn't update
this run, not that the whole batch fails."""

import logging
from datetime import datetime

from django.utils import timezone

from .integrations import tmdb
from .models import Episode, ReleaseSchedule

logger = logging.getLogger(__name__)


def _parse_tmdb_date(raw):
    """TMDB gives release/air dates as bare "YYYY-MM-DD", no time-of-day -
    mirrors csv_import.py's parse_date() date-only-string handling."""
    if not raw:
        return None
    try:
        dt = datetime.strptime(raw, "%Y-%m-%d")
    except ValueError:
        return None
    return timezone.make_aware(dt) if timezone.is_naive(dt) else dt


def _sync_tv_release(title, details):
    next_ep = details.get("next_episode_to_air")
    if not next_ep:
        return 0
    release_dt = _parse_tmdb_date(next_ep.get("air_date"))
    season, number = next_ep.get("season_number"), next_ep.get("episode_number")
    if not release_dt or season is None or number is None:
        return 0
    episode, _ = Episode.objects.get_or_create(
        title=title, season=season, episode=number, defaults={"name": next_ep.get("name") or ""}
    )
    release_type = (
        ReleaseSchedule.ReleaseType.SEASON_PREMIERE if number == 1 else ReleaseSchedule.ReleaseType.EPISODE
    )
    ReleaseSchedule.objects.update_or_create(
        title=title, episode=episode, release_type=release_type, defaults={"release_date": release_dt}
    )
    return 1


def _sync_movie_release(title, details):
    release_dt = _parse_tmdb_date(details.get("release_date"))
    if not release_dt or release_dt < timezone.now():
        return 0
    # episode is always NULL for a movie row, and NULL != NULL under the
    # unique constraint (see ReleaseSchedule's own Meta comment), so this
    # can't rely on update_or_create's constraint-matching - look the row
    # up explicitly first instead.
    existing = ReleaseSchedule.objects.filter(
        title=title, release_type=ReleaseSchedule.ReleaseType.MOVIE_RELEASE
    ).first()
    if existing:
        if existing.release_date != release_dt:
            existing.release_date = release_dt
            existing.save(update_fields=["release_date"])
    else:
        ReleaseSchedule.objects.create(
            title=title, release_type=ReleaseSchedule.ReleaseType.MOVIE_RELEASE, release_date=release_dt
        )
    return 1


def sync_title_releases(title):
    """Fetches title's current TMDB details and upserts whatever upcoming
    release (next episode, season premiere, or movie release) it implies.
    Returns the number of ReleaseSchedule rows touched (0 if the title has
    no TMDB id, TMDB has nothing scheduled, or anything failed)."""
    tmdb_id = title.external_ids.get("tmdb")
    if not tmdb_id:
        return 0
    try:
        media_type = tmdb.media_type_for(title)
        details = tmdb.get_full_details(media_type, tmdb_id)
        if not details:
            return 0
        if media_type == "movie":
            return _sync_movie_release(title, details)
        return _sync_tv_release(title, details)
    except Exception:
        logger.warning("release_sync: failed for title=%s", title.pk, exc_info=True)
        return 0
