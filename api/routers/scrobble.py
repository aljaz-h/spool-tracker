from typing import Optional

from ninja import Router, Schema
from ninja.errors import HttpError

from api.auth import ScrobbleTokenAuth
from tracker.integrations import scrobble
from tracker.models import MediaType

router = Router(auth=ScrobbleTokenAuth())


class ScrobbleIn(Schema):
    action: str  # "start" | "pause" | "stop" - see docs/SCROBBLE_API.md
    media_type: str  # "movie" | "tv"
    tmdb_id: int
    progress: float  # 0-100, percent watched so far
    season: Optional[int] = None
    episode: Optional[int] = None
    title: Optional[str] = None  # name hint, only used if TMDB lookup fails
    year: Optional[int] = None  # year hint, only used if TMDB lookup fails


@router.post("")
def record_scrobble(request, payload: ScrobbleIn):
    """POST /api/scrobble - see docs/SCROBBLE_API.md for the full contract
    (auth, payload shape, examples). request.auth is the Profile the
    bearer token resolved to (api/auth.py) - every scrobble is recorded
    against that profile, there's no way to scrobble as anyone else."""
    if payload.action not in ("start", "pause", "stop"):
        raise HttpError(422, "action must be one of: start, pause, stop")
    if payload.media_type not in ("movie", "tv"):
        raise HttpError(422, "media_type must be one of: movie, tv")
    media_type = MediaType.MOVIE if payload.media_type == "movie" else MediaType.TV
    if media_type == MediaType.TV and (payload.season is None or payload.episode is None):
        raise HttpError(422, "season and episode are required when media_type is tv")

    result = scrobble.record_scrobble(
        profile=request.auth,
        action=payload.action,
        media_type=media_type,
        tmdb_id=payload.tmdb_id,
        season=payload.season,
        episode_number=payload.episode,
        progress=payload.progress,
        name_hint=payload.title or "",
        year_hint=payload.year,
    )
    if result is None:
        raise HttpError(422, "could not resolve or place this title")
    return result
