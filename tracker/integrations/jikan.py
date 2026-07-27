"""Jikan lookups - a free, unauthenticated, unofficial MyAnimeList API
(https://api.jikan.moe/v4). Used purely to fill gaps TMDB has no data for
at all: per-episode filler/recap status on the anime episode browser
(views._episode_panel_context), plus a handful of MAL-specific detail-page
facts (score, native Japanese title, studio, source material) TMDB either
doesn't track for anime or tracks less precisely than MAL's own
community - TMDB stays the source of truth for everything else
(discovery, matching, posters, completion tracking).

Every function here is best-effort and silently returns None/empty on any
failure - no match found, network error, Jikan's own upstream MAL proxy
erroring (observed live: the search endpoint occasionally 504s, unlike
the DB-backed episode endpoints which were reliable) - so a lookup
failure never blocks the page it's attached to, same philosophy as
tmdb.py. No API key needed, so unlike tmdb._api_key() there's nothing to
gate on except the request itself succeeding.
"""

import hashlib
import json
import logging

import requests

logger = logging.getLogger(__name__)

API_BASE = "https://api.jikan.moe/v4"

# Jikan's public rate limit is modest (a few requests/second, tens/minute)
# - fine for Spool's actual load (a handful of household profiles, results
# cached well past any single session's needs) but real: their episodes
# endpoint is paginated at ~100/episode/page, so a long-running show like
# Bleach (366 episodes) needs a handful of sequential requests per cold
# cache miss. _FILLER_TTL is a week (not tmdb.py's 6h _CACHE_TTL) since,
# unlike trending lists, an aired episode's filler status never changes.
_FILLER_TTL = 7 * 24 * 3600
_MAX_EPISODE_PAGES = 10  # guards against an unbounded loop on a malformed response


def _cache_key(prefix, value):
    return f"jikan:{prefix}:" + hashlib.sha1(json.dumps(value, sort_keys=True).encode()).hexdigest()


def find_match(name, year=None):
    """Returns {"mal_id": int} for the best search match, or None if
    nothing matched or the request failed. year (if given) only
    disambiguates between multiple results with the same name - an exact
    match isn't required, since Jikan's own "year" field is sometimes
    null even when a match is otherwise good."""
    try:
        resp = requests.get(f"{API_BASE}/anime", params={"q": name, "limit": 5}, timeout=10)
        resp.raise_for_status()
    except requests.RequestException:
        logger.warning("Jikan search failed for %r", name, exc_info=True)
        return None
    results = resp.json().get("data") or []
    if not results:
        return None
    if year:
        for result in results:
            if result.get("year") == year:
                return {"mal_id": result["mal_id"]}
    return {"mal_id": results[0]["mal_id"]}


def get_episode_filler_map(mal_id):
    """Returns {episode_number: {"filler": bool, "recap": bool}} for every
    episode Jikan knows about for this anime, or {} on failure. Cached as
    a whole (not per-page) since callers only ever want the full map for
    one absolute episode number lookup at a time."""
    from django.core.cache import cache

    key = _cache_key("episodes", mal_id)
    try:
        cached = cache.get(key)
    except Exception:
        logger.warning("Jikan filler-map cache read failed, continuing without cache", exc_info=True)
        cached = None
    if cached is not None:
        return cached

    filler_map = {}
    page = 1
    has_next = True
    while has_next and page <= _MAX_EPISODE_PAGES:
        try:
            resp = requests.get(f"{API_BASE}/anime/{mal_id}/episodes", params={"page": page}, timeout=10)
            resp.raise_for_status()
        except requests.RequestException:
            logger.warning("Jikan episodes request failed for mal_id=%s page=%s", mal_id, page, exc_info=True)
            break
        payload = resp.json()
        for ep in payload.get("data") or []:
            episode_number = ep.get("mal_id")
            if episode_number is not None:
                filler_map[episode_number] = {"filler": bool(ep.get("filler")), "recap": bool(ep.get("recap"))}
        has_next = bool((payload.get("pagination") or {}).get("has_next_page"))
        page += 1

    if filler_map:
        try:
            cache.set(key, filler_map, _FILLER_TTL)
        except Exception:
            logger.warning("Jikan filler-map cache write failed, continuing without cache", exc_info=True)
    return filler_map


def get_anime_details(mal_id):
    """Returns {"score": float|None, "title_japanese": str|None,
    "source": str|None, "studios": [str]} or None on failure. Cached like
    get_episode_filler_map (a week - these facts change rarely, if ever,
    once an anime's aired)."""
    from django.core.cache import cache

    key = _cache_key("details", mal_id)
    try:
        cached = cache.get(key)
    except Exception:
        logger.warning("Jikan details cache read failed, continuing without cache", exc_info=True)
        cached = None
    if cached is not None:
        return cached

    try:
        resp = requests.get(f"{API_BASE}/anime/{mal_id}", timeout=10)
        resp.raise_for_status()
    except requests.RequestException:
        logger.warning("Jikan anime details failed for mal_id=%s", mal_id, exc_info=True)
        return None
    data = resp.json().get("data") or {}
    result = {
        "score": data.get("score"),
        "title_japanese": data.get("title_japanese"),
        "source": data.get("source"),
        "studios": [s["name"] for s in (data.get("studios") or []) if s.get("name")],
    }

    try:
        cache.set(key, result, _FILLER_TTL)
    except Exception:
        logger.warning("Jikan details cache write failed, continuing without cache", exc_info=True)
    return result
