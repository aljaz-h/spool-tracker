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

    stats = WrappedStats(entry_count=len(entries))
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
