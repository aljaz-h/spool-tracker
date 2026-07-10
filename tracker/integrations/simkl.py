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


def _headers(access_token, client_id):
    return {
        "Content-Type": "application/json",
        "simkl-api-key": client_id,
        "Authorization": f"Bearer {access_token}",
    }


def fetch_history(access_token, client_id):
    resp = requests.get(f"{API_BASE}/sync/activities", headers=_headers(access_token, client_id), timeout=15)
    resp.raise_for_status()
    return resp.json()


def _get_or_create_title(media_type, name, year, simkl_id):
    from tracker.integrations import tmdb
    from tracker.models import Title

    title = Title.objects.filter(media_type=media_type, external_ids__simkl=str(simkl_id)).first()
    if title:
        return title
    poster_url = tmdb.find_poster_url(media_type, name, year) or ""
    return Title.objects.create(
        media_type=media_type, name=name, year=year or 0, external_ids={"simkl": str(simkl_id)}, poster_url=poster_url
    )


def upsert_history_items(profile, items):
    """Structurally mirrors trakt.upsert_history_items() — same dedup
    strategy, same shape assumption. See module docstring."""
    from django.utils.dateparse import parse_datetime

    from tracker.models import Episode, MediaType, WatchEvent

    created = 0
    for item in items:
        watched_at = parse_datetime(item.get("watched_at", ""))
        if watched_at is None:
            continue

        if item.get("type") == "movie":
            m = item.get("movie") or {}
            ids = m.get("ids") or {}
            if "simkl" not in ids:
                continue
            title = _get_or_create_title(MediaType.MOVIE, m.get("title", "Untitled"), m.get("year"), ids["simkl"])
            episode = None
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
            title = _get_or_create_title(MediaType.ANIME, s.get("title", "Untitled"), s.get("year"), ids["simkl"])
            episode, _ = Episode.objects.get_or_create(
                title=title, season=e["season"], episode=e["number"], defaults={"name": e.get("title") or ""}
            )
        else:
            continue

        already_logged = WatchEvent.objects.filter(
            profile=profile, title=title, episode=episode, watched_at=watched_at
        ).exists()
        if not already_logged:
            WatchEvent.objects.create(profile=profile, title=title, episode=episode, watched_at=watched_at)
            created += 1
    return created
