"""TMDB lookups — best-effort, matched by title/year search. Used to
populate Title.poster_url and Title.external_ids["tmdb"] for titles
created via Trakt/Simkl sync or CSV import, none of which carry a TMDB id
of their own, plus (once that id is known) episode/movie runtime and
watch-completion inference - see tracker/completion.py.

Verified against TMDB's current docs (api_key query-param auth still
works alongside the newer v4 bearer-token style, poster_path e.g.
"/xyz.jpg" appends directly onto https://image.tmdb.org/t/p/w500,
/tv/{id} returns number_of_episodes/episode_run_time/a seasons[] array
with per-season episode_count, /movie/{id} returns runtime), but not
against a live account from this environment. Every function here
silently returns None on any failure - no TMDB_API_KEY configured, no
match found, network error - so a lookup failure never blocks whatever
it's attached to.
"""

import logging

import requests

logger = logging.getLogger(__name__)

API_BASE = "https://api.themoviedb.org/3"
IMAGE_BASE = "https://image.tmdb.org/t/p/w500"

# TMDB has no separate "anime" category - anime shows and anime movies both
# just live in its ordinary tv/movie search, so anime tries both, tv first
# since most tracked anime is episodic. "kind" says which one actually
# matched, since for anime that can differ from our own media_type.
_SEARCH_PATHS = {
    "movie": [("search/movie", "year", "movie")],
    "tv": [("search/tv", "first_air_date_year", "tv")],
    "anime": [("search/tv", "first_air_date_year", "tv"), ("search/movie", "year", "movie")],
}


def _api_key():
    from tracker.instance_config import get_tmdb_api_key

    return get_tmdb_api_key()


def find_match(media_type, name, year):
    """Returns {"id": tmdb_id, "kind": "movie"|"tv", "poster_url": str|None}
    for the best search match, or None if no key is configured or nothing
    matched."""
    api_key = _api_key()
    if not api_key:
        return None
    for path, year_param, kind in _SEARCH_PATHS.get(media_type, []):
        params = {"api_key": api_key, "query": name}
        if year:
            params[year_param] = year
        try:
            resp = requests.get(f"{API_BASE}/{path}", params=params, timeout=10)
            resp.raise_for_status()
        except requests.RequestException:
            logger.warning("TMDB search failed for %r (%s, %s)", name, media_type, year, exc_info=True)
            continue
        results = resp.json().get("results") or []
        if not results:
            continue
        result = results[0]
        poster_path = result.get("poster_path")
        return {
            "id": result.get("id"),
            "kind": kind,
            "poster_url": f"{IMAGE_BASE}{poster_path}" if poster_path else None,
        }
    return None


def get_movie_details(tmdb_id):
    """Returns {"runtime": int|None} or None on failure."""
    api_key = _api_key()
    if not api_key:
        return None
    try:
        resp = requests.get(f"{API_BASE}/movie/{tmdb_id}", params={"api_key": api_key}, timeout=10)
        resp.raise_for_status()
    except requests.RequestException:
        logger.warning("TMDB movie details failed for id=%s", tmdb_id, exc_info=True)
        return None
    return {"runtime": resp.json().get("runtime")}


def get_tv_details(tmdb_id):
    """Returns {"number_of_episodes": int|None, "episode_run_time": int|None,
    "seasons": [{"season_number": int, "episode_count": int}, ...]} or None
    on failure. episode_run_time is TMDB's show-level typical duration
    (first value of its episode_run_time array, when present) - not a
    precise per-episode figure, which would need one API call per episode
    and isn't worth it just for a watch-time estimate."""
    api_key = _api_key()
    if not api_key:
        return None
    try:
        resp = requests.get(f"{API_BASE}/tv/{tmdb_id}", params={"api_key": api_key}, timeout=10)
        resp.raise_for_status()
    except requests.RequestException:
        logger.warning("TMDB tv details failed for id=%s", tmdb_id, exc_info=True)
        return None
    data = resp.json()
    episode_run_times = data.get("episode_run_time") or []
    return {
        "number_of_episodes": data.get("number_of_episodes"),
        "episode_run_time": episode_run_times[0] if episode_run_times else None,
        "seasons": [
            {"season_number": s.get("season_number"), "episode_count": s.get("episode_count")}
            for s in (data.get("seasons") or [])
        ],
    }
