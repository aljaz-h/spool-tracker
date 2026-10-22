"""Turns TMDB's per-title release info into ReleaseSchedule/Episode rows -
the sync job the ReleaseSchedule model's own docstring always assumed
would exist, but that never got built (see tracker/tasks.py's
sync_release_schedules, the Celery task that calls sync_title_releases in
a loop). Mirrors trakt.py/simkl.py's Episode.get_or_create idiom for
creating not-yet-aired episodes, and the same "a lookup failure never
blocks whatever it's attached to" philosophy tmdb.py's module docstring
states - a failure here just means this title's schedule doesn't update
this run, not that the whole batch fails.

TMDB only ever gives a bare date for a TV episode, no time-of-day or
timezone (see _parse_tmdb_date) - a TV release's release_date supplements
that with TVmaze's own airstamp when a matching TVmaze show can be found
(see _resolve_tvmaze_cache/_tvmaze_episode_times), setting
ReleaseSchedule.has_exact_time so the Calendar/"Up Next" can show a real
time instead of just a date. TVmaze has no movie data at all - movies
only ever get TMDB's date, has_exact_time always False for them. Same
never-blocks philosophy as everything else here: no TVmaze match, a
TVmaze outage, or skip_tvmaze=True (see tasks.sync_release_schedules'
own cheap-nightly-sync check) just means this sync falls back to TMDB's
date-only release_date, not that the sync fails."""

import logging
from datetime import datetime, timedelta

from django.utils import timezone

from .integrations import tmdb, tvmaze
from .models import Episode, ReleaseSchedule, TitleTvmazeCache

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


def _resolve_tvmaze_cache(title, tmdb_id):
    """Looks up (once ever) and caches which TVmaze show, if any, matches
    this title - the IMDb-id lookup only ever runs the first time a TV
    title's release schedule is synced; every later call just reads the
    cached row. tasks.sync_release_schedules' own updates-dict check
    answers a different question - whether the mapped show's EPISODE DATA
    needs re-fetching this run - the mapping itself, once resolved, never
    changes, so there's nothing to re-check here on later syncs."""
    cache, _ = TitleTvmazeCache.objects.get_or_create(title=title)
    if cache.fetch_attempted:
        return cache
    imdb_id = tmdb.get_tv_external_ids(tmdb_id)["imdb_id"]
    show = tvmaze.lookup_by_imdb(imdb_id) if imdb_id else None
    cache.tvmaze_id = show.get("id") if show else None
    cache.last_update_timestamp = show.get("updated") if show else None
    cache.fetch_attempted = True
    cache.last_synced_at = timezone.now()
    cache.save(update_fields=["tvmaze_id", "last_update_timestamp", "fetch_attempted", "last_synced_at"])
    return cache


def _tvmaze_episode_times(tvmaze_id):
    """{(season, episode): aware_datetime, ...} from every dated episode
    TVmaze has for this show - one call covers every season at once
    (unlike TMDB's own per-season endpoint), so _sync_tv_release only
    ever needs to call this once per sync regardless of how many TMDB
    seasons it ends up touching."""
    times = {}
    for ep in tvmaze.get_episodes(tvmaze_id):
        season, number, airstamp = ep.get("season"), ep.get("number"), ep.get("airstamp")
        if season is None or number is None or not airstamp:
            continue
        try:
            times[(season, number)] = datetime.fromisoformat(airstamp)
        except ValueError:
            continue
    return times


def _upsert_season_episodes(title, season_number, season_data, tvmaze_episode_times=None):
    """Upserts one ReleaseSchedule row per dated episode in season_data
    (both already-aired and upcoming - TMDB's season endpoint carries air
    dates for the whole season, not just what's left), so a weekly show
    shows every remaining Thursday at once instead of only the single
    next one, and already-aired episodes still populate the calendar's
    past view. tvmaze_episode_times (see _tvmaze_episode_times): when this
    exact (season, episode) has a TVmaze-sourced instant, that replaces
    TMDB's date-only release_dt and has_exact_time is set True - otherwise
    TMDB's date stands, same as before TVmaze existed. Returns how many
    dated episodes were processed (whether or not either row actually
    changed - same "touched" meaning release_sync.sync_title_releases'
    own docstring already promises).

    Batched into a handful of queries rather than one get_or_create +
    one update_or_create per episode (Sentry flagged the per-episode
    version as an N+1 - a 20+ episode season fired 60+ round trips for
    one title, nightly, across the whole library). Safe to bulk_create/
    bulk_update here specifically because nothing else in this codebase
    listens for a post_save/pre_save signal on Episode or ReleaseSchedule
    (confirmed - bulk operations skip both, unlike get_or_create/
    update_or_create, which this would silently break if that ever
    changed)."""
    tvmaze_episode_times = tvmaze_episode_times or {}
    dated = {}
    for ep_data in season_data.get("episodes") or []:
        number = ep_data.get("episode_number")
        release_dt = _parse_tmdb_date(ep_data.get("air_date"))
        if number is None or not release_dt:
            continue
        dated[number] = (ep_data.get("name") or "", release_dt)
    if not dated:
        return 0

    episodes_by_number = {
        e.episode: e
        for e in Episode.objects.filter(title=title, season=season_number, episode__in=dated)
    }
    to_create_episodes = [
        Episode(title=title, season=season_number, episode=number, name=name)
        for number, (name, _) in dated.items()
        if number not in episodes_by_number
    ]
    if to_create_episodes:
        Episode.objects.bulk_create(to_create_episodes)
        episodes_by_number.update({e.episode: e for e in to_create_episodes})

    schedules_by_key = {
        (rs.episode_id, rs.release_type): rs
        for rs in ReleaseSchedule.objects.filter(
            title=title, episode_id__in=[episodes_by_number[number].id for number in dated]
        )
    }

    to_create_schedules = []
    to_update_schedules = []
    for number, (_, release_dt) in dated.items():
        episode = episodes_by_number[number]
        release_type = (
            ReleaseSchedule.ReleaseType.SEASON_PREMIERE if number == 1 else ReleaseSchedule.ReleaseType.EPISODE
        )
        exact_dt = tvmaze_episode_times.get((season_number, number))
        final_dt = exact_dt or release_dt
        has_exact = exact_dt is not None
        existing = schedules_by_key.get((episode.id, release_type))
        if existing is None:
            to_create_schedules.append(
                ReleaseSchedule(
                    title=title, episode=episode, release_type=release_type,
                    release_date=final_dt, has_exact_time=has_exact,
                )
            )
        else:
            existing.release_date = final_dt
            existing.has_exact_time = has_exact
            to_update_schedules.append(existing)

    if to_create_schedules:
        ReleaseSchedule.objects.bulk_create(to_create_schedules)
    if to_update_schedules:
        ReleaseSchedule.objects.bulk_update(to_update_schedules, ["release_date", "has_exact_time"])

    return len(dated)


def _sync_tv_release(title, tmdb_id, details, skip_tvmaze=False):
    """Picks the season to pull full episode data for - next_episode_to_air's
    season when the show has one still coming, else last_episode_to_air's
    (an ended show or one between seasons still gets its most recent
    season's air dates, rather than nothing at all once there's no
    "next" episode) - then syncs every dated episode in it, not just a
    single one. Also backfills the previous season when this one only
    just started, so a season premiere within the last
    BACKFILL_PREVIOUS_SEASON_WITHIN_DAYS days doesn't leave a hole right
    before it on the calendar's past view. skip_tvmaze=True (see
    tasks.sync_release_schedules) skips the TVmaze episode-time fetch
    entirely for this run - today's TMDB-date-only behavior - used when
    the nightly bulk updates check already found nothing changed for
    this show since the last time it was fetched."""
    next_ep = details.get("next_episode_to_air")
    last_ep = details.get("last_episode_to_air")
    anchor = next_ep or last_ep
    if not anchor or anchor.get("season_number") is None:
        return 0
    season_number = anchor["season_number"]

    tvmaze_episode_times = {}
    if not skip_tvmaze:
        tvmaze_cache = _resolve_tvmaze_cache(title, tmdb_id)
        if tvmaze_cache.tvmaze_id:
            tvmaze_episode_times = _tvmaze_episode_times(tvmaze_cache.tvmaze_id)

    season_data = tmdb.get_season_details(tmdb_id, season_number)
    if not season_data:
        return 0
    touched = _upsert_season_episodes(title, season_number, season_data, tvmaze_episode_times)

    if season_number > 1:
        episodes = season_data.get("episodes") or []
        premiere_dt = min(
            (dt for dt in (_parse_tmdb_date(e.get("air_date")) for e in episodes) if dt), default=None
        )
        if premiere_dt and premiere_dt >= timezone.now() - timedelta(days=BACKFILL_PREVIOUS_SEASON_WITHIN_DAYS):
            previous_season_data = tmdb.get_season_details(tmdb_id, season_number - 1)
            if previous_season_data:
                touched += _upsert_season_episodes(
                    title, season_number - 1, previous_season_data, tvmaze_episode_times
                )

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


def sync_title_releases(title, skip_tvmaze=False):
    """Fetches title's current TMDB details and upserts whatever release
    schedule it implies (a movie's release date, or a TV season's full set
    of dated episodes - see _sync_tv_release). Returns the number of
    ReleaseSchedule rows touched (0 if the title has no TMDB id, TMDB has
    nothing scheduled, or anything failed). skip_tvmaze: see
    _sync_tv_release/tasks.sync_release_schedules - ignored for a movie,
    which never has TVmaze data to skip."""
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
        return _sync_tv_release(title, tmdb_id, details, skip_tvmaze=skip_tvmaze)
    except Exception:
        logger.warning("release_sync: failed for title=%s", title.pk, exc_info=True)
        return 0
