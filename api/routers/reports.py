"""GET /api/reports/ - spool-wrapped's own Reports API (see
github.com/aljaz-h/spool-wrapped's contract.md, the authoritative spec
this router implements against). Read-only, across every profile that's
separately opted in (Profile.wrapped_enabled) - a ServiceAPIKey doesn't
pick which profiles it can see, that flag does. Same router shape as
api/routers/scrobble.py, different auth class (api.auth.ServiceAPIKeyAuth,
not ScrobbleTokenAuth) - a ServiceAPIKey must never authenticate a
scrobble call and vice versa."""

from datetime import date, datetime, time
from typing import List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.shortcuts import get_object_or_404
from ninja import Router, Schema
from ninja.errors import HttpError

from api.auth import ServiceAPIKeyAuth
from tracker.models import Profile, WatchEvent

router = Router(auth=ServiceAPIKeyAuth())


class WrappedProfileOut(Schema):
    id: int
    display_name: str
    wrapped_webhook_url: Optional[str] = None
    wrapped_email_enabled: bool
    timezone: str


class ProfilesOut(Schema):
    profiles: List[WrappedProfileOut]


@router.get("/profiles/", response=ProfilesOut)
def list_wrapped_profiles(request):
    """Every profile with wrapped_enabled=True - a profile that never set
    that flag is simply absent, not listed with some "opted out" marker;
    spool-wrapped never learns it exists at all (see contract.md)."""
    profiles = Profile.objects.filter(wrapped_enabled=True).order_by("display_name")
    return {
        "profiles": [
            {
                "id": p.id,
                "display_name": p.display_name,
                "wrapped_webhook_url": p.wrapped_webhook_url or None,
                "wrapped_email_enabled": p.wrapped_email_enabled,
                "timezone": p.timezone,
            }
            for p in profiles
        ]
    }


class HistoryEntryOut(Schema):
    title: str
    type: str
    genres: List[str]
    rating: Optional[int] = None
    watched_at: datetime
    runtime_minutes: int
    country: Optional[str] = None
    studio: Optional[str] = None
    network: Optional[str] = None
    cast: List[str]
    directors: List[str]
    writers: List[str]


class HistoryOut(Schema):
    profile_id: int
    since: date
    until: date
    history: List[HistoryEntryOut]


def _parse_required_date(value, field_name):
    """since/until arrive as plain query strings (not a typed date param) -
    ninja/pydantic would only validate "is this string present", not "is
    it a real YYYY-MM-DD date", so that second check is manual (per
    contract.md: 422 for missing OR not-a-valid-date, same status either
    way)."""
    if not value:
        raise HttpError(422, f"{field_name} is required (YYYY-MM-DD)")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HttpError(422, f"{field_name} must be a valid YYYY-MM-DD date")


@router.get("/profiles/{profile_id}/history/", response=HistoryOut)
def profile_history(request, profile_id: int, since: str = "", until: str = ""):
    """Watch history for one profile, restricted to the half-open range
    [since, until) - resolved against *that profile's own* timezone
    (Profile.timezone, blank meaning UTC), not server-local time, so a
    "January" report doesn't clip the last few hours of Jan 31 for a
    profile west of UTC (see contract.md). 404 regardless of whether the
    profile has actually opted in to Wrapped - this endpoint doesn't leak
    which profile ids exist vs. which are opted in, that distinction only
    shows up in /profiles/ above."""
    profile = get_object_or_404(Profile, pk=profile_id)
    since_date = _parse_required_date(since, "since")
    until_date = _parse_required_date(until, "until")
    if since_date >= until_date:
        raise HttpError(422, "since must be before until")

    try:
        tz = ZoneInfo(profile.timezone) if profile.timezone else ZoneInfo("UTC")
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")
    since_dt = datetime.combine(since_date, time.min, tzinfo=tz)
    until_dt = datetime.combine(until_date, time.min, tzinfo=tz)

    events = (
        WatchEvent.objects.filter(profile=profile, watched_at__gte=since_dt, watched_at__lt=until_dt)
        .select_related("title", "episode")
        .prefetch_related("title__genres")
        .order_by("watched_at")
    )
    history = []
    for event in events:
        title = event.title
        episode = event.episode
        # Per-entry runtime (per-episode for tv/anime, not per-season) -
        # the episode's own runtime first (more specific), falling back
        # to the title's if that episode's runtime isn't known; 0 (not
        # null) if neither is, since runtime_minutes is a required
        # integer field in the contract, used for a sum, not a per-entry
        # display value where a missing figure would need to stand out.
        runtime = (episode.runtime_minutes if episode else None) or title.runtime_minutes or 0
        history.append(
            {
                "title": title.name,
                "type": title.media_type,
                "genres": [g.name for g in title.genres.all()],
                "rating": event.user_rating,
                "watched_at": event.watched_at,
                "runtime_minutes": runtime,
                "country": title.country or None,
                "studio": title.studio or None,
                "network": title.network or None,
                "cast": title.cast,
                "directors": title.directors,
                "writers": title.writers,
            }
        )
    return {"profile_id": profile.id, "since": since_date, "until": until_date, "history": history}
