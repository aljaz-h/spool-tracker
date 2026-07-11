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

import hashlib
import json
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


# --- Discovery (Movies & TV / Anime "browse new things" pages) ----------
#
# Trending/Popular/Upcoming/Top Rated are all implemented via
# /discover/{movie|tv} with a category-specific sort/date preset, rather
# than TMDB's separate dedicated endpoints (/trending/..., /movie/popular,
# etc). That sacrifices TMDB's own proprietary "trending" scoring
# algorithm in favor of "recent + high popularity", but the payoff is that
# every category composes with the filter panel (genre/year/runtime/
# rating/language) for free, since they're all the same endpoint - the
# dedicated endpoints don't accept those filter params at all, so a
# faithful "Trending, filtered by Horror" wouldn't otherwise be possible.

ANIMATION_GENRE_ID = 16  # stable TMDB genre id, same for movie and tv

_CACHE_TTL = 6 * 3600  # trending/popular lists don't need to be fresher than this


def _cache_key(path, params):
    normalized = json.dumps(params, sort_keys=True)
    return "tmdb:" + hashlib.sha1(f"{path}:{normalized}".encode()).hexdigest()


def _list_request(path, params=None):
    from django.core.cache import cache

    api_key = _api_key()
    if not api_key:
        return {"results": [], "total_pages": 0}
    params = params or {}
    key = _cache_key(path, params)

    # A down/unreachable cache backend should degrade to "no caching" for
    # this request, not fail the whole discovery page - same reasoning as
    # the Celery broker timeout fix elsewhere in this project.
    try:
        cached = cache.get(key)
    except Exception:
        logger.warning("TMDB result cache read failed, continuing without cache", exc_info=True)
        cached = None
    if cached is not None:
        return cached

    try:
        resp = requests.get(f"{API_BASE}/{path}", params={"api_key": api_key, **params}, timeout=10)
        resp.raise_for_status()
    except requests.RequestException:
        logger.warning("TMDB list request failed for %s", path, exc_info=True)
        return {"results": [], "total_pages": 0}
    data = resp.json()

    try:
        cache.set(key, data, _CACHE_TTL)
    except Exception:
        logger.warning("TMDB result cache write failed, continuing without cache", exc_info=True)

    return data


def _normalize_result(item, media_type):
    is_movie = media_type == "movie"
    title = item.get("title") if is_movie else item.get("name")
    date = item.get("release_date") if is_movie else item.get("first_air_date")
    poster_path = item.get("poster_path")
    return {
        "tmdb_id": item.get("id"),
        "media_type": media_type,
        "name": title or "Untitled",
        "year": date[:4] if date else None,
        "poster_url": f"{IMAGE_BASE}{poster_path}" if poster_path else None,
        "vote_average": item.get("vote_average"),
        "overview": item.get("overview", ""),
    }


def genres(media_type):
    """[{"id": 16, "name": "Animation"}, ...] - populates the filter
    panel's genre picker (TMDB's with_genres param takes ids, not names)."""
    data = _list_request(f"genre/{media_type}/list")
    return data.get("genres") or []


def discover(media_type, category="popular", page=1, genre_ids=None, year_from=None, year_to=None,
             runtime_from=None, runtime_to=None, rating_from=None, rating_to=None,
             original_language=None, origin_country=None, with_companies=None):
    """Returns {"results": [...normalized...], "page": int, "total_pages": int}.
    category picks a sort/date preset (see module docstring); every other
    param is an optional filter layered on top of that preset, all of them
    straight from TMDB's own documented /discover parameter set."""
    date_field = "primary_release_date" if media_type == "movie" else "first_air_date"
    params = {"page": page, "sort_by": "popularity.desc"}

    if category == "top_rated":
        params["sort_by"] = "vote_average.desc"
        params["vote_count.gte"] = 200  # otherwise a single 10/10 vote from one person tops the list
    elif category == "upcoming":
        import datetime

        today = datetime.date.today().isoformat()
        params["sort_by"] = f"{date_field}.asc"
        params[f"{date_field}.gte"] = today
    # "trending" and "popular" both use the popularity.desc default above -
    # see the module docstring for why they're not more differentiated here.

    if genre_ids:
        params["with_genres"] = ",".join(str(g) for g in genre_ids)
    if year_from:
        params[f"{date_field}.gte"] = f"{year_from}-01-01"
    if year_to:
        params[f"{date_field}.lte"] = f"{year_to}-12-31"
    if runtime_from:
        params["with_runtime.gte"] = runtime_from
    if runtime_to:
        params["with_runtime.lte"] = runtime_to
    if rating_from:
        params["vote_average.gte"] = rating_from
    if rating_to:
        params["vote_average.lte"] = rating_to
    if original_language:
        params["with_original_language"] = original_language
    if origin_country:
        params["with_origin_country"] = origin_country
    if with_companies:
        params["with_companies"] = with_companies

    data = _list_request(f"discover/{media_type}", params)
    return {
        "results": [_normalize_result(r, media_type) for r in data.get("results") or []],
        "page": data.get("page", 1),
        "total_pages": data.get("total_pages", 0),
    }
