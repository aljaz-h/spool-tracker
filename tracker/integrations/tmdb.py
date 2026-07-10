"""TMDB poster lookup — best-effort, matched by title/year search. Used to
populate Title.poster_url for titles created via Trakt/Simkl sync or CSV
import, none of which carry poster data of their own (Trakt's API doesn't
serve images at all - that's the whole reason TMDB_API_KEY exists as a
setting here - and CSV files never do either).

Verified against TMDB's current docs (api_key query-param auth still
works alongside the newer v4 bearer-token style, poster_path e.g.
"/xyz.jpg" appends directly onto https://image.tmdb.org/t/p/w500), but
not against a live account from this environment. Silently returns None
on any failure - no TMDB_API_KEY configured, no match found, network
error - so a poster lookup failure never blocks the title/watch-event
creation it's attached to.
"""

import logging

import requests

logger = logging.getLogger(__name__)

API_BASE = "https://api.themoviedb.org/3"
IMAGE_BASE = "https://image.tmdb.org/t/p/w500"

# TMDB has no separate "anime" category - anime shows and anime movies both
# just live in its ordinary tv/movie search, so anime tries both, tv first
# since most tracked anime is episodic.
_SEARCH_PATHS = {
    "movie": [("search/movie", "year")],
    "tv": [("search/tv", "first_air_date_year")],
    "anime": [("search/tv", "first_air_date_year"), ("search/movie", "year")],
}


def find_poster_url(media_type, name, year):
    from tracker.instance_config import get_tmdb_api_key

    api_key = get_tmdb_api_key()
    if not api_key:
        return None
    for path, year_param in _SEARCH_PATHS.get(media_type, []):
        params = {"api_key": api_key, "query": name}
        if year:
            params[year_param] = year
        try:
            resp = requests.get(f"{API_BASE}/{path}", params=params, timeout=10)
            resp.raise_for_status()
        except requests.RequestException:
            logger.warning("TMDB lookup failed for %r (%s, %s)", name, media_type, year, exc_info=True)
            continue
        results = resp.json().get("results") or []
        if not results:
            continue
        poster_path = results[0].get("poster_path")
        if poster_path:
            return f"{IMAGE_BASE}{poster_path}"
    return None
