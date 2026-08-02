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
from datetime import datetime, timedelta

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


# How far back a freshly-premiered season's previous season is still
# worth pulling, so the calendar's past view isn't cut off right at this
# season's own premiere date - see _sync_tv_release.
BACKFILL_PREVIOUS_SEASON_WITHIN_DAYS = 60


def _upsert_season_episodes(title, season_number, season_data):
    """Upserts one ReleaseSchedule row per dated episode in season_data
    (both already-aired and upcoming - TMDB's season endpoint carries air
    dates for the whole season, not just what's left), so a weekly show
    shows every remaining Thursday at once instead of only the single
    next one, and already-aired episodes still populate the calendar's
    past view. Returns how many rows were touched."""
    touched = 0
    for ep_data in season_data.get("episodes") or []:
        number = ep_data.get("episode_number")
        release_dt = _parse_tmdb_date(ep_data.get("air_date"))
        if number is None or not release_dt:
            continue
        episode, _ = Episode.objects.get_or_create(
            title=title, season=season_number, episode=number, defaults={"name": ep_data.get("name") or ""}
        )
        release_type = (
            ReleaseSchedule.ReleaseType.SEASON_PREMIERE if number == 1 else ReleaseSchedule.ReleaseType.EPISODE
        )
        ReleaseSchedule.objects.update_or_create(
            title=title, episode=episode, release_type=release_type, defaults={"release_date": release_dt}
        )
        touched += 1
    return touched


def _sync_tv_release(title, tmdb_id, details):
    """Picks the season to pull full episode data for - next_episode_to_air's
    season when the show has one still coming, else last_episode_to_air's
    (an ended show or one between seasons still gets its most recent
    season's air dates, rather than nothing at all once there's no
    "next" episode) - then syncs every dated episode in it, not just a
    single one. Also backfills the previous season when this one only
    just started, so a season premiere within the last
    BACKFILL_PREVIOUS_SEASON_WITHIN_DAYS days doesn't leave a hole right
    before it on the calendar's past view."""
    next_ep = details.get("next_episode_to_air")
    last_ep = details.get("last_episode_to_air")
    anchor = next_ep or last_ep
    if not anchor or anchor.get("season_number") is None:
        return 0
    season_number = anchor["season_number"]

    season_data = tmdb.get_season_details(tmdb_id, season_number)
    if not season_data:
        return 0
    touched = _upsert_season_episodes(title, season_number, season_data)

    if season_number > 1:
        episodes = season_data.get("episodes") or []
        premiere_dt = min(
            (dt for dt in (_parse_tmdb_date(e.get("air_date")) for e in episodes) if dt), default=None
        )
        if premiere_dt and premiere_dt >= timezone.now() - timedelta(days=BACKFILL_PREVIOUS_SEASON_WITHIN_DAYS):
            previous_season_data = tmdb.get_season_details(tmdb_id, season_number - 1)
            if previous_season_data:
                touched += _upsert_season_episodes(title, season_number - 1, previous_season_data)

    return touched


def _sync_movie_release(title, details):
    release_dt = _parse_tmdb_date(details.get("release_date"))
    if not release_dt:
        return 0
    # episode is always NULL for a movie row, and NULL != NULL under the
    # unique constraint (see ReleaseSchedule's own Meta comment), so this
    # can't rely on update_or_create's constraint-matching - look the row
    # up explicitly first instead. Upserted regardless of whether
    # release_dt is already in the past, same reasoning as the TV side
    # backfilling aired episodes - a movie added to a list after its own
    # release should still show up on the calendar's past view instead
    # of never getting a row at all.
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
    """Fetches title's current TMDB details and upserts whatever release
    schedule it implies (a movie's release date, or a TV season's full set
    of dated episodes - see _sync_tv_release). Returns the number of
    ReleaseSchedule rows touched (0 if the title has no TMDB id, TMDB has
    nothing scheduled, or anything failed)."""
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
        return _sync_tv_release(title, tmdb_id, details)
    except Exception:
        logger.warning("release_sync: failed for title=%s", title.pk, exc_info=True)
        return 0
