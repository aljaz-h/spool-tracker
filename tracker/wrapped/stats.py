"""Turns a list of spool_client.HistoryEntry into the numbers a card
template renders. Kept separate from renderer.py so the two are testable
independently (numbers are easy to unit test; a screenshot is not)."""

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .entries import HistoryEntry


def _parse_watched_at(raw: str, tz: ZoneInfo) -> datetime:
    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz)


@dataclass
class TypeSplit:
    movie: int = 0
    tv: int = 0
    anime: int = 0

    @property
    def total(self) -> int:
        return self.movie + self.tv + self.anime

    def percent(self, kind: str) -> int:
        if self.total == 0:
            return 0
        return round(getattr(self, kind) / self.total * 100)


@dataclass
class RankedTitle:
    title: str
    plays: int
    minutes: int

    @property
    def hours_display(self) -> str:
        return f"{self.minutes // 60}h" if self.minutes >= 60 else f"{self.minutes}m"


@dataclass
class CardMoment:
    """The first or last watch of the period, for a banner."""

    title: str  # with the episode, "Show 2 Harbor 1x01", for a show
    backdrop_url: str = ""
    poster_url: str = ""


@dataclass
class WrappedStats:
    total_titles: int = 0
    total_watch_minutes: int = 0
    top_genre: str | None = None
    top_genre_count: int = 0
    longest_streak_days: int = 0
    top_rated_title: str | None = None
    top_rated_rating: int | None = None
    type_split: TypeSplit = field(default_factory=TypeSplit)
    entry_count: int = 0
    total_plays: int = 0
    top_shows: list = field(default_factory=list)
    top_movies: list = field(default_factory=list)
    first_play: CardMoment | None = None
    last_play: CardMoment | None = None

    @property
    def total_watch_hours_display(self) -> str:
        hours, minutes = divmod(self.total_watch_minutes, 60)
        if hours and minutes:
            return f"{hours}h {minutes}m"
        if hours:
            return f"{hours}h"
        return f"{minutes}m"


def compute_stats(entries: list[HistoryEntry], profile_timezone: str = "UTC") -> WrappedStats:
    if not entries:
        return WrappedStats()

    try:
        tz = ZoneInfo(profile_timezone)
    except Exception:
        tz = ZoneInfo("UTC")

    stats = WrappedStats(entry_count=len(entries), total_plays=len(entries))
    stats.total_watch_minutes = sum(e.runtime_minutes for e in entries)
    stats.total_titles = len({e.title for e in entries})

    genre_counter: Counter[str] = Counter()
    for e in entries:
        genre_counter.update(e.genres)
    if genre_counter:
        stats.top_genre, stats.top_genre_count = genre_counter.most_common(1)[0]

    rated = [e for e in entries if e.rating is not None]
    if rated:
        best = max(rated, key=lambda e: (e.rating, e.watched_at))
        stats.top_rated_title = best.title
        stats.top_rated_rating = best.rating

    split = TypeSplit()
    for e in entries:
        if e.type == "movie":
            split.movie += 1
        elif e.type == "tv":
            split.tv += 1
        elif e.type == "anime":
            split.anime += 1
    stats.type_split = split

    # Top 3 shows by plays and top 3 movies by time, and the first and last
    # watch of the period for the banners.
    plays, minutes, kind = Counter(), Counter(), {}
    for e in entries:
        plays[e.title] += 1
        minutes[e.title] += e.runtime_minutes
        kind[e.title] = e.type
    shows = sorted((t for t in plays if kind[t] in ("tv", "anime")), key=lambda t: (plays[t], minutes[t]), reverse=True)
    movies = sorted((t for t in plays if kind[t] == "movie"), key=lambda t: (minutes[t], plays[t]), reverse=True)
    stats.top_shows = [RankedTitle(t, plays[t], minutes[t]) for t in shows[:3]]
    stats.top_movies = [RankedTitle(t, plays[t], minutes[t]) for t in movies[:3]]
    ordered = sorted(entries, key=lambda e: e.watched_at)

    def moment(entry):
        name = f"{entry.title} {entry.episode_label}".strip()
        return CardMoment(name, entry.backdrop_url, entry.poster_url)

    stats.first_play, stats.last_play = moment(ordered[0]), moment(ordered[-1])

    watch_days = sorted({_parse_watched_at(e.watched_at, tz).date() for e in entries})
    longest = current = 1
    for i in range(1, len(watch_days)):
        if (watch_days[i] - watch_days[i - 1]).days == 1:
            current += 1
            longest = max(longest, current)
        else:
            current = 1
    stats.longest_streak_days = longest if watch_days else 0

    return stats
