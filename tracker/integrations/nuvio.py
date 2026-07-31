"""Nuvio Cloud sync - email/password auth against Nuvio's undocumented
backend at api.nuvio.tv, used to pull a profile's own watch history and
continue-watching progress. Nuvio has no public developer API; this
module is built from a third-party open-source reference implementation
(github.com/ellite/scrob's backend/core/nuvio.py and
backend/tests/test_nuvio.py, read directly), not official docs, and is
unverified against a live account from this environment - same honesty
bar as this repo's own Simkl integration (see simkl.py's docstring).

CONFIRMED (read directly from scrob's source/tests):
- api.nuvio.tv is a Supabase backend - /auth/v1/token (Supabase Auth) and
  /rest/v1/rpc/{fn} (PostgREST RPC) are Supabase's standard URL shapes,
  not a bespoke API.
- PUBLISHABLE_KEY below is a constant baked into the Nuvio app itself
  (Supabase's "publishable key" concept), not a per-user secret - unlike
  Trakt/Simkl, there's no server-owner setup step: every profile can
  connect immediately.
- Sign-in: POST /auth/v1/token?grant_type=password,
  {"email", "password"} -> {"access_token", "refresh_token", "expires_in"}.
- Refresh: POST /auth/v1/token?grant_type=refresh_token,
  {"refresh_token"} -> same shape. Supabase rotates the refresh token on
  every use - the caller (tasks.sync_nuvio_history) must persist the new
  one immediately or the *next* sync fails.
- sync_pull_profiles (no params) returns a profile list; the only
  confirmed field is "profile_index" (int), and it - not some other id -
  is what every other RPC's p_profile_id parameter expects (confirmed:
  scrob's validate_connection compares
  int(profile.get("profile_index") or 0) == profile_id, and its pull
  functions pass that same profile_id straight through as p_profile_id).
- sync_pull_watched_items: {"p_profile_id", "p_page", "p_page_size": 500}
  (page-number pagination).
- sync_pull_watch_progress: {"p_profile_id", "p_limit": 200} - no
  p_offset. scrob has a regression test specifically because passing one
  404s the real API.
- content_id shapes seen in scrob's fixtures: "tmdb:550" (TMDB id) or a
  bare/prefixed IMDb id ("tt0137523", or "tt0903747:1:1" =
  imdb_id:season:episode for an episode).
- content_type values: "movie" and "series" (not "tv"/"show").
- Episode items carry explicit separate "season"/"episode" fields on the
  incoming item too, not only encoded in the id string.
- watched_at is an epoch-millisecond integer, not ISO-8601 like
  Trakt/Simkl/CSV import all use elsewhere in this repo.
- Progress entries carry "position"/"duration" in milliseconds.

INFERRED / not directly confirmed - flagged here rather than guessed
silently:
- Whether a profile dict carries a human-readable name/avatar field
  (scrob's own get_profiles() returns the raw dicts unfiltered). The
  connect UI tries profile.get("name"), falling back to "Profile N".
- Whether sync_pull_watched_items' pagination terminates on an empty/
  short page or a total-count field - assumed "stop on a short page",
  same pattern trakt.fetch_history's own pagination loop already uses.
- Full field completeness of a watched/progress item beyond scrob's
  fixtures (e.g. whether a title/year ever accompanies an item). Where
  this is wrong, upsert_history_items/upsert_progress_items skip the
  unparseable item rather than raise (mirrors how trakt.py's own
  upsert_history_items already skips items missing expected keys), and
  whatever real failure does occur is recorded on the sync's SyncLog row
  (via tasks._run_sync) - visible in Settings -> Logs, not silent.
"""

import requests

DEFAULT_URL = "https://api.nuvio.tv"
PUBLISHABLE_KEY = "sb_publishable_1Clq8rlTVACkdcZuqr6_AD__xUUC_EN"
_PAGE_SIZE = 500


def _public_headers():
    return {"apikey": PUBLISHABLE_KEY, "Content-Type": "application/json"}


def _auth_headers(access_token):
    return {**_public_headers(), "Authorization": f"Bearer {access_token}"}


def sign_in(email, password):
    resp = requests.post(
        f"{DEFAULT_URL}/auth/v1/token",
        params={"grant_type": "password"},
        headers=_public_headers(),
        json={"email": email, "password": password},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def refresh_access_token(refresh_token):
    resp = requests.post(
        f"{DEFAULT_URL}/auth/v1/token",
        params={"grant_type": "refresh_token"},
        headers=_public_headers(),
        json={"refresh_token": refresh_token},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def _rpc(function_name, access_token, params=None):
    resp = requests.post(
        f"{DEFAULT_URL}/rest/v1/rpc/{function_name}",
        headers=_auth_headers(access_token),
        json=params or {},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, list) else []


def get_profiles(access_token):
    return _rpc("sync_pull_profiles", access_token)


def authenticate(email, password):
    """Sign in + fetch profiles in one call - the connect view's happy
    path (see views.nuvio_connect_submit). The password is used here and
    nowhere else; only the resulting refresh_token is ever persisted."""
    session = sign_in(email, password)
    profiles = get_profiles(session["access_token"])
    return session, profiles


def fetch_watched_items(access_token, profile_id, page_size=_PAGE_SIZE, max_pages=200):
    """Pages sync_pull_watched_items until a short/empty page. max_pages
    is a hard safety bound (200 * 500 = 100k items) so an unexpected
    pagination signal can't turn this into a truly unbounded loop inside
    a background worker - same reasoning as trakt.fetch_history's own
    max_pages bound."""
    items = []
    page = 1
    while page <= max_pages:
        batch = _rpc(
            "sync_pull_watched_items", access_token,
            {"p_profile_id": profile_id, "p_page": page, "p_page_size": page_size},
        )
        if not batch:
            break
        items.extend(batch)
        if len(batch) < page_size:
            break
        page += 1
    return items


def fetch_watch_progress(access_token, profile_id, limit=200):
    # No p_offset - confirmed via scrob's own regression test that
    # passing one 404s the real API.
    return _rpc("sync_pull_watch_progress", access_token, {"p_profile_id": profile_id, "p_limit": limit})


def _parse_content_id(content_id):
    """content_id shapes confirmed from scrob's fixtures: "tmdb:550"
    (TMDB id), "tt0137523" (bare IMDb id, movie), "tt0903747:1:1"
    (imdb_id:season:episode). Returns {"tmdb_id": int|None,
    "imdb_id": str|None, "season": int|None, "episode": int|None} -
    unrecognized shapes return all-None."""
    empty = {"tmdb_id": None, "imdb_id": None, "season": None, "episode": None}
    content_id = (content_id or "").strip()
    if content_id.startswith("tmdb:"):
        rest = content_id[len("tmdb:") :]
        return {**empty, "tmdb_id": int(rest)} if rest.isdigit() else empty
    if content_id.startswith("tt"):
        parts = content_id.split(":")
        if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
            return {**empty, "imdb_id": parts[0], "season": int(parts[1]), "episode": int(parts[2])}
        return {**empty, "imdb_id": parts[0]}
    return empty


def _get_or_create_title(media_type, content_id, name_hint="", year_hint=None):
    """Matching preference: an existing title already synced from this
    exact content_id (resync dedup) > a direct TMDB id lookup (when
    content_id is "tmdb:"-prefixed - more reliable than a fuzzy search,
    since it's a confirmed id rather than a guess) > a TMDB find-by-imdb
    lookup (bare/prefixed imdb ids) > a fuzzy title/year search (only
    possible if the item happened to carry a name) > a bare Title with
    no TMDB match at all. Whatever gets created/matched has
    external_ids["nuvio"] set to this content_id, so
    disconnect_and_wipe_provider's title__external_ids__nuvio filter
    works the same way it already does for trakt/simkl."""
    from tracker.integrations import tmdb
    from tracker.models import MediaType, Title, attach_genres

    title = Title.objects.filter(media_type=media_type, external_ids__nuvio=content_id).first()
    if title:
        return title

    parsed = _parse_content_id(content_id)
    kind = "movie" if media_type == MediaType.MOVIE else "tv"

    external_ids = {"nuvio": content_id}
    name = name_hint or "Untitled"
    year = year_hint
    poster_url = ""
    genre_names = []
    details = None

    if parsed["tmdb_id"]:
        details = tmdb.get_full_details(kind, parsed["tmdb_id"])
        if details:
            external_ids["tmdb"] = str(parsed["tmdb_id"])
            external_ids["tmdb_kind"] = kind
    elif parsed["imdb_id"]:
        match = tmdb.find_by_imdb_id(parsed["imdb_id"], media_type)
        if match:
            external_ids["tmdb"] = str(match["id"])
            external_ids["tmdb_kind"] = match["kind"]
            details = tmdb.get_full_details(match["kind"], match["id"])

    if details:
        name = details["name"]
        year = details["year"]
        poster_url = details["poster_url"] or ""
        genre_names = details["genres"]
    elif name_hint:
        match = tmdb.find_match(media_type, name_hint, year_hint)
        if match:
            external_ids["tmdb"] = str(match["id"])
            external_ids["tmdb_kind"] = match["kind"]
            poster_url = match["poster_url"] or ""
            fallback_details = tmdb.get_full_details(match["kind"], match["id"])
            if fallback_details:
                genre_names = fallback_details["genres"]

    title = Title.objects.create(
        media_type=media_type, name=name, year=int(year) if year else 0,
        external_ids=external_ids, poster_url=poster_url,
    )
    attach_genres(title, genre_names)
    return title


def _season_episode(item, parsed):
    season = item.get("season") if item.get("season") is not None else parsed["season"]
    episode = item.get("episode") if item.get("episode") is not None else parsed["episode"]
    return season, episode


def upsert_history_items(profile, items):
    """items: raw dicts from fetch_watched_items(). Returns the count of
    newly created WatchEvent rows - existing ones (same profile, title,
    episode, watched_at) are left alone, same dedup key
    trakt.py/simkl.py's own upsert_history_items already use. Items
    missing what's needed to place them (no content_id, no parseable
    watched_at, unrecognized content_type, a TV item with no resolvable
    season/episode) are skipped, not fatal - see module docstring."""
    import datetime

    from tracker import completion, recommendations, rewatches
    from tracker.models import Episode, MediaType, Title, WatchEvent

    created = 0
    touched_movies = set()
    touched_shows = set()
    touched_watch_keys = set()
    for item in items:
        content_id = item.get("content_id")
        watched_at_ms = item.get("watched_at")
        if not content_id or not isinstance(watched_at_ms, (int, float)):
            continue
        watched_at = datetime.datetime.fromtimestamp(watched_at_ms / 1000, tz=datetime.timezone.utc)

        content_type = item.get("content_type")
        if content_type == "movie":
            media_type = MediaType.MOVIE
        elif content_type == "series":
            media_type = MediaType.TV
        else:
            continue

        parsed = _parse_content_id(content_id)
        name_hint = item.get("name") or item.get("title") or ""
        title = _get_or_create_title(media_type, content_id, name_hint, item.get("year"))

        episode = None
        if media_type == MediaType.TV:
            season, episode_num = _season_episode(item, parsed)
            if season is None or episode_num is None:
                continue
            episode, _ = Episode.objects.get_or_create(title=title, season=season, episode=episode_num)
            touched_shows.add(title.id)
        else:
            touched_movies.add(title.id)

        already_logged = WatchEvent.objects.filter(
            profile=profile, title=title, episode=episode, watched_at=watched_at
        ).exists()
        if not already_logged:
            WatchEvent.objects.create(profile=profile, title=title, episode=episode, watched_at=watched_at)
            created += 1
            touched_watch_keys.add((title.id, episode.id if episode else None))

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


NEAR_COMPLETE_REMAINING_MS = 120_000  # 2 minutes


def upsert_progress_items(profile, items):
    """items: raw dicts from fetch_watch_progress(). Returns the count of
    WatchProgress rows written WATCHING (created or updated) - keyed by
    (profile, title), same as unique_progress_per_profile_title already
    enforces. position/duration are milliseconds (see module docstring);
    only position is stored - WatchProgress has no duration field, the
    app derives % complete from Title/Episode.runtime_minutes elsewhere.
    Unparseable items are skipped, not fatal.

    An item within NEAR_COMPLETE_REMAINING_MS of its own duration is
    treated as finished rather than upserted as WATCHING - confirmed
    against a real synced account that Nuvio's own "continue watching"
    feed can keep reporting something as in-progress indefinitely once
    actually finished (many players never clear a completed entry from
    that list on their own), which without this would re-appear as
    "0 min left"/"1 min left" in the Dashboard's Watching tab
    (selectors.continue_watching) on every single sync instead of ever
    settling. Marked COMPLETED (same status sync_show_completion already
    uses for a finished show) rather than deleted, so there's still a
    record of it - continue_watching() only ever surfaces status=WATCHING
    rows, so this alone is enough to drop it out of Watching. Only ever
    updates an *existing* WatchProgress row already matched by
    external_ids__nuvio - never creates a Title/Episode just to
    immediately discard it for something that was already finished
    before this sync ever saw it. Doesn't touch WatchEvent/history at
    all either way."""
    from tracker.models import Episode, MediaType, Title, WatchProgress

    updated = 0
    for item in items:
        content_id = item.get("content_id")
        position_ms = item.get("position")
        if not content_id or not isinstance(position_ms, (int, float)):
            continue

        content_type = item.get("content_type")
        if content_type == "movie":
            media_type = MediaType.MOVIE
        elif content_type == "series":
            media_type = MediaType.TV
        else:
            continue

        duration_ms = item.get("duration")
        near_complete = (
            isinstance(duration_ms, (int, float))
            and duration_ms > 0
            and (duration_ms - position_ms) <= NEAR_COMPLETE_REMAINING_MS
        )
        if near_complete:
            existing_title = Title.objects.filter(media_type=media_type, external_ids__nuvio=content_id).first()
            if existing_title:
                WatchProgress.objects.filter(profile=profile, title=existing_title).update(
                    status=WatchProgress.Status.COMPLETED
                )
            continue

        parsed = _parse_content_id(content_id)
        name_hint = item.get("name") or item.get("title") or ""
        title = _get_or_create_title(media_type, content_id, name_hint, item.get("year"))

        current_episode = None
        if media_type == MediaType.TV:
            season, episode_num = _season_episode(item, parsed)
            if season is None or episode_num is None:
                continue
            current_episode, _ = Episode.objects.get_or_create(title=title, season=season, episode=episode_num)

        WatchProgress.objects.update_or_create(
            profile=profile, title=title,
            defaults={
                "current_episode": current_episode,
                "position_seconds": int(position_ms / 1000),
                "status": WatchProgress.Status.WATCHING,
            },
        )
        updated += 1
    return updated
