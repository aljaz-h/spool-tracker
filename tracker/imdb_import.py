"""IMDb CSV import - ratings.csv and (separately) a watchlist export,
both downloaded from the user's own IMDb account (Your Ratings / Your
Watchlist -> Export, under imdb.com/list/ratings and imdb.com/watchlist).
No API exists, so this is file parsing only.

IMDb's exports carry no "watched" signal at all - only a rating (with
the date it was *rated*, never the date it was watched) and a
watchlist (titles not yet watched). A title watched without being
rated leaves no trace in either export, so there's nothing to import
for it - not a limitation of this parser, a limitation of what IMDb
exposes at all. See docs/IMPORTING.md for how this is explained to a
user before they upload anything.

Both files share enough columns (Const, Title, Title Type, Year) that
which one was uploaded can be told apart from its header row alone -
the watchlist export's own Position/Created/Modified/Description
columns are the tell - rather than needing two separate upload buttons
in Settings -> Import & Export.

Individual TV episode ratings (Title Type "TV Episode") are skipped,
not guessed at - the export carries no season/episode number to place
one correctly against Spool's per-episode tracking. Whole-series TV
ratings are skipped too, from the opposite direction - a WatchEvent
needs one specific episode to attach a rating to, and a series-level
rating has none. Only the watchlist side is unaffected (adding a show
to the Watchlist needs no episode at all), so a TV series/mini-series
can still be imported there.
"""

import csv
import io

from django.utils import timezone
from django.utils.dateparse import parse_date

from .models import MediaType

# IMDb's own human-readable "Title Type" column values (confirmed
# against a real export for "Movie"/"TV Series"; the rest are IMDb's
# own documented title types, written out the same title-cased,
# space-separated way).
_MOVIE_TYPES = {"movie", "tv movie", "tv short", "short", "video"}
_TV_TYPES = {"tv series", "tv mini series", "tv miniseries", "tv special"}

# The one column only the watchlist export has - everything else
# overlaps between the two exports, so this is the whole detection rule.
_WATCHLIST_TELL = "position"


def detect_file_kind(headers):
    """"ratings" or "watchlist" from an uploaded IMDb CSV's header row,
    or None if it doesn't look like either export."""
    lowered = {(h or "").strip().lower() for h in headers}
    if "const" not in lowered or "title" not in lowered:
        return None
    return "watchlist" if _WATCHLIST_TELL in lowered else "ratings"


def _media_type(title_type_raw):
    t = (title_type_raw or "").strip().lower()
    if t in _MOVIE_TYPES:
        return MediaType.MOVIE
    if t in _TV_TYPES:
        return MediaType.TV
    return None  # TV Episode, Video Game, Podcast, or anything unrecognized


def _reader(fileobj):
    text = io.TextIOWrapper(fileobj, encoding="utf-8-sig", newline="")
    return csv.DictReader(text)


def _aware_midnight(d):
    return timezone.make_aware(timezone.datetime.combine(d, timezone.datetime.min.time()))


def parse_ratings(fileobj):
    """Returns a list of normalized items in the shape
    import_pipeline.scan_normalized_items expects - each row either
    becomes a watch event (Date Rated stands in for a watched date,
    the closest thing IMDb's export has) carrying its star rating, or
    an explained error (an unsupported title type, most often - see
    module docstring)."""
    normalized = []
    for i, row in enumerate(_reader(fileobj), start=2):
        const = (row.get("Const") or "").strip()
        title = (row.get("Title") or "").strip()
        year_raw = (row.get("Year") or "").strip()
        rating_raw = (row.get("Your Rating") or "").strip()
        date_raw = (row.get("Date Rated") or "").strip()
        title_type_raw = row.get("Title Type") or ""
        media_type = _media_type(title_type_raw)

        error = None
        if not title:
            error = "missing title"
        elif media_type is None:
            error = f'IMDb title type "{title_type_raw or "unknown"}" isn\'t supported (no season/episode data for individual episode ratings)'
        elif media_type == MediaType.TV:
            # A WatchEvent for a show needs a specific episode - IMDb's
            # rating is for the whole series, with no episode to anchor
            # it to, so there's genuinely nowhere in Spool's model to
            # put it (not a parsing gap - skipping a per-episode rating,
            # above, is the same limitation from the other direction).
            error = "IMDb's rating is for the whole TV series, not a specific episode - Spool can't store a show-level rating on its own"
        elif not rating_raw:
            error = "no rating on this row"
        elif not date_raw:
            error = "missing Date Rated"

        rating = None
        watched_at = None
        if not error:
            try:
                rating = int(float(rating_raw))
            except ValueError:
                rating = None
            if rating is None or not (1 <= rating <= 10):
                error = f'unrecognized rating "{rating_raw}"'
            else:
                d = parse_date(date_raw)
                if d is None:
                    error = f'unrecognized date "{date_raw}"'
                else:
                    watched_at = _aware_midnight(d)

        normalized.append(
            {
                "media_type": media_type,
                "title_name": title or f"Row {i}",
                "year": int(year_raw) if year_raw.isdigit() else None,
                "season": None,
                "episode": None,
                "watched_at": watched_at,
                "rating": rating,
                "source_external_ids": {"imdb": const} if const else {},
                "source_row": i,
                "error": error,
            }
        )
    return normalized


def parse_watchlist(fileobj):
    """Returns a tracker.backup.restore_lists-shaped list dict
    (is_watchlist=True), ready to stash on
    session.source_metadata["backup_lists_data"]. Rows with an
    unsupported title type are left out silently (a watchlist entry
    with no clean way to track it isn't worth surfacing as an import
    error the way a skipped rating is - nothing is lost, it's just not
    on the Watchlist)."""
    items = []
    for row in _reader(fileobj):
        const = (row.get("Const") or "").strip()
        title = (row.get("Title") or "").strip()
        year_raw = (row.get("Year") or "").strip()
        title_type_raw = row.get("Title Type") or ""
        position_raw = (row.get("Position") or "").strip()
        if not title:
            continue
        media_type = _media_type(title_type_raw)
        if media_type is None:
            continue
        items.append(
            {
                "type": "movie" if media_type == MediaType.MOVIE else "show",
                "media_type": media_type,
                "title": title,
                "year": int(year_raw) if year_raw.isdigit() else None,
                "ids": {"imdb": const} if const else {},
                "position": int(position_raw) if position_raw.isdigit() else 0,
            }
        )
    return {"is_watchlist": True, "items": items}
