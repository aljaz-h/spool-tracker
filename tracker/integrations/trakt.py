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

from urllib.parse import urlencode

import requests
from django.conf import settings

AUTHORIZE_URL = "https://trakt.tv/oauth/authorize"
TOKEN_URL = "https://api.trakt.tv/oauth/token"
API_BASE = "https://api.trakt.tv"


def authorize_url(redirect_uri, state):
    params = {
        "response_type": "code",
        "client_id": settings.TRAKT_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def exchange_code(code, redirect_uri):
    resp = requests.post(
        TOKEN_URL,
        json={
            "code": code,
            "client_id": settings.TRAKT_CLIENT_ID,
            "client_secret": settings.TRAKT_CLIENT_SECRET,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def _headers(access_token):
    return {
        "Content-Type": "application/json",
        "trakt-api-version": "2",
        "trakt-api-key": settings.TRAKT_CLIENT_ID,
        "Authorization": f"Bearer {access_token}",
    }


def fetch_history(access_token, limit=200):
    resp = requests.get(
        f"{API_BASE}/sync/history", headers=_headers(access_token), params={"limit": limit}, timeout=15
    )
    resp.raise_for_status()
    return resp.json()


def _get_or_create_title(media_type, name, year, trakt_id):
    from tracker.models import Title

    # Manual filter-then-create instead of get_or_create(): a JSONField key
    # lookup like external_ids__trakt=X can't double as a constructor kwarg
    # (Title(external_ids__trakt=X) isn't a real field), which is exactly
    # the pitfall get_or_create's defaults-merging would hit here.
    title = Title.objects.filter(media_type=media_type, external_ids__trakt=str(trakt_id)).first()
    if title:
        return title
    return Title.objects.create(
        media_type=media_type, name=name, year=year or 0, external_ids={"trakt": str(trakt_id)}
    )


def upsert_history_items(profile, items):
    """items: the parsed JSON list from fetch_history(). Returns the count
    of newly created WatchEvent rows (existing ones are left alone —
    dedup is by (profile, title, episode, watched_at), since Trakt history
    entries don't have a field we're already storing to key off of)."""
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
            if "trakt" not in ids:
                continue
            title = _get_or_create_title(MediaType.MOVIE, m.get("title", "Untitled"), m.get("year"), ids["trakt"])
            episode = None
        elif item.get("type") == "episode":
            s = item.get("show") or {}
            e = item.get("episode") or {}
            ids = s.get("ids") or {}
            if "trakt" not in ids or "season" not in e or "number" not in e:
                continue
            title = _get_or_create_title(MediaType.TV, s.get("title", "Untitled"), s.get("year"), ids["trakt"])
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
