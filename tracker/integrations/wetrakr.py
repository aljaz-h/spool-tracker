"""WeTrakr OAuth2 (PKCE) + history sync.

Written against WeTrakr's public API documentation (https://api.wetrakr.com):
a Trakt-shaped REST API - OAuth2 + PKCE, with its own conventions that this
module follows exactly:

- Every request carries the app's client_id in a `wetrakr-api-key` header
  and `wetrakr-api-version: 1`; a request that acts on a user also carries
  that user's `Authorization: Bearer` token. Without the key header the API
  answers 401 MISSING_API_KEY (_headers adds them all, in one place). A
  descriptive User-Agent is sent too - their edge blocks anonymous HTTP
  library defaults.
- Watched history comes one row per play from
  GET /sync/tracking/watched/history/{movies|episodes}, newest first, at
  most 100 per page, paged with page/limit and X-Pagination-* headers.
  `from_date` limits it to plays added or moved since a date (incremental
  sync). A play carries its own `id` and `watched_at`, and either a compact
  `movie`, or an `episode` (season_number, number) with its `show` nested
  inside - the WeTrakr id is each media object's top-level `id`, and `ids`
  holds only EXTERNAL ids (tmdb, imdb, tvdb, ...). There is no `year`
  field: it is derived from release_date / first_air_date when present, and
  otherwise resolved from TMDB by the title matcher.
- Token exchange needs PKCE (generate_pkce_pair/authorize_url/
  exchange_code); the refresh grant takes only the refresh token and
  rotates it (refresh_access_token's signature still matches trakt.py's, so
  tasks._call_with_refresh works against this module unchanged). Access
  tokens last 7 days, refresh tokens 180.
- Limits (see docs/IMPORTING.md): a daily request quota per connected user
  (free plan 1,000/day, resets at UTC midnight) plus a daily quota for the
  whole app key (a develop key is small - 1,000-2,000/day across every
  connected account). There are no X-RateLimit-* headers; the only signal is
  a 429 whose JSON body carries one of QUOTA_EXCEEDED / APP_QUOTA_EXCEEDED /
  APP_USER_QUOTA_EXCEEDED (a day-scale ceiling - NOT retried here, raised as
  WeTrakrQuotaError so the caller can pause and resume later) or
  CONCURRENCY_LIMIT (retried once with Retry-After). See tracker/tasks.py's
  quota bookkeeping (_wetrakr_account_budget_ok etc.) and _scan_wetrakr_session.

Checked against the documentation, not yet against a live account: the
network-calling functions here follow the documented contract, and the
pure parsing (upsert_history_items/normalize_history_item) is covered by
unit tests built from the documented response shape. The one thing worth
confirming on first use with real credentials is an actual history page.
"""

import base64
import hashlib
import logging
import secrets
import time
from urllib.parse import urlencode

import requests

from tracker import version

logger = logging.getLogger(__name__)

AUTHORIZE_URL = "https://wetrakr.com/oauth/authorize"
TOKEN_URL = "https://api.wetrakr.com/oauth/token"
TOKEN_REFRESH_URL = "https://api.wetrakr.com/oauth/token/refresh"
API_BASE = "https://api.wetrakr.com"
API_VERSION = "1"
# Plays per page: the documented maximum for the regular (non-compact) history.
HISTORY_PAGE_SIZE = 100
USER_AGENT = f"Spool/{version.APP_VERSION} (+https://github.com/aljaz-h/spool-tracker)"

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
        headers=_headers(None, client_id),
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def refresh_access_token(refresh_token, client_id, client_secret, redirect_uri):
    """WeTrakr's refresh grant - takes only the refresh token (the app is
    identified by the key header) and returns a new access token plus a
    ROTATED refresh token (`refresh_token`, also sent as `new_refresh_token`);
    the old one stops working after a short grace window, so the caller
    must store the new one - tasks._refresh_account_token does. The
    signature matches trakt.refresh_access_token so
    tasks._call_with_refresh works against this module with zero changes;
    client_secret/redirect_uri are accepted for that and not sent."""
    resp = requests.post(
        TOKEN_REFRESH_URL,
        json={"refresh_token": refresh_token},
        headers=_headers(None, client_id),
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def _headers(access_token, client_id):
    """Every WeTrakr request: the app key and API version on all of them, the
    user's bearer token when it acts on a user (None for the public/token
    endpoints), and a User-Agent that names this app."""
    headers = {
        "Content-Type": "application/json",
        "wetrakr-api-key": client_id or "",
        "wetrakr-api-version": API_VERSION,
        "User-Agent": USER_AGENT,
    }
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    return headers


def test_credentials(client_id):
    """Settings' "Test connection" button. /search/trending is a public
    read, so the app key alone is enough (and a wrong key answers 401/403).
    Raises requests.RequestException on failure, same contract as
    trakt.test_credentials."""
    resp = requests.get(
        f"{API_BASE}/search/trending", params={"limit": 1}, headers=_headers(None, client_id), timeout=10
    )
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


def _request(method, path, access_token, client_id, on_request=None, **kwargs):
    """requests.request against API_BASE + path, aware of WeTrakr's 429
    error codes. CONCURRENCY_LIMIT (or any 429 without a recognized
    day-scale code) sleeps for Retry-After (default/parse-failure: 1s,
    capped at 5s, same bounds as tenrai.py's _get) and retries exactly
    once - mirrors tenrai.py's _throttle/_get pattern. Any of the three
    day-scale codes raises WeTrakrQuotaError immediately, no retry.

    on_request: an optional zero-arg callback invoked once per actual
    outbound HTTP attempt made here (the initial request, and again if
    the CONCURRENCY_LIMIT retry above fires) - this module has no
    knowledge of Spool's own quota counters (kept decoupled, same
    separation trakt.py's fetch functions have from tasks.py's state),
    so the caller (tasks.py) passes in whatever it needs to record each
    spent request against ExternalAccount.wetrakr_quota_count/
    InstanceConfig.wetrakr_app_quota_count - see tasks._record_wetrakr_request.
    Both the proactive pre-check (budget still available?) and this
    post-request accounting matter: the pre-check is what makes self-
    pacing *proactive*, but without this callback a retried request (or
    any call site that doesn't itself loop with its own counting) would
    silently go unrecorded, undercounting real usage against the daily
    ceiling this whole mechanism exists to respect."""
    url = f"{API_BASE}{path}"
    headers = _headers(access_token, client_id)
    resp = requests.request(method, url, headers=headers, timeout=15, **kwargs)
    if on_request:
        on_request()
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
        if on_request:
            on_request()
        if resp.status_code == 429:
            error_code = None
            try:
                error_code = (resp.json() or {}).get("error")
            except ValueError:
                pass
            raise WeTrakrQuotaError(error_code or "CONCURRENCY_LIMIT", f"WeTrakr still rate-limited on {method} {path}", response=resp)
    resp.raise_for_status()
    return resp


def get_plan_usage(access_token, client_id, on_request=None):
    """GET /plan-usage - {"plan": "free"|"vip"|"patron", "tier": ...,
    "tiers": [...], "features": [...]}. Requires the user's own bearer
    token (per WeTrakr's docs, this is an "oauth"-scoped endpoint, not a
    client-id-only one). Called once up front by a new scan (see
    tasks._check_wetrakr_plan) so Spool knows the account's real daily
    budget before starting a potentially-large pull, not just after
    discovering it via a 429. on_request: see _request's own docstring."""
    resp = _request("GET", "/plan-usage", access_token, client_id, on_request=on_request)
    return resp.json()


def _parse_watched_at(item):
    from django.utils.dateparse import parse_datetime

    return parse_datetime(item.get("watched_at") or "")


def fetch_history_page(
    access_token, client_id, cursor=None, limit=HISTORY_PAGE_SIZE, start_at=None, on_request=None
):
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

    start_at: a datetime - when given, only plays added or moved since that
    point are requested (sent as `from_date`; incremental sync, used by
    routine daily sync). None means a full history pull. A play that was
    deleted does not appear in a from_date read - the account's
    last_tracking_removed_at (GET /sync/last_activities) moves instead.

    on_request: see _request's own docstring - both tasks._scan_wetrakr_session
    and tasks.sync_wetrakr_history call this page-at-a-time (routine sync
    loops it in memory, no resumability needed for an already-small
    incremental delta) so every real request either one makes, including
    this function's own retry, is counted the same way.

    Paged with the documented X-Pagination-Page-Count response header
    (the maximum page size is 100; a larger limit is capped, not rejected)."""
    if cursor is None:
        target, page = _HISTORY_TARGETS[0], 1
    else:
        target, _, page_str = cursor.partition(":")
        page = int(page_str)

    params = {"limit": min(int(limit), HISTORY_PAGE_SIZE), "page": page}
    if start_at is not None:
        from .trakt import _format_trakt_datetime

        params["from_date"] = _format_trakt_datetime(start_at)

    resp = _request(
        "GET", f"/sync/tracking/watched/history/{target}", access_token, client_id,
        on_request=on_request, params=params,
    )
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


def _get_or_create_title(media_type, name, year, wetrakr_id, tmdb_id=None, imdb_id=None):
    """Thin wrapper over tracker.title_matching.apply_title_match - same
    shared matching every other provider (and the Import Review pipeline's
    commit step) uses, so a title WeTrakr reports never forks a duplicate
    Title from one Trakt/Simkl/Nuvio already created. See trakt.py's own
    _get_or_create_title for the full rationale."""
    from tracker.title_matching import apply_title_match

    return apply_title_match(
        media_type, "wetrakr", wetrakr_id, name=name, year=year, tmdb_id=tmdb_id, imdb_id=imdb_id
    )


def _year_of(date_string):
    """The year of an ISO date string ("2023-11-02T12:00:00.000Z"), or None."""
    head = (date_string or "")[:4]
    return int(head) if head.isdigit() else None


def _extract_play(item):
    """One fetch_history_page item -> (play, error). play is a dict of what
    both the live sync (upsert_history_items) and the Import Review scan
    (normalize_history_item) need - they share this so they always agree on
    which raw items are valid:
    {media_type, wetrakr_id, name, year, season, episode, episode_name, ids}.
    A movie play carries a compact `movie`; an episode play an `episode`
    with its `show` nested in it (see the module docstring)."""
    from tracker.models import MediaType

    if item.get("type") == "movie":
        movie = item.get("movie") or {}
        if movie.get("id") is None:
            return None, "missing WeTrakr movie id"
        return {
            "media_type": MediaType.MOVIE, "wetrakr_id": movie["id"], "name": movie.get("title") or "Untitled",
            "year": _year_of(movie.get("release_date")), "season": None, "episode": None, "episode_name": "",
            "ids": movie.get("ids") or {},
        }, None
    if item.get("type") == "episode":
        episode = item.get("episode") or {}
        show = episode.get("show") or {}
        season, number = episode.get("season_number"), episode.get("number")
        if show.get("id") is None or season is None or number is None:
            return None, "missing show id or episode season/number"
        return {
            "media_type": MediaType.TV, "wetrakr_id": show["id"], "name": show.get("title") or "Untitled",
            "year": _year_of(show.get("first_air_date")), "season": season, "episode": number,
            "episode_name": episode.get("title") or "", "ids": show.get("ids") or {},
        }, None
    return None, f"unrecognized item type {item.get('type')!r}"


def upsert_history_items(profile, items, labels_out=None):
    """items: a list accumulated from one or more fetch_history_page()
    calls - routine sync's own in-memory loop, or the accumulated pages
    from a resumed Import Review scan. Mirrors trakt.upsert_history_items
    exactly - same dedup key (profile, title, episode, watched_at), same
    downstream batching - just keyed off a "wetrakr" id instead of a
    "trakt" one. See that function's own docstring for the full
    rationale; not re-documented here to avoid drift between two copies
    of the same explanation."""
    from django.utils.dateparse import parse_datetime

    from tracker import completion, recommendations, rewatches
    from tracker.models import Episode, Title, WatchEvent

    created = 0
    touched_movies = set()
    touched_shows = set()
    touched_watch_keys = set()
    for item in items:
        watched_at = parse_datetime(item.get("watched_at", ""))
        if watched_at is None:
            continue
        play, _error = _extract_play(item)
        if play is None:
            continue
        ids = play["ids"]
        title = _get_or_create_title(
            play["media_type"], play["name"], play["year"], play["wetrakr_id"], ids.get("tmdb"), ids.get("imdb")
        )
        if play["episode"] is None:
            episode = None
            touched_movies.add(title.id)
        else:
            episode, _ = Episode.objects.get_or_create(
                title=title, season=play["season"], episode=play["episode"], defaults={"name": play["episode_name"]}
            )
            touched_shows.add(title.id)

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
    docstring) - extracts fields through the same _extract_play as
    upsert_history_items, same invariant trakt.normalize_history_item
    documents, for the same reason (scan and routine sync must always
    agree on which raw items are valid)."""
    source_row = item.get("id") or index
    watched_at = _parse_watched_at(item)
    if watched_at is None:
        return _history_item_error(source_row, "unparseable watched_at")
    play, error = _extract_play(item)
    if play is None:
        return _history_item_error(source_row, error)
    return _history_item(
        source_row, play["media_type"], play["name"], play["year"], play["season"], play["episode"],
        watched_at, play["wetrakr_id"], play["ids"],
    )


def _history_item(source_row, media_type, title_name, year, season, episode, watched_at, wetrakr_id, ids):
    source_external_ids = {"wetrakr": str(wetrakr_id)}
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
