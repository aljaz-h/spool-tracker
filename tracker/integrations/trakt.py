"""Trakt OAuth2 + history sync.

Implemented against Trakt's publicly documented API shape (OAuth2
authorization-code flow at trakt.tv/oauth/*, api.trakt.tv/sync/history for
watch history) — this environment has no real Trakt developer app
credentials to exercise it against, so the network-calling functions
(authorize_url/exchange_code/fetch_history) are unverified against the
live API. upsert_history_items() is pure data-shuffling with no network
dependency and *is* verified, via a unit test against a fixture modeled on
Trakt's documented /sync/history response.

Before relying on this in production: confirm the exact field names in a
real /sync/history response with valid credentials, since Trakt's API can
drift from documentation.
"""

import datetime
from urllib.parse import urlencode

import requests

AUTHORIZE_URL = "https://trakt.tv/oauth/authorize"
TOKEN_URL = "https://api.trakt.tv/oauth/token"
API_BASE = "https://api.trakt.tv"


def authorize_url(redirect_uri, state, client_id):
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def exchange_code(code, redirect_uri, client_id, client_secret):
    resp = requests.post(
        TOKEN_URL,
        json={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def refresh_access_token(refresh_token, client_id, client_secret, redirect_uri):
    """Trakt's refresh grant, mirroring exchange_code() - per Trakt's docs
    the redirect_uri must match the one the original authorization-code
    exchange used, which is why ExternalAccount.redirect_uri exists to
    capture it (see models.py)."""
    resp = requests.post(
        TOKEN_URL,
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
        "trakt-api-version": "2",
        "trakt-api-key": client_id,
        "Authorization": f"Bearer {access_token}",
    }


def test_credentials(client_id):
    """Settings' "Test connection" button - a public GET that only needs
    trakt-api-key, no user auth, to confirm client_id is a live, registered
    Trakt app. Raises requests.RequestException on failure, same as every
    other network call in this module - the caller decides what to show.
    Can't validate client_secret this way: Trakt only checks that during
    OAuth token exchange, never on a plain GET, so a right id paired with a
    wrong secret still passes this check."""
    resp = requests.get(
        f"{API_BASE}/movies/trending",
        params={"limit": 1},
        headers={"Content-Type": "application/json", "trakt-api-version": "2", "trakt-api-key": client_id},
        timeout=10,
    )
    resp.raise_for_status()


def _format_trakt_datetime(dt):
    """Trakt's own documented example format is 2014-09-01T09:10:11.000Z -
    matched exactly here, though the start_at param itself is unverified
    against a live account (see fetch_history). If the format's wrong,
    Trakt should reject it with a 4xx that shows up as a failed sync in
    the sync log (see tracker/tasks.py's _run_sync) rather than silently
    misbehaving - worth checking there after this ships."""
    return dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def fetch_history(access_token, client_id, limit=200, max_pages=500, start_at=None):
    """Follows Trakt's /sync/history pagination (X-Pagination-Page-Count
    response header) instead of returning just the first page — a first
    version of this that only fetched page 1 silently capped every sync at
    200 items, confirmed against a real account importing exactly 200. A
    second version's max_pages=50 (10k item) cap turned out too tight too -
    confirmed against a real account with 10,303 plays, which got silently
    truncated to exactly 10,000, dropping its oldest ~300 events. max_pages
    is still a hard safety bound so a missing/unexpected pagination header
    can't turn this into a truly unbounded loop inside a background worker,
    but 500 pages (100k items) is now far enough beyond any real personal
    watch history that it should never actually be the thing that stops
    the loop.

    start_at: a datetime - when given, only history after that point is
    requested (incremental sync). None means a full history pull, same as
    every sync before this parameter existed."""
    items = []
    page = 1
    params = {"limit": limit}
    if start_at is not None:
        params["start_at"] = _format_trakt_datetime(start_at)
    while page <= max_pages:
        resp = requests.get(
            f"{API_BASE}/sync/history",
            headers=_headers(access_token, client_id),
            params={**params, "page": page},
            timeout=15,
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        items.extend(batch)
        page_count = int(resp.headers.get("X-Pagination-Page-Count", page))
        if page >= page_count:
            break
        page += 1
    return items


def _get_or_create_title(media_type, name, year, trakt_id, tmdb_id=None):
    """tmdb_id: Trakt's own ids.tmdb for this item, when the caller has it
    (see upsert_history_items/upsert_lists) - checked first and, when
    creating a new title, used directly instead of the fuzzy name/year
    find_match() search below. find_match can come up empty (a year
    mismatch between Trakt's and TMDB's own metadata returns nothing) or
    match the wrong TMDB entry, leaving the title Trakt is reporting
    unlinked from what the Movies & TV/Anime grid matches watched status
    against - Trakt's id is exact, so prefer it whenever the response
    actually included one."""
    from tracker.integrations import tmdb
    from tracker.models import MediaType, Title, attach_genres, attach_reports_metadata

    # Manual filter-then-create instead of get_or_create(): a JSONField key
    # lookup like external_ids__trakt=X can't double as a constructor kwarg
    # (Title(external_ids__trakt=X) isn't a real field), which is exactly
    # the pitfall get_or_create's defaults-merging would hit here.
    title = Title.objects.filter(media_type=media_type, external_ids__trakt=str(trakt_id)).first()
    if title:
        if tmdb_id and not title.external_ids.get("tmdb"):
            kind = "movie" if media_type == MediaType.MOVIE else "tv"
            title.external_ids = {**title.external_ids, "tmdb": str(tmdb_id), "tmdb_kind": kind}
            title.save(update_fields=["external_ids"])
        return title
    external_ids = {"trakt": str(trakt_id)}
    poster_url = ""
    genre_names = []
    details = None
    if tmdb_id:
        kind = "movie" if media_type == MediaType.MOVIE else "tv"
        match = {"id": tmdb_id, "kind": kind, "poster_url": None}
    else:
        match = tmdb.find_match(media_type, name, year)
    if match:
        external_ids["tmdb"] = str(match["id"])
        external_ids["tmdb_kind"] = match["kind"]
        details = tmdb.get_full_details(match["kind"], match["id"])
        if details:
            poster_url = match["poster_url"] or details.get("poster_url") or ""
            genre_names = details["genres"]
        else:
            poster_url = match["poster_url"] or ""
        # A title already tracked via Simkl/CSV import/Nuvio before Trakt
        # was ever connected must reuse that same Title, not fork a
        # duplicate that leaves the original stuck showing "not watched"
        # while this one silently absorbs the new WatchEvent (see
        # nuvio.py's _get_or_create_title docstring - the same bug, first
        # caught there against a real account).
        existing = Title.objects.filter(media_type=media_type, external_ids__tmdb=external_ids["tmdb"]).first()
        if existing:
            if existing.external_ids.get("trakt") != str(trakt_id):
                existing.external_ids = {**existing.external_ids, "trakt": str(trakt_id)}
                existing.save(update_fields=["external_ids"])
            return existing
    title = Title.objects.create(
        media_type=media_type, name=name, year=year or 0, external_ids=external_ids, poster_url=poster_url
    )
    attach_genres(title, genre_names)
    if details:
        attach_reports_metadata(title, tmdb.get_reports_metadata(match["kind"], match["id"], details))
    return title


def upsert_history_items(profile, items, labels_out=None):
    """items: the parsed JSON list from fetch_history(). Returns the count
    of newly created WatchEvent rows (existing ones are left alone —
    dedup is by (profile, title, episode, watched_at), since Trakt history
    entries don't have a field we're already storing to key off of). A
    rewatch has a different watched_at than the original, so it's never
    collapsed by that dedup key - it lands as its own WatchEvent row same
    as any other watch; is_rewatch just marks which one it is (see
    tracker/rewatches.py).

    labels_out: an optional list this appends a human-readable label to
    for every newly created WatchEvent ("Title" for a movie, "Title
    S1E2" for an episode) - tasks._run_sync saves these (capped) onto the
    SyncLog row so the Logs tab can show what was actually imported, not
    just a count. None (the default) skips this entirely - every other
    caller of this function doesn't need it."""
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
            if "trakt" not in ids:
                continue
            title = _get_or_create_title(
                MediaType.MOVIE, m.get("title", "Untitled"), m.get("year"), ids["trakt"], ids.get("tmdb")
            )
            episode = None
            touched_movies.add(title.id)
        elif item.get("type") == "episode":
            s = item.get("show") or {}
            e = item.get("episode") or {}
            ids = s.get("ids") or {}
            if "trakt" not in ids or "season" not in e or "number" not in e:
                continue
            title = _get_or_create_title(
                MediaType.TV, s.get("title", "Untitled"), s.get("year"), ids["trakt"], ids.get("tmdb")
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
                source=WatchEvent.Source.TRAKT,
            )
            created += 1
            if labels_out is not None:
                labels_out.append(title.name if episode is None else f"{title.name} S{episode.season}E{episode.episode}")
            touched_watch_keys.add((title.id, episode.id if episode else None))
        elif not existing.source:
            # Every sync re-pulls the whole history (no incremental
            # cursor - see this function's own docstring), so this is how
            # rows logged before the source field existed get backfilled
            # with the marker on the next sync, without a one-off
            # migration/management command - same as nuvio.py's own dedup.
            existing.source = WatchEvent.Source.TRAKT
            existing.save(update_fields=["source"])

    for title_id, episode_id in touched_watch_keys:
        rewatches.recompute_is_rewatch(
            profile, Title.objects.get(id=title_id), Episode.objects.get(id=episode_id) if episode_id else None
        )

    # Best-effort - a TMDB hiccup here shouldn't fail a sync that already
    # successfully wrote the watch history itself.
    for title in Title.objects.filter(id__in=touched_movies):
        completion.update_movie_runtime(title)
        completion.sync_watchlist_removal(profile, title)
        recommendations.mark_title_watched(profile, title)
    for title in Title.objects.filter(id__in=touched_shows):
        completion.sync_show_completion(profile, title)
        completion.sync_watchlist_removal(profile, title)
        recommendations.mark_title_watched(profile, title)

    return created


def fetch_lists(access_token, client_id):
    """Returns [{"name": str, "items": [...]}, ...] - the built-in
    Watchlist plus every custom list, each with its items in the same
    {type, movie|show, ids} shape /sync/history uses (list items are
    whole titles, not individual episodes - Trakt lists don't hold
    per-episode entries the way history does).

    Matches Trakt's documented /sync/watchlist, /users/me/lists, and
    /users/me/lists/{id}/items endpoints, and the URL shapes are
    corroborated by multiple third-party Trakt client libraries, but -
    same caveat as the rest of this module - unverified against a live
    account from this environment."""
    headers = _headers(access_token, client_id)
    lists = []

    resp = requests.get(f"{API_BASE}/sync/watchlist", headers=headers, timeout=15)
    resp.raise_for_status()
    lists.append({"name": "Watchlist", "items": resp.json()})

    resp = requests.get(f"{API_BASE}/users/me/lists", headers=headers, timeout=15)
    resp.raise_for_status()
    for entry in resp.json():
        list_id = (entry.get("ids") or {}).get("trakt")
        if not list_id:
            continue
        items_resp = requests.get(f"{API_BASE}/users/me/lists/{list_id}/items", headers=headers, timeout=15)
        items_resp.raise_for_status()
        lists.append({"name": entry.get("name") or "Untitled list", "items": items_resp.json()})

    return lists


def upsert_lists(profile, lists_data, labels_out=None):
    """lists_data: fetch_lists()'s return value. Creates/updates a
    WatchList per Trakt list - matched by name, since Trakt list ids
    aren't tracked anywhere else in this schema, so renaming a list on
    Trakt creates a new one here rather than renaming the existing one -
    and adds items via the same title-matching fetch_history/
    upsert_history_items already use. Returns the count of newly added
    WatchListItems.

    labels_out: see upsert_history_items's own docstring - same idea,
    labeled "Title (List Name)" so a list add reads distinctly from a
    watch-history import in the Logs tab's detail view."""
    from tracker.models import MediaType, WatchList, WatchListItem

    added = 0
    for entry in lists_data:
        watchlist, _ = WatchList.objects.get_or_create(
            profile=profile, name=entry["name"], defaults={"is_watchlist": entry["name"] == "Watchlist"}
        )
        for item in entry["items"]:
            if item.get("type") == "movie":
                m = item.get("movie") or {}
                ids = m.get("ids") or {}
                if "trakt" not in ids:
                    continue
                title = _get_or_create_title(
                    MediaType.MOVIE, m.get("title", "Untitled"), m.get("year"), ids["trakt"], ids.get("tmdb")
                )
            elif item.get("type") == "show":
                s = item.get("show") or {}
                ids = s.get("ids") or {}
                if "trakt" not in ids:
                    continue
                title = _get_or_create_title(
                    MediaType.TV, s.get("title", "Untitled"), s.get("year"), ids["trakt"], ids.get("tmdb")
                )
            else:
                continue
            _, created = WatchListItem.objects.get_or_create(watchlist=watchlist, title=title)
            if created:
                added += 1
                if labels_out is not None:
                    labels_out.append(f"{title.name} ({entry['name']})")
    return added
