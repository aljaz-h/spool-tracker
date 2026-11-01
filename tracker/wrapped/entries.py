"""Watch history for one profile and period, in the shape the Wrapped stats
(stats.py, year_stats.py) work on - the same fields api/routers/reports.py
serves to external callers, read directly from the database here."""

from datetime import datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class HistoryEntry:
    def __init__(self, title, type, genres, rating, watched_at, runtime_minutes,
                 country=None, studio=None, network=None, cast=None, directors=None, writers=None):
        self.title = title
        self.type = type
        self.genres = genres
        self.rating = rating
        self.watched_at = watched_at
        self.runtime_minutes = runtime_minutes
        # Year in Review only; the compact card never reads these.
        self.country = country
        self.studio = studio
        self.network = network
        self.cast = cast or []
        self.directors = directors or []
        self.writers = writers or []


def profile_tz(profile):
    """The profile's own timezone, UTC when blank or unknown."""
    try:
        return ZoneInfo(profile.timezone) if profile.timezone else ZoneInfo("UTC")
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def history_entries(profile, since, until):
    """Every watch in the half-open range [since, until) of dates, resolved
    in the profile's own timezone so a "January" recap doesn't clip the
    last hours of Jan 31 for someone west of UTC."""
    from tracker.models import WatchEvent

    tz = profile_tz(profile)
    since_dt = datetime.combine(since, time.min, tzinfo=tz)
    until_dt = datetime.combine(until, time.min, tzinfo=tz)
    events = (
        WatchEvent.objects.filter(profile=profile, watched_at__gte=since_dt, watched_at__lt=until_dt)
        .select_related("title", "episode")
        .prefetch_related("title__genres")
        .order_by("watched_at")
    )
    entries = []
    for event in events:
        title = event.title
        episode = event.episode
        # Per-entry runtime: the episode's own first, then the title's, else
        # 0 (a missing figure just doesn't add to the total).
        runtime = (episode.runtime_minutes if episode else None) or title.runtime_minutes or 0
        entries.append(
            HistoryEntry(
                title=title.name, type=title.media_type, genres=[g.name for g in title.genres.all()],
                rating=event.user_rating, watched_at=event.watched_at.isoformat(), runtime_minutes=runtime,
                country=title.country or None, studio=title.studio or None, network=title.network or None,
                cast=title.cast, directors=title.directors, writers=title.writers,
            )
        )
    return entries
