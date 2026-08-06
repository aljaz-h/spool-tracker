"""mdblist.com lookups - a paid-optional, free-tier (1,000 requests/day)
API that aggregates per-title ratings from IMDb, Rotten Tomatoes,
Metacritic, Letterboxd, Trakt, and others, keyed by TMDB id. Used purely to
supplement the TMDB rating already shown on a title's page (see
views._mdblist_ratings_context / tasks.fetch_mdblist_ratings) - TMDB stays
the source of truth for everything else (matching, posters, completion).

Verified against the real API via its Jellyfin client's source (the hosted
docs at docs.mdblist.com/api.mdblist.com return 403 to an unauthenticated
fetch): GET https://api.mdblist.com/tmdb/{movie|show}/{tmdbId}?apikey=...
-> {"ratings": [{"source": "imdb", "value": 7.8, "score": 78, "votes": 12345,
"url": "..."}, ...]}, plus X-RateLimit-Remaining/X-RateLimit-Reset response
headers. Every function here is best-effort and never raises to its caller
- no API key configured, no match, rate-limited, network error - so a
lookup failure never blocks the page/task it's attached to, same
philosophy as tmdb.py/jikan.py.
"""

import logging

import requests

logger = logging.getLogger(__name__)

API_BASE = "https://api.mdblist.com"

# MDBList calls a tv show "show", not "tv" - media_type_for(title) (see
# tmdb.py) only ever returns "movie"/"tv", so this is the one place that
# needs translating.
_TYPE_PATH = {"movie": "movie", "tv": "show"}


def _api_key():
    from tracker import instance_config

    return instance_config.get_mdblist_api_key()


def _rate_limit_headers(resp):
    remaining = resp.headers.get("X-RateLimit-Remaining")
    reset_epoch = resp.headers.get("X-RateLimit-Reset")
    return {
        "remaining": int(remaining) if remaining is not None and remaining.isdigit() else None,
        "reset_epoch": int(reset_epoch) if reset_epoch is not None and reset_epoch.isdigit() else None,
    }


def fetch_ratings(media_type, tmdb_id):
    """Returns {"status": "ok"|"not_found"|"rate_limited"|"error",
    "ratings": [...], "remaining": int|None, "reset_epoch": int|None}.
    "ok" covers a 200 with an empty ratings list too - that's a real
    "MDBList has no ratings for this title" result, not a failure."""
    api_key = _api_key()
    if not api_key:
        return {"status": "error", "ratings": [], "remaining": None, "reset_epoch": None}

    path = _TYPE_PATH.get(media_type)
    if path is None:
        return {"status": "error", "ratings": [], "remaining": None, "reset_epoch": None}

    try:
        resp = requests.get(f"{API_BASE}/tmdb/{path}/{tmdb_id}", params={"apikey": api_key}, timeout=10)
    except requests.RequestException:
        logger.warning("MDBList request failed for %s/%s", media_type, tmdb_id, exc_info=True)
        return {"status": "error", "ratings": [], "remaining": None, "reset_epoch": None}

    limits = _rate_limit_headers(resp)

    if resp.status_code == 404:
        return {"status": "not_found", "ratings": [], **limits}
    if resp.status_code == 429:
        return {"status": "rate_limited", "ratings": [], **limits}
    if not resp.ok:
        logger.warning("MDBList returned HTTP %s for %s/%s", resp.status_code, media_type, tmdb_id)
        return {"status": "error", "ratings": [], **limits}

    try:
        ratings = resp.json().get("ratings") or []
    except ValueError:
        logger.warning("MDBList returned non-JSON for %s/%s", media_type, tmdb_id)
        return {"status": "error", "ratings": [], **limits}

    return {"status": "ok", "ratings": ratings, **limits}


def test_api_key(api_key):
    """Settings' "Test connection" button - a cheap known-good lookup
    (Fight Club, tmdb id 550, exists in both TMDB and MDBList) using the
    candidate key directly rather than whatever's already saved, so testing
    works both before and after hitting Save. Raises requests.RequestException
    on failure/invalid key, same contract as tmdb.test_api_key."""
    resp = requests.get(f"{API_BASE}/tmdb/movie/550", params={"apikey": api_key}, timeout=10)
    resp.raise_for_status()
    return True
