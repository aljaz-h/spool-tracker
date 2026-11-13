"""What a list is made of, how long it takes to watch and how far through it
a profile is - the stats strip on a list's page, the mini version on the
Lists cards, and the runtime / time-left line on each tile.

Everything is per profile (the viewer's own watches) and worked out from the
live WatchEvents, so it follows a watch however it arrived. A movie counts
its runtime; a show or anime counts its unwatched, *aired* episodes times an
average episode length (unaired episodes are never counted, only mentioned).
The show side needs TMDB's episode counts, which are cached for hours; the
per-title result is cached too, keyed by how many episodes were watched, so a
watch changes the key and the number follows at once.
"""

import math
from concurrent.futures import ThreadPoolExecutor

from django.core.cache import cache

from . import instance_config
from .integrations import tmdb
from .models import MediaType, WatchEvent
from .planner import DEFAULT_ANIME_EPISODE_MINUTES, DEFAULT_EPISODE_MINUTES, DEFAULT_MOVIE_MINUTES
from .show_progress import aired_episode_count

INFO_TTL = 6 * 60 * 60
POOL_WORKERS = 8
TYPE_ORDER = (MediaType.MOVIE, MediaType.TV, MediaType.ANIME)
TYPE_LABELS = {MediaType.MOVIE: "Movies", MediaType.TV: "TV", MediaType.ANIME: "Anime"}
# How the composition note names a type in a sentence ("mostly movies and TV").
TYPE_WORDS = {MediaType.MOVIE: "movies", MediaType.TV: "TV", MediaType.ANIME: "anime"}
MOSTLY_SHARE = 0.8
NOTE_SHARE = 0.25


# --- durations ---------------------------------------------------------------------------


def hm_full(minutes):
    """612h 10m, 5h, 45m, 0h - always exact."""
    hours, mins = divmod(int(round(minutes)), 60)
    if hours == 0 and mins:
        return f"{mins}m"
    return f"{hours}h {mins}m" if mins else f"{hours}h"


def _rounded_hours(minutes):
    return f"{round(minutes / 60):,}h"


def hm_headline(minutes):
    """The big totals: exact below 100 hours (68h 40m), whole hours above (1,418h)."""
    return _rounded_hours(minutes) if minutes >= 6000 else hm_full(minutes)


def hm_short(minutes):
    """The progress block: exact below a day (17h 30m), whole hours above (143h)."""
    return _rounded_hours(minutes) if minutes >= 1440 else hm_full(minutes)


def days_note(minutes):
    days = round(minutes / 1440)
    return f"about {days} day{'s' if days != 1 else ''}" if days >= 1 else ""


# --- per title ---------------------------------------------------------------------------


def _is_movie(title):
    return title.media_type == MediaType.MOVIE or tmdb.media_type_for(title) == "movie"


def _movie_info(title, watched):
    minutes = title.runtime_minutes or DEFAULT_MOVIE_MINUTES
    return {
        "kind": title.media_type, "total": minutes, "watched_min": minutes if watched else 0,
        "left_min": 0 if watched else minutes, "unaired": 0, "done": watched, "partial": False, "known": True,
    }


def _show_info(title, details, watched_n):
    default = DEFAULT_ANIME_EPISODE_MINUTES if title.media_type == MediaType.ANIME else DEFAULT_EPISODE_MINUTES
    if not details:
        # TMDB has nothing (no key, down, unknown id): count what was watched, say nothing about the rest.
        return {
            "kind": title.media_type, "total": watched_n * default, "watched_min": watched_n * default,
            "left_min": 0, "unaired": 0, "done": False, "partial": False, "known": False,
        }
    aired = aired_episode_count(details)
    average = details.get("episode_runtime") or default
    left = max(0, aired - watched_n)
    seen = min(watched_n, aired)
    return {
        "kind": title.media_type, "total": (seen + left) * average, "watched_min": seen * average,
        "left_min": left * average, "unaired": max(0, (details.get("number_of_episodes") or aired) - aired),
        "done": aired > 0 and left == 0, "partial": 0 < watched_n and left > 0, "known": True,
    }


def _watch_state(profile, title_ids):
    watched_titles = set(
        WatchEvent.objects.filter(profile=profile, title_id__in=title_ids).order_by().values_list("title_id", flat=True).distinct()
    )
    episode_counts = {}
    for title_id, _season, _episode in (
        WatchEvent.objects.filter(profile=profile, title_id__in=title_ids, episode__isnull=False, episode__season__gt=0)
        .order_by()
        .values_list("title_id", "episode__season", "episode__episode")
        .distinct()
    ):
        episode_counts[title_id] = episode_counts.get(title_id, 0) + 1
    return watched_titles, episode_counts


def _info_key(profile, title, watched_n):
    return f"listinfo:v1:{profile.id}:{title.id}:{watched_n}"


def title_infos(profile, titles, fetch=True):
    """{title.pk: info} for the titles. A movie is always answered (it is local).
    A show comes from the cache, or - with fetch - from TMDB's episode counts
    (one pooled round of calls for whatever is not cached yet). With
    fetch=False an uncached show is simply absent. Info: kind, total,
    watched_min, left_min, unaired, done, partial, known."""
    titles = list(titles)
    watched_titles, episode_counts = _watch_state(profile, [t.pk for t in titles])
    infos, shows = {}, []
    for title in titles:
        if _is_movie(title):
            infos[title.pk] = _movie_info(title, title.pk in watched_titles)
        else:
            shows.append(title)
    if not shows:
        return infos
    keys = {t.pk: _info_key(profile, t, episode_counts.get(t.pk, 0)) for t in shows}
    try:
        cached = cache.get_many(list(keys.values()))
    except Exception:
        cached = {}
    missing = []
    for title in shows:
        hit = cached.get(keys[title.pk])
        if hit is not None:
            infos[title.pk] = hit
        else:
            missing.append(title)
    if not (fetch and missing):
        return infos
    api_key = instance_config.get_tmdb_api_key()

    def details_for(title):
        tmdb_id = title.external_ids.get("tmdb")
        if not tmdb_id or not api_key:
            return None
        try:
            return tmdb.get_full_details(tmdb.media_type_for(title), tmdb_id, api_key=api_key)
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=POOL_WORKERS) as executor:
        fetched = list(executor.map(details_for, missing))
    to_cache = {}
    for title, details in zip(missing, fetched):
        info = _show_info(title, details, episode_counts.get(title.pk, 0))
        infos[title.pk] = info
        if info["known"]:
            to_cache[keys[title.pk]] = info
    if to_cache:
        try:
            cache.set_many(to_cache, INFO_TTL)
        except Exception:
            pass
    return infos


def tile_text(title, info):
    """The tile's runtime line: a movie's runtime, a show's time left. "" when there is nothing to say."""
    if not info:
        return ""
    if _is_movie(title):
        return hm_full(info["total"])
    return f"{hm_full(info['left_min'])} left" if info["left_min"] > 0 else ""


def tile_texts(profile, titles):
    """{title.pk: text} for a grid of tiles, from local data and the cache only - never a TMDB call."""
    titles = list(titles)
    infos = title_infos(profile, titles, fetch=False)
    return {t.pk: tile_text(t, infos.get(t.pk)) for t in titles}


def time_left_order(titles, infos):
    """The titles sorted shortest-to-watch first; finished ones, and shows with no episode data, last."""
    def key(title):
        info = infos.get(title.pk)
        left = info["left_min"] if info else 0
        return (left <= 0, left)

    return sorted(titles, key=key)


# --- per list ----------------------------------------------------------------------------


def _composition(counts, count):
    present = [t for t in TYPE_ORDER if counts[t]]
    if len(present) == 1:
        only = present[0]
        noun = {MediaType.MOVIE: "movies", MediaType.TV: "TV shows", MediaType.ANIME: "anime"}[only]
        return f"{TYPE_LABELS[only]} only", f"all {count} are {noun}"
    top = max(present, key=lambda t: counts[t])
    if counts[top] / count >= MOSTLY_SHARE:
        word = TYPE_WORDS[top]
        return f"Mostly {word}", f"{math.floor(counts[top] * 100 / count)}% of the list"
    ranked = sorted(present, key=lambda t: -counts[t])
    named = [t for t in ranked if counts[t] / count >= NOTE_SHARE] or ranked[:2]
    words = [TYPE_WORDS[t] for t in named]
    joined = words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]
    return "Mixed", f"mostly {joined}"


def summarize(titles, infos):
    """Everything the strip and the mini card show, as ready-to-print values."""
    titles = list(titles)
    count = len(titles)
    if not count:
        return {"empty": True, "count": 0}
    counts = {t: 0 for t in TYPE_ORDER}
    minutes = {t: 0 for t in TYPE_ORDER}
    total = watched = left = unaired = 0
    titles_done = left_titles = unknown = 0
    partial_shows = False
    for title in titles:
        info = infos.get(title.pk)
        kind = title.media_type if title.media_type in counts else MediaType.MOVIE
        counts[kind] += 1
        if not info:
            unknown += 1
            continue
        minutes[kind] += info["total"]
        total += info["total"]
        watched += info["watched_min"]
        left += info["left_min"]
        unaired += info["unaired"]
        partial_shows = partial_shows or info["partial"]
        unknown += 0 if info["known"] else 1
        if info["done"]:
            titles_done += 1
        elif info["left_min"] > 0:
            left_titles += 1
    label, note = _composition(counts, count)
    percent = min(100, round(watched * 100 / total)) if total else 0
    # "Watched" is everything not still to watch, so a list of shows with nothing left reads 100%.
    all_watched = total > 0 and left == 0 and left_titles == 0
    if all_watched:
        percent = 100
    elif percent >= 100:
        percent = 99
    return {
        "empty": False,
        "count": count,
        "counts": counts,
        "segments": [
            {"key": t, "label": TYPE_LABELS[t], "count": counts[t]} for t in TYPE_ORDER if counts[t]
        ],
        "label": label,
        "label_note": note,
        "total": hm_headline(total),
        "total_days": days_note(total),
        "total_minutes": total,
        "watched": hm_full(watched),
        "left": hm_full(left),
        "watched_short": hm_short(watched),
        "left_short": hm_short(left),
        "left_minutes": left,
        "by_type": [{"key": t, "label": TYPE_LABELS[t], "time": hm_headline(minutes[t])} for t in TYPE_ORDER if counts[t] and minutes[t]],
        "unaired": unaired,
        "percent": percent,
        "titles_done": titles_done,
        "titles_left": count - titles_done if not all_watched else 0,
        "all_watched": all_watched,
        "partial_shows": partial_shows,
        "unknown": unknown,
    }
