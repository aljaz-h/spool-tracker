"""Everything the Year in Review page shows, worked out from the same
HistoryEntry list the compact Wrapped card uses (see entries.py). Kept apart
from stats.py since the two share almost nothing: this is the long, Trakt-style
annual report - activity over the year, most-played titles, genres, countries,
networks/studios, cast and crew - split into Shows and Movies."""

import calendar
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .entries import HistoryEntry

SHOW_TYPES = ("tv", "anime")


def _parse_watched_at(raw: str, tz: ZoneInfo) -> datetime:
    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz)


def _day_first(dt, rest_fmt: str) -> str:
    """strftime with a leading-zero-free day, without %-d (glibc only)."""
    return f"{dt.day} {dt.strftime(rest_fmt)}"


def duration_display(minutes: int) -> str:
    hours, mins = divmod(int(minutes), 60)
    if hours and mins:
        return f"{hours}h {mins:02d}m"
    if hours:
        return f"{hours}h"
    return f"{mins}m"


def _number(value: float) -> str:
    return f"{value:,.1f}" if value < 100 else f"{value:,.0f}"


@dataclass
class NamedCount:
    name: str
    count: int
    percent: int = 0  # share of the list's total, for the bars
    photo_url: str = ""  # the featured person's portrait (people cards)

    @property
    def letter(self) -> str:
        return (self.name or "?")[:1].upper()


@dataclass
class TitleAgg:
    title: str
    type: str
    play_count: int
    total_minutes: int
    poster_url: str = ""
    backdrop_url: str = ""
    tmdb_id: str = ""

    @property
    def hours_display(self) -> str:
        return f"{self.total_minutes / 60:,.0f}"

    @property
    def duration_display(self) -> str:
        return duration_display(self.total_minutes)


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
    poster_url: str = ""
    backdrop_url: str = ""
    tmdb_id: str = ""


@dataclass
class SectionStats:
    label: str  # "Movies" or "Shows"
    secondary_label: str  # "Studios" or "Networks"
    total_minutes: int = 0
    total_titles: int = 0
    total_plays: int = 0
    monthly_activity: list = field(default_factory=list)
    weekly_activity: list = field(default_factory=list)
    top_titles: list = field(default_factory=list)
    secondary: list = field(default_factory=list)
    genres: list = field(default_factory=list)
    countries: list = field(default_factory=list)
    peak_week_label: str | None = None
    peak_week_plays: int = 0
    peak_hour: int | None = None
    # Averages, as display strings: [hours, plays] per month / week / day.
    per_month: tuple = ("0", "0")
    per_week: tuple = ("0", "0")
    per_day: tuple = ("0", "0")

    @property
    def singular(self):
        return self.label[:-1]

    @property
    def total_hours_display(self) -> str:
        return f"{self.total_minutes / 60:,.0f}"

    @property
    def top_title(self):
        return self.top_titles[0] if self.top_titles else None

    @property
    def most_watched_secondary(self):
        return self.secondary[0].name if self.secondary else None

    @property
    def least_watched_secondary(self):
        return self.secondary[-1].name if self.secondary else None

    @property
    def most_watched_genre(self):
        return self.genres[0].name if self.genres else None

    @property
    def least_watched_genre(self):
        return self.genres[-1].name if self.genres else None

    @property
    def most_watched_country(self):
        return self.countries[0].name if self.countries else None

    @property
    def least_watched_country(self):
        return self.countries[-1].name if self.countries else None

    @property
    def max_week_minutes(self):
        return max((w.minutes for w in self.weekly_activity), default=0)


@dataclass
class YearInReviewStats:
    year: int
    total_minutes: int = 0
    total_titles: int = 0
    total_plays: int = 0
    days_covered: int = 365
    is_partial: bool = False
    first_play: PlayMoment | None = None
    last_play: PlayMoment | None = None
    most_active_week_label: str | None = None
    most_active_week_plays: int = 0
    most_popular_hour: int | None = None
    collage: list = field(default_factory=list)
    movies: SectionStats = field(default_factory=lambda: SectionStats("Movies", "Studios"))
    shows: SectionStats = field(default_factory=lambda: SectionStats("Shows", "Networks"))
    most_watched_cast: list = field(default_factory=list)
    most_watched_actors: list = field(default_factory=list)
    most_watched_actresses: list = field(default_factory=list)
    most_watched_directors: list = field(default_factory=list)
    most_watched_writers: list = field(default_factory=list)

    @property
    def total_hours_display(self) -> str:
        return f"{self.total_minutes / 60:,.0f}"

    @property
    def country_codes_json(self):
        """Countries watched across movies and shows, with counts, for the map."""
        merged = Counter()
        for section in (self.movies, self.shows):
            for item in section.countries:
                merged[item.name] += item.count
        return dict(merged)


def _week_buckets(year, entries_by_week, minutes_by_week):
    """52/53 weekly points counted from Jan 1 (week 1 = Jan 1-7), so the
    chart reads left to right across the calendar year."""
    jan1 = date(year, 1, 1)
    last = date(year, 12, 31)
    count = (last.timetuple().tm_yday - 1) // 7 + 1
    points = []
    for index in range(1, count + 1):
        start = jan1 + timedelta(days=(index - 1) * 7)
        points.append(
            ActivityPoint(
                label=_day_first(datetime.combine(start, datetime.min.time()), "%b"),
                plays=entries_by_week.get(index, 0),
                minutes=minutes_by_week.get(index, 0),
            )
        )
    return points


def _averages(total_minutes, total_plays, days):
    days = max(days, 1)
    months = days / (365.25 / 12)
    weeks = days / 7
    hours = total_minutes / 60

    def pair(divisor):
        return (_number(hours / divisor), _number(total_plays / divisor))

    return pair(max(months, 1)), pair(max(weeks, 1)), pair(days)


def _section_stats(entries, label, secondary_label, secondary_field, year, tz, days, rank_by):
    section = SectionStats(label=label, secondary_label=secondary_label)
    if not entries:
        return section

    section.total_minutes = sum(e.runtime_minutes for e in entries)
    section.total_titles = len({e.title for e in entries})
    section.total_plays = len(entries)
    section.per_month, section.per_week, section.per_day = _averages(
        section.total_minutes, section.total_plays, days
    )

    month_plays, month_minutes = Counter(), Counter()
    week_plays, week_minutes = Counter(), Counter()
    hour_counter = Counter()
    for e in entries:
        local = _parse_watched_at(e.watched_at, tz)
        month_plays[local.month] += 1
        month_minutes[local.month] += e.runtime_minutes
        week = (local.timetuple().tm_yday - 1) // 7 + 1
        week_plays[week] += 1
        week_minutes[week] += e.runtime_minutes
        hour_counter[local.hour] += 1
    section.monthly_activity = [
        ActivityPoint(calendar.month_abbr[m], month_plays.get(m, 0), month_minutes.get(m, 0)) for m in range(1, 13)
    ]
    section.weekly_activity = _week_buckets(year, week_plays, week_minutes)
    if week_plays:
        top_week, top_plays = week_plays.most_common(1)[0]
        start = date(year, 1, 1) + timedelta(days=(top_week - 1) * 7)
        end = min(start + timedelta(days=6), date(year, 12, 31))
        section.peak_week_label = (
            f"{_day_first(datetime.combine(start, datetime.min.time()), '%b')} - "
            f"{_day_first(datetime.combine(end, datetime.min.time()), '%b')}"
        )
        section.peak_week_plays = top_plays
    if hour_counter:
        section.peak_hour = hour_counter.most_common(1)[0][0]

    plays, minutes = Counter(), Counter()
    kind, poster, backdrop, tmdb_of = {}, {}, {}, {}
    for e in entries:
        plays[e.title] += 1
        minutes[e.title] += e.runtime_minutes
        kind[e.title] = e.type
        if e.poster_url:
            poster[e.title] = e.poster_url
        if e.backdrop_url:
            backdrop[e.title] = e.backdrop_url
        if e.tmdb_id:
            tmdb_of[e.title] = e.tmdb_id
    ranking = minutes if rank_by == "minutes" else plays
    ordered = sorted(plays, key=lambda t: (ranking[t], minutes[t], plays[t]), reverse=True)
    section.top_titles = [
        TitleAgg(t, kind[t], plays[t], minutes[t], poster.get(t, ""), backdrop.get(t, ""), tmdb_of.get(t, ""))
        for t in ordered[:10]
    ]

    secondary = Counter()
    for e in entries:
        value = getattr(e, secondary_field)
        if value:
            secondary[value] += 1
    secondary_total = sum(secondary.values()) or 1
    section.secondary = [NamedCount(n, c, round(c / secondary_total * 100)) for n, c in secondary.most_common(30)]

    genres = Counter()
    for e in entries:
        genres.update(e.genres)
    section.genres = [NamedCount(n, c) for n, c in genres.most_common(24)]

    countries = Counter()
    for e in entries:
        if e.country:
            countries[e.country] += 1
    country_total = sum(countries.values()) or 1
    section.countries = [NamedCount(n, c, round(c / country_total * 100)) for n, c in countries.most_common()]
    return section


def compute_year_in_review(entries: list[HistoryEntry], year: int, profile_timezone: str = "UTC") -> YearInReviewStats:
    stats = YearInReviewStats(year=year)
    if not entries:
        return stats

    try:
        tz = ZoneInfo(profile_timezone)
    except Exception:
        tz = ZoneInfo("UTC")

    today = datetime.now(tz).date()
    year_end = date(year, 12, 31)
    stats.is_partial = today < year_end
    stats.days_covered = ((min(today, year_end) - date(year, 1, 1)).days + 1) if year <= today.year else 365

    ordered = sorted(entries, key=lambda e: e.watched_at)
    stats.total_minutes = sum(e.runtime_minutes for e in ordered)
    stats.total_titles = len({e.title for e in ordered})
    stats.total_plays = len(ordered)

    plays_by_title = Counter(e.title for e in ordered)

    def moment(entry):
        return PlayMoment(
            title=entry.title, type=entry.type,
            watched_at_display=_day_first(_parse_watched_at(entry.watched_at, tz), "%b %Y, %H:%M"),
            play_count_in_period=plays_by_title[entry.title],
            poster_url=entry.poster_url, backdrop_url=entry.backdrop_url, tmdb_id=entry.tmdb_id,
        )

    stats.first_play = moment(ordered[0])
    stats.last_play = moment(ordered[-1])

    week_counter, hour_counter = Counter(), Counter()
    for e in ordered:
        local = _parse_watched_at(e.watched_at, tz)
        week_counter[(local.timetuple().tm_yday - 1) // 7 + 1] += 1
        hour_counter[local.hour] += 1
    if week_counter:
        top_week, top_plays = week_counter.most_common(1)[0]
        start = date(year, 1, 1) + timedelta(days=(top_week - 1) * 7)
        end = min(start + timedelta(days=6), year_end)
        stats.most_active_week_label = (
            f"{_day_first(datetime.combine(start, datetime.min.time()), '%b')} - "
            f"{_day_first(datetime.combine(end, datetime.min.time()), '%b')}"
        )
        stats.most_active_week_plays = top_plays
    if hour_counter:
        stats.most_popular_hour = hour_counter.most_common(1)[0][0]

    movie_entries = [e for e in ordered if e.type == "movie"]
    show_entries = [e for e in ordered if e.type in SHOW_TYPES]
    # Shows ranked by how many times they were played, movies by time spent.
    stats.shows = _section_stats(show_entries, "Shows", "Networks", "network", year, tz, stats.days_covered, "plays")
    stats.movies = _section_stats(movie_entries, "Movies", "Studios", "studio", year, tz, stats.days_covered, "minutes")

    # Posters for the cover: the most-watched titles by time, any type.
    minutes, plays, kind, poster, backdrop, tmdb_of = Counter(), Counter(), {}, {}, {}, {}
    for e in ordered:
        minutes[e.title] += e.runtime_minutes
        plays[e.title] += 1
        kind[e.title] = e.type
        if e.poster_url:
            poster[e.title] = e.poster_url
        if e.backdrop_url:
            backdrop[e.title] = e.backdrop_url
        if e.tmdb_id:
            tmdb_of[e.title] = e.tmdb_id
    stats.collage = [
        TitleAgg(t, kind[t], plays[t], minutes[t], poster[t], backdrop.get(t, ""), tmdb_of.get(t, ""))
        for t in sorted(minutes, key=lambda t: (minutes[t], plays[t]), reverse=True)
        if t in poster
    ][:6]

    # Distinct titles per person ("watched 12 titles with her"), not plays.
    cast_t, actor_t, actress_t, director_t, writer_t = (defaultdict(set) for _ in range(5))
    for e in ordered:
        for name in e.cast:
            cast_t[name].add(e.title)
            gender = e.cast_genders.get(name)
            if gender == 2:
                actor_t[name].add(e.title)
            elif gender == 1:
                actress_t[name].add(e.title)
        for name in e.directors:
            director_t[name].add(e.title)
        for name in e.writers:
            writer_t[name].add(e.title)

    def top(titles_by_name, limit=10):
        ranked = sorted(titles_by_name.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        return [NamedCount(name, len(titles)) for name, titles in ranked[:limit]]

    stats.most_watched_cast = top(cast_t)
    stats.most_watched_actors = top(actor_t)
    stats.most_watched_actresses = top(actress_t)
    stats.most_watched_directors = top(director_t)
    stats.most_watched_writers = top(writer_t)
    return stats

# ---- All Time ----------------------------------------------------------


@dataclass
class AllTimeSection:
    label: str  # "Shows" or "Movies"
    secondary_label: str  # "Networks" or "Studios"
    total_titles: int = 0
    total_plays: int = 0
    total_minutes: int = 0
    top_titles: list = field(default_factory=list)
    genres: list = field(default_factory=list)
    countries: list = field(default_factory=list)
    secondary: list = field(default_factory=list)  # (NamedCount, percent of the shown total)
    release_years: list = field(default_factory=list)  # [(year, titles), ...] one per year, gaps filled

    @property
    def label_lower(self):
        return self.label.lower()

    @property
    def top_title(self):
        return self.top_titles[0] if self.top_titles else None

    @property
    def most_watched_genre(self):
        return self.genres[0].name if self.genres else None

    @property
    def least_watched_genre(self):
        return self.genres[-1].name if self.genres else None

    @property
    def most_watched_secondary(self):
        return self.secondary[0].name if self.secondary else None

    @property
    def least_watched_secondary(self):
        return self.secondary[-1].name if self.secondary else None

    @property
    def singular(self):
        return self.label[:-1]

    @property
    def release_max(self):
        return max((n for _y, n in self.release_years), default=0)

    @property
    def release_mid(self):
        return self.release_max // 2

    @property
    def release_ticks(self):
        years = [y for y, _n in self.release_years]
        if not years:
            return []
        return [years[0], years[len(years) // 2], years[-1]]

    @property
    def release_bars(self):
        """(year, titles, height percent) per release year, for the histogram."""
        top = self.release_max or 1
        return [(y, n, max(round(n / top * 100), 2) if n else 0) for y, n in self.release_years]


@dataclass
class AllTimeStats:
    has_data: bool = False
    first_year: int | None = None
    days_on_spool: int = 0
    movie_titles: int = 0
    show_titles: int = 0
    episodes: int = 0
    total_minutes: int = 0
    split: list = field(default_factory=list)  # [(label, css class, minutes, percent), ...]
    movies: AllTimeSection = field(default_factory=lambda: AllTimeSection("Movies", "Studios"))
    shows: AllTimeSection = field(default_factory=lambda: AllTimeSection("Shows", "Networks"))
    countries: list = field(default_factory=list)
    collage: list = field(default_factory=list)
    most_watched_actors: list = field(default_factory=list)
    most_watched_actresses: list = field(default_factory=list)
    most_watched_directors: list = field(default_factory=list)
    most_watched_writers: list = field(default_factory=list)

    @property
    def time_display(self) -> str:
        return all_time_duration(self.total_minutes)


def all_time_duration(minutes: int) -> str:
    days, rest = divmod(int(minutes), 1440)
    hours, mins = divmod(rest, 60)
    if days:
        return f"{days}d {hours}h"
    return f"{hours}h {mins:02d}m" if hours else f"{mins}m"


def _all_time_section(entries, label, secondary_label, secondary_field):
    section = AllTimeSection(label=label, secondary_label=secondary_label)
    if not entries:
        return section
    section.total_plays = len(entries)
    section.total_minutes = sum(e.runtime_minutes for e in entries)
    section.total_titles = len({e.title for e in entries})

    plays, minutes, kind, poster, backdrop, tmdb_of = Counter(), Counter(), {}, {}, {}, {}
    year_of = {}
    for e in entries:
        plays[e.title] += 1
        minutes[e.title] += e.runtime_minutes
        kind[e.title] = e.type
        if e.poster_url:
            poster[e.title] = e.poster_url
        if e.backdrop_url:
            backdrop[e.title] = e.backdrop_url
        if e.tmdb_id:
            tmdb_of[e.title] = e.tmdb_id
        if e.release_year:
            year_of[e.title] = e.release_year
    ordered = sorted(plays, key=lambda t: (plays[t], minutes[t]), reverse=True)
    section.top_titles = [
        TitleAgg(t, kind[t], plays[t], minutes[t], poster.get(t, ""), backdrop.get(t, ""), tmdb_of.get(t, ""))
        for t in ordered[:10]
    ]

    per_year = Counter(year_of.values())
    if per_year:
        section.release_years = [(y, per_year.get(y, 0)) for y in range(min(per_year), max(per_year) + 1)]

    genres = Counter()
    for e in entries:
        genres.update(e.genres)
    section.genres = [NamedCount(n, c) for n, c in genres.most_common(24)]

    countries = Counter(e.country for e in entries if e.country)
    country_total = sum(countries.values()) or 1
    section.countries = [NamedCount(n, c, round(c / country_total * 100)) for n, c in countries.most_common()]

    secondary = Counter(getattr(e, secondary_field) for e in entries if getattr(e, secondary_field))
    secondary_total = sum(secondary.values()) or 1
    section.secondary = [NamedCount(n, c, round(c / secondary_total * 100)) for n, c in secondary.most_common(8)]
    return section


def compute_all_time(entries: list[HistoryEntry], profile_timezone: str = "UTC") -> AllTimeStats:
    stats = AllTimeStats()
    if not entries:
        return stats
    try:
        tz = ZoneInfo(profile_timezone)
    except Exception:
        tz = ZoneInfo("UTC")
    stats.has_data = True
    first = min(_parse_watched_at(e.watched_at, tz) for e in entries)
    stats.first_year = first.year
    stats.days_on_spool = max((datetime.now(tz).date() - first.date()).days, 0) + 1
    stats.total_minutes = sum(e.runtime_minutes for e in entries)
    stats.episodes = sum(1 for e in entries if e.episode_label)

    movie_entries = [e for e in entries if e.type == "movie"]
    show_entries = [e for e in entries if e.type in SHOW_TYPES]
    stats.movie_titles = len({e.title for e in movie_entries})
    stats.show_titles = len({e.title for e in show_entries})
    stats.movies = _all_time_section(movie_entries, "Movies", "Studios", "studio")
    stats.shows = _all_time_section(show_entries, "Shows", "Networks", "network")

    minutes_by_type = Counter()
    for e in entries:
        minutes_by_type[e.type] += e.runtime_minutes
    total = sum(minutes_by_type.values()) or 1
    for key, label in (("movie", "Movies"), ("tv", "TV"), ("anime", "Anime")):
        if minutes_by_type.get(key):
            stats.split.append((label, key, minutes_by_type[key], round(minutes_by_type[key] / total * 100)))

    countries = Counter(e.country for e in entries if e.country)
    country_total = sum(countries.values()) or 1
    stats.countries = [NamedCount(n, c, round(c / country_total * 100)) for n, c in countries.most_common()]

    # Posters / backdrops for the cover: the most played titles overall.
    overall = sorted(
        stats.shows.top_titles + stats.movies.top_titles,
        key=lambda a: (a.play_count, a.total_minutes), reverse=True,
    )
    stats.collage = [a for a in overall if a.poster_url][:6]

    actor_t, actress_t, director_t, writer_t = (defaultdict(set) for _ in range(4))
    for e in entries:
        for name in e.cast:
            gender = e.cast_genders.get(name)
            if gender == 2:
                actor_t[name].add(e.title)
            elif gender == 1:
                actress_t[name].add(e.title)
        for name in e.directors:
            director_t[name].add(e.title)
        for name in e.writers:
            writer_t[name].add(e.title)

    def top(titles_by_name, limit=10):
        ranked = sorted(titles_by_name.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        return [NamedCount(name, len(titles)) for name, titles in ranked[:limit]]

    stats.most_watched_actors = top(actor_t)
    stats.most_watched_actresses = top(actress_t)
    stats.most_watched_directors = top(director_t)
    stats.most_watched_writers = top(writer_t)
    return stats
