"""Who is watching something right now, as the Friends menu and a friend's
popup show it.

Nuvio is the only source: it reports how far into a title a profile is, and
nothing else here tells whether anyone is actually at the screen. So the
Nuvio sync (tasks.sync_nuvio_history, every 30 minutes by default) calls
refresh_nuvio_watching() once per profile, which compares the profile's latest
in-progress item with the snapshot kept from the previous sync:

  - the percentage rose, or the title / season / episode changed -> watching
    (the snapshot is updated and the change time is stamped);
  - nothing moved -> not watching;
  - the item is finished, marked watched, or gone from continue-watching -> not
    watching;
  - an item with no snapshot yet (the first sync, or a title just started) is
    watching straight away; it stops showing once a later sync sees the same
    percentage.

The answer is stored on the Profile (watching_title, watching_season,
watching_episode, watching_percent, watching_changed_at, is_watching) and read
as it is - nothing is worked out when a page loads. The one thing a page does
check is how old the last change is (Profile.watching_now), so a sync that has
stopped doesn't leave a friend "watching" for days.
"""

from django.utils import timezone

from .models import WatchProgress


def progress_percent(row):
    """How far through the item (0-100, one decimal), from the player's own
    duration when it gave one, else the title's / episode's runtime. None when
    neither is known - there is nothing to compare then."""
    duration = row.duration_seconds
    if not duration:
        runtime = (row.current_episode.runtime_minutes if row.current_episode_id else None) or row.title.runtime_minutes
        duration = runtime * 60 if runtime else None
    if not duration:
        return None
    return round(min(100.0, row.position_seconds / duration * 100), 1)


def refresh_nuvio_watching(profile, now=None):
    """Update `profile`'s stored watching state from its in-progress items
    (already written by the sync) and return whether it is watching now."""
    now = now or timezone.now()
    row = (
        WatchProgress.objects.filter(profile=profile, status=WatchProgress.Status.WATCHING)
        .select_related("title", "current_episode")
        .first()  # newest activity first (WatchProgress.Meta.ordering)
    )
    percent = progress_percent(row) if row is not None else None
    if row is None or percent is None:
        # Finished, marked watched, or no longer in continue-watching.
        if profile.is_watching:
            profile.is_watching = False
            profile.save(update_fields=["is_watching"])
        return False

    episode = row.current_episode
    season = episode.season if episode else None
    number = episode.episode if episode else None
    first_look = profile.watching_title_id is None
    same_item = (
        profile.watching_title_id == row.title_id
        and profile.watching_season == season
        and profile.watching_episode == number
    )
    moved = first_look or not same_item or (profile.watching_percent is not None and percent > profile.watching_percent)

    profile.watching_title = row.title
    profile.watching_season = season
    profile.watching_episode = number
    profile.watching_percent = percent
    profile.is_watching = moved
    fields = ["watching_title", "watching_season", "watching_episode", "watching_percent", "is_watching"]
    if moved:
        profile.watching_changed_at = now
        fields.append("watching_changed_at")
    profile.save(update_fields=fields)
    return moved
