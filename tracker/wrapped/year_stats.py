"""Computes the much larger set of numbers the Year in Review report
needs (app/templates/reports/year_in_review.html) from the same
HistoryEntry list stats.py's compute_stats() uses for the compact
Wrapped card. Kept in its own module rather than folded into stats.py
since the two have almost no overlap in what they compute, and Year in
Review's inputs (country/studio/network/cast/directors/writers) are a
distinct, optional slice of the contract - see contract.md's "The Year
in Review report" section."""

import calendar
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .entries import HistoryEntry

SHOW_TYPES = ("tv", "anime")


def _parse_watched_at(raw: str, tz: ZoneInfo) -> datetime:
    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz)


def _day_first(dt: datetime, rest_fmt: str) -> str:
    """strftime with a leading-zero-free day, without relying on %-d -
    that's a glibc-only extension that raises on Windows, and this
    module gets unit-tested/run locally on Windows during development
    even though it only ever ships inside the Linux container."""
    return f"{dt.day} {dt.strftime(rest_fmt)}"


def _hours_display(minutes: int) -> str:
    hours = minutes / 60
    return f"{hours:,.0f}"


@dataclass
class NamedCount:
    name: str
    count: int


@dataclass
class TitleAgg:
    title: str
    type: str
    play_count: int
    total_minutes: int

    @property
    def hours_display(self) -> str:
        return _hours_display(self.total_minutes)


@dataclass
class ActivityPoint:
    label: str
    plays: int
    minutes: int

    @property
    def hours(self) -> float:
        return round(self.minutes / 60, 1)


@dataclass
class PlayMoment:
    title: str
    type: str
    watched_at_display: str
    play_count_in_period: int


@dataclass
class SectionStats:
    label: str  # "Movies" or "Shows"
    secondary_label: str  # "Most Watched Studios" or "Most Watched Networks"
    total_minutes: int = 0
    total_titles: int = 0
    total_plays: int = 0
    monthly_activity: list[ActivityPoint] = field(default_factory=list)
    most_played: list[TitleAgg] = field(default_factory=list)
    most_watched_secondary: list[NamedCount] = field(default_factory=list)
    most_watched_genres: list[NamedCount] = field(default_factory=list)
    most_watched_countries: list[NamedCount] = field(default_factory=list)
    least_watched_country: str | None = None
    country_count: int = 0

    @property
    def total_hours_display(self) -> str:
        return _hours_display(self.total_minutes)

    @property
    def avg_hours_per_month(self) -> str:
        return f"{self.total_minutes / 60 / 12:,.1f}"

    @property
    def avg_plays_per_month(self) -> str:
        return f"{self.total_plays / 12:,.1f}"


@dataclass
class YearInReviewStats:
    year: int
    total_minutes: int = 0
    total_titles: int = 0
    total_plays: int = 0
    first_play: PlayMoment | None = None
    last_play: PlayMoment | None = None
    most_active_week_label: str | None = None
    most_active_week_plays: int = 0
    most_popular_hour: int | None = None
    movies: SectionStats = field(default_factory=lambda: SectionStats("Movies", "Most Watched Studios"))
    shows: SectionStats = field(default_factory=lambda: SectionStats("Shows", "Most Watched Networks"))
    most_watched_cast: list[NamedCount] = field(default_factory=list)
    most_watched_directors: list[NamedCount] = field(default_factory=list)
    most_watched_writers: list[NamedCount] = field(default_factory=list)

    @property
    def total_hours_display(self) -> str:
        return _hours_display(self.total_minutes)


def _section_stats(entries: list[HistoryEntry], label: str, secondary_label: str,
                    secondary_field: str) -> SectionStats:
    section = SectionStats(label=label, secondary_label=secondary_label)
    if not entries:
        return section

    section.total_minutes = sum(e.runtime_minutes for e in entries)
    section.total_titles = len({e.title for e in entries})
    section.total_plays = len(entries)

    month_counter: Counter[int] = Counter()
    month_minutes: Counter[int] = Counter()
    for e in entries:
        month = int(e.watched_at[5:7])
        month_counter[month] += 1
        month_minutes[month] += e.runtime_minutes
    section.monthly_activity = [
        ActivityPoint(
            label=calendar.month_abbr[m],
            plays=month_counter.get(m, 0),
            minutes=month_minutes.get(m, 0),
        )
        for m in range(1, 13)
    ]

    play_counter: Counter[str] = Counter()
    minutes_by_title: Counter[str] = Counter()
    type_by_title: dict[str, str] = {}
    for e in entries:
        play_counter[e.title] += 1
        minutes_by_title[e.title] += e.runtime_minutes
        type_by_title[e.title] = e.type
    section.most_played = [
        TitleAgg(title=title, type=type_by_title[title], play_count=count,
                  total_minutes=minutes_by_title[title])
        for title, count in play_counter.most_common(5)
    ]

    secondary_counter: Counter[str] = Counter()
    for e in entries:
        value = getattr(e, secondary_field)
        if value:
            secondary_counter[value] += 1
    section.most_watched_secondary = [
        NamedCount(name=name, count=count) for name, count in secondary_counter.most_common(6)
    ]

    genre_counter: Counter[str] = Counter()
    for e in entries:
        genre_counter.update(e.genres)
    section.most_watched_genres = [
        NamedCount(name=name, count=count) for name, count in genre_counter.most_common(8)
    ]

    country_counter: Counter[str] = Counter()
    for e in entries:
        if e.country:
            country_counter[e.country] += 1
    ranked = country_counter.most_common()
    section.most_watched_countries = [NamedCount(name=n, count=c) for n, c in ranked[:6]]
    section.country_count = len(ranked)
    section.least_watched_country = ranked[-1][0] if ranked else None

    return section


def compute_year_in_review(entries: list[HistoryEntry], year: int, profile_timezone: str = "UTC") -> YearInReviewStats:
    stats = YearInReviewStats(year=year)
    if not entries:
        return stats

    try:
        tz = ZoneInfo(profile_timezone)
    except Exception:
        tz = ZoneInfo("UTC")

    ordered = sorted(entries, key=lambda e: e.watched_at)

    stats.total_minutes = sum(e.runtime_minutes for e in ordered)
    stats.total_titles = len({e.title for e in ordered})
    stats.total_plays = len(ordered)

    play_count_by_title: Counter[str] = Counter(e.title for e in ordered)

    first, last = ordered[0], ordered[-1]
    stats.first_play = PlayMoment(
        title=first.title, type=first.type,
        watched_at_display=_day_first(_parse_watched_at(first.watched_at, tz), "%b %Y, %H:%M"),
        play_count_in_period=play_count_by_title[first.title],
    )
    stats.last_play = PlayMoment(
        title=last.title, type=last.type,
        watched_at_display=_day_first(_parse_watched_at(last.watched_at, tz), "%b %Y, %H:%M"),
        play_count_in_period=play_count_by_title[last.title],
    )

    week_counter: Counter[str] = Counter()
    week_bounds: dict[str, tuple[str, str]] = {}
    hour_counter: Counter[int] = Counter()
    for e in ordered:
        local_dt = _parse_watched_at(e.watched_at, tz)
        iso_year, iso_week, _ = local_dt.isocalendar()
        key = f"{iso_year}-W{iso_week:02d}"
        week_counter[key] += 1
        week_start = local_dt.date() - timedelta(days=local_dt.isoweekday() - 1)
        week_end = week_start + timedelta(days=6)
        week_start_dt = datetime.combine(week_start, datetime.min.time())
        week_end_dt = datetime.combine(week_end, datetime.min.time())
        week_bounds[key] = (_day_first(week_start_dt, "%b"), _day_first(week_end_dt, "%b"))
        hour_counter[local_dt.hour] += 1

    if week_counter:
        top_week, top_week_plays = week_counter.most_common(1)[0]
        start_label, end_label = week_bounds[top_week]
        stats.most_active_week_label = f"{start_label} - {end_label}"
        stats.most_active_week_plays = top_week_plays
    if hour_counter:
        stats.most_popular_hour = hour_counter.most_common(1)[0][0]

    movie_entries = [e for e in ordered if e.type == "movie"]
    show_entries = [e for e in ordered if e.type in SHOW_TYPES]

    stats.movies = _section_stats(movie_entries, "Movies", "Most Watched Studios", "studio")
    stats.shows = _section_stats(show_entries, "Shows", "Most Watched Networks", "network")

    cast_counter: Counter[str] = Counter()
    director_counter: Counter[str] = Counter()
    writer_counter: Counter[str] = Counter()
    for e in ordered:
        cast_counter.update(e.cast)
        director_counter.update(e.directors)
        writer_counter.update(e.writers)
    stats.most_watched_cast = [NamedCount(n, c) for n, c in cast_counter.most_common(8)]
    stats.most_watched_directors = [NamedCount(n, c) for n, c in director_counter.most_common(6)]
    stats.most_watched_writers = [NamedCount(n, c) for n, c in writer_counter.most_common(6)]

    return stats
