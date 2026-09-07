"""Tenrai lookups - a free, unofficial MyAnimeList API
(https://api.tenrai.org/documentation) implementing the Jikan v4
response schema. Used purely to fill gaps TMDB has no data for at all:
per-episode filler/recap status on the anime episode browser
(views._episode_panel_context), a handful of MAL-specific detail-page
facts (score, native Japanese title, studio, source material) TMDB
either doesn't track for anime or tracks less precisely than MAL's own
community, and the per-cour episode-count offsets
tracker/episode_matching.py needs to reconcile a player's own season
split against TMDB's - TMDB stays the source of truth for everything
else (discovery, matching, posters, completion tracking).

This used to talk to Jikan (api.jikan.moe) directly - migrated here
once Jikan itself went down entirely. Tenrai deliberately mirrors
Jikan v4's endpoints/response shape field-for-field (its own docs:
"existing applications can point requests at this API instead of or
alongside Jikan with only a base URL update"), which is why every
function below still reads the same "mal_id"/"data" shape a Jikan
integration would - "MAL id" throughout this module (and
Title.external_ids["mal"]) still means a real MyAnimeList id; Tenrai
is just the proxy currently serving it.

Every function here is best-effort and silently returns None/empty on
any failure - no match found, network error, a proxy outage (observed
live against Jikan: the search endpoint occasionally 504s, unlike the
DB-backed episode endpoints, which were reliable) - so a lookup failure
never blocks the page it's attached to, same philosophy as tmdb.py. No
API key needed for Tenrai's public tier (120 RPM/4 RPS/40,000 RPD, no
credentials), so unlike tmdb._api_key() there's nothing to gate on
except the request itself succeeding - though every outbound request
does go through _get/_throttle below, which paces batch-style callers
(a cold filler-map fetch, a Sequel-chain walk, reconcile_episode_seasons
looping either across many episodes) so they stay comfortably under
that 4 RPS ceiling on their own, without needing a server key.
"""

import hashlib
import json
import logging
import time

import requests

logger = logging.getLogger(__name__)

API_BASE = "https://api.tenrai.org/v1"

# A few requests/second, tens/minute is fine for Spool's actual load (a
# handful of household profiles, results cached well past any single
# session's needs) but real: the episodes endpoint is paginated at
# ~100/episode/page, so a long-running show like Bleach (366 episodes)
# needs a handful of sequential requests per cold cache miss.
# _FILLER_TTL is a week (not tmdb.py's 6h _CACHE_TTL) since, unlike
# trending lists, an aired episode's filler status never changes.
_FILLER_TTL = 7 * 24 * 3600
_MAX_EPISODE_PAGES = 10  # guards against an unbounded loop on a malformed response

# A safety margin under Tenrai's public-tier ceiling (120 RPM/4 RPS, no
# credentials needed) - see _throttle. Once an X-Server-Key is configured
# (300 RPM/5 RPS) this stays conservative rather than needing to track
# which tier is active; the cap only matters for batch-style call
# patterns (reconcile_episode_seasons, a cold filler-map fetch) that
# don't exist on this codebase's actual request-volume scale otherwise.
_MAX_REQUESTS_PER_SECOND = 3


def _cache_key(prefix, value):
    return f"tenrai:{prefix}:" + hashlib.sha1(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _throttle():
    """Blocks briefly if needed so this process - and, since CACHES is
    shared Redis in production, every gunicorn worker/Celery task
    together - doesn't send more than _MAX_REQUESTS_PER_SECOND requests
    to Tenrai within any given wall-clock second. A single interactive
    page load never comes close to this; it exists for the loops that
    can: get_episode_filler_map's pagination (up to _MAX_EPISODE_PAGES
    sequential requests on a cold cache miss), get_season_episode_offset's
    Sequel-chain hops (up to _MAX_SEQUEL_HOPS for one title), and
    reconcile_episode_seasons looping either of those across many
    episodes/titles in one run with no pacing of its own.

    Fixed-window against a shared cache counter, same approach as
    ratelimit.is_rate_limited - simple over precise, since this only
    needs to keep a burst under Tenrai's ceiling, not account requests
    exactly. A down/unreachable cache backend degrades to unpaced rather
    than blocking forever - losing pacing during a cache outage is
    better than every Tenrai-backed page hanging."""
    from django.conf import settings
    from django.core.cache import cache

    if not getattr(settings, "TENRAI_THROTTLE_ENABLED", True):
        return

    for _ in range(60):  # ~3s worst case - never blocks indefinitely on a stuck window
        window = int(time.time())
        key = f"tenrai:throttle:{window}"
        try:
            try:
                count = cache.incr(key)
            except ValueError:
                # incr() raises when the key doesn't exist yet - the normal
                # "first request in a new window" path, not a real failure.
                cache.set(key, 1, timeout=2)
                return
        except Exception:
            logger.warning("Tenrai throttle cache access failed, continuing unpaced", exc_info=True)
            return
        if count <= _MAX_REQUESTS_PER_SECOND:
            return
        time.sleep(0.05)


def _get(url, params=None):
    """requests.get, paced by _throttle and aware of Tenrai's own 429s:
    if a request gets rate-limited anyway (the proactive throttle above
    is a local approximation, not a guarantee - another process could
    race it, or Tenrai's own window might not align with ours), this
    sleeps for the Retry-After duration Tenrai names (or 1s if that
    header's missing/unparsable, capped at 5s) and retries exactly once.
    Callers still get back a single requests.Response either way - no
    new exception type to handle, same contract requests.get already
    has."""
    _throttle()
    resp = requests.get(url, params=params, timeout=10)
    if resp.status_code == 429:
        try:
            delay = float(resp.headers.get("Retry-After", 1))
        except (TypeError, ValueError):
            delay = 1.0
        logger.warning("Tenrai rate-limited us, retrying %s in %.1fs", url, delay)
        time.sleep(min(delay, 5.0))
        _throttle()
        resp = requests.get(url, params=params, timeout=10)
    return resp


def find_match(name, year=None):
    """Returns {"mal_id": int} for the best search match, or None if
    nothing matched or the request failed. year (if given) only
    disambiguates between multiple results with the same name - an exact
    match isn't required, since the "year" field is sometimes null even
    when a match is otherwise good."""
    try:
        resp = _get(f"{API_BASE}/anime", params={"q": name, "limit": 5})
        resp.raise_for_status()
    except requests.RequestException:
        logger.warning("Tenrai search failed for %r", name, exc_info=True)
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
    episode Tenrai knows about for this anime, or {} on failure. Cached as
    a whole (not per-page) since callers only ever want the full map for
    one absolute episode number lookup at a time."""
    from django.core.cache import cache

    key = _cache_key("episodes", mal_id)
    try:
        cached = cache.get(key)
    except Exception:
        logger.warning("Tenrai filler-map cache read failed, continuing without cache", exc_info=True)
        cached = None
    if cached is not None:
        return cached

    filler_map = {}
    page = 1
    has_next = True
    while has_next and page <= _MAX_EPISODE_PAGES:
        try:
            resp = _get(f"{API_BASE}/anime/{mal_id}/episodes", params={"page": page})
            resp.raise_for_status()
        except requests.RequestException:
            logger.warning("Tenrai episodes request failed for mal_id=%s page=%s", mal_id, page, exc_info=True)
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
            logger.warning("Tenrai filler-map cache write failed, continuing without cache", exc_info=True)
    return filler_map


def _get_raw_details(mal_id):
    """Full cached /anime/{id}/full payload - shared by get_anime_details
    (which narrows it to a handful of detail-page facts) and
    get_season_episode_offset (which needs episodes/relations instead),
    so a mal_id already looked up for one never costs a second request
    for the other. /full specifically, not the plain /anime/{id} this
    used to call - "relations" (the Sequel-chain data
    get_season_episode_offset depends on) only exists on /full, same
    endpoint split Jikan v4 itself uses; calling the plain endpoint
    left get_season_episode_offset silently unable to find any relation
    data at all, always falling back to "no offset found" regardless of
    whether the anime actually had a Sequel entry."""
    from django.core.cache import cache

    key = _cache_key("raw", mal_id)
    try:
        cached = cache.get(key)
    except Exception:
        logger.warning("Tenrai raw-details cache read failed, continuing without cache", exc_info=True)
        cached = None
    if cached is not None:
        return cached

    try:
        resp = _get(f"{API_BASE}/anime/{mal_id}/full")
        resp.raise_for_status()
    except requests.RequestException:
        logger.warning("Tenrai anime details failed for mal_id=%s", mal_id, exc_info=True)
        return None
    data = resp.json().get("data") or {}

    try:
        cache.set(key, data, _FILLER_TTL)
    except Exception:
        logger.warning("Tenrai raw-details cache write failed, continuing without cache", exc_info=True)
    return data


def get_anime_details(mal_id):
    """Returns {"score": float|None, "title_japanese": str|None,
    "source": str|None, "studios": [str], "trailer_youtube_id": str|None}
    or None on failure. Cached like get_episode_filler_map (a week - these
    facts change rarely, if ever, once an anime's aired). trailer_youtube_id
    is MAL's own trailer (usually the Japanese-market one) - preferred
    over TMDB's own /videos for anime specifically (see
    views._media_gallery_context), falling back to TMDB's when MAL has
    none on file."""
    data = _get_raw_details(mal_id)
    if data is None:
        return None
    return {
        "score": data.get("score"),
        "title_japanese": data.get("title_japanese"),
        "source": data.get("source"),
        "studios": [s["name"] for s in (data.get("studios") or []) if s.get("name")],
        "trailer_youtube_id": (data.get("trailer") or {}).get("youtube_id"),
    }


def _next_sequel_mal_id(data):
    """The first "Sequel" relation entry's own mal_id, or None - a split-
    cour anime's MAL entries link to each other this way (e.g. "Hell's
    Paradise" -> Sequel -> "Hell's Paradise Season 2"), each with its
    own accurate `episodes` count get_season_episode_offset below walks."""
    for rel in data.get("relations") or []:
        if rel.get("relation") != "Sequel":
            continue
        for entry in rel.get("entry") or []:
            if entry.get("type") == "anime" and entry.get("mal_id"):
                return entry["mal_id"]
    return None


_MAX_SEQUEL_HOPS = 6  # a real cour/part chain is never long; guards a malformed/cyclic relation graph


def get_season_episode_offset(mal_id, virtual_season):
    """How many episodes precede `virtual_season` in mal_id's own Sequel
    chain - e.g. virtual_season=2 returns season 1's own `episodes`
    count. mal_id is assumed to be the virtual-season-1 entry (whatever
    resolve_mal_id matched the title to).

    Exists for tracker/episode_matching.py's own season reconciliation:
    a player reporting its own "season 2" for a show TMDB only lists as
    one season needs a reliable episode-count offset to resolve that
    against TMDB's real numbering, and MAL's own separate per-cour
    entries are a far more stable source for that than trying to infer
    it from how much of the show Spool itself has ingested so far (which
    would drift depending on the order episodes happen to arrive in -
    out-of-order/rewatch scrobbles included).

    Returns None - not a guess - when the chain doesn't reach that far
    (no further Sequel relation, a request failure, or more hops than
    _MAX_SEQUEL_HOPS) - resolve_episode_season leaves the episode as
    reported rather than act on a partial/wrong offset."""
    if virtual_season <= 1:
        return 0
    offset = 0
    current_id = mal_id
    for _ in range(min(virtual_season - 1, _MAX_SEQUEL_HOPS)):
        data = _get_raw_details(current_id)
        if not data:
            return None
        offset += data.get("episodes") or 0
        next_id = _next_sequel_mal_id(data)
        if next_id is None:
            return None
        current_id = next_id
    return offset


_NO_MATCH_TTL = 3600  # 1 hour - short enough to retry once an outage clears, long enough that a
# caller re-checking the same title many times in one run (e.g. once per episode - see
# tracker/episode_matching.py's own reconcile_episode_seasons backfill) doesn't re-hit Tenrai's
# live search for every single one, which risks tipping a transient 504 into a hard 429 (observed
# live: a title with 25 affected episodes retried the same failing search 9 times in one run).


def resolve_mal_id(title):
    """Best-effort MAL id resolution for an anime Title (via Tenrai),
    matched by name/year and cached onto external_ids["mal"] once found
    so it's only ever looked up once - shared by the episode browser's
    filler overlay, the detail page's MAL score/Japanese title/studio
    enrichment, and tracker/episode_matching.py's season reconciliation.

    Falls back to an exact-title match against AniFiller's own (much
    smaller, ~180-show) bundle when Tenrai's live search comes back empty
    - either a genuine no-match, or Tenrai's search endpoint having one of
    its occasional outages (see this module's own docstring). Either way,
    the id AniFiller supplies is a real MAL id, cached identically to one
    Tenrai found directly - anifiller.py's own per-episode data is a
    separate, explicitly-secondary fallback, but a MAL id is a MAL id
    regardless of which source resolved it.

    A genuine "no match anywhere" result is itself cached, briefly (see
    _NO_MATCH_TTL) - unlike a found id, which is cached permanently on
    the Title itself since it never changes, "not found today" isn't
    written back to external_ids (it might resolve later, e.g. once a
    title gets added to MAL, or once an outage clears) but still
    shouldn't re-trigger a live search for every caller in a tight loop
    against the same title."""
    from django.core.cache import cache

    from . import anifiller

    mal_id = title.external_ids.get("mal")
    if mal_id is not None:
        return mal_id

    no_match_key = _cache_key("no_mal_match", (title.name, title.year))
    try:
        cached_no_match = cache.get(no_match_key)
    except Exception:
        logger.warning("Tenrai no-match cache read failed, continuing without cache", exc_info=True)
        cached_no_match = None
    if cached_no_match:
        return None

    match = find_match(title.name, title.year)
    mal_id = match["mal_id"] if match else anifiller.find_mal_id_by_name(title.name)
    if mal_id is None:
        try:
            cache.set(no_match_key, True, _NO_MATCH_TTL)
        except Exception:
            logger.warning("Tenrai no-match cache write failed, continuing without cache", exc_info=True)
        return None
    title.external_ids["mal"] = mal_id
    title.save(update_fields=["external_ids"])
    return mal_id
