"""Infers WatchProgress completion and backfills runtime_minutes using
TMDB show/movie details (tracker/integrations/tmdb.py). Best-effort: any
TMDB lookup failure just skips that title rather than raising, consistent
with the rest of the TMDB integration.

Nothing populated WatchProgress or runtime_minutes during Trakt/CSV import
before this existed, which is why "Shows completed" and total watch time
showed 0/nothing regardless of how much was actually watched - those
stats were never wrong, there was just never any data behind them.
"""

from django.db.models import Q

from .integrations import tmdb
from .models import Episode, MediaType, Profile, WatchEvent, WatchListItem, WatchProgress


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


def _next_episode_after(title, episode, details=None):
    """Return/create the next episode after ``episode``.

    The local Episode table is sparse: marking S1E1 creates S1E1, but not
    necessarily S1E2.  The Watching row, however, should point at the next
    episode to watch.  Prefer an existing local row, then materialize the
    next TMDB episode when possible.
    """
    local_next = (
        Episode.objects.filter(title=title)
        .filter(Q(season__gt=episode.season) | Q(season=episode.season, episode__gt=episode.episode))
        .order_by("season", "episode")
        .first()
    )
    if local_next:
        return local_next

    tmdb_id = _tmdb_id(title)
    if not tmdb_id:
        return None
    details = details or tmdb.get_tv_details(tmdb_id)
    for season_info in sorted((details or {}).get("seasons") or [], key=lambda s: s.get("season_number") or 0):
        season_number = season_info.get("season_number")
        if season_number is None or season_number < episode.season:
            continue
        season_data = tmdb.get_season_details(tmdb_id, season_number)
        if not season_data:
            continue
        for ep_data in sorted(season_data.get("episodes") or [], key=lambda e: e.get("episode_number") or 0):
            episode_number = ep_data.get("episode_number")
            if episode_number is None or (season_number, episode_number) <= (episode.season, episode.episode):
                continue
            next_episode, _ = Episode.objects.get_or_create(
                title=title,
                season=season_number,
                episode=episode_number,
                defaults={
                    "name": ep_data.get("name") or "",
                    "runtime_minutes": ep_data.get("runtime"),
                },
            )
            return next_episode
    return None


def _ensure_watching(profile, title, details=None):
    """Puts a partially-watched show on the Dashboard's Watching row (a
    WATCHING WatchProgress row pointing at the next episode to watch),
    so an episode marked in Spool alone shows up there without needing a
    player like Nuvio to have reported progress first. An existing row is
    left alone unless the next episode after the latest watched episode is later than its
    current_episode (Nuvio's own in-progress episode can be ahead of
    what's been marked watched, and shouldn't be pulled backward); a
    DROPPED row stays dropped."""
    latest = (
        WatchEvent.objects.filter(profile=profile, title=title, episode__isnull=False)
        .select_related("episode")
        .order_by("-episode__season", "-episode__episode")
        .first()
    )
    if latest is None:
        return
    resume_episode = _next_episode_after(title, latest.episode, details) or latest.episode
    progress = WatchProgress.objects.filter(profile=profile, title=title).first()
    if progress is None:
        WatchProgress.objects.create(
            profile=profile,
            title=title,
            status=WatchProgress.Status.WATCHING,
            current_episode=resume_episode,
        )
        return
    if progress.status != WatchProgress.Status.WATCHING:
        return
    if resume_episode is not None and (
        progress.current_episode_id is None
        or (resume_episode.season, resume_episode.episode)
        > (progress.current_episode.season, progress.current_episode.episode)
    ):
        progress.current_episode = resume_episode
        progress.save(update_fields=["current_episode", "updated_at"])


def sync_show_completion(profile, title, ensure_watching=False):
    """Marks WatchProgress COMPLETED once a profile has logged at least as
    many distinct episodes of a show as TMDB reports it has in total.
    Also backfills Episode.runtime_minutes from TMDB's show-level typical
    duration for any episode that doesn't have one yet, then falls back
    to each episode's own runtime from the season endpoint for whatever
    that coarse pass didn't cover (see _backfill_episode_runtimes).

    ensure_watching is only passed by the in-app "mark episode watched"
    actions - a partially-watched show then also lands on the Watching
    row (see _ensure_watching). Imports/syncs leave it off so a
    mid-series title pulled in from Trakt/Simkl/CSV doesn't suddenly
    flood the row, or resurrect one that was dismissed from it."""
    watched_episode_count = (
        WatchEvent.objects.filter(profile=profile, title=title, episode__isnull=False)
        .values("episode_id")
        .distinct()
        .count()
    )
    if watched_episode_count == 0:
        # Nothing left watched (every episode was unmarked/deleted): drop
        # the COMPLETED row that no longer qualifies, and any WATCHING row
        # with no playback position - that's the shape _ensure_watching
        # creates for an episode marked in-app, whereas a player like
        # Nuvio always reports a real position. Without this, the leftover
        # row keeps the show in Up Next/Calendar (scoped to "any
        # WatchProgress row") so its upcoming episodes appear for a show
        # with no watch history at all. DROPPED rows are left alone.
        # Runs before the TMDB lookups so it also works with no tmdb_id or
        # no TMDB response.
        WatchProgress.objects.filter(
            profile=profile,
            title=title,
            status__in=[WatchProgress.Status.COMPLETED, WatchProgress.Status.WATCHING],
            position_seconds=0,
        ).delete()
    tmdb_id = _tmdb_id(title)
    if not tmdb_id:
        if ensure_watching:
            _ensure_watching(profile, title)
        return
    details = tmdb.get_tv_details(tmdb_id)
    if not details:
        if ensure_watching:
            _ensure_watching(profile, title)
        return

    episode_run_time = details.get("episode_run_time")
    if episode_run_time:
        Episode.objects.filter(title=title, runtime_minutes__isnull=True).update(runtime_minutes=episode_run_time)
    _backfill_episode_runtimes(title, tmdb_id)

    total_episodes = details.get("number_of_episodes")
    if not total_episodes:
        if ensure_watching:
            _ensure_watching(profile, title, details)
        return
    if watched_episode_count >= total_episodes:
        WatchProgress.objects.update_or_create(
            profile=profile, title=title, defaults={"status": WatchProgress.Status.COMPLETED}
        )
    elif watched_episode_count:
        WatchProgress.objects.filter(profile=profile, title=title, status=WatchProgress.Status.COMPLETED).update(
            status=WatchProgress.Status.WATCHING
        )
        if ensure_watching:
            _ensure_watching(profile, title, details)


def resync_completed_profiles(title):
    """Re-validates every profile's WatchProgress.COMPLETED for title
    against TMDB's *current* episode count - sync_show_completion above
    only ever runs when a profile takes a fresh watch action (marks/
    unmarks an episode), so a show correctly marked COMPLETED at the
    time stays marked that way even once TMDB reports more episodes
    later (a currently-airing show renewed, or simply continuing to air
    past where a profile had caught up) - nothing re-checks it until
    that profile happens to watch something new for this exact title.
    Confirmed as a real, reported case, not hypothetical: a show marked
    complete when it only had N aired episodes kept showing the same
    "fully watched" treatment after being picked up for N+more, despite
    the profile demonstrably not being caught up anymore.

    Hooked into tasks.sync_title_release (the nightly per-title release
    sync, which already re-fetches this exact title's fresh TMDB details
    for every title with any WatchProgress row at all - see
    selectors.titles_needing_release_sync) rather than run on its own
    schedule - piggybacking on a sync that already has to happen anyway
    for the same titles, instead of a second nightly TMDB pass over the
    same set. The per-profile sync_show_completion call below still
    makes its own get_tv_details call - a second one for the same title
    on the same run - but that hits get_tv_details' own 6h cache instead
    of a second live request, not worth threading the already-fetched
    details dict through just to skip a cache hit."""
    completed_profile_ids = WatchProgress.objects.filter(
        title=title, status=WatchProgress.Status.COMPLETED
    ).values_list("profile_id", flat=True)
    for profile in Profile.objects.filter(id__in=completed_profile_ids):
        sync_show_completion(profile, title)


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
