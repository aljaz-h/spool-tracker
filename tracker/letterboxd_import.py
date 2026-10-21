"""Letterboxd ZIP import - the account data export from Letterboxd's
own Settings -> Import & Export -> Export your data. Films only (not a
distinction Letterboxd's own data model makes for TV), so every row
here is MediaType.MOVIE.

watched.csv (every film marked watched, whether rated or not) and
ratings.csv (only films with a star rating) are merged by (name, year)
into one watch-history item per watched.csv row - its own Date becomes
watched_at, and ratings.csv's Rating (when a same-name/year row exists
there) rides along as that row's rating. watchlist.csv becomes Spool's
own Watchlist, the same way tracker.backup.restore_lists already
restores a Spool backup's lists - a single zip can carry both, so one
import can produce real history candidates AND a Watchlist addition
together.

None of Letterboxd's export rows carry a stable, resolvable film id -
"Letterboxd URI" is a per-account short link (boxd.it/xxxx), not a
public film slug - so every row here matches purely by (name, year),
the same fuzzy-search limitation the generic CSV importer already
documents for sources with no native id at all.
"""

import csv
import io
import zipfile

from django.utils import timezone
from django.utils.dateparse import parse_date

from .models import MediaType

# Letterboxd's own 0.5-5.0 star scale, doubled onto Spool's 1-10
# integer scale - every Letterboxd rating is a multiple of 0.5, so
# doubling always lands on a whole number.
_RATING_SCALE = 2

# The upload view only caps the zip's own (compressed) size - a small,
# highly-compressible zip can still unpack into gigabytes (a "zip bomb"),
# so every member gets checked against its own declared uncompressed size
# before anything reads through it. 200MB is wildly more than a real
# Letterboxd export needs (even tens of thousands of watched films is a
# few MB of CSV) but still a hard ceiling. _MAX_ROWS backs that up while
# actually streaming each file, in case a crafted zip's central-directory
# size field doesn't match what decompressing it actually produces.
_MAX_MEMBER_SIZE = 200 * 1024 * 1024
_MAX_ROWS = 500_000


def _check_member_size(zf, name):
    if zf.getinfo(name).file_size > _MAX_MEMBER_SIZE:
        raise ValueError(f'"{name}" in this zip is too large to import (over 200MB uncompressed).')


def _reader(fileobj):
    text = io.TextIOWrapper(fileobj, encoding="utf-8-sig", newline="")
    return csv.DictReader(text)


def _aware_midnight(d):
    return timezone.make_aware(timezone.datetime.combine(d, timezone.datetime.min.time()))


def _row_key(name, year_raw):
    return (name or "").strip().lower(), (year_raw or "").strip()


def parse_zip(path):
    """Returns (normalized, watchlist_entry). normalized is
    scan_normalized_items-shaped history items built from watched.csv
    (merged with ratings.csv, if present). watchlist_entry is a
    tracker.backup.restore_lists-shaped list dict, or None if the zip
    had no watchlist.csv."""
    with zipfile.ZipFile(path) as zf:
        names = {n.lower(): n for n in zf.namelist()}

        ratings_by_key = {}
        if "ratings.csv" in names:
            _check_member_size(zf, names["ratings.csv"])
            with zf.open(names["ratings.csv"]) as f:
                for row_count, row in enumerate(_reader(f), start=1):
                    if row_count > _MAX_ROWS:
                        raise ValueError("ratings.csv has more rows than this import can handle.")
                    rating_raw = (row.get("Rating") or "").strip()
                    try:
                        rating = round(float(rating_raw) * _RATING_SCALE)
                    except ValueError:
                        continue
                    if 1 <= rating <= 10:
                        ratings_by_key[_row_key(row.get("Name"), row.get("Year"))] = rating

        normalized = []
        if "watched.csv" in names:
            _check_member_size(zf, names["watched.csv"])
            with zf.open(names["watched.csv"]) as f:
                for i, row in enumerate(_reader(f), start=2):
                    if i - 1 > _MAX_ROWS:
                        raise ValueError("watched.csv has more rows than this import can handle.")
                    name = (row.get("Name") or "").strip()
                    year_raw = (row.get("Year") or "").strip()
                    date_raw = (row.get("Date") or "").strip()

                    error = None if name else "missing title"
                    watched_at = None
                    if not error:
                        d = parse_date(date_raw)
                        if d is None:
                            error = f'unrecognized date "{date_raw}"'
                        else:
                            watched_at = _aware_midnight(d)

                    normalized.append(
                        {
                            "media_type": MediaType.MOVIE,
                            "title_name": name or f"Row {i}",
                            "year": int(year_raw) if year_raw.isdigit() else None,
                            "season": None,
                            "episode": None,
                            "watched_at": watched_at,
                            "rating": ratings_by_key.get(_row_key(name, year_raw)),
                            "source_external_ids": {},
                            "source_row": i,
                            "error": error,
                        }
                    )

        watchlist_entry = None
        if "watchlist.csv" in names:
            _check_member_size(zf, names["watchlist.csv"])
            items = []
            with zf.open(names["watchlist.csv"]) as f:
                for pos, row in enumerate(_reader(f), start=1):
                    if pos > _MAX_ROWS:
                        raise ValueError("watchlist.csv has more rows than this import can handle.")
                    name = (row.get("Name") or "").strip()
                    year_raw = (row.get("Year") or "").strip()
                    if not name:
                        continue
                    items.append(
                        {
                            "type": "movie",
                            "media_type": MediaType.MOVIE,
                            "title": name,
                            "year": int(year_raw) if year_raw.isdigit() else None,
                            "ids": {},
                            "position": pos,
                        }
                    )
            watchlist_entry = {"is_watchlist": True, "items": items}

    return normalized, watchlist_entry
