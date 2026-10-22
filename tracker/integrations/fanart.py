"""Fanart.tv lookups - a purely cosmetic enhancement layer over TMDB's own
images: transparent clearlogos (title text with no background), clearart,
season banners, and backdrops. TMDB images/data remain the source of truth
everywhere else (matching, posters, completion, metadata) - this only ever
supplies a nicer-looking alternative when Fanart happens to have one for a
given title (see views._fanart_context / tasks.fetch_fanart_images).

Verified against the real API via its official Node client's source
(github.com/fanart-tv/fanart.tv-api - api.fanart.tv itself 403s an
unauthenticated doc-page fetch): GET https://webservice.fanart.tv/v3/...,
every request takes api_key as a query param (the user's own personal key
from fanart.tv/get-an-api-key/ - just the one key, no separate project-vs-
personal client_key distinction). Movies are keyed by tmdb_id directly
(/movies/{tmdb_id}); TV shows are keyed by TheTVDB id, not tmdb_id
(/tv/{tvdb_id}) - see tmdb.get_tv_external_ids for how that's resolved. Using plain
v3 (not v3.1/v3.2) since nothing here needs their extra added/width/height
fields, just {"id", "url", "lang", "likes"} per image.

Every public fetch function here is best-effort and never raises to its
caller (no api key, no match, 404, network error - same philosophy as
mdblist.py/tmdb.py) - only test_api_key (Settings' "Test connection"
button) raises, so it can report a real failure reason.
"""

import logging
import time

import requests

logger = logging.getLogger(__name__)

API_BASE = "https://webservice.fanart.tv/v3"

# Fanart's rate limiting isn't documented as a published day-scale quota
# (unlike WeTrakr/MDBList) - just an occasional 429 under heavy use, so a
# short bounded retry respecting Retry-After is enough, no quota bookkeeping.
_MAX_RETRIES = 3


def _raise_for_status(resp):
    """resp.raise_for_status(), but with the request URL stripped out of
    the exception - every call here carries api_key as a query param, and
    HTTPError's own message echoes the full request URL back verbatim,
    which would otherwise end up readable in logs (same fix already
    applied to tracker/integrations/tmdb_account.py for the same reason -
    not repeating that mistake here)."""
    try:
        resp.raise_for_status()
    except requests.HTTPError:
        raise requests.HTTPError(f"Fanart.tv request failed (HTTP {resp.status_code})", response=resp) from None


def _request(path, api_key):
    url = f"{API_BASE}{path}"
    for attempt in range(_MAX_RETRIES):
        try:
            resp = requests.get(url, params={"api_key": api_key}, timeout=15)
        except requests.RequestException as e:
            raise requests.RequestException(f"Fanart.tv request failed: {type(e).__name__}") from None
        if resp.status_code == 429 and attempt < _MAX_RETRIES - 1:
            try:
                delay = float(resp.headers.get("Retry-After", 1))
            except (TypeError, ValueError):
                delay = 1.0
            time.sleep(min(max(delay, 0.0), 5.0))
            continue
        _raise_for_status(resp)
        return resp
    _raise_for_status(resp)
    return resp


def get_movie_images(api_key, tmdb_id):
    """Raw Fanart /movies/{tmdb_id} response, or {} on a 404 (no artwork
    for this movie at all), missing api_key, or any other failure - never
    raises, see module docstring."""
    if not api_key or not tmdb_id:
        return {}
    try:
        resp = _request(f"/movies/{tmdb_id}", api_key)
    except requests.RequestException as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        if status == 404:
            return {}
        logger.warning("Fanart.tv movie lookup failed for tmdb:%s (%s)", tmdb_id, e)
        return {}
    try:
        return resp.json() or {}
    except ValueError:
        logger.warning("Fanart.tv returned non-JSON for movie tmdb:%s", tmdb_id)
        return {}


def get_show_images(api_key, tvdb_id):
    """Raw Fanart /tv/{tvdb_id} response, or {} - same contract as
    get_movie_images above, just keyed by TheTVDB id instead of tmdb_id."""
    if not api_key or not tvdb_id:
        return {}
    try:
        resp = _request(f"/tv/{tvdb_id}", api_key)
    except requests.RequestException as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        if status == 404:
            return {}
        logger.warning("Fanart.tv show lookup failed for tvdb:%s (%s)", tvdb_id, e)
        return {}
    try:
        return resp.json() or {}
    except ValueError:
        logger.warning("Fanart.tv returned non-JSON for show tvdb:%s", tvdb_id)
        return {}


def test_api_key(api_key):
    """Settings' "Test connection" button - a cheap known-good lookup
    (Fight Club, tmdb id 550, the same title tmdb.py/mdblist.py's own
    test_api_key use) against the candidate key directly, so testing works
    both before and after hitting Save. Raises requests.RequestException on
    failure/invalid key, same contract as tmdb.test_api_key/
    mdblist.test_api_key."""
    _request("/movies/550", api_key)
    return True


def _likes(entry):
    try:
        return int(entry.get("likes") or 0)
    except (TypeError, ValueError):
        return 0


def best_image(entries):
    """The single "best" image url from a Fanart image-array field
    ({"id", "url", "lang", "likes"} each), or None if the field is
    missing/empty. Prefers an English or textless (lang="") entry - the
    common case for a logo/clearart - sorted by likes; falls back to the
    single highest-likes entry overall when nothing in that language
    group exists, rather than returning nothing just because every
    uploaded image happens to be in some other language."""
    if not entries:
        return None
    preferred = [e for e in entries if (e.get("lang") or "") in ("", "en")]
    pool = preferred or entries
    best = max(pool, key=_likes)
    return best.get("url") or None


def _best_of(*entry_lists):
    """best_image over several fields combined (e.g. moviebackground +
    movie4kbackground - a newer title sometimes only has the 4K one)."""
    combined = [e for entries in entry_lists if entries for e in entries]
    return best_image(combined)


def extract_movie_images(raw):
    """raw: get_movie_images' own return shape. {"clearlogo_url",
    "clearart_url", "backdrop_url"} - each None if Fanart had nothing
    usable. HD variant preferred over the plain one when both exist."""
    if not raw:
        return {"clearlogo_url": None, "clearart_url": None, "backdrop_url": None}
    return {
        "clearlogo_url": best_image(raw.get("hdmovielogo")) or best_image(raw.get("movielogo")),
        "clearart_url": best_image(raw.get("hdmovieclearart")) or best_image(raw.get("movieart")),
        "backdrop_url": _best_of(raw.get("moviebackground"), raw.get("movie4kbackground")),
    }


def _season_banners(entries):
    """{"1": url, "2": url, ...} from a seasonbanner field - one best pick
    per season number. Entries without a plain positive-int season (a
    missing field, or Fanart's own "all" sentinel for an all-seasons
    banner) are skipped - nothing in Spool's episode browser is keyed by
    "all seasons" the way a per-season header strip needs to be."""
    if not entries:
        return {}
    by_season = {}
    for entry in entries:
        season = entry.get("season")
        if not season or not str(season).isdigit():
            continue
        by_season.setdefault(season, []).append(entry)
    return {season: url for season, entries in by_season.items() if (url := best_image(entries))}


def extract_show_images(raw):
    """raw: get_show_images' own return shape. {"clearlogo_url",
    "clearart_url", "backdrop_url", "season_banners"} - same shape as
    extract_movie_images plus season_banners (see _season_banners)."""
    if not raw:
        return {"clearlogo_url": None, "clearart_url": None, "backdrop_url": None, "season_banners": {}}
    return {
        "clearlogo_url": best_image(raw.get("hdtvlogo")) or best_image(raw.get("clearlogo")),
        "clearart_url": best_image(raw.get("hdclearart")),
        "backdrop_url": _best_of(raw.get("showbackground"), raw.get("show4kbackground")),
        "season_banners": _season_banners(raw.get("seasonbanner")),
    }
