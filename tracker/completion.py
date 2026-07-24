"""Infers WatchProgress completion and backfills runtime_minutes using
TMDB show/movie details (tracker/integrations/tmdb.py). Best-effort: any
TMDB lookup failure just skips that title rather than raising, consistent
with the rest of the TMDB integration.

Nothing populated WatchProgress or runtime_minutes during Trakt/CSV import
before this existed, which is why "Shows completed" and total watch time
showed 0/nothing regardless of how much was actually watched - those
stats were never wrong, there was just never any data behind them.
"""

from .integrations import tmdb
from .models import Episode, MediaType, WatchEvent, WatchListItem, WatchProgress


def _tmdb_id(title):
    return title.external_ids.get("tmdb")


def update_movie_runtime(title):
    tmdb_id = _tmdb_id(title)
    if not tmdb_id or title.runtime_minutes:
        return
    details = tmdb.get_movie_details(tmdb_id)
    if details and details.get("runtime"):
        title.runtime_minutes = details["runtime"]
        title.save(update_fields=["runtime_minutes"])


def _backfill_episode_runtimes(title, tmdb_id):
    """Fallback for episodes sync_show_completion's show-level pass didn't
    cover - TMDB's show-level episode_run_time is often empty (many shows,
    especially anime/foreign titles, never had it filled in) even when
    the season/episode endpoint has a real per-episode runtime, and this
    episode may also just be one of the show's own outliers the show-level
    "typical" figure doesn't represent. Only fetches seasons this profile
    actually has a still-missing episode in, not every season the show
    has ever aired, to keep this to a handful of calls per show."""
    # .order_by() clears Episode's own default Meta.ordering (by season,
    # then episode) before .distinct() - otherwise the episode column
    # rides along into the implicit ORDER BY/SELECT DISTINCT comparison,
    # and every episode number ends up its own "distinct" season.
    missing_seasons = sorted(
        Episode.objects.filter(title=title, runtime_minutes__isnull=True)
        .order_by()
        .values_list("season", flat=True)
        .distinct()
    )
    for season in missing_seasons:
        season_data = tmdb.get_season_details(tmdb_id, season)
        if not season_data:
            continue
        for ep in season_data["episodes"]:
            if ep.get("runtime"):
                Episode.objects.filter(
                    title=title, season=season, episode=ep["episode_number"], runtime_minutes__isnull=True
                ).update(runtime_minutes=ep["runtime"])


def sync_show_completion(profile, title):
    """Marks WatchProgress COMPLETED once a profile has logged at least as
    many distinct episodes of a show as TMDB reports it has in total.
    Also backfills Episode.runtime_minutes from TMDB's show-level typical
    duration for any episode that doesn't have one yet, then falls back
    to each episode's own runtime from the season endpoint for whatever
    that coarse pass didn't cover (see _backfill_episode_runtimes)."""
    tmdb_id = _tmdb_id(title)
    if not tmdb_id:
        return
    details = tmdb.get_tv_details(tmdb_id)
    if not details:
        return

    episode_run_time = details.get("episode_run_time")
    if episode_run_time:
        Episode.objects.filter(title=title, runtime_minutes__isnull=True).update(runtime_minutes=episode_run_time)
    _backfill_episode_runtimes(title, tmdb_id)

    total_episodes = details.get("number_of_episodes")
    if not total_episodes:
        return
    watched_episode_count = (
        WatchEvent.objects.filter(profile=profile, title=title, episode__isnull=False)
        .values("episode_id")
        .distinct()
        .count()
    )
    if watched_episode_count >= total_episodes:
        WatchProgress.objects.update_or_create(
            profile=profile, title=title, defaults={"status": WatchProgress.Status.COMPLETED}
        )


def sync_watchlist_removal(profile, title):
    """Trakt/Simkl-style behavior: once a title is finished - a movie
    watched at least once, or a show/anime fully watched per
    sync_show_completion's WatchProgress.COMPLETED - it comes off the
    profile's auto-managed Watchlist automatically. Only ever touches the
    WatchList flagged is_watchlist=True; custom lists (whatever they're
    named) are untouched, since this filters on the flag, not on name."""
    if title.media_type == MediaType.MOVIE:
        finished = WatchEvent.objects.filter(profile=profile, title=title).exists()
    else:
        finished = WatchProgress.objects.filter(
            profile=profile, title=title, status=WatchProgress.Status.COMPLETED
        ).exists()
    if finished:
        WatchListItem.objects.filter(watchlist__profile=profile, watchlist__is_watchlist=True, title=title).delete()
