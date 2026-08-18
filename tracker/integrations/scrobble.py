"""The generic scrobble webhook's own write path (api/routers/scrobble.py) -
the player-agnostic counterpart to nuvio.py's own upsert_history_items/
upsert_progress_items. Where nuvio.py has to reverse-engineer a specific
player's private sync protocol and its own id scheme (content_id strings),
this only ever accepts a bare TMDB id directly - any player author who
already knows what they're playing almost always already has that id on
hand, so there's no per-player integration to build or maintain here at
all (see docs/SCROBBLE_API.md).

STOP_WATCHED_THRESHOLD mirrors the common "count it as watched" convention
several public scrobble APIs already use (Trakt's own /scrobble/stop, for
one) - close enough to the end that a viewer clearly finished, not so
close it excludes someone who stopped a few seconds before the credits."""

from django.utils import timezone

from tracker.models import Episode, MediaType, Title, WatchEvent, WatchProgress, attach_genres, attach_reports_metadata

STOP_WATCHED_THRESHOLD = 90.0


def _get_or_create_title(media_type, tmdb_id, name_hint="", year_hint=None):
    """Matching preference: an existing Title already carrying this exact
    tmdb_id (the common case for anything previously synced/tracked any
    other way - Trakt/Simkl/CSV import/Nuvio/browsing all converge on the
    same external_ids["tmdb"] key) > freshly fetched TMDB details > a bare
    Title with just the caller's own hints, if TMDB_API_KEY isn't
    configured or the id doesn't resolve. Never touches external_ids
    beyond "tmdb"/"tmdb_kind" - unlike nuvio.py's own version, there's no
    per-provider marker to set here, since this endpoint isn't tied to
    any one player."""
    from tracker.integrations import tmdb as tmdb_integration

    kind = "movie" if media_type == MediaType.MOVIE else "tv"
    existing = Title.objects.filter(media_type=media_type, external_ids__tmdb=str(tmdb_id)).first()
    if existing:
        return existing

    details = tmdb_integration.get_full_details(kind, tmdb_id)
    external_ids = {"tmdb": str(tmdb_id), "tmdb_kind": kind}
    if details:
        title = Title.objects.create(
            media_type=media_type, name=details["name"], year=details["year"] or 0,
            external_ids=external_ids, poster_url=details["poster_url"] or "",
        )
        attach_genres(title, details["genres"])
        attach_reports_metadata(title, tmdb_integration.get_reports_metadata(kind, tmdb_id, details))
        return title

    title = Title.objects.create(
        media_type=media_type, name=name_hint or "Untitled", year=int(year_hint) if year_hint else 0,
        external_ids=external_ids,
    )
    return title


def _position_seconds(title, episode, progress_percent):
    """progress_percent (0-100) -> a position_seconds figure for
    WatchProgress, derived from whatever runtime this app already knows
    (episode's own first, else the title's) - 0 if neither is known yet
    (a freshly created Title/Episode has no runtime until TMDB backfills
    it), same "best effort, never fatal" spirit as the rest of this
    endpoint. Clamped to [0, 100] first - a player sending a stray >100
    or negative value from a rounding quirk shouldn't produce a nonsense
    or negative position."""
    runtime_minutes = (episode.runtime_minutes if episode else None) or title.runtime_minutes
    if not runtime_minutes:
        return 0
    clamped = max(0.0, min(100.0, progress_percent))
    return round(runtime_minutes * 60 * clamped / 100)


def record_scrobble(profile, action, media_type, tmdb_id, season, episode_number, progress, name_hint="", year_hint=None):
    """The webhook's one real entry point - action is "start"/"pause"
    (always a progress update) or "stop" (progress update, UNLESS
    progress clears STOP_WATCHED_THRESHOLD, in which case it's logged as
    a genuine watch instead - see module docstring). Returns
    {"watch_event_created": bool, "title_id": int} for the endpoint's own
    response body.

    A TV item with no resolvable season/episode is impossible to place
    (there's no "episode-less" WatchEvent/current_episode concept for a
    show the way a movie has), so the whole call is a no-op rather than
    guessing - checked before anything is matched/created, not after, so
    a rejected call never leaves behind a Title nothing else references
    yet. The caller (api/routers/scrobble.py) turns a None return into a
    422, matching how a malformed request should be rejected rather than
    silently accepted and dropped."""
    if media_type == MediaType.TV and (season is None or episode_number is None):
        return None

    title = _get_or_create_title(media_type, tmdb_id, name_hint, year_hint)

    ep = None
    if media_type == MediaType.TV:
        ep, _ = Episode.objects.get_or_create(title=title, season=season, episode=episode_number)

    if action == "stop" and progress >= STOP_WATCHED_THRESHOLD:
        from tracker import completion, recommendations, rewatches

        WatchEvent.objects.create(
            profile=profile, title=title, episode=ep, watched_at=timezone.now(), source=WatchEvent.Source.WEBHOOK
        )
        rewatches.recompute_is_rewatch(profile, title, ep)
        if media_type == MediaType.MOVIE:
            completion.update_movie_runtime(title)
        else:
            completion.sync_show_completion(profile, title)
        completion.sync_watchlist_removal(profile, title)
        recommendations.mark_title_watched(profile, title)
        WatchProgress.objects.filter(profile=profile, title=title).delete()
        return {"watch_event_created": True, "title_id": title.id}

    WatchProgress.objects.update_or_create(
        profile=profile, title=title,
        defaults={
            "current_episode": ep,
            "position_seconds": _position_seconds(title, ep, progress),
            "status": WatchProgress.Status.WATCHING,
        },
    )
    return {"watch_event_created": False, "title_id": title.id}
