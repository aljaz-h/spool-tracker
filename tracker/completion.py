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
from .models import Episode, WatchEvent, WatchProgress


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


def sync_show_completion(profile, title):
    """Marks WatchProgress COMPLETED once a profile has logged at least as
    many distinct episodes of a show as TMDB reports it has in total.
    Also backfills Episode.runtime_minutes from TMDB's show-level typical
    duration for any episode that doesn't have one yet."""
    tmdb_id = _tmdb_id(title)
    if not tmdb_id:
        return
    details = tmdb.get_tv_details(tmdb_id)
    if not details:
        return

    episode_run_time = details.get("episode_run_time")
    if episode_run_time:
        Episode.objects.filter(title=title, runtime_minutes__isnull=True).update(runtime_minutes=episode_run_time)

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
