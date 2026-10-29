"""The title page's watch-progress card for a show or anime: how far through
the released episodes a profile is, the time already watched, the time
still to go, and the episode counts.

Only released episodes count - an episode that has not aired is neither in
the total nor in the time left. The numbers are worked out from the live
WatchEvents every time the card renders, so they follow a watch however it
arrived (a button here, Trakt, Simkl, Nuvio, the Scrobble API, an import).
"""

from django.db.models import Avg

from .models import Episode, MediaType, WatchEvent

DEFAULT_EPISODE_MINUTES = 45
DEFAULT_ANIME_EPISODE_MINUTES = 24


def aired_episode_count(details):
    """How many episodes of a show have aired, from TMDB's season episode
    counts and its last aired episode (specials are not counted). A show
    with no last-aired episode that has ended counts all of its episodes;
    one that has not started counts none."""
    last = details.get("last_episode_to_air") or {}
    if last.get("season_number") and last.get("episode_number"):
        season_counts = dict(details.get("seasons") or [])
        before = sum(count for number, count in season_counts.items() if number < last["season_number"])
        return before + last["episode_number"]
    if (details.get("status") or "") in ("Ended", "Canceled"):
        return details.get("number_of_episodes") or 0
    return 0


def _typical_minutes(title, details):
    if details.get("episode_runtime"):
        return details["episode_runtime"]
    local = Episode.objects.filter(title=title, runtime_minutes__isnull=False).aggregate(avg=Avg("runtime_minutes"))["avg"]
    if local:
        return round(local)
    return DEFAULT_ANIME_EPISODE_MINUTES if title.media_type == MediaType.ANIME else DEFAULT_EPISODE_MINUTES


ENDED_STATUSES = ("Ended", "Canceled")


def build(profile, title, details):
    """{"percent", "watched", "total", "left", "watched_minutes",
    "left_minutes", "complete", "state"} or None when there is nothing to
    show (a movie, a title TMDB knows nothing about, or nothing aired yet).
    state is what the card says: "start" (nothing watched yet), "watching"
    (partway), "caught_up" (everything released is watched and the show is
    still airing) or "finished" (the same, and the show has ended or been
    cancelled - taken from TMDB's own status, never a toggle)."""
    if title.media_type == MediaType.MOVIE or not details or details.get("media_type") == "movie":
        return None
    total = aired_episode_count(details)
    if total <= 0:
        return None
    last = details.get("last_episode_to_air") or {}
    counts = dict(details.get("seasons") or [])
    rows = (
        WatchEvent.objects.filter(profile=profile, title=title, episode__isnull=False, episode__season__gt=0)
        .values_list("episode__season", "episode__episode", "episode__runtime_minutes")
        .distinct()
    )
    typical = _typical_minutes(title, details)
    watched_minutes, seen = 0, set()
    for season, number, runtime in rows:
        key = (season, number)
        if key in seen:
            continue
        # Only released episodes: inside the season's known size, and not
        # beyond the last one that has aired.
        if counts.get(season) is not None and number > counts[season]:
            continue
        if last.get("season_number") and (season, number) > (last["season_number"], last["episode_number"]):
            continue
        seen.add(key)
        watched_minutes += runtime or typical
    watched = min(len(seen), total)
    left = total - watched
    if watched == 0:
        state = "start"
    elif left:
        state = "watching"
    else:
        state = "finished" if (details.get("status") or "") in ENDED_STATUSES else "caught_up"
    return {
        "state": state,
        "percent": min(100, round(watched / total * 100)),
        "watched": watched,
        "total": total,
        "left": left,
        "watched_minutes": watched_minutes,
        "left_minutes": left * typical,
        "complete": left == 0,
    }
