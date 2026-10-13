"""Spool's own full backup format - everything a profile owns that the
CSV and Trakt-JSON exports leave out.

    {
      "format": "spool-backup",
      "version": 1,
      "exported_at": "...", "app_version": "...", "profile": "Name",
      "history": [ Trakt /sync/history-shaped items, plus "rating" ],
      "lists": [
        {"name", "is_watchlist", "is_shared", "tags", "created_at",
         "items": [{"type": "movie"|"show", "media_type", "title", "year",
                    "ids": {"tmdb", "tmdb_kind", "trakt"}, "position",
                    "added_at"}]}
      ]
    }

history reuses the Trakt shape on purpose: csv_import already parses it
(see parse_json_rows), so a backup's watches go through the normal Import
Review like any other file. lists are restored when that review is
committed (import_pipeline._commit_candidates -> restore_lists, from the
copy the scan keeps on the session - the upload itself is deleted once
scanned), matching
each item to a local title by its ids the same way history rows are.
"""

from django.utils import timezone

FORMAT = "spool-backup"
VERSION = 1


def is_spool_backup(data):
    return isinstance(data, dict) and data.get("format") == FORMAT


def _ids(title):
    ids = {}
    tmdb_id = title.external_ids.get("tmdb")
    if tmdb_id:
        ids["tmdb"] = int(tmdb_id) if str(tmdb_id).isdigit() else tmdb_id
        if title.external_ids.get("tmdb_kind"):
            ids["tmdb_kind"] = title.external_ids["tmdb_kind"]
    if title.external_ids.get("trakt"):
        ids["trakt"] = title.external_ids["trakt"]
    return ids


def build_backup(profile):
    """The full backup dict for one profile - history (with ratings) and
    every list it owns, the Watchlist included."""
    from tracker.models import MediaType, WatchEvent, WatchList
    from tracker.version import APP_VERSION

    history = []
    events = WatchEvent.objects.filter(profile=profile).select_related("title", "episode").order_by("watched_at")
    for event in events:
        ids = _ids(event.title)
        if event.episode:
            item = {
                "type": "episode",
                "watched_at": event.watched_at.isoformat(),
                "show": {"title": event.title.name, "year": event.title.year, "ids": ids},
                "episode": {"season": event.episode.season, "number": event.episode.episode, "title": event.episode.name},
            }
        else:
            item = {
                "type": "movie",
                "watched_at": event.watched_at.isoformat(),
                "movie": {"title": event.title.name, "year": event.title.year, "ids": ids},
            }
        if event.title.media_type == MediaType.ANIME:
            # Not a Trakt field - lets an anime series/film come back as
            # anime (csv_import._row_from_trakt_item reads it).
            item["media_type"] = MediaType.ANIME
        if event.user_rating:
            item["rating"] = event.user_rating
        history.append(item)

    lists = []
    for wl in WatchList.objects.filter(profile=profile).prefetch_related("items__title").order_by("-is_watchlist", "name"):
        items = []
        for li in sorted(wl.items.all(), key=lambda i: (i.position, i.id)):
            t = li.title
            items.append(
                {
                    "type": "movie" if (t.external_ids.get("tmdb_kind") or ("movie" if t.media_type == MediaType.MOVIE else "tv")) == "movie" else "show",
                    "media_type": t.media_type,
                    "title": t.name,
                    "year": t.year or None,
                    "ids": _ids(t),
                    "position": li.position,
                    "added_at": li.added_at.isoformat() if li.added_at else None,
                }
            )
        lists.append(
            {
                "name": wl.name,
                "is_watchlist": wl.is_watchlist,
                "is_shared": wl.is_shared,
                "tags": list(wl.tags or []),
                "created_at": wl.created_at.isoformat() if wl.created_at else None,
                "items": items,
            }
        )

    return {
        "format": FORMAT,
        "version": VERSION,
        "exported_at": timezone.now().isoformat(),
        "app_version": APP_VERSION,
        "profile": profile.display_name,
        "history": history,
        "lists": lists,
    }


def list_summary(data):
    """{"lists": N, "items": M} for a backup's lists - shown on the Import
    Review page so it's clear lists will be restored too."""
    lists = [entry for entry in (data.get("lists") or []) if isinstance(entry, dict)]
    return {"lists": len(lists), "items": sum(len(entry.get("items") or []) for entry in lists)}


def restore_lists(profile, data, labels_out=None):
    """Recreates a backup's lists on this profile. The backup's Watchlist
    goes into this profile's own Watchlist (ensure_watchlist); any other
    list is matched by name, created if missing (with its sharing and
    tags). Items already on a list are left alone, so restoring the same
    backup twice adds nothing. Titles are matched/created by their ids
    exactly as history rows are (csv_import._get_or_create_title).
    Returns (lists_touched, items_added)."""
    from tracker.csv_import import TITLE_MAX_LENGTH, _get_or_create_title, _safe_int
    from tracker.models import MediaType, WatchList, WatchListItem
    from tracker.watchlists import ensure_watchlist

    lists_touched = items_added = 0
    for entry in data.get("lists") or []:
        if not isinstance(entry, dict) or not (entry.get("name") or entry.get("is_watchlist")):
            continue
        if entry.get("is_watchlist"):
            watchlist = ensure_watchlist(profile)
        else:
            name = str(entry["name"])[:100]
            tags = [str(t)[:50] for t in (entry.get("tags") or []) if str(t).strip()]
            watchlist, _ = WatchList.objects.get_or_create(
                profile=profile, name=name,
                defaults={"is_shared": bool(entry.get("is_shared")), "tags": tags},
            )
        lists_touched += 1
        for item in entry.get("items") or []:
            if not isinstance(item, dict) or not item.get("title"):
                continue
            media_type = item.get("media_type")
            if media_type not in (MediaType.MOVIE, MediaType.TV, MediaType.ANIME):
                media_type = MediaType.MOVIE if item.get("type") == "movie" else MediaType.TV
            ids = item.get("ids") or {}
            tmdb_kind = ids.get("tmdb_kind") or ("movie" if item.get("type") == "movie" else "tv")
            title = _get_or_create_title(
                media_type, str(item["title"])[:TITLE_MAX_LENGTH], _safe_int(item.get("year")),
                ids.get("trakt"), ids.get("tmdb"), tmdb_kind,
            )
            _, created = WatchListItem.objects.get_or_create(
                watchlist=watchlist, title=title,
                defaults={"position": _safe_int(item.get("position")) or 0},
            )
            if created:
                items_added += 1
                if labels_out is not None:
                    labels_out.append(f"{title.name} ({watchlist.name})")
    return lists_touched, items_added


def read_backup(path):
    """The parsed backup at path, or None when the file isn't a Spool
    backup (any other JSON export, a CSV, a zip, unreadable)."""
    import json

    try:
        with open(path, "rb") as f:
            data = json.loads(f.read().decode("utf-8-sig"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return data if is_spool_backup(data) else None
