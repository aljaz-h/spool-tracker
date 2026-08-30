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
import itertools
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor

import requests

logger = logging.getLogger(__name__)

API_BASE = "https://api.themoviedb.org/3"
IMAGE_BASE = "https://image.tmdb.org/t/p/w500"

# Shared across every _list_request call (list, detail, credits, search,
# ...) so requests to api.themoviedb.org reuse pooled/keep-alive TCP+TLS
# connections instead of paying a fresh handshake per call - bare
# requests.get(...) is a module-level convenience wrapper that opens and
# discards its own Session every time, so this is a real, free win. Safe
# to share across discover()'s own ThreadPoolExecutor workers below:
# no cookies/auth state is ever mutated on it (the api_key is a plain
# per-call query param), and urllib3's connection pool underneath a
# Session is itself thread-safe.
_http_session = requests.Session()

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


_FIND_RESULT_KEYS = {
    "movie": [("movie_results", "movie")],
    "tv": [("tv_results", "tv")],
    "anime": [("tv_results", "tv"), ("movie_results", "movie")],
}


def find_by_imdb_id(imdb_id, media_type):
    """TMDB's /find endpoint, looking up by IMDb id instead of a fuzzy
    title/year search - for a source that hands over an IMDb id directly
    (currently Nuvio - see tracker/integrations/nuvio.py) rather than
    just a title. Returns the same {"id", "kind", "poster_url"} shape
    find_match() does, or None if no key is configured or nothing
    matched."""
    api_key = _api_key()
    if not api_key or not imdb_id:
        return None
    try:
        resp = requests.get(
            f"{API_BASE}/find/{imdb_id}",
            params={"api_key": api_key, "external_source": "imdb_id"},
            timeout=10,
        )
        resp.raise_for_status()
    except requests.RequestException:
        logger.warning("TMDB find-by-imdb failed for %r (%s)", imdb_id, media_type, exc_info=True)
        return None
    data = resp.json()
    for result_key, kind in _FIND_RESULT_KEYS.get(media_type, []):
        results = data.get(result_key) or []
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
    """Returns {"runtime": int|None} or None on failure. Routed through
    _list_request (same 6h cache as every other TMDB lookup here) since
    completion.py's own runtime backfill already self-limits to once per
    title (see update_movie_runtime's title.runtime_minutes guard), but
    this is also called from title_detail's episode panel context on
    every page view of a show - see get_tv_details below. data.get("id")
    is None on failure the same way get_full_details checks it - a real
    TMDB movie/tv object always carries its own id, unlike _list_request's
    {"results": [], "total_pages": 0} fallback."""
    data = _list_request(f"movie/{tmdb_id}")
    if not data or data.get("id") is None:
        return None
    return {"runtime": data.get("runtime")}


def get_tv_details(tmdb_id):
    """Returns {"number_of_episodes": int|None, "episode_run_time": int|None,
    "seasons": [{"season_number": int, "episode_count": int, "vote_average": float|None}, ...]}
    or None on failure. episode_run_time is TMDB's show-level typical
    duration (first value of its episode_run_time array, when present) -
    not a precise per-episode figure, which would need one API call per
    episode and isn't worth it just for a watch-time estimate. Each
    season's own vote_average is TMDB's rating for that season's own
    page (voted on directly, not a mean of its episodes' own ratings) -
    cheap per-season rating data from this one call, vs. the per-episode
    average get_season_details' episodes would need a whole extra call
    per season to compute. Routed through _list_request (same 6h cache
    as every other TMDB lookup here) - this is called on every page view
    of a show's detail/episode-browser page (see views._episode_panel_
    context), previously an uncached request per view."""
    data = _list_request(f"tv/{tmdb_id}")
    if not data or data.get("id") is None:
        return None
    episode_run_times = data.get("episode_run_time") or []
    return {
        "number_of_episodes": data.get("number_of_episodes"),
        "episode_run_time": episode_run_times[0] if episode_run_times else None,
        "seasons": [
            {
                "season_number": s.get("season_number"),
                "episode_count": s.get("episode_count"),
                "vote_average": s.get("vote_average"),
            }
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

# TMDB's own "adult" flag is not a reliable content filter on its own -
# verified against the live API: well-known explicit hentai titles
# ("Overflow", "Adam's Sweet Agony", "Souryo to Majiwaru Shikiyoku no
# Yoru ni...") all come back adult=false, genre_ids indistinguishable
# from ordinary anime (just [16], same as everything else). Each of
# those three is only actually tagged by a *different* one of the
# keywords below (hentai/erotic/ecchi respectively - checked each
# title's own /keywords endpoint directly, not guessed), which is why
# this list has to be this broad rather than just "hentai" alone.
# "ecchi" (195669) is included deliberately even though it also tags
# some mainstream, not-actually-explicit fan-service anime (High School
# DxD, Mushoku Tensei) - for a content-safety filter, a false positive
# (hiding a legitimate show) is a far smaller problem than a false
# negative (showing porn to someone who asked not to see it), so this
# errs toward over-filtering. Still not exhaustive - some explicit
# titles on TMDB carry no matching keyword at all, a genuine gap in
# TMDB's own community-maintained data that server-side filtering can't
# fully close - but it materially cuts down what surfaces while browsing.
_UNSAFE_KEYWORD_IDS = "198385|195669|360629|284535|161919|315444|325693|256466|356759|378118"
# hentai, ecchi, adult, adult video, adult animation, adult cartoon, erotica, erotic, porn, 18+

# TMDB's US movie certification order, for the discover filter panel's
# rating dropdown and the detail page's own certification badge -
# TV shows use their own per-network rating strings (TV-Y, TV-14, TV-MA,
# ...) that don't share this scale, and /discover/tv has no certification
# filter param at all (a TMDB API gap, not something this app can work
# around) - the certification badge still renders for TV (fetched
# straight off the title, not through discover), only the *filter*
# is movie-only.
MOVIE_CERTIFICATIONS = ["G", "PG", "PG-13", "R", "NC-17"]

# TMDB's own /discover/tv with_status codes (verified live: applying one to
# /discover/movie is silently ignored, total_results unchanged) - the mirror
# image of MOVIE_CERTIFICATIONS above, just flipped to TV since /discover/movie
# has no status-filter equivalent at all.
TV_STATUSES = {
    "Returning Series": 0,
    "Planned": 1,
    "In Production": 2,
    "Ended": 3,
    "Canceled": 4,
    "Pilot": 5,
}

# TMDB's with_watch_monetization_types values, grouped into the two choices
# that are actually meaningful to offer here. AVAILABILITY_WATCH_REGION is
# only the *fallback* now - discover()'s own region param (Profile.
# preferred_region) takes priority whenever a profile is available; this
# constant just covers the no-profile/no-preference case, same as
# certification_country's own hardcoded "US" above.
# "streaming" = watchable right now without paying per-title (subscription/
# free/ad-supported); "digital" widens that to anything with a digital
# release at all, rent/buy included. Verified live against the real API
# that both meaningfully shrink result counts for movies and TV alike.
AVAILABILITY_WATCH_REGION = "US"
AVAILABILITY_CHOICES = {
    "streaming": "flatrate|free|ads",
    "digital": "flatrate|free|ads|rent|buy",
}

_CACHE_TTL = 6 * 3600  # trending/popular lists don't need to be fresher than this

# TMDB returns 20 results per page - too little to fill a wide grid (barely
# 2 rows at desktop widths). discover() merges this many consecutive TMDB
# pages into one logical "page" instead (3 * 20 = 60, which fills roughly 6
# rows at typical desktop column counts). Each underlying TMDB page is still
# cached/requested individually, so this doesn't change caching behavior.
RESULTS_PAGE_SIZE = 3


def _cache_key(path, params):
    normalized = json.dumps(params, sort_keys=True)
    return "tmdb:" + hashlib.sha1(f"{path}:{normalized}".encode()).hexdigest()


def _list_request(path, params=None, api_key=None):
    """api_key lets a caller resolve it once and pass it through, rather
    than each call hitting _api_key()'s own InstanceConfig.load() DB read -
    discover()'s parallel page fetches (below) rely on this so their worker
    threads never touch the DB independently/concurrently, only the
    already-pooled cache/HTTP clients."""
    from django.core.cache import cache

    api_key = api_key if api_key is not None else _api_key()
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
        resp = _http_session.get(f"{API_BASE}/{path}", params={"api_key": api_key, **params}, timeout=10)
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


def test_api_key(api_key):
    """Settings' "Test connection" button - TMDB's own dedicated key-check
    endpoint, unlike Trakt/Simkl's test_credentials() this is a full check
    (TMDB has just the one secret, no separate id/secret pair). Raises
    requests.RequestException on failure; returns the endpoint's own
    "success" flag on a 200 rather than assuming 200 always means valid."""
    resp = requests.get(f"{API_BASE}/authentication", params={"api_key": api_key}, timeout=10)
    resp.raise_for_status()
    return bool(resp.json().get("success"))


def _normalize_result(item, media_type):
    is_movie = media_type == "movie"
    title = item.get("title") if is_movie else item.get("name")
    date = item.get("release_date") if is_movie else item.get("first_air_date")
    poster_path = item.get("poster_path")
    return {
        "tmdb_id": item.get("id"),
        "media_type": media_type,
        # Same signal discover()'s own anime filter uses (Animation genre +
        # Japanese origin), just read off the fields a list/search result
        # already carries (genre_ids, original_language) instead of the
        # with_genres/with_origin_country params a /discover call takes -
        # search/multi has no anime-specific endpoint to ask instead.
        "is_anime": ANIMATION_GENRE_ID in (item.get("genre_ids") or []) and item.get("original_language") == "ja",
        "name": title or "Untitled",
        "year": date[:4] if date else None,
        "poster_url": f"{IMAGE_BASE}{poster_path}" if poster_path else None,
        "vote_average": item.get("vote_average"),
        "overview": item.get("overview", ""),
    }


_YEAR_RE = re.compile(r"^(.*\S)\s+((?:19|20)\d{2})$")

_spell = None


def _get_spellchecker():
    global _spell
    if _spell is None:
        from spellchecker import SpellChecker

        _spell = SpellChecker()
    return _spell


def _autocorrected_query(text):
    """Best-effort spelling fix, one word at a time - TMDB's own search has
    none (confirmed against the live API: a single typo like "avangers" or
    "interstellr" returns zero results or, worse, a single irrelevant
    literal match, never the intended title). Any word the dictionary
    doesn't recognize (proper nouns, foreign titles - "Sakamoto", "Silo")
    is left exactly as typed rather than forced into an unrelated
    dictionary word - correction() returns None for those, not a guess.
    Returns None (not the unchanged text) when nothing would actually
    change, so search() can skip a redundant second TMDB call for a query
    that was already spelled fine."""
    words = text.split()
    if not words:
        return None
    try:
        spell = _get_spellchecker()
        corrected = " ".join(spell.correction(w) or w for w in words)
    except Exception:
        logger.warning("Spellchecker unavailable, skipping autocorrect", exc_info=True)
        return None
    return corrected if corrected.lower() != text.lower() else None


def _split_query_year(query):
    """Pulls a trailing 4-digit year off a query like "avengers 2012" - a
    same-named movie and show (or a franchise's several entries) need a
    year to disambiguate, the way most sites let you. /search/multi has no
    year param at all, so search() only uses this to run an *additional*
    year-qualified lookup via /search/movie and /search/tv (which do have
    one) alongside the plain full-text search, never in place of it - a
    title that's genuinely just a number, like "Blade Runner 2049" or
    "1917", still gets found via the unmodified full-text query either
    way."""
    m = _YEAR_RE.match(query.strip())
    if not m:
        return query, None
    return m.group(1), int(m.group(2))


def _merge_normalized(results, seen, raw_items, media_type):
    """media_type=None pulls each item's own "media_type" field (a
    /search/multi response, where it varies per item) - otherwise every
    item is normalized under the given fixed type (a /search/movie or
    /search/tv response, which has no such field of its own)."""
    for item in raw_items:
        item_media_type = media_type or item.get("media_type")
        normalized = _normalize_result(item, item_media_type)
        key = (normalized["tmdb_id"], normalized["media_type"])
        if key not in seen:
            seen.add(key)
            results.append(normalized)


def search(query, page=1):
    """The navbar search box's TMDB half - /search/multi covers movies and
    TV in one call (a search box has no natural "which section" context
    the way Discover's per-page tabs do), person results dropped since
    this only ever backs a title-cards grid. Normalized to the exact
    shape discover() returns, so results render with the same
    discover_tile.html card. Unlike discover(), only TMDB's own first
    page (up to 20 results) is fetched - a search box doesn't need
    discover()'s multi-page merge, and callers wanting more can pass a
    higher page number themselves.

    Layers extra lookups on top of the baseline full-text search, each
    only firing when it'd actually add something: a year-qualified retry
    via /search/movie + /search/tv when the query ends in a year
    (_split_query_year - /search/multi has no year param at all to give
    this to directly), and a spelling-corrected retry when autocorrection
    actually changed a word (_autocorrected_query). Each merges new
    candidates in behind the baseline results rather than replacing them -
    the baseline already covers everything TMDB's own multi-search gets
    right, including a title that's genuinely just a number ("Blade
    Runner 2049", "1917"), which _split_query_year would otherwise
    misparse as a year qualifier and strip. When a year was given,
    exact-year matches are stably sorted to the front afterward - "the
    one with that exact release year" - without discarding the rest if
    the year turns out to be wrong."""
    text, year = _split_query_year(query)
    corrected_text = _autocorrected_query(text)
    text_variants = [text, *([corrected_text] if corrected_text else [])]

    # include_adult=false is already TMDB's own default for every one of
    # these endpoints - passed explicitly anyway so that's never silently
    # dependent on TMDB not changing it. Unlike discover(), there's no
    # without_keywords equivalent available on /search/* at all, so this
    # is weaker protection than the browse pages get (see _UNSAFE_KEYWORD_IDS).
    data = _list_request("search/multi", {"query": query, "page": page, "include_adult": "false"})
    results = [
        _normalize_result(item, item["media_type"])
        for item in (data.get("results") or [])
        if item.get("media_type") in ("movie", "tv")
    ]
    seen = {(r["tmdb_id"], r["media_type"]) for r in results}

    for variant in text_variants:
        if year:
            movie_data = _list_request(
                "search/movie", {"query": variant, "year": year, "page": page, "include_adult": "false"}
            )
            _merge_normalized(results, seen, movie_data.get("results") or [], "movie")
            tv_data = _list_request(
                "search/tv",
                {"query": variant, "first_air_date_year": year, "page": page, "include_adult": "false"},
            )
            _merge_normalized(results, seen, tv_data.get("results") or [], "tv")
        elif variant != query:
            # No year: the un-corrected variant equals query, already
            # covered by the baseline /search/multi call above - only the
            # corrected variant (if any) needs its own extra lookup.
            variant_data = _list_request("search/multi", {"query": variant, "page": page, "include_adult": "false"})
            _merge_normalized(
                results,
                seen,
                (item for item in (variant_data.get("results") or []) if item.get("media_type") in ("movie", "tv")),
                None,
            )

    if year:
        results.sort(key=lambda r: r["year"] != str(year))

    return {"results": results, "total_pages": data.get("total_pages") or 0}


def genres(media_type):
    """[{"id": 16, "name": "Animation"}, ...] - populates the filter
    panel's genre picker (TMDB's with_genres param takes ids, not names)."""
    data = _list_request(f"genre/{media_type}/list")
    return data.get("genres") or []


def discover(media_type, category="popular", page=1, genre_ids=None, year_from=None, year_to=None,
             runtime_from=None, runtime_to=None, rating_from=None, rating_to=None,
             original_language=None, origin_country=None, with_companies=None, certification=None,
             status=None, availability=None, watch_providers=None, region=None, page_size=RESULTS_PAGE_SIZE):
    """Returns {"results": [...normalized, up to page_size*20...], "page": int,
    "total_pages": int}. category picks a sort/date preset (see module docstring);
    every other param is an optional filter layered on top of that preset, all of
    them straight from TMDB's own documented /discover parameter set.

    watch_providers (a list of TMDB provider ids) requires watch_region
    alongside it, same as availability below - region falls back to
    AVAILABILITY_WATCH_REGION when not given, so an old call site that
    never passes a profile's region still behaves exactly as before.

    page_size overrides RESULTS_PAGE_SIZE per call - views.discover raises it
    when the Display filter is hiding watched/watchlisted titles, since a
    profile that's watched most of what's popular can otherwise filter a
    normal 60-item batch down to a handful of visible tiles or fewer; a
    bigger raw pool makes that far less likely without touching pagination
    math (still a plain, reversible page-number multiple, just a bigger
    one).

    include_adult/without_keywords are always applied, not opt-in filters -
    see _UNSAFE_KEYWORD_IDS for why (TMDB's own adult flag alone isn't
    reliable for this)."""
    date_field = "primary_release_date" if media_type == "movie" else "first_air_date"
    params = {"sort_by": "popularity.desc", "include_adult": "false", "without_keywords": _UNSAFE_KEYWORD_IDS}

    if category == "top_rated":
        params["sort_by"] = "vote_average.desc"
        params["vote_count.gte"] = 200  # otherwise a single 10/10 vote from one person tops the list

    # gte/lte are built from candidate dates rather than assigned directly,
    # since "upcoming"'s gte=today preset and a user's year_from filter (the
    # range slider always submits a value, even at its untouched default)
    # must combine rather than one silently clobbering the other - gte takes
    # the latest/strictest candidate, lte the earliest/strictest one.
    gte_candidates = []
    lte_candidates = []
    if category == "upcoming":
        import datetime

        params["sort_by"] = f"{date_field}.asc"
        gte_candidates.append(datetime.date.today().isoformat())
    # "trending" and "popular" both use the popularity.desc default above -
    # see the module docstring for why they're not more differentiated here.

    if genre_ids:
        params["with_genres"] = ",".join(str(g) for g in genre_ids)
    if year_from:
        gte_candidates.append(f"{year_from}-01-01")
    if year_to:
        lte_candidates.append(f"{year_to}-12-31")
    if gte_candidates:
        params[f"{date_field}.gte"] = max(gte_candidates)
    if lte_candidates:
        params[f"{date_field}.lte"] = min(lte_candidates)
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
    if certification and media_type == "movie":
        # TMDB requires a country alongside an exact certification match -
        # no discover/tv equivalent exists (see MOVIE_CERTIFICATIONS), so
        # this is silently a no-op for tv/anime rather than an error.
        params["certification_country"] = "US"
        params["certification"] = certification
    if status and media_type == "tv":
        # No /discover/movie equivalent (see TV_STATUSES) - silently a
        # no-op for movies, same shape as the certification no-op above.
        params["with_status"] = TV_STATUSES[status]
    if availability:
        params["watch_region"] = region or AVAILABILITY_WATCH_REGION
        params["with_watch_monetization_types"] = AVAILABILITY_CHOICES[availability]
    if watch_providers:
        params["with_watch_providers"] = "|".join(str(p) for p in watch_providers)
        params["watch_region"] = region or AVAILABILITY_WATCH_REGION

    # page_size real TMDB pages get merged into one logical Spool page (see
    # RESULTS_PAGE_SIZE) - fetched as one sequential call (page 1) followed
    # by up to page_size-1 more *in parallel*, not one-after-another. The
    # first page has to come first regardless: its own total_pages is what
    # tells the rest of this function how many of the remaining pages are
    # even worth requesting (TMDB returns an empty results list rather than
    # an error for an out-of-range page, but there's no reason to spend a
    # request finding that out when the first page already said so).
    # Resolved once here rather than left to each _list_request call's own
    # _api_key() - that reads InstanceConfig from the DB, and the parallel
    # calls below run on worker threads that should never need to touch
    # the DB independently/concurrently themselves.
    api_key = _api_key()
    tmdb_start_page = (page - 1) * page_size + 1
    results = []
    first_data = _list_request(f"discover/{media_type}", {**params, "page": tmdb_start_page}, api_key=api_key)
    total_pages_raw = first_data.get("total_pages") or 0
    first_results = first_data.get("results") or []
    results.extend(_normalize_result(r, media_type) for r in first_results)

    if first_results and page_size > 1 and tmdb_start_page < total_pages_raw:
        remaining_pages = [
            p for p in range(tmdb_start_page + 1, tmdb_start_page + page_size) if p <= total_pages_raw
        ]
        if remaining_pages:
            with ThreadPoolExecutor(max_workers=len(remaining_pages)) as executor:
                # executor.map yields results in the order its inputs were
                # given, not completion order (blocking on each in turn as
                # needed) - so this merges in TMDB page order every time,
                # regardless of which parallel request actually finishes
                # first.
                for page_data in executor.map(
                    lambda p: _list_request(f"discover/{media_type}", {**params, "page": p}, api_key=api_key),
                    remaining_pages,
                ):
                    page_results = page_data.get("results") or []
                    results.extend(_normalize_result(r, media_type) for r in page_results)

    return {
        "results": results,
        "page": page,
        "total_pages": -(-total_pages_raw // page_size) if total_pages_raw else 0,
    }


# Earliest decade offered by the Year filter's chip row (discover.html) -
# TMDB's own catalog thins out well before this for anything reliably
# discoverable, so there's no real value in reaching further back.
DECADE_START = 1950


def decades_through_now():
    """[(decade_start, is_current), ...] from DECADE_START through the
    decade containing today - e.g. (1950, False), (1960, False), ...,
    (2020, True) as of 2026. Computed per call (not hardcoded) so a new
    decade appears in the Year filter on its own once the calendar rolls
    into it, and is_current lets the template label that one "(so far)"
    rather than reading like a decade that's already finished."""
    import datetime

    current_decade_start = (datetime.date.today().year // 10) * 10
    return [(start, start == current_decade_start) for start in range(DECADE_START, current_decade_start + 1, 10)]


def discover_by_decades(media_type, category="popular", page=1, decades=None, page_size=RESULTS_PAGE_SIZE, **filters):
    """discover()'s own Year filter, widened to accept several *disjoint*
    decades at once (Genre-style OR - "1980s or 2020s") instead of
    discover()'s single contiguous year_from/year_to range, which
    can't express that: TMDB's /discover has no OR-of-date-ranges param
    the way with_genres has an OR-of-ids one (see discover()'s own
    with_genres line), so >1 decade means one real /discover call per
    decade, merged and deduped here - the same "no single-call solution
    exists, so issue N calls and merge" shape collections() above already
    uses, just keyed on decade instead of per-movie detail lookups.

    0 or 1 decades - by far the common case, since most people never
    touch Year at all, and picking exactly one decade is still just an
    ordinary year range - both cost exactly one real discover() call
    with exact pagination, identical to calling discover() directly (no
    merge overhead, no approximation). Only 2+ decades at once pays for
    the extra calls and an approximated total_pages: each decade's own
    total_pages is already just an estimate (see discover()'s own
    docstring), and there's no way to know the *combined* filter's true
    result count without exhausting every decade's every page, so this
    takes the largest of the per-decade estimates - undercounts rather
    than overcounts, so pagination runs out a little early rather than
    offering a "page 40" that comes back empty.

    Results are round-robin interleaved across decades (one from 1980s,
    one from 2020s, one from 1980s, ...) rather than pooled and sorted
    by popularity/rating - a flat popularity sort would let a recency-
    biased decade (TMDB's own "popularity" score heavily favors current
    releases) bury an older one entirely off the first page, which
    defeats the point of picking "1980s and 2020s" for an actual mix of
    both - confirmed live: an early popularity-sorted version returned a
    page of nothing but current-year movies despite 1980 being selected
    too. Each decade's own results stay in TMDB's own per-category order
    internally; only the interleaving is done here.

    page_size is forwarded to every underlying discover() call unchanged -
    see that function's own docstring."""
    if not decades:
        return discover(media_type, category=category, page=page, page_size=page_size, **filters)
    if len(decades) == 1:
        decade = decades[0]
        return discover(
            media_type, category=category, page=page, year_from=decade, year_to=decade + 9,
            page_size=page_size, **filters,
        )

    per_decade_results = []
    total_pages_estimate = 0
    for decade in decades:
        decade_page = discover(
            media_type, category=category, page=page, year_from=decade, year_to=decade + 9,
            page_size=page_size, **filters,
        )
        total_pages_estimate = max(total_pages_estimate, decade_page["total_pages"])
        per_decade_results.append(decade_page["results"])

    seen = set()
    results = []
    for row in itertools.zip_longest(*per_decade_results):
        for item in row:
            if item is None:
                continue
            key = (item["tmdb_id"], item["media_type"])
            if key in seen:
                continue
            seen.add(key)
            results.append(item)

    return {"results": results, "page": page, "total_pages": total_pages_estimate}


# Movie franchises (John Wick, Indiana Jones, ...) for the Movies & TV
# page's "Collections" tab - TMDB, unlike trending/popular/upcoming/top
# rated above, has no /discover-style endpoint for these at all: no
# "popular collections" or "trending collections" list exists, and
# belongs_to_collection is only present on a movie's own full /movie/{id}
# details, never on the compact /movie/popular list-result shape. So
# there's no way to build this without per-movie detail lookups - this
# scans currently-popular movies (already how "Popular" itself is
# approximated) and collects whichever collections they belong to, in
# roughly popularity order. Every one of those lookups goes through
# _list_request's own 6h cache, so this is only ever slow (up to
# movies_to_scan real requests) on a cold cache; every load after that
# is free until the cache expires.
def collections(limit=40, movies_to_scan=300):
    seen = {}
    page = 1
    while len(seen) < limit and (page - 1) * 20 < movies_to_scan:
        data = _list_request("movie/popular", {"page": page})
        results = (data or {}).get("results") or []
        if not results:
            break
        for movie in results:
            details = _list_request(f"movie/{movie['id']}")
            collection = (details or {}).get("belongs_to_collection")
            if collection and collection["id"] not in seen:
                poster_path = collection.get("poster_path")
                seen[collection["id"]] = {
                    "id": collection["id"],
                    "name": collection.get("name") or "Untitled Collection",
                    "poster_url": f"{IMAGE_BASE}{poster_path}" if poster_path else None,
                }
                if len(seen) >= limit:
                    break
        page += 1
    return list(seen.values())


def get_collection_details(collection_id):
    """{"id", "name", "overview", "poster_url", "backdrop_url", "parts":
    [...normalized movies, same shape discover()/get_similar() use...]}
    or None if TMDB has nothing for this id (also covers "no API key
    configured", since _list_request returns {} in that case too)."""
    data = _list_request(f"collection/{collection_id}")
    if not data or data.get("id") is None:
        return None
    poster_path = data.get("poster_path")
    backdrop_path = data.get("backdrop_path")
    parts = [_normalize_result(p, "movie") for p in data.get("parts") or []]
    parts.sort(key=lambda p: p["year"] or "")
    return {
        "id": data["id"],
        "name": data.get("name") or "Untitled Collection",
        "overview": data.get("overview", ""),
        "poster_url": f"{IMAGE_BASE}{poster_path}" if poster_path else None,
        "backdrop_url": f"{BACKDROP_BASE}{backdrop_path}" if backdrop_path else None,
        "parts": parts,
    }


# --- Title detail page (tracker/views.title_detail / title_preview) -----
#
# Unlike find_match/get_movie_details/get_tv_details above (each narrowly
# scoped to what an existing caller needed), these back a page that shows
# everything at once - overview, genres, runtime, cast, similar titles -
# so each pulls its own TMDB endpoint rather than composing the narrower
# helpers, and all three go through _list_request for the same caching/
# timeout/no-crash-without-a-key behavior as discover().

BACKDROP_BASE = "https://image.tmdb.org/t/p/w1280"
PROFILE_BASE = "https://image.tmdb.org/t/p/w185"


# TMDB's raw `status` string (both movie and tv share the field, but use
# different vocabularies) mapped to a short display label + a daisyUI
# semantic color to badge it with on the title detail hero. "Released" is
# deliberately omitted - a released movie is the default/expected state
# and doesn't need a badge, whereas everything else here is worth calling
# out (spool-product-spec.md has no prior art for this; picked to mirror
# how Trakt/TMDB's own web UIs label the same statuses).
STATUS_BADGES = {
    "Returning Series": {"label": "Ongoing", "color": "success"},
    "Ended": {"label": "Ended", "color": "ink-dim"},
    "Canceled": {"label": "Cancelled", "color": "error"},
    "In Production": {"label": "In Production", "color": "info"},
    "Planned": {"label": "Upcoming", "color": "info"},
    "Pilot": {"label": "Pilot", "color": "ink-dim"},
    "Post Production": {"label": "Post Production", "color": "info"},
    "Rumored": {"label": "Rumored", "color": "ink-dim"},
}


def status_badge(status):
    """{"label", "color"} for the title detail hero's status badge, or
    None if this status isn't worth badging (e.g. a released movie)."""
    return STATUS_BADGES.get(status)


def media_type_for(title):
    """external_ids["tmdb_kind"] is the authoritative source (set at match
    time by trakt.py/simkl.py/csv_import.py) since anime is almost always
    matched against TMDB's tv catalog, not movie - title.media_type alone
    can't be trusted to pick the right TMDB endpoint."""
    from tracker.models import MediaType

    return title.external_ids.get("tmdb_kind") or ("movie" if title.media_type == MediaType.MOVIE else "tv")


def _extract_certification(data, is_movie):
    """The US age rating - movies from the append_to_response=release_dates
    payload (several entries per country, one per release type/re-release;
    most carry an empty certification string except whichever one actually
    set it, so the first non-empty one wins), tv from
    append_to_response=content_ratings (one rating per country, no
    per-release-type noise to filter). Deliberately US-only - the rest of
    this app has no per-profile region setting to pick a different country
    by, same simplification as discover()'s own certification_country."""
    if is_movie:
        countries = (data.get("release_dates") or {}).get("results") or []
    else:
        countries = (data.get("content_ratings") or {}).get("results") or []
    us = next((c for c in countries if c.get("iso_3166_1") == "US"), None)
    if not us:
        return None
    if is_movie:
        return next((rd["certification"] for rd in us.get("release_dates") or [] if rd.get("certification")), None)
    return us.get("rating") or None


#  ISO 3166-1 alpha-2 -> display name, for tv/anime's origin_country codes
#  (movies get full names straight from TMDB's own production_countries,
#  see below) - a curated map, not a live /configuration/countries fetch,
#  same "hardcode a manageable lookup" convention as views.DISCOVER_REGIONS,
#  just scoped to whatever's realistic as a TV/anime production country
#  rather than that picker's narrower watch-region list. An origin_country
#  code missing from this map (rare) falls back to the raw code itself
#  rather than raising - a slightly-off display beats an error.
_ISO_COUNTRY_NAMES = {
    "US": "United States", "GB": "United Kingdom", "CA": "Canada", "AU": "Australia",
    "IE": "Ireland", "NZ": "New Zealand", "DE": "Germany", "FR": "France", "ES": "Spain",
    "IT": "Italy", "NL": "Netherlands", "SE": "Sweden", "NO": "Norway", "DK": "Denmark",
    "FI": "Finland", "PL": "Poland", "BR": "Brazil", "MX": "Mexico", "JP": "Japan",
    "KR": "South Korea", "IN": "India", "CN": "China", "TW": "Taiwan", "HK": "Hong Kong",
    "TH": "Thailand", "PH": "Philippines", "ID": "Indonesia", "VN": "Vietnam", "TR": "Turkey",
    "AT": "Austria", "CH": "Switzerland", "PT": "Portugal", "BE": "Belgium", "CZ": "Czech Republic",
    "HU": "Hungary", "RO": "Romania", "GR": "Greece", "RU": "Russia", "UA": "Ukraine",
    "IL": "Israel", "ZA": "South Africa", "AR": "Argentina", "CO": "Colombia", "CL": "Chile",
    "IS": "Iceland", "SG": "Singapore", "MY": "Malaysia",
}


def get_full_details(media_type, tmdb_id):
    """{"name", "year", "overview", "tagline", "genres": [str,...],
    "runtime": int|None (movie), "number_of_seasons"/"number_of_episodes":
    int|None (tv), "backdrop_url", "poster_url", "vote_average",
    "vote_count", "original_language", "status": str|None,
    "certification": str|None (US age rating - "PG-13"/"R" for a movie,
    "TV-14"/"TV-MA" for a show; see _extract_certification),
    "release_date": str|None (movie, raw YYYY-MM-DD, distinct from the
    truncated "year" above), "first_air_date"/"last_air_date": str|None
    (tv, raw YYYY-MM-DD - a show's own run may not fit in a single date
    the way a movie's release does, since seasons can drop all at once
    or air weekly; views._release_info turns these plus "status" into
    the detail hero's "aired Jan 2020 · Ongoing"-style summary),
    "next_episode_to_air"/"last_episode_to_air": dict|None (tv only,
    {"air_date", "season_number", "episode_number", "name"} - used by
    release_sync.py to pick which season to pull the full episode list
    for: next_episode_to_air's season when the show has one upcoming,
    else last_episode_to_air's season as a fallback for an ended/
    between-seasons show, so its most recent season still shows up on
    the calendar), "budget"/"revenue":
    int|None (movie only - TMDB returns 0, not null, when unknown; that's
    normalized to None here since "$0" reads as data, not "no data"),
    "production_companies": [str,...], "countries": [str,...] (movie:
    full names from production_countries; tv/anime: origin_country's ISO
    codes resolved through _ISO_COUNTRY_NAMES above), "networks": [str,...]
    (tv/anime only, always [] for a movie - broadcast/streaming networks,
    used for Title.network/the Reports API), "collection_id": int|None
    (movie only - TMDB's belongs_to_collection, e.g. "Iron Man Collection";
    tv has no franchise-grouping concept at all, so this is always None for
    a show. Feed straight into get_collection_details() for the other
    entries)} or None if nothing came back (no api key, bad id, network
    error)."""
    is_movie = media_type == "movie"
    append = "release_dates" if is_movie else "content_ratings"
    data = _list_request(f"{media_type}/{tmdb_id}", {"append_to_response": append})
    if not data or data.get("id") is None:
        return None
    date = data.get("release_date") if is_movie else data.get("first_air_date")
    backdrop_path = data.get("backdrop_path")
    poster_path = data.get("poster_path")
    next_episode = None
    last_episode = None
    if not is_movie:
        raw_next = data.get("next_episode_to_air") or {}
        if raw_next.get("air_date"):
            next_episode = {
                "air_date": raw_next["air_date"],
                "season_number": raw_next.get("season_number"),
                "episode_number": raw_next.get("episode_number"),
                "name": raw_next.get("name") or "",
            }
        raw_last = data.get("last_episode_to_air") or {}
        if raw_last.get("air_date"):
            last_episode = {
                "air_date": raw_last["air_date"],
                "season_number": raw_last.get("season_number"),
                "episode_number": raw_last.get("episode_number"),
                "name": raw_last.get("name") or "",
            }
    return {
        "tmdb_id": tmdb_id,
        "media_type": media_type,
        "name": (data.get("title") if is_movie else data.get("name")) or "Untitled",
        "year": date[:4] if date else None,
        "overview": data.get("overview") or "",
        "tagline": data.get("tagline") or "",
        "genres": [g["name"] for g in data.get("genres") or []],
        "runtime": data.get("runtime") if is_movie else None,
        "number_of_seasons": data.get("number_of_seasons") if not is_movie else None,
        "number_of_episodes": data.get("number_of_episodes") if not is_movie else None,
        "backdrop_url": f"{BACKDROP_BASE}{backdrop_path}" if backdrop_path else None,
        "poster_url": f"{IMAGE_BASE}{poster_path}" if poster_path else None,
        "vote_average": data.get("vote_average"),
        "vote_count": data.get("vote_count"),
        "original_language": data.get("original_language"),
        "status": data.get("status"),
        "certification": _extract_certification(data, is_movie),
        "release_date": date if is_movie else None,
        "first_air_date": None if is_movie else data.get("first_air_date"),
        "last_air_date": None if is_movie else data.get("last_air_date"),
        "next_episode_to_air": next_episode,
        "last_episode_to_air": last_episode,
        "budget": data.get("budget") if is_movie and data.get("budget") else None,
        "revenue": data.get("revenue") if is_movie and data.get("revenue") else None,
        "production_companies": [c["name"] for c in data.get("production_companies") or [] if c.get("name")],
        "countries": (
            [c["name"] for c in data.get("production_countries") or [] if c.get("name")]
            if is_movie
            else [_ISO_COUNTRY_NAMES.get(code, code) for code in data.get("origin_country") or []]
        ),
        # tv/anime only - Title.network/the Reports API's per-entry
        # "network" field (see Title's own field comment). Movies have no
        # equivalent concept, so this key is simply absent for a movie.
        "networks": [] if is_movie else [n["name"] for n in data.get("networks") or [] if n.get("name")],
        "collection_id": (data.get("belongs_to_collection") or {}).get("id") if is_movie else None,
    }


def get_credits(media_type, tmdb_id, limit=12):
    """[{"name", "character", "profile_url", "tmdb_person_id"}, ...]
    billing-ordered cast, or [] if nothing came back. tmdb_person_id
    (None if TMDB's own entry has no id, which shouldn't normally happen)
    is what the title detail page's Cast row links to /person/<id>/ with."""
    data = _list_request(f"{media_type}/{tmdb_id}/credits")
    cast = (data or {}).get("cast") or []
    results = []
    for person in cast[:limit]:
        profile_path = person.get("profile_path")
        results.append(
            {
                "name": person.get("name") or "",
                "character": person.get("character") or "",
                "profile_url": f"{PROFILE_BASE}{profile_path}" if profile_path else None,
                "tmdb_person_id": person.get("id"),
            }
        )
    return results


def get_similar(media_type, tmdb_id, limit=12):
    """Normalized results (same shape as discover()'s) for "if you like
    this, check out..." - TMDB's own recommendations, not a fresh
    /discover call, so it reflects TMDB's similarity model rather than
    just "popular in the same genre"."""
    data = _list_request(f"{media_type}/{tmdb_id}/recommendations")
    results = (data or {}).get("results") or []
    return [_normalize_result(r, media_type) for r in results[:limit]]


def get_director(media_type, tmdb_id):
    """{"name", "profile_url", "tmdb_person_id"} for the credited director,
    or None - shaped like get_credits()'s cast entries so the title detail
    page can render the director as the lead entry in the same Cast row,
    not a separate element. Hits the same /credits endpoint as
    get_credits() - _list_request's cache means calling both for the same
    title in one request doesn't cost a second real HTTP call, so this
    doesn't need its own cache-key/network story."""
    data = _list_request(f"{media_type}/{tmdb_id}/credits")
    crew = (data or {}).get("crew") or []
    director = next((c for c in crew if c.get("job") == "Director"), None)
    if not director:
        return None
    profile_path = director.get("profile_path")
    return {
        "name": director.get("name") or "",
        "profile_url": f"{PROFILE_BASE}{profile_path}" if profile_path else None,
        "tmdb_person_id": director.get("id"),
    }


# TMDB has no single consistent "the writer" job title the way "Director"
# is for directing - these are the job strings it actually uses across the
# Writing department (a writing-credit crew entry's "department" is always
# "Writing", but "job" varies by what kind of writing credit it was).
_WRITER_JOBS = {"Writer", "Screenplay", "Story", "Teleplay"}


def get_writers(media_type, tmdb_id):
    """[str, ...] names of everyone credited in TMDB's Writing department
    for this title (see _WRITER_JOBS above), or [] - same /credits
    endpoint as get_credits()/get_director(), cached the same way, so
    calling all three for one title costs one real HTTP call, not three.
    Unlike get_director() this collects every match, not just the first -
    a title can have several credited writers and the Reports API's
    "writers" field is a list."""
    data = _list_request(f"{media_type}/{tmdb_id}/credits")
    crew = (data or {}).get("crew") or []
    seen = set()
    names = []
    for c in crew:
        if c.get("job") not in _WRITER_JOBS:
            continue
        name = c.get("name")
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def get_reports_metadata(media_type, tmdb_id, details):
    """{"country", "studio", "network", "cast", "directors", "writers"} -
    the six Title fields the Reports API's Year in Review needs (see
    Title's own field comments and models.attach_reports_metadata).
    details is whatever get_full_details() already returned for this
    title - country/studio/network are read straight from it instead of
    a second network call; cast/directors/writers cost one more real
    HTTP call to /credits (cached, and shared across all three - see
    get_writers()'s own docstring)."""
    is_movie = media_type == "movie"
    countries = details.get("countries") or []
    companies = details.get("production_companies") or []
    networks = details.get("networks") or []
    cast_names = [c["name"] for c in get_credits(media_type, tmdb_id, limit=5) if c["name"]]
    director = get_director(media_type, tmdb_id)
    return {
        "country": countries[0] if countries else "",
        "studio": companies[0] if is_movie and companies else "",
        "network": networks[0] if not is_movie and networks else "",
        "cast": cast_names,
        "directors": [director["name"]] if director and director["name"] else [],
        "writers": get_writers(media_type, tmdb_id),
    }


def get_season_details(tmdb_id, season_number):
    """{"episodes": [{"episode_number", "name", "still_url", "air_date", "runtime", "vote_average"}, ...]}
    or None if nothing came back. Always the /tv/ endpoint regardless of
    the title's own media_type - anime is matched against TMDB's tv
    catalog same as any other show (see media_type_for()), and seasons
    only exist there; movies never call this at all. "runtime" is this
    specific episode's own minutes (None if TMDB doesn't have it) - a
    finer-grained figure than get_tv_details()'s show-level "typical"
    episode_run_time, and worth carrying since completion.py falls back
    to it per-episode when the coarse figure is missing or wrong.
    "vote_average" is TMDB's own public rating for this one episode
    (0 or missing means "not enough votes yet," same convention as the
    title-level vote_average elsewhere in this module - callers should
    treat a falsy value as "no rating," not a real zero)."""
    data = _list_request(f"tv/{tmdb_id}/season/{season_number}")
    if not data or data.get("id") is None:
        return None
    episodes = []
    for ep in data.get("episodes") or []:
        still_path = ep.get("still_path")
        episodes.append(
            {
                "episode_number": ep.get("episode_number"),
                "name": ep.get("name") or "",
                "still_url": f"{IMAGE_BASE}{still_path}" if still_path else None,
                "air_date": ep.get("air_date"),
                "runtime": ep.get("runtime"),
                "vote_average": ep.get("vote_average"),
            }
        )
    return {"episodes": episodes}


def get_watch_providers(media_type, tmdb_id, region="US"):
    """[{"name", "logo_url"}, ...] flatrate/free/ad-supported streaming
    availability for one region - TMDB's data is region-keyed and Spool
    has no per-profile region setting yet, so this defaults to "US"
    rather than trying to guess. [] if nothing came back for that region."""
    data = _list_request(f"{media_type}/{tmdb_id}/watch/providers")
    region_data = ((data or {}).get("results") or {}).get(region) or {}
    seen = set()
    providers = []
    for key in ("flatrate", "free", "ads"):
        for p in region_data.get(key) or []:
            name = p.get("provider_name")
            if not name or name in seen:
                continue
            seen.add(name)
            logo_path = p.get("logo_path")
            providers.append({"name": name, "logo_url": f"{PROFILE_BASE}{logo_path}" if logo_path else None})
    return providers[:6]


def get_trailer(media_type, tmdb_id):
    """{"key": youtube_video_id, "name": str} for the best trailer TMDB
    has on file, or None if there isn't one - the detail hero's "Watch
    trailer" button and the Media gallery's own first tile are both
    absent (not a disabled/empty state) when this is None, per that
    feature's own contract. Only ever a YouTube id: TMDB does list
    Vimeo-hosted videos for some titles, but the embed markup this
    feeds (title_detail.html's trailer <dialog>) is YouTube-specific.
    Prefers whichever trailer TMDB flags "official" (its own curated
    pick, usually the studio's real uploaded trailer rather than a fan
    reupload); among ties (or when nothing is flagged official at all)
    picks the most recently published one, same "freshest wins"
    tiebreak get_similar's own caller-side sorting uses elsewhere.
    "Trailer" specifically, not Teaser/Clip/Featurette/Behind the Scenes -
    those are real TMDB video types on the same endpoint but aren't a
    stand-in for the real thing this button promises."""
    data = _list_request(f"{media_type}/{tmdb_id}/videos")
    results = (data or {}).get("results") or []
    trailers = [v for v in results if v.get("site") == "YouTube" and v.get("type") == "Trailer" and v.get("key")]
    if not trailers:
        return None
    official = [v for v in trailers if v.get("official")]
    pool = official or trailers
    best = max(pool, key=lambda v: v.get("published_at") or "")
    return {"key": best["key"], "name": best.get("name") or ""}


_BACKDROP_THUMB_BASE = "https://image.tmdb.org/t/p/w300"


def get_backdrops(media_type, tmdb_id, limit=12):
    """[{"url" (full w1280, for the lightbox), "thumbnail_url" (w300, for
    the gallery strip's own small tiles)}, ...] widescreen backdrop
    stills for the Media gallery, TMDB's own best-first ordering (its
    images endpoint sorts by vote_average descending already) -
    re-sorted explicitly here rather than trusted, since that ordering
    isn't documented as a guarantee. Two separate sizes rather than one
    URL downsized via the |poster_size template filter elsewhere in this
    app: that filter only ever rewrites IMAGE_BASE's own "w500" segment,
    a no-op against BACKDROP_BASE's "w1280". [] if nothing came back."""
    data = _list_request(f"{media_type}/{tmdb_id}/images")
    backdrops = (data or {}).get("backdrops") or []
    backdrops.sort(key=lambda b: b.get("vote_average") or 0, reverse=True)
    return [
        {"url": f"{BACKDROP_BASE}{b['file_path']}", "thumbnail_url": f"{_BACKDROP_THUMB_BASE}{b['file_path']}"}
        for b in backdrops[:limit]
        if b.get("file_path")
    ]


_PROVIDER_BUNDLE_NAME_MARKERS = ("channel", "with ads")  # see watch_provider_catalog's own docstring


def watch_provider_catalog(media_type, region="US"):
    """[{"id", "name", "logo_url"}, ...] TMDB's streaming-provider catalog
    for a region, sorted by TMDB's own display_priority (most prominent/
    popular first) - Settings' provider-preference chip picker and
    Discover's own provider filter. Unlike get_watch_providers above
    (per-title availability), this is the full regional catalog, used to
    populate a *filter* rather than to show what's available on one
    title - same "thin wrapper over _list_request" shape as genres().

    TMDB's raw regional catalog is mostly not standalone apps - it's
    padded with a given service resold as a bundle/add-on through another
    storefront ("Starz Amazon Channel", "AMC+ Apple TV Channel", ...) and
    ad-supported tiers of a service already listed plainly ("Amazon Prime
    Video with Ads" alongside "Amazon Prime Video") - noise a filter
    picker doesn't need duplicated. TMDB has no clean "is a bundle" flag,
    so these are dropped by a name-pattern heuristic instead - the
    accepted trade-off is a real provider whose only name happens to
    contain "Channel" would be dropped too, in exchange for the list
    fitting on screen instead of running to 100+ entries."""
    data = _list_request(f"watch/providers/{media_type}", {"watch_region": region})
    results = sorted(data.get("results") or [], key=lambda p: p.get("display_priority", 999))
    providers = []
    for p in results:
        provider_id = p.get("provider_id")
        name = p.get("provider_name")
        if not provider_id or not name:
            continue
        lowered = name.lower()
        if any(marker in lowered for marker in _PROVIDER_BUNDLE_NAME_MARKERS):
            continue
        logo_path = p.get("logo_path")
        providers.append({"id": provider_id, "name": name, "logo_url": f"{PROFILE_BASE}{logo_path}" if logo_path else None})
    return providers


# --- Person detail page (tracker/views.person_detail) -------------------

CREDIT_CAP = 40  # public - views.person_detail surfaces this in the stats card's cap tooltip


def get_person_details(person_id):
    """{"id", "name", "biography", "birthday", "deathday",
    "place_of_birth", "profile_url", "known_for_department"} or None if
    nothing came back (no api key, bad id, network error) - same failure
    convention as get_collection_details/get_season_details above."""
    data = _list_request(f"person/{person_id}")
    if not data or data.get("id") is None:
        return None
    profile_path = data.get("profile_path")
    return {
        "id": data["id"],
        "name": data.get("name") or "",
        "biography": data.get("biography") or "",
        "birthday": data.get("birthday"),
        "deathday": data.get("deathday"),
        "place_of_birth": data.get("place_of_birth"),
        "profile_url": f"{PROFILE_BASE}{profile_path}" if profile_path else None,
        "known_for_department": data.get("known_for_department"),
    }


def _normalize_credit(item):
    """Same normalized shape _normalize_result produces (tmdb_id,
    media_type, name, year, poster_url, vote_average - so these render
    through discover_tile.html exactly like "if you like this" does),
    plus release_date/vote_count kept for get_person_credits' own
    sort/cap use - discover_tile.html ignores keys it doesn't read.
    Reads media_type off the credit item itself rather than taking a
    single passed-in value, since a person's combined_credits response
    mixes movie and tv entries in one list."""
    media_type = item.get("media_type") or "movie"
    is_movie = media_type == "movie"
    date = item.get("release_date") if is_movie else item.get("first_air_date")
    poster_path = item.get("poster_path")
    return {
        "tmdb_id": item.get("id"),
        "media_type": media_type,
        "name": (item.get("title") if is_movie else item.get("name")) or "Untitled",
        "year": date[:4] if date else None,
        "release_date": date,
        "poster_url": f"{IMAGE_BASE}{poster_path}" if poster_path else None,
        "vote_average": item.get("vote_average"),
        "vote_count": item.get("vote_count") or 0,
    }


def get_person_credits(person_id):
    """{"acting": [...], "directing": [...], "writing": [...]} - each a
    list of normalized credit dicts (see _normalize_credit), deduped by
    tmdb_id within its own section (a person can hold two crew jobs on
    one title - e.g. Writer + Story - which would otherwise list the
    same poster twice) and capped to the CREDIT_CAP most-notable
    credits (TMDB's own vote_count, highest first) per section - a
    prolific person's full combined_credits can run past 200 entries,
    and matching each against the local library costs one query per
    credit (see selectors.discover_action_context's own docstring for
    why that's not batched), so capping keeps one page load's query
    count bounded. Scoped to just these three departments - TMDB's
    combined_credits crew list spans many more (Production, Sound,
    Editing...) that this page doesn't surface. Every section is []
    rather than missing when there's nothing, so callers don't need an
    extra "credits is None" branch on top of "list is empty"."""
    data = _list_request(f"person/{person_id}/combined_credits")
    cast = (data or {}).get("cast") or []
    crew = (data or {}).get("crew") or []

    def dedupe_and_cap(items):
        seen = {}
        for item in items:
            seen.setdefault(item["tmdb_id"], item)
        ordered = sorted(seen.values(), key=lambda c: c["vote_count"], reverse=True)
        return ordered[:CREDIT_CAP]

    return {
        "acting": dedupe_and_cap([_normalize_credit(c) for c in cast]),
        "directing": dedupe_and_cap([_normalize_credit(c) for c in crew if c.get("department") == "Directing"]),
        "writing": dedupe_and_cap([_normalize_credit(c) for c in crew if c.get("department") == "Writing"]),
    }
