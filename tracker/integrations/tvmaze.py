"""TVmaze lookups (https://api.tvmaze.com) - a purely cosmetic/precision
enhancement layer over TMDB's own TV release dates: TMDB only ever gives
a bare "YYYY-MM-DD" air date (see release_sync._parse_tmdb_date), never a
time-of-day or timezone, so Spool's Calendar/"Up Next" could only ever
say "airs Thursday," never "airs tonight at 9:00 PM." TVmaze's own
episode data carries a full timezone-resolved instant (airstamp, see
get_episodes below) that fills exactly that gap. TMDB stays the source
of truth for everything else (matching, posters, completion, metadata,
movies entirely) - TVmaze has no movie data at all, this module is never
called for a movie.

No API key - TVmaze's free tier needs none, so unlike every other
integration in this codebase there's no credential to configure and
nothing for Settings' Server Integrations tab to show. Licensed CC
BY-SA 4.0 with a stated minimum of "linking back to TVmaze from
within your application" - see the credit line in
tracker/templates/tracker/base.html alongside TMDB/Jikan/AniFiller's.

Verified live against the real API (no auth needed, so this was
confirmed directly rather than against third-party docs):
- GET /lookup/shows?imdb={imdb_id} - 301-redirects to /shows/{id} on a
  match (requests.get follows redirects by default, so this comes back
  as a normal 200 with the show's own JSON body), 404 otherwise.
- GET /shows/{id}/episodes - full episode list (specials excluded by
  default). Each episode carries "airdate" (local "YYYY-MM-DD"),
  "airtime" (local "HH:MM" in the show's own timezone), and "airstamp" -
  a full ISO-8601 UTC instant with explicit offset
  ("2013-06-25T02:00:00+00:00") - this is the only field release_sync.py
  actually needs; it's already timezone-resolved, no need to combine
  airdate+airtime+network.country.timezone by hand.
- GET /updates/shows?since=day - a flat {"<tvmaze_id>": <unix_ts>, ...}
  covering every show TVmaze touched in the last 24h (large - hundreds/
  thousands of entries). One call tells the nightly sync which of a
  household's tracked shows changed at all today, so most of them can
  skip a full episode re-fetch entirely (see tasks.sync_release_schedules).

Rate limit: officially "at least 20 calls every 10 seconds" per IP (no
keys at all, so this is the only ceiling that exists). No documented
Retry-After on a 429 - their own docs just say to retry after a small
pause. _throttle below paces outbound calls well under that ceiling
(mirrors tenrai.py's own shared-cache fixed-window approach exactly,
since this process and every Celery worker/beat task share the same
Redis-backed cache in production); _get retries once on a 429 regardless,
for the rare case another process races the same window.

Every function here is best-effort and never raises to its caller (no
match, 404, network error) - same "a lookup failure never blocks
whatever it's attached to" philosophy release_sync.py's own module
docstring already states."""

import logging
import time

import requests

logger = logging.getLogger(__name__)

API_BASE = "https://api.tvmaze.com"

# A safety margin well under TVmaze's own "20 calls every 10 seconds"
# ceiling - see _throttle. This only matters for the batch-style call
# patterns that could plausibly burst past it (a cold library's first
# nightly sync resolving many shows' TVmaze ids at once); a single
# interactive page load never comes close.
_MAX_REQUESTS_PER_SECOND = 1


def _throttle():
    """Blocks briefly if needed so this process - and, since CACHES is
    shared Redis in production, every gunicorn worker/Celery task
    together - doesn't send more than _MAX_REQUESTS_PER_SECOND requests
    to TVmaze within any given wall-clock second. Fixed-window against a
    shared cache counter, same approach as tenrai.py's own _throttle
    (and ratelimit.is_rate_limited) - simple over precise, since this
    only needs to keep a burst under TVmaze's ceiling, not account
    requests exactly. A down/unreachable cache backend degrades to
    unpaced rather than blocking forever - losing pacing during a cache
    outage is better than every TVmaze-backed sync hanging. Gated by
    TVMAZE_THROTTLE_ENABLED the same way tenrai.py's own _throttle is -
    off under `manage.py test` (no real API to protect there, and
    several mocked calls in one test would otherwise stall waiting for
    the window to roll over)."""
    from django.conf import settings
    from django.core.cache import cache

    if not getattr(settings, "TVMAZE_THROTTLE_ENABLED", True):
        return

    for _ in range(60):  # ~3s worst case - never blocks indefinitely on a stuck window
        window = int(time.time())
        key = f"tvmaze:throttle:{window}"
        try:
            try:
                count = cache.incr(key)
            except ValueError:
                # incr() raises when the key doesn't exist yet - the normal
                # "first request in a new window" path, not a real failure.
                cache.set(key, 1, timeout=2)
                return
        except Exception:
            logger.warning("TVmaze throttle cache access failed, continuing unpaced", exc_info=True)
            return
        if count <= _MAX_REQUESTS_PER_SECOND:
            return
        time.sleep(0.05)


def _get(url, params=None):
    """requests.get, paced by _throttle and aware of TVmaze's own 429s -
    retries once after a fixed small pause (no Retry-After header is
    documented for this API) if rate-limited anyway. Returns None on any
    RequestException (including after the retry) rather than raising -
    callers treat that exactly like "nothing found"."""
    _throttle()
    try:
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code == 429:
            logger.warning("TVmaze rate-limited us, retrying %s in 2s", url)
            time.sleep(2.0)
            _throttle()
            resp = requests.get(url, params=params, timeout=10)
        return resp
    except requests.RequestException:
        logger.warning("TVmaze request failed for %s", url, exc_info=True)
        return None


def lookup_by_imdb(imdb_id):
    """GET /lookup/shows?imdb=... - the show TVmaze's own id/schedule/
    network data for this IMDb id, or None if there's no match (404) or
    the request otherwise failed. Movies never have an IMDb-matched
    TVmaze show - only ever call this for a TV title."""
    if not imdb_id:
        return None
    resp = _get(f"{API_BASE}/lookup/shows", params={"imdb": imdb_id})
    if resp is None or resp.status_code != 200:
        return None
    try:
        return resp.json()
    except ValueError:
        return None


def get_episodes(tvmaze_id):
    """GET /shows/{id}/episodes - every dated episode TVmaze has for this
    show (specials excluded, matching TMDB's own season-episode numbering
    convention this is meant to supplement), or [] on any failure."""
    if not tvmaze_id:
        return []
    resp = _get(f"{API_BASE}/shows/{tvmaze_id}/episodes")
    if resp is None or resp.status_code != 200:
        return []
    try:
        data = resp.json()
    except ValueError:
        return []
    return data if isinstance(data, list) else []


def get_updates(since="day"):
    """GET /updates/shows?since=... - {"<tvmaze_id>": <unix_timestamp>, ...}
    for every show TVmaze touched in that window, or {} on any failure.
    Keys come back as strings (TVmaze's own JSON shape) - callers compare
    against their own cached tvmaze_id cast to str."""
    resp = _get(f"{API_BASE}/updates/shows", params={"since": since})
    if resp is None or resp.status_code != 200:
        return {}
    try:
        data = resp.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}
