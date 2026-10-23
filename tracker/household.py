"""Title detail page's household view - who else in the household has
watched this title, as a list and (for shows) a shared-axis timeline.

Pure like sittings.py: selectors.title_household() does the (fixed number
of) queries and hands plain member dicts in; everything here is grouping,
counting and positions. Privacy is the caller's job - only members whose
share_activity is on (plus the viewer) may be passed in, so nothing here
can leak a profile that opted out into a row, a lane, a count or an
average.

Only episode *counts* ever cross between profiles ("3 episodes behind
you") - never which episodes or their titles."""

from datetime import timedelta

from .sittings import _pct, ago_text, cluster_sittings, group_sittings, node_size

# Timeline window: the last 12 weeks, stretched back to the earliest play
# by anyone when that falls within the cap.
DEFAULT_WINDOW = timedelta(weeks=12)
MAX_WINDOW = timedelta(weeks=26)

_TONES = {"completed": "success", "watching": "info", "dropped": "error", "planned": ""}
_LABELS = {"completed": "Completed", "watching": "Watching", "dropped": "Dropped", "planned": "Planned"}


def _episode_count(plays):
    return len({(p.season, p.episode) for p in plays if p.episode is not None}) or len(plays)


def _tv_status(member, watched, total):
    status = member["progress_status"]
    if status is None and member["plays"]:
        status = "completed" if total and watched >= total else "watching"
    return status


def _tv_row(member, viewer_watched, total, is_you, now):
    plays = member["plays"]
    watched = _episode_count(plays) if plays else 0
    status = _tv_status(member, watched, total)
    if status is None:
        status = "not_started"
    relative = None
    if not is_you and watched:
        diff = watched - viewer_watched
        if diff > 0:
            relative = "Ahead of you"
        elif diff < 0:
            n = -diff
            relative = f"{n} episode{'s' if n != 1 else ''} behind you"
        else:
            relative = "Same as you"
    pct = min(round(watched / total * 100), 100) if total else None
    return {
        "profile": member["profile"],
        "is_you": is_you,
        "name": "You" if is_you else member["profile"].display_name,
        "status": status,
        "status_label": "Not started" if status == "not_started" else _LABELS.get(status, status.title()),
        "status_tone": _TONES.get(status, ""),
        "watched": watched,
        "total": total,
        "pct": pct,
        "position": (f"E{watched} of {total}" if total else f"{watched} episode{'s' if watched != 1 else ''}") if watched else "",
        "relative": relative,
        "last_ago": ago_text(now - max(p.watched_at for p in plays)) if plays else None,
        "last_at": max(p.watched_at for p in plays) if plays else None,
        "rating": member["rating"],
        "recommend": status == "not_started",
        "recommended": member["recommended"],
        "reply": member["reply"],
    }


def _lane(row, member, start, end, tz):
    plays = [p for p in member["plays"] if start <= p.watched_at <= end]
    clusters = cluster_sittings(group_sittings(plays), start, end)
    nodes = []
    for c in clusters:
        count = sum(len(s["plays"]) for s in c["sittings"])
        nodes.append(
            {
                "left": c["left"],
                "width": round(c["right"] - c["left"], 2),
                "count": count,
                "size": node_size(count),
            }
        )
    note = None
    if row["status"] == "completed" and row["last_at"]:
        note = f"Finished {row['last_at']:%b} {row['last_at'].day}"
    elif row["status"] == "dropped":
        note = "Dropped"
    sub = row["status_label"]
    if row["status"] == "watching" and row["position"]:
        sub = f"Watching · {row['position']}"
    elif row["status"] == "completed" and row["rating"]:
        sub = f"Finished · rated {row['rating']}"
    elif row["status"] == "dropped" and row["watched"]:
        sub = f"Dropped after E{row['watched']}"
    note_left = round(nodes[-1]["left"] + nodes[-1]["width"], 2) if nodes else 0
    return {"row": row, "nodes": nodes, "note": note, "note_left": note_left, "sub": sub}


def _month_ticks(start, end):
    ticks = []
    cur = start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    while cur <= end:
        if cur > start:
            ticks.append({"left": _pct(cur, start, end), "label": f"{cur:%b}"})
        cur = cur.replace(year=cur.year + 1, month=1) if cur.month == 12 else cur.replace(month=cur.month + 1)
    return ticks


def build_tv(members, viewer_id, total_episodes, now, tz):
    """members: [{"profile", "plays", "rating", "progress_status",
    "recommended"}...] - already privacy-filtered, plays already in `tz`."""
    viewer = next(m for m in members if m["profile"].pk == viewer_id)
    viewer_watched = _episode_count(viewer["plays"]) if viewer["plays"] else 0
    rows = [(m, _tv_row(m, viewer_watched, total_episodes, m["profile"].pk == viewer_id, now)) for m in members]
    rows.sort(key=lambda mr: (not mr[1]["is_you"], mr[1]["status"] == "not_started", -mr[1]["watched"], mr[1]["name"].lower()))

    counts = {}
    for _m, r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    summary_parts = []
    for key, word in (("completed", "finished"), ("watching", "watching"), ("dropped", "dropped"), ("planned", "planned"), ("not_started", "not started")):
        if counts.get(key):
            summary_parts.append(f"{counts[key]} {word}")

    earliest = min((p.watched_at for m in members for p in m["plays"]), default=None)
    start = now - DEFAULT_WINDOW
    if earliest is not None and earliest < start:
        start = max(earliest, now - MAX_WINDOW)
    start = start.replace(hour=0, minute=0, second=0, microsecond=0)
    weeks = max(round((now - start).days / 7), 1)
    lanes = [_lane(r, m, start, now, tz) for m, r in rows]
    return {
        "kind": "tv",
        "rows": [r for _m, r in rows],
        "summary": " · ".join(summary_parts),
        "lanes": lanes,
        "window": {
            "label": f"Last {weeks} weeks · {start:%b} {start.day} – today",
            "ticks": _month_ticks(start, now),
        },
    }


def build_movie(members, viewer_id, now):
    rows = []
    for m in members:
        plays = sorted(m["plays"], key=lambda p: p.watched_at)
        is_you = m["profile"].pk == viewer_id
        if plays:
            status, label, tone = "watched", "Watched", "success"
        elif m["on_watchlist"]:
            status, label, tone = "watchlist", "On watchlist", ""
        else:
            status, label, tone = "not_watched", "Not watched", ""
        first = plays[0].watched_at if plays else None
        rows.append(
            {
                "profile": m["profile"],
                "is_you": is_you,
                "name": "You" if is_you else m["profile"].display_name,
                "status": status,
                "status_label": label,
                "status_tone": tone,
                "viewings": len(plays),
                "first": f"{first:%b} {first.day}, {first.year}" if first else None,
                "rating": m["rating"],
                "last_ago": ago_text(now - plays[-1].watched_at) if plays else None,
                "recommend": status == "not_watched",
                "recommended": m["recommended"],
                "reply": m["reply"],
            }
        )
    rows.sort(key=lambda r: (not r["is_you"], r["status"] != "watched", r["name"].lower()))
    watched = sum(1 for r in rows if r["status"] == "watched")
    ratings = [r["rating"] for r in rows if r["rating"]]
    rewatchers = sum(1 for r in rows if r["viewings"] >= 2)
    summary = [
        {"label": "Watched by", "value": f"{watched} of {len(rows)}"},
        {
            "label": "Household rating",
            "value": f"{sum(ratings) / len(ratings):.1f}" if ratings else "—",
            "sub": f"from {len(ratings)} rating{'s' if len(ratings) != 1 else ''}" if ratings else "No ratings yet",
        },
        {"label": "Rewatched", "value": f"By {rewatchers} {'person' if rewatchers == 1 else 'people'}" if rewatchers else "Nobody yet"},
    ]
    return {"kind": "movie", "rows": rows, "summary_facts": summary}
