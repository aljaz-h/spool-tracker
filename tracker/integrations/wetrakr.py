"""WeTrakr OAuth2 (PKCE) + history sync.

Implemented against WeTrakr's own public API docs (api.wetrakr.com) - a
brand-new, Trakt-shaped REST API (OAuth2 + PKCE, catalog/scrobble/sync
endpoints under the same naming conventions Trakt uses). This environment
has no real WeTrakr developer app credentials to exercise it against, so
every network-calling function here is unverified against the live API -
same caveat trakt.py's own module docstring carries, for the same reason.
upsert_history_items()/normalize_history_item() are pure data-shuffling
with no network dependency and *are* verified, via unit tests against a
fixture modeled on WeTrakr's documented response shape.

Two things WeTrakr does differently from Trakt, both load-bearing here:

1. PKCE: the authorization-code exchange needs a code_verifier/
   code_challenge pair (generate_pkce_pair/authorize_url/exchange_code),
   not just client_id/client_secret. The refresh grant doesn't need PKCE
   - refresh_access_token's signature matches trakt.py's exactly, so
   tasks._call_with_refresh works against this module unchanged.

2. A daily API request quota per connected user (free plan: 1000/day,
   reset at UTC midnight) plus an app-wide quota shared across every
   account connected to this Spool instance (18,600/day). There are no
   X-RateLimit-* response headers on this API at all - the only signal is
   a 429 with a JSON body carrying one of four error codes:
   CONCURRENCY_LIMIT (a short per-minute window - retried once here,
   transparently, same as tenrai.py's _throttle/_get pattern) or
   QUOTA_EXCEEDED / APP_QUOTA_EXCEEDED / APP_USER_QUOTA_EXCEEDED (a
   day-scale ceiling - NOT retried here; raised as WeTrakrQuotaError so
   the caller can pause and resume later, since "sleep and retry" makes
   no sense for something that only resets at midnight). See
   tracker/tasks.py's quota bookkeeping (_wetrakr_account_budget_ok etc.)
   and _scan_wetrakr_session for how a WeTrakrQuotaError actually pauses
   a sync/scan.

Before relying on this in production: confirm the exact field names in a
real response with valid credentials, confirm the pagination cursor shape
documented below against a live account, and confirm whether CONCURRENCY_LIMIT's
429 actually carries a usable Retry-After header (assumed here, matching
what WeTrakr's own docs say, not independently observed).
"""

import base64
import hashlib
import logging
import secrets
import time
from urllib.parse import urlencode

import requests

logger = logging.getLogger(__name__)

AUTHORIZE_URL = "https://wetrakr.com/oauth/authorize"
TOKEN_URL = "https://api.wetrakr.com/oauth/token"
TOKEN_REFRESH_URL = "https://api.wetrakr.com/oauth/token/refresh"
API_BASE = "https://api.wetrakr.com"

# Resumable fetch_history_page walks these two target resources in order -
# see that function's own docstring for why a single cursor string encodes
# both, rather than the caller having to know there are two underlying
# endpoints.
_HISTORY_TARGETS = ("movies", "episodes")


def generate_pkce_pair():
    """(code_verifier, code_challenge) for the S256 PKCE method - the
    verifier is stashed in the session by views.oauth_connect and read
    back in oauth_callback to pass through to exchange_code; it's never
    persisted anywhere past that one request/callback round trip."""
    verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def authorize_url(redirect_uri, state, client_id, code_challenge):
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def exchange_code(code, redirect_uri, client_id, client_secret, code_verifier):
    resp = requests.post(
        TOKEN_URL,
        json={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
            "grant_type": "authorization_code",
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def refresh_access_token(refresh_token, client_id, client_secret, redirect_uri):
    """WeTrakr's refresh grant - signature matches trakt.refresh_access_token
    exactly (no PKCE verifier needed here) so tasks._call_with_refresh works
    against this module with zero changes. redirect_uri is accepted for
    signature compatibility but WeTrakr's refresh grant may not actually
    require it the way Trakt's does - harmless to send either way."""
    resp = requests.post(
        TOKEN_REFRESH_URL,
        json={
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "refresh_token",
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def _headers(access_token, client_id):
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}",
    }


def test_credentials(client_id):
    """Settings' "Test connection" button. WeTrakr's /search/trending is
    assumed public (no user auth) the same way Trakt's /movies/trending
    is - unverified. Raises requests.RequestException on failure, same
    contract as trakt.test_credentials."""
    resp = requests.get(f"{API_BASE}/search/trending", params={"limit": 1}, timeout=10)
    resp.raise_for_status()


class WeTrakrQuotaError(requests.HTTPError):
    """A 429 whose body named one of WeTrakr's day-scale quota codes -
    QUOTA_EXCEEDED (this account's own free-plan daily cap),
    APP_QUOTA_EXCEEDED (this whole Spool instance's shared daily cap), or
    APP_USER_QUOTA_EXCEEDED (this account's share of the app-wide cap).
    Deliberately not raised for CONCURRENCY_LIMIT, which _request()
    retries inline instead - this exception only ever means "come back
    after the quota resets", not "try again in a moment"."""

    def __init__(self, error_code, message, response=None):
        super().__init__(message, response=response)
        self.error_code = error_code


_DAY_SCALE_ERROR_CODES = {"QUOTA_EXCEEDED", "APP_QUOTA_EXCEEDED", "APP_USER_QUOTA_EXCEEDED"}


def _request(method, path, access_token, client_id, **kwargs):
    """requests.request against API_BASE + path, aware of WeTrakr's 429
    error codes. CONCURRENCY_LIMIT (or any 429 without a recognized
    day-scale code) sleeps for Retry-After (default/parse-failure: 1s,
    capped at 5s, same bounds as tenrai.py's _get) and retries exactly
    once - mirrors tenrai.py's _throttle/_get pattern. Any of the three
    day-scale codes raises WeTrakrQuotaError immediately, no retry."""
    url = f"{API_BASE}{path}"
    headers = _headers(access_token, client_id)
    resp = requests.request(method, url, headers=headers, timeout=15, **kwargs)
    if resp.status_code == 429:
        error_code = None
        try:
            error_code = (resp.json() or {}).get("error")
        except ValueError:
            pass
        if error_code in _DAY_SCALE_ERROR_CODES:
            message = f"WeTrakr {error_code} on {method} {path}"
            raise WeTrakrQuotaError(error_code, message, response=resp)
        try:
            delay = float(resp.headers.get("Retry-After", 1))
        except (TypeError, ValueError):
            delay = 1.0
        logger.warning("WeTrakr rate-limited us (%s), retrying %s in %.1fs", error_code, path, min(delay, 5.0))
        time.sleep(min(delay, 5.0))
        resp = requests.request(method, url, headers=headers, timeout=15, **kwargs)
        if resp.status_code == 429:
            error_code = None
            try:
                error_code = (resp.json() or {}).get("error")
            except ValueError:
                pass
            raise WeTrakrQuotaError(error_code or "CONCURRENCY_LIMIT", f"WeTrakr still rate-limited on {method} {path}", response=resp)
    resp.raise_for_status()
    return resp


def get_plan_usage(access_token, client_id):
    """GET /plan-usage - {"plan": "free"|"vip"|"patron", "tier": ...,
    "tiers": [...], "features": [...]}. Requires the user's own bearer
    token (per WeTrakr's docs, this is an "oauth"-scoped endpoint, not a
    client-id-only one). Called once up front by a new scan (see
    tasks._check_wetrakr_plan) so Spool knows the account's real daily
    budget before starting a potentially-large pull, not just after
    discovering it via a 429."""
    resp = _request("GET", "/plan-usage", access_token, client_id)
    return resp.json()


def _parse_watched_at(item):
    from django.utils.dateparse import parse_datetime

    return parse_datetime(item.get("watched_at") or "")


def fetch_history_page(access_token, client_id, cursor=None, limit=200, start_at=None):
    """One page of watched history, from either of WeTrakr's two
    target-scoped endpoints (GET /sync/tracking/watched/history/movies
    and .../episodes - WeTrakr splits what Trakt's /sync/history returns
    as one unified feed into two separate resources). Returns
    (items, next_cursor) - items are tagged with a "type" key ("movie" or
    "episode") matching Trakt's own item shape, so upsert_history_items/
    normalize_history_item barely need to differ from trakt.py's.

    cursor encodes both "which target" and "which page within it" as a
    single opaque string ("movies:1", "movies:2", ..., "episodes:1", ...)
    so a caller (tasks._scan_wetrakr_session's resumable loop) just keeps
    calling this with whatever next_cursor it got back, with no need to
    know there are two underlying endpoints - this is the one thing that
    makes this module's resumable primitive genuinely different from
    trakt.fetch_history's "loop everything internally" shape; see this
    module's own top-of-file docstring point 2 for why that shape can't
    be paused/resumed.

    start_at: a datetime - when given, only history after that point is
    requested (incremental sync, used by routine sync's fetch_all_history
    below). None means a full history pull.

    Follows the same X-Pagination-Page-Count response-header pagination
    convention Trakt documents (unverified against a live WeTrakr
    account) - if WeTrakr's actual pagination shape differs, a sync
    should fail loudly via SyncLog rather than silently under/over-
    fetching, same expectation trakt.py's own fetch_history docstring
    sets."""
    if cursor is None:
        target, page = _HISTORY_TARGETS[0], 1
    else:
        target, _, page_str = cursor.partition(":")
        page = int(page_str)

    params = {"limit": limit}
    if start_at is not None:
        from .trakt import _format_trakt_datetime

        params["start_at"] = _format_trakt_datetime(start_at)
    params["page"] = page

    resp = _request("GET", f"/sync/tracking/watched/history/{target}", access_token, client_id, params=params)
    batch = resp.json()
    item_type = "movie" if target == "movies" else "episode"
    items = [{**raw, "type": item_type} for raw in batch]

    page_count = int(resp.headers.get("X-Pagination-Page-Count", page))
    if page < page_count:
        return items, f"{target}:{page + 1}"

    next_target_index = _HISTORY_TARGETS.index(target) + 1
    if next_target_index < len(_HISTORY_TARGETS):
        return items, f"{_HISTORY_TARGETS[next_target_index]}:1"

    return items, None


def fetch_all_history(access_token, client_id, start_at=None, max_pages=500):
    """Convenience wrapper for routine (already-incremental, normally-
    small) daily sync, which doesn't need pause/resume - loops
    fetch_history_page with no quota handling of its own. If a
    WeTrakrQuotaError is raised mid-loop it propagates straight out;
    tasks.sync_wetrakr_history's caller is what decides not to advance
    last_synced_at in that case (see that function's own docstring).
    max_pages is a hard safety bound, same reasoning as trakt.fetch_history's
    own - two targets x 500 pages x 200 items is far beyond any real
    personal watch history."""
    items = []
    cursor = None
    pages_fetched = 0
    while pages_fetched < max_pages:
        page_items, cursor = fetch_history_page(access_token, client_id, cursor=cursor, start_at=start_at)
        items.extend(page_items)
        pages_fetched += 1
        if cursor is None:
            break
    return items


def _get_or_create_title(media_type, name, year, wetrakr_id, tmdb_id=None):
    """Thin wrapper over tracker.title_matching.apply_title_match - same
    shared matching every other provider (and the Import Review pipeline's
    commit step) uses, so a title WeTrakr reports never forks a duplicate
    Title from one Trakt/Simkl/Nuvio already created. See trakt.py's own
    _get_or_create_title for the full rationale."""
    from tracker.title_matching import apply_title_match

    return apply_title_match(media_type, "wetrakr", wetrakr_id, name=name, year=year, tmdb_id=tmdb_id)


def upsert_history_items(profile, items, labels_out=None):
    """items: a list from fetch_all_history() (or the accumulated pages
    from a resumed Import Review scan). Mirrors trakt.upsert_history_items
    exactly - same dedup key (profile, title, episode, watched_at), same
    downstream batching - just keyed off a "wetrakr" id instead of a
    "trakt" one. See that function's own docstring for the full
    rationale; not re-documented here to avoid drift between two copies
    of the same explanation."""
    from django.utils.dateparse import parse_datetime

    from tracker import completion, recommendations, rewatches
    from tracker.models import Episode, MediaType, Title, WatchEvent

    created = 0
    touched_movies = set()
    touched_shows = set()
    touched_watch_keys = set()
    for item in items:
        watched_at = parse_datetime(item.get("watched_at", ""))
        if watched_at is None:
            continue

        if item.get("type") == "movie":
            m = item.get("movie") or {}
            ids = m.get("ids") or {}
            if "wetrakr" not in ids:
                continue
            title = _get_or_create_title(
                MediaType.MOVIE, m.get("title", "Untitled"), m.get("year"), ids["wetrakr"], ids.get("tmdb")
            )
            episode = None
            touched_movies.add(title.id)
        elif item.get("type") == "episode":
            s = item.get("show") or {}
            e = item.get("episode") or {}
            ids = s.get("ids") or {}
            if "wetrakr" not in ids or "season" not in e or "number" not in e:
                continue
            title = _get_or_create_title(
                MediaType.TV, s.get("title", "Untitled"), s.get("year"), ids["wetrakr"], ids.get("tmdb")
            )
            episode, _ = Episode.objects.get_or_create(
                title=title, season=e["season"], episode=e["number"], defaults={"name": e.get("title") or ""}
            )
            touched_shows.add(title.id)
        else:
            continue

        existing = WatchEvent.objects.filter(
            profile=profile, title=title, episode=episode, watched_at=watched_at
        ).first()
        if existing is None:
            WatchEvent.objects.create(
                profile=profile, title=title, episode=episode, watched_at=watched_at,
                source=WatchEvent.Source.WETRAKR,
            )
            created += 1
            if labels_out is not None:
                labels_out.append(title.name if episode is None else f"{title.name} S{episode.season}E{episode.episode}")
            touched_watch_keys.add((title.id, episode.id if episode else None))
        elif not existing.source:
            existing.source = WatchEvent.Source.WETRAKR
            existing.save(update_fields=["source"])

    for title_id, episode_id in touched_watch_keys:
        rewatches.recompute_is_rewatch(
            profile, Title.objects.get(id=title_id), Episode.objects.get(id=episode_id) if episode_id else None
        )

    for title in Title.objects.filter(id__in=touched_movies):
        completion.update_movie_runtime(title)
        completion.sync_watchlist_removal(profile, title)
        recommendations.mark_title_watched(profile, title)
    for title in Title.objects.filter(id__in=touched_shows):
        completion.sync_show_completion(profile, title)
        completion.sync_watchlist_removal(profile, title)
        recommendations.mark_title_watched(profile, title)

    return created


def normalize_history_item(item, index):
    """Converts one fetch_history_page item into the common Import
    Review candidate shape (see tracker/import_pipeline.py's own module
    docstring) - mirrors upsert_history_items' own field extraction/
    validation exactly, same invariant trakt.normalize_history_item
    documents, for the same reason (scan and routine sync must always
    agree on which raw items are valid)."""
    from tracker.models import MediaType

    source_row = item.get("id") or index
    watched_at = _parse_watched_at(item)
    if watched_at is None:
        return _history_item_error(source_row, "unparseable watched_at")

    if item.get("type") == "movie":
        m = item.get("movie") or {}
        ids = m.get("ids") or {}
        if "wetrakr" not in ids:
            return _history_item_error(source_row, "missing WeTrakr id")
        return _history_item(
            source_row, MediaType.MOVIE, m.get("title") or "Untitled", m.get("year"), None, None, watched_at, ids
        )

    if item.get("type") == "episode":
        s = item.get("show") or {}
        e = item.get("episode") or {}
        ids = s.get("ids") or {}
        if "wetrakr" not in ids or "season" not in e or "number" not in e:
            return _history_item_error(source_row, "missing show id or episode season/number")
        return _history_item(
            source_row, MediaType.TV, s.get("title") or "Untitled", s.get("year"),
            e.get("season"), e.get("number"), watched_at, ids,
        )

    return _history_item_error(source_row, f"unrecognized item type {item.get('type')!r}")


def _history_item(source_row, media_type, title_name, year, season, episode, watched_at, ids):
    source_external_ids = {"wetrakr": str(ids["wetrakr"])}
    if ids.get("tmdb"):
        source_external_ids["tmdb"] = str(ids["tmdb"])
    return {
        "media_type": media_type, "title_name": title_name, "year": year, "season": season, "episode": episode,
        "watched_at": watched_at, "rating": None, "source_external_ids": source_external_ids,
        "source_row": source_row, "error": None,
    }


def _history_item_error(source_row, reason):
    return {
        "media_type": None, "title_name": f"WeTrakr item {source_row}", "year": None, "season": None,
        "episode": None, "watched_at": None, "rating": None, "source_external_ids": {},
        "source_row": source_row, "error": reason,
    }
