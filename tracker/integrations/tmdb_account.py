"""TMDB account sync - favorites and watchlist (movies+TV), via TMDB's
own v3 "request token" auth flow, not OAuth2: no client_secret, no PKCE,
just the same server-wide TMDB API key Spool's catalog lookups already
use (tracker/instance_config.get_tmdb_api_key - see tracker/integrations/
tmdb.py). TMDB issues a separate v4 "API Read Access Token" too, but
every v3 endpoint - including the account ones this module calls - has
always accepted the classic api_key query param, so this stays consistent
with every other call tmdb.py already makes rather than introducing a
second TMDB credential Spool's config has no field for.

TMDB has no watched-history/dates at all - favorites and watchlist are
just current set membership, nothing resembling a /sync/history feed -
so this never produces a WatchEvent, only list membership. See
tracker/tasks.py's _scan_tmdb_session for how that routes through the
exact same backup_lists/backup_lists_data mechanism the IMDb/Letterboxd
watchlist-only import already established, and fetch_lists/upsert_lists
below for the routine-resync equivalent (mirrors trakt.py's own
fetch_lists/upsert_lists, minus any history fetch).

Auth flow (https://developer.themoviedb.org/docs/authentication-application):
1. create_request_token(api_key) - a short-lived token (TMDB's docs say
   on the order of an hour; treat it as good for this one connect-
   >approve->callback round trip only, never persisted beyond that).
2. authenticate_url(request_token, redirect_to) - send the user here to
   approve; TMDB redirects back to redirect_to once they do (or don't -
   there's no separate "denied" signal to check here, since a denied or
   expired token just makes create_session fail outright in step 3).
3. create_session(api_key, request_token) -> session_id - long-lived,
   no documented expiry and no refresh grant at all (unlike every OAuth2
   provider in this codebase) - stored via ExternalAccount's ordinary
   encrypted_access_token/get_access_token()/set_access_token(), even
   though it isn't technically an OAuth access token.
4. get_account_id(api_key, session_id) - the numeric id every favorites/
   watchlist endpoint below needs, fetched once right after
   create_session and cached on ExternalAccount.tmdb_account_id since
   TMDB gives no other way to look it up later.

Unverified against a live account/app from this environment - same
caveat every other integration module here carries (see trakt.py's own
module docstring) - worth confirming the v3 request-token/session flow
and the exact favorite/watchlist response shapes against a real TMDB
account before relying on this in production.
"""

import time
from urllib.parse import urlencode

import requests

API_BASE = "https://api.themoviedb.org/3"
AUTHENTICATE_URL = "https://www.themoviedb.org/authenticate"

# TMDB's documented soft ceiling (~40-50 req/s, enforced via 429 rather
# than a published header) is a per-second burst limit, not a day-scale
# quota like WeTrakr's - a short bounded retry is enough, there's no
# "pause until tomorrow" case to handle the way wetrakr.py's
# WeTrakrQuotaError does.
_MAX_RETRIES = 3


def _raise_for_status(resp):
    """resp.raise_for_status(), but with the request URL stripped out of
    the exception - every call in this module carries api_key (and often
    session_id) as a query param, and HTTPError's own message echoes the
    full request URL back verbatim, which would otherwise end up readable
    in SyncLog/DataLog or a failure notification (see module docstring)."""
    try:
        resp.raise_for_status()
    except requests.HTTPError:
        raise requests.HTTPError(f"TMDB request failed (HTTP {resp.status_code})", response=resp) from None


def _request(method, url, **kwargs):
    for attempt in range(_MAX_RETRIES):
        try:
            resp = requests.request(method, url, timeout=15, **kwargs)
        except requests.RequestException as e:
            # Connection-level failures (DNS, timeout, refused) embed the
            # same credential-bearing URL in their own message - same
            # redaction reasoning as _raise_for_status above.
            raise requests.RequestException(f"TMDB request failed: {type(e).__name__}") from None
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


def create_request_token(api_key):
    resp = _request("GET", f"{API_BASE}/authentication/token/new", params={"api_key": api_key})
    return resp.json()["request_token"]


def authenticate_url(request_token, redirect_to):
    return f"{AUTHENTICATE_URL}/{request_token}?{urlencode({'redirect_to': redirect_to})}"


def create_session(api_key, request_token):
    resp = _request(
        "POST", f"{API_BASE}/authentication/session/new",
        params={"api_key": api_key}, json={"request_token": request_token},
    )
    return resp.json()["session_id"]


def get_account_id(api_key, session_id):
    resp = _request("GET", f"{API_BASE}/account", params={"api_key": api_key, "session_id": session_id})
    return resp.json()["id"]


def test_credentials(api_key):
    """Settings' "Test connection" button for the TMDB API key card -
    reuses the exact same key catalog lookups already depend on, so this
    just confirms that key is valid against a public v3 endpoint, no user
    auth involved. Raises requests.RequestException on failure, same
    contract as trakt.test_credentials."""
    _request("GET", f"{API_BASE}/authentication/token/new", params={"api_key": api_key})


def _paged(path, api_key, session_id, account_id):
    items = []
    page = 1
    while True:
        resp = _request(
            "GET", f"{API_BASE}/account/{account_id}{path}",
            params={"api_key": api_key, "session_id": session_id, "page": page},
        )
        data = resp.json()
        items.extend(data.get("results") or [])
        total_pages = data.get("total_pages", page)
        if page >= total_pages:
            break
        page += 1
    return items


def fetch_favorites(api_key, session_id, account_id):
    """{"movie": [...], "tv": [...]} - each item is TMDB's own /movie or
    /tv object shape (id, title/name, release_date/first_air_date)."""
    return {
        "movie": _paged("/favorite/movies", api_key, session_id, account_id),
        "tv": _paged("/favorite/tv", api_key, session_id, account_id),
    }


def fetch_watchlist(api_key, session_id, account_id):
    return {
        "movie": _paged("/watchlist/movies", api_key, session_id, account_id),
        "tv": _paged("/watchlist/tv", api_key, session_id, account_id),
    }


def _year_from(item, kind):
    date = item.get("release_date") if kind == "movie" else item.get("first_air_date")
    return int(date[:4]) if date and date[:4].isdigit() else None


def _to_list_items(bucket):
    """bucket: {"movie": [...], "tv": [...]} from fetch_favorites/
    fetch_watchlist, normalized into tracker.backup.restore_lists' own
    item shape. Every item already carries TMDB's own id directly - no
    fuzzy name/year matching ambiguity the way IMDb/Letterboxd's ids-less
    exports have, since restore_lists' _get_or_create_title resolves a
    given tmdb_id+tmdb_kind through its fast direct-match path, never
    find_match()."""
    from tracker.models import MediaType

    items = []
    position = 0
    for kind, bucket_items in bucket.items():
        for raw in bucket_items:
            name = raw.get("title") if kind == "movie" else raw.get("name")
            if not name or not raw.get("id"):
                continue
            items.append(
                {
                    "type": "movie" if kind == "movie" else "show",
                    "media_type": MediaType.MOVIE if kind == "movie" else MediaType.TV,
                    "title": name,
                    "year": _year_from(raw, kind),
                    "ids": {"tmdb": raw["id"], "tmdb_kind": kind},
                    "position": position,
                }
            )
            position += 1
    return items


def fetch_lists(api_key, session_id, account_id):
    """[{"name": "Favorites", "is_watchlist": False, "items": [...]},
    {"name": "Watchlist", "is_watchlist": True, "items": [...]}] -
    tracker.backup.restore_lists-shaped, same convention trakt.fetch_lists
    uses, but with only these two fixed buckets - TMDB has no concept of
    a custom list the way Trakt does. "Watchlist" is marked
    is_watchlist=True so restore_lists folds it into this profile's own
    auto-managed Watchlist, same as every other provider's watchlist -
    "Favorites" is a plain named list, since Spool has no separate
    favorites concept of its own to map onto instead."""
    favorites = _to_list_items(fetch_favorites(api_key, session_id, account_id))
    watchlist = _to_list_items(fetch_watchlist(api_key, session_id, account_id))
    return [
        {"name": "Favorites", "is_watchlist": False, "items": favorites},
        {"name": "Watchlist", "is_watchlist": True, "items": watchlist},
    ]


def upsert_lists(profile, lists_data, labels_out=None):
    """Routine re-sync's own entry point - the exact same backup.
    restore_lists mechanism the first-connect scan uses (see
    tasks._scan_tmdb_session), just called directly instead of staged
    through an ImportSession. Only ever adds - restore_lists' own
    semantics ("titles already on a list are left as they are") - never
    removes something unfavorited/removed-from-watchlist on TMDB's side
    since the last sync, same add-only behavior every other provider's
    own list sync has (see import_review.html's copy). Returns the count
    of newly added items."""
    from tracker import backup

    _, items_added = backup.restore_lists(profile, {"lists": lists_data}, labels_out=labels_out)
    return items_added
