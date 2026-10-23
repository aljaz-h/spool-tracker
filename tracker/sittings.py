"""Title detail "Your activity" timeline - grouping a title's plays into
sittings and turning them into a ready-to-render view model (layout choice,
facts row, node/block positions as percentages).

Pure on purpose: no ORM, no request, no "now" or timezone read from the
environment - callers pass plays, a tzinfo and `now`, so every edge case
(gap at the threshold, crossing midnight, a profile's own timezone) is a
plain unit test. selectors.title_activity_timeline() is the thin ORM
wrapper; the template only renders what this returns."""

import statistics
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

# Consecutive plays no further apart than this belong to one sitting
# (inclusive: a gap of exactly 6 hours still counts as the same sitting).
SITTING_GAP = timedelta(hours=6)

# TV/anime whose first-to-last span is at most this get the time-of-day
# layout; up to DAYS_LAYOUT_MAX the proportional axis uses day ticks,
# beyond it month ticks.
HOURS_LAYOUT_MAX = timedelta(hours=24)
DAYS_LAYOUT_MAX = timedelta(days=21)

# Two sittings whose circles would be closer than ~40px on a ~600px axis
# (as a percentage of it) are merged into one stretched node.
MERGE_PCT = 7.0

# A pause inside a binge is only labelled if it is longer than this beyond
# a typical episode's own length.
BREAK_MIN = timedelta(minutes=10)

# With a single play there is no gap to measure a typical episode from.
DEFAULT_EPISODE_LENGTH = timedelta(minutes=45)


@dataclass
class Play:
    watched_at: datetime
    season: int | None = None
    episode: int | None = None
    is_rewatch: bool = False


def _plural(n, word):
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def duration_text(delta):
    """'Took' style: 16 min / 6 h 24 min / 103 days."""
    total_min = int(delta.total_seconds() // 60)
    if total_min < 60:
        return f"{total_min} min"
    if total_min < 24 * 60:
        h, m = divmod(total_min, 60)
        return f"{h} h {m} min" if m else f"{h} h"
    return _plural(delta.days, "day")


def gap_text(delta):
    """Gaps between plays: 16 min / 5 h / 3 days / 6 weeks / 3 months /
    4 yrs 4 mo."""
    secs = delta.total_seconds()
    if secs < 3600:
        return f"{max(int(secs // 60), 1)} min"
    if secs < 48 * 3600:
        return f"{int(secs // 3600)} h"
    days = delta.days
    if days < 14:
        return _plural(days, "day")
    if days < 60:
        return _plural(days // 7, "week")
    if days < 730:
        return _plural(round(days / 30.44), "month")
    months = round(days / 30.44)
    years, rem = divmod(months, 12)
    return f"{years} yrs {rem} mo" if rem else f"{years} yrs"


def ago_text(delta):
    secs = delta.total_seconds()
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60)} min ago"
    if secs < 24 * 3600:
        return f"{int(secs // 3600)} h ago"
    days = delta.days
    if days < 14:
        return f"{_plural(days, 'day')} ago"
    if days < 60:
        return f"{_plural(days // 7, 'week')} ago"
    if days < 730:
        return f"{_plural(round(days / 30.44), 'month')} ago"
    return f"{_plural(round(days / 365.25), 'year')} ago"


def fmt_time(dt, h24=False):
    if h24:
        return f"{dt.hour:02d}:{dt.minute:02d}"
    h = dt.hour % 12 or 12
    return f"{h}:{dt.minute:02d} {'AM' if dt.hour < 12 else 'PM'}"


def _fmt_hour(dt, h24):
    if h24:
        return f"{dt.hour:02d}:00"
    return f"{dt.hour % 12 or 12} {'AM' if dt.hour < 12 else 'PM'}"


def fmt_date(dt, include_year=False):
    base = f"{dt:%a}, {dt:%b} {dt.day}"
    return f"{base}, {dt.year}" if include_year else base


def group_sittings(plays, gap=SITTING_GAP):
    """Plays (any order) -> list of sittings, oldest first. A sitting is a
    dict {"plays": [...oldest first], "start": dt, "end": dt}."""
    sittings = []
    for play in sorted(plays, key=lambda p: p.watched_at):
        # Compared in UTC: subtracting two datetimes that share a zoneinfo
        # tzinfo is wall-clock arithmetic, which would be an hour off
        # across a DST change and could split/merge a sitting at the edge.
        if sittings and play.watched_at.astimezone(UTC) - sittings[-1]["end"].astimezone(UTC) <= gap:
            sittings[-1]["plays"].append(play)
            sittings[-1]["end"] = play.watched_at
        else:
            sittings.append({"plays": [play], "start": play.watched_at, "end": play.watched_at})
    return sittings


def choose_layout(media_type, plays):
    if not plays:
        return "empty"
    if media_type == "movie":
        return "movie-multi" if len(plays) >= 2 else "movie-single"
    times = sorted(p.watched_at for p in plays)
    span = times[-1] - times[0]
    if span <= HOURS_LAYOUT_MAX:
        return "hours"
    if span <= DAYS_LAYOUT_MAX:
        return "days"
    return "months"


def episodes_label(plays, multi_season):
    """E1-E3 / E4 / S1E9-S2E2; runs of consecutive episodes collapse."""
    codes = sorted({(p.season, p.episode) for p in plays if p.episode is not None})
    if not codes:
        return ""

    def code(s, e):
        return f"S{s}E{e}" if multi_season else f"E{e}"

    runs = []
    for season, ep in codes:
        if runs and runs[-1][1][0] == season and ep == runs[-1][1][1] + 1:
            runs[-1][1] = (season, ep)
        else:
            runs.append([(season, ep), (season, ep)])
    if len(runs) > 3:
        return f"{code(*runs[0][0])}–{code(*runs[-1][1])}"
    return ", ".join(code(*a) if a == b else f"{code(*a)}–{code(*b)}" for a, b in runs)


def _pct(dt, start, end):
    total = (end - start).total_seconds()
    return 0.0 if total <= 0 else round((dt - start).total_seconds() / total * 100, 2)


def _facts_tv(plays, sittings, completed, tz, h24, hours):
    first, last = plays[0].watched_at, plays[-1].watched_at
    gaps = [b.watched_at - a.watched_at for a, b in zip(plays, plays[1:])]
    longest = gap_text(max(gaps)) if gaps else "—"

    def when(dt):
        return f"{fmt_date(dt)} · {fmt_time(dt, h24)}" if hours else fmt_date(dt)

    facts = [{"label": "Started", "value": when(first)}]
    if completed:
        facts.append({"label": "Finished", "value": when(last)})
        facts.append({"label": "Took", "value": duration_text(last - first)})
    else:
        facts.append({"label": "Last watched", "value": when(last)})
        facts.append({"label": "Plays", "value": f"{len(plays)} across {_plural(len(sittings), 'sitting')}"})
    facts.append({"label": "Longest break", "value": longest})
    return facts


def _facts_movie(plays, now, tz):
    first, last = plays[0].watched_at, plays[-1].watched_at
    gaps = [b.watched_at - a.watched_at for a, b in zip(plays, plays[1:])]
    return [
        {"label": "Viewings", "value": f"{len(plays)} times"},
        {"label": "First watched", "value": f"{first:%b} {first.day}, {first.year}"},
        {"label": "Latest", "value": ago_text(now - last)},
        {"label": "Longest gap", "value": gap_text(max(gaps)) if gaps else "—"},
    ]


def _plays_list(sittings, multi_season, now, h24, include_year):
    rows = []
    for s in reversed(sittings):
        start, end = s["start"], s["end"]
        same_day = start.date() == end.date()
        date = fmt_date(start, include_year) if same_day else f"{fmt_date(start)} – {fmt_date(end)}"
        if start == end or fmt_time(start, h24) == fmt_time(end, h24):
            when = fmt_time(start, h24)
        else:
            when = f"{fmt_time(start, h24)} – {fmt_time(end, h24)}"
        rows.append(
            {
                "date": date,
                "episodes": episodes_label(s["plays"], multi_season),
                "time": when,
                "ago": ago_text(now - end),
                "plays": [
                    {
                        "episodes": episodes_label([p], multi_season),
                        "time": fmt_time(p.watched_at, h24),
                        "date": fmt_date(p.watched_at),
                    }
                    for p in reversed(s["plays"])
                ],
                "multiple": len(s["plays"]) > 1,
            }
        )
    return rows


def _hours_layout(plays, tz, h24):
    times = [p.watched_at for p in plays]
    gaps = [(b - a) for a, b in zip(times, times[1:])]
    typical = statistics.median(gaps) if gaps else DEFAULT_EPISODE_LENGTH
    axis_start = times[0].replace(minute=0, second=0, microsecond=0)
    last_end = times[-1] + typical
    axis_end = last_end.replace(minute=0, second=0, microsecond=0)
    if axis_end < last_end:
        axis_end += timedelta(hours=1)
    total_hours = (axis_end - axis_start).total_seconds() / 3600
    step = 1 if total_hours <= 12 else 2
    multi_season = len({p.season for p in plays if p.episode is not None}) > 1

    blocks, breaks = [], []
    for i, p in enumerate(plays):
        nxt = times[i + 1] if i + 1 < len(times) else None
        end = times[i] + (min(typical, nxt - times[i]) if nxt else typical)
        blocks.append(
            {
                "label": episodes_label([p], multi_season) or "Watched",
                "left": _pct(times[i], axis_start, axis_end),
                "width": round(_pct(end, axis_start, axis_end) - _pct(times[i], axis_start, axis_end), 2),
                "time": fmt_time(times[i], h24),
            }
        )
        if nxt and (nxt - times[i]) - typical > BREAK_MIN:
            brk_start = times[i] + typical
            breaks.append(
                {
                    "left": _pct(brk_start, axis_start, axis_end),
                    "width": round(_pct(nxt, axis_start, axis_end) - _pct(brk_start, axis_start, axis_end), 2),
                    "center": round((_pct(brk_start, axis_start, axis_end) + _pct(nxt, axis_start, axis_end)) / 2, 2),
                    "label": f"{gap_text(nxt - brk_start)} break",
                }
            )

    ticks, t = [], axis_start
    while t <= axis_end:
        ticks.append({"left": _pct(t, axis_start, axis_end), "label": _fmt_hour(t, h24)})
        t += timedelta(hours=step)

    midnight = None
    nxt_midnight = (axis_start + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    if axis_start < nxt_midnight < axis_end:
        midnight = {
            "left": _pct(nxt_midnight, axis_start, axis_end),
            "before": fmt_date(axis_start),
            "after": fmt_date(nxt_midnight),
        }

    b_left, b_right = _pct(times[0], axis_start, axis_end), _pct(times[-1], axis_start, axis_end)
    return {
        "blocks": blocks,
        "breaks": breaks,
        "ticks": ticks,
        "midnight": midnight,
        "bracket": {
            "left": b_left,
            "width": round(b_right - b_left, 2),
            "center": round((b_left + b_right) / 2, 2),
            "right": round(100 - b_right, 2),
            "label": f"{duration_text(times[-1] - times[0])} in one sitting"
            if len(group_sittings(plays)) == 1
            else duration_text(times[-1] - times[0]),
            "start": fmt_time(times[0], h24),
            "end": fmt_time(times[-1], h24),
        },
    }


def _axis_ticks(first, last, layout):
    ticks = []
    span_days = (last - first).total_seconds() / 86400
    show_year = span_days > 330
    if layout == "months":
        cur = first.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        while cur <= last:
            if cur > first:
                label = f"{cur:%b} ’{cur:%y}" if show_year else f"{cur:%b}"
                ticks.append({"left": _pct(cur, first, last), "label": label})
            cur = cur.replace(year=cur.year + 1, month=1) if cur.month == 12 else cur.replace(month=cur.month + 1)
    else:
        step = max(1, int(span_days // 8) + (1 if span_days % 8 else 0)) if span_days > 8 else 1
        cur = first.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        while cur <= last:
            ticks.append({"left": _pct(cur, first, last), "label": f"{cur:%b} {cur.day}"})
            cur += timedelta(days=step)
    return ticks


def cluster_sittings(sittings, start, end):
    """Place sittings on an axis running start..end and merge any that
    would sit closer than MERGE_PCT. -> [{"left", "right", "sittings"}]
    (percentages; a merged cluster is stretched from left to right)."""
    clusters = []
    for s in sittings:
        pct = _pct(s["start"], start, end)
        if clusters and pct - clusters[-1]["right"] < MERGE_PCT:
            c = clusters[-1]
            c["sittings"].append(s)
            c["right"] = pct
        else:
            clusters.append({"left": pct, "right": pct, "sittings": [s]})
    return clusters


def node_size(count):
    """Circle diameter in px for a sitting/cluster of `count` plays."""
    return 14 + min(count, 8) * 2


def _axis_layout(sittings, plays, layout, completed, multi_season):
    first, last = sittings[0]["start"], sittings[-1]["start"]
    clusters = cluster_sittings(sittings, first, last)

    nodes = []
    for i, c in enumerate(clusters):
        all_plays = [p for s in c["sittings"] for p in s["plays"]]
        a, b = c["sittings"][0]["start"], c["sittings"][-1]["start"]
        dates = f"{a:%b} {a.day}" if a.date() == b.date() else (
            f"{a:%b} {a.day}–{b.day}" if (a.month, a.year) == (b.month, b.year) else f"{a:%b} {a.day} – {b:%b} {b.day}"
        )
        nodes.append(
            {
                "left": c["left"],
                "width": round(c["right"] - c["left"], 2),
                "center": round((c["left"] + c["right"]) / 2, 2),
                "count": len(all_plays),
                "size": node_size(len(all_plays)),
                "sittings": len(c["sittings"]),
                "dates": dates,
                "episodes": episodes_label(all_plays, multi_season),
                "finale": completed and i == len(clusters) - 1 and len(clusters) > 1,
            }
        )
    gaps = []
    for a, b in zip(clusters, clusters[1:]):
        delta = b["sittings"][0]["start"] - a["sittings"][-1]["end"]
        gaps.append({"left": round((a["right"] + b["left"]) / 2, 2), "label": gap_text(delta)})
    return {"nodes": nodes, "gaps": gaps, "ticks": _axis_ticks(first, last, layout)}


def _movie_items(plays, tz, now):
    items = []
    prev = None
    for i, p in enumerate(plays):
        year = p.watched_at.year
        if prev is not None:
            gap = p.watched_at - prev.watched_at
            empty = list(range(prev.watched_at.year + 1, year))
            items.append(
                {
                    "kind": "gap",
                    "text": f"+{gap_text(gap)}",
                    "empty_years": (f"{empty[0]} – {empty[-1]}" if len(empty) > 1 else str(empty[0])) if empty else None,
                }
            )
        items.append(
            {
                "kind": "viewing",
                "year_label": year if prev is None or prev.watched_at.year != year else None,
                "rewatch": p.is_rewatch or i > 0,
                "date": f"{p.watched_at:%b} {p.watched_at.day}, {year}",
                "weekday_time": None,  # filled by the caller (needs the 12/24h preference)
                "dt": p.watched_at,
                "ago": ago_text(now - p.watched_at),
            }
        )
        prev = p
    return items


def build_timeline(media_type, plays, tz, now, completed=False, h24=False):
    """plays: iterable of Play (UTC or aware, any order). Returns None when
    there are none, else a dict the template renders as-is - see layout keys
    above. All datetimes in the result are already in `tz`."""
    plays = sorted(
        (Play(p.watched_at.astimezone(tz), p.season, p.episode, p.is_rewatch) for p in plays),
        key=lambda p: p.watched_at,
    )
    now = now.astimezone(tz)
    layout = choose_layout(media_type, plays)
    if layout == "empty":
        return None
    sittings = group_sittings(plays)
    multi_season = len({p.season for p in plays if p.episode is not None}) > 1
    is_movie = media_type == "movie"
    result = {
        "layout": layout,
        "plays_count": len(plays),
        "sittings_count": len(sittings),
        "plays_list": _plays_list(sittings, multi_season, now, h24, include_year=is_movie or plays[0].watched_at.year != now.year),
    }
    if is_movie:
        result["facts"] = _facts_movie(plays, now, tz) if layout == "movie-multi" else []
        if layout == "movie-multi":
            items = _movie_items(plays, tz, now)
            for it in items:
                if it["kind"] == "viewing":
                    d = it.pop("dt")
                    it["weekday_time"] = f"{d:%a} · {fmt_time(d, h24)}"
            result["items"] = items
        else:
            p = plays[0]
            result["single"] = {
                "date": fmt_date(p.watched_at, True),
                "time": fmt_time(p.watched_at, h24),
                "ago": ago_text(now - p.watched_at),
            }
        return result
    result["facts"] = _facts_tv(plays, sittings, completed, tz, h24, hours=layout == "hours")
    if layout == "hours":
        result.update(_hours_layout(plays, tz, h24))
    else:
        result.update(_axis_layout(sittings, plays, layout, completed, multi_season))
    return result
