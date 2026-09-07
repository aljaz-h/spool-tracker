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


def _get_raw_details(mal_id):
    """Full cached Jikan /anime/{id} payload - shared by get_anime_details
    (which narrows it to a handful of detail-page facts) and
    get_season_episode_offset (which needs episodes/relations instead),
    so a mal_id already looked up for one never costs a second request
    for the other."""
    from django.core.cache import cache

    key = _cache_key("raw", mal_id)
    try:
        cached = cache.get(key)
    except Exception:
        logger.warning("Jikan raw-details cache read failed, continuing without cache", exc_info=True)
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

    try:
        cache.set(key, data, _FILLER_TTL)
    except Exception:
        logger.warning("Jikan raw-details cache write failed, continuing without cache", exc_info=True)
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
# tracker/episode_matching.py's own reconcile_episode_seasons backfill) doesn't re-hit Jikan's
# live search for every single one, which risks tipping a transient 504 into a hard 429 (observed
# live: a title with 25 affected episodes retried the same failing search 9 times in one run).


def resolve_mal_id(title):
    """Best-effort Jikan/MAL id resolution for an anime Title, matched by
    name/year and cached onto external_ids["mal"] once found so it's only
    ever looked up once - shared by the episode browser's filler overlay,
    the detail page's MAL score/Japanese title/studio enrichment, and
    tracker/episode_matching.py's season reconciliation.

    Falls back to an exact-title match against AniFiller's own (much
    smaller, ~180-show) bundle when Jikan's live search comes back empty
    - either a genuine no-match, or Jikan's search endpoint having one of
    its occasional outages (see this module's own docstring). Either way,
    the id AniFiller supplies is a real MAL id, cached identically to one
    Jikan found directly - anifiller.py's own per-episode data is a
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
        logger.warning("Jikan no-match cache read failed, continuing without cache", exc_info=True)
        cached_no_match = None
    if cached_no_match:
        return None

    match = find_match(title.name, title.year)
    mal_id = match["mal_id"] if match else anifiller.find_mal_id_by_name(title.name)
    if mal_id is None:
        try:
            cache.set(no_match_key, True, _NO_MATCH_TTL)
        except Exception:
            logger.warning("Jikan no-match cache write failed, continuing without cache", exc_info=True)
        return None
    title.external_ids["mal"] = mal_id
    title.save(update_fields=["external_ids"])
    return mal_id
