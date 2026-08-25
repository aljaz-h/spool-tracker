"""Simkl OAuth2 + history sync.

Same caveat as trakt.py, more so: Simkl's OAuth2 endpoints
(simkl.com/oauth/authorize, api.simkl.com/oauth/token) are well-documented
and standard, but the exact response shape of its history/activity sync
endpoints is something this environment could not verify against a real
account. The upsert logic below assumes a response shaped like Trakt's
(a flat list of {watched_at, type, movie|show+episode} items) since
that's the most defensible guess without live docs — treat
upsert_history_items() as a structural placeholder to adjust once real
Simkl API responses are available, not a verified integration.
"""

from urllib.parse import urlencode

import requests

AUTHORIZE_URL = "https://simkl.com/oauth/authorize"
TOKEN_URL = "https://api.simkl.com/oauth/token"
API_BASE = "https://api.simkl.com"


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
    """Structurally mirrors trakt.refresh_access_token() - same caveat as
    the rest of this module (see docstring): Simkl access tokens are
    documented as long-lived/non-expiring, so this is unverified against
    a real 401, but costs nothing to have wired up the same way as Trakt
    in case that's ever wrong."""
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
        "simkl-api-key": client_id,
        "Authorization": f"Bearer {access_token}",
    }


def test_credentials(client_id):
    """Settings' "Test connection" button. Best-effort, like the rest of
    this module (see its own docstring) - Simkl's public API surface
    beyond OAuth isn't documented well enough here to be fully confident
    this endpoint tells a valid key apart from an invalid one, but it's
    the closest thing to a public, no-user-auth GET Simkl appears to
    offer. Raises requests.RequestException on failure."""
    resp = requests.get(f"{API_BASE}/search/movie", params={"q": "test"}, headers={"simkl-api-key": client_id}, timeout=10)
    resp.raise_for_status()


def fetch_history(access_token, client_id):
    resp = requests.get(f"{API_BASE}/sync/activities", headers=_headers(access_token, client_id), timeout=15)
    resp.raise_for_status()
    return resp.json()


def _get_or_create_title(media_type, name, year, simkl_id, tmdb_id=None):
    """tmdb_id: Simkl's own ids.tmdb for this item, when the caller has it
    - see trakt.py's own _get_or_create_title docstring for why this is
    preferred over the fuzzy name/year find_match() search below whenever
    it's present."""
    from tracker.integrations import tmdb
    from tracker.models import MediaType, Title, attach_genres, attach_reports_metadata

    # Not filtered by media_type: a title this same simkl_id already
    # created may since have been reclassified from TV to ANIME (see the
    # reclassify_anime_titles management command/task) - it's still the
    # right row to reuse, not a mismatch to fork a duplicate over.
    title = Title.objects.filter(external_ids__simkl=str(simkl_id)).first()
    if title:
        if tmdb_id and not title.external_ids.get("tmdb"):
            kind = "movie" if media_type == MediaType.MOVIE else "tv"
            title.external_ids = {**title.external_ids, "tmdb": str(tmdb_id), "tmdb_kind": kind}
            title.save(update_fields=["external_ids"])
        return title
    external_ids = {"simkl": str(simkl_id)}
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
        # Same duplicate-Title bug nuvio.py's _get_or_create_title
        # docstring describes - reuse a title already tracked via another
        # provider instead of forking a second one for this same TMDB id.
        # tmdb_kind (not media_type) disambiguates - a title already
        # reclassified to ANIME must still match here.
        existing = Title.objects.filter(
            external_ids__tmdb=external_ids["tmdb"], external_ids__tmdb_kind=external_ids["tmdb_kind"]
        ).first()
        if existing:
            if existing.external_ids.get("simkl") != str(simkl_id):
                existing.external_ids = {**existing.external_ids, "simkl": str(simkl_id)}
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
    """Structurally mirrors trakt.upsert_history_items() — same dedup
    strategy, same shape assumption, same optional labels_out (see that
    function's own docstring). See module docstring."""
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
            if "simkl" not in ids:
                continue
            title = _get_or_create_title(
                MediaType.MOVIE, m.get("title", "Untitled"), m.get("year"), ids["simkl"], ids.get("tmdb")
            )
            episode = None
            touched_movies.add(title.id)
        elif item.get("type") == "episode":
            s = item.get("show") or {}
            e = item.get("episode") or {}
            ids = s.get("ids") or {}
            if "simkl" not in ids or "season" not in e or "number" not in e:
                continue
            # Simkl titles routed here are Anime by convention — Movies &
            # TV vs. Anime is decided by media_type, never genre, per
            # spool-product-spec.md §5; a real integration needs Simkl's
            # own show-type field to pick TV vs. ANIME correctly instead
            # of always assuming anime.
            title = _get_or_create_title(
                MediaType.ANIME, s.get("title", "Untitled"), s.get("year"), ids["simkl"], ids.get("tmdb")
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
                source=WatchEvent.Source.SIMKL,
            )
            created += 1
            if labels_out is not None:
                labels_out.append(title.name if episode is None else f"{title.name} S{episode.season}E{episode.episode}")
            touched_watch_keys.add((title.id, episode.id if episode else None))
        elif not existing.source:
            # Every sync re-pulls the whole history (no incremental
            # cursor), so this backfills rows logged before the source
            # field existed on the next sync - same as nuvio.py/trakt.py's
            # own dedup.
            existing.source = WatchEvent.Source.SIMKL
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
