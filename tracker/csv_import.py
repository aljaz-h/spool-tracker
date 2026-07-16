"""CSV import: parsing + upsert logic for the Settings → Import "CSV file"
flow. No tracker ships one canonical export schema, so this accepts the
common ground between Trakt's, Simkl's, and a generic export (aliased,
case-insensitive headers below) rather than requiring an exact format.

Title matching is by (name, year, media_type) — CSV rows carry no external
ID the way Trakt/Simkl sync rows do (see trakt.py's external_ids__trakt
matching), so a title imported via CSV and later synced from Trakt (or vice
versa) will only dedupe correctly if the name/year happen to match exactly.
That's a real limitation, not an oversight: without a TMDB/IMDB id column
in the CSV there's no reliable key to match on.
"""

import csv
import io
from datetime import datetime

from django.utils import timezone

from .models import Episode, MediaType, Title, WatchEvent

COLUMN_ALIASES = {
    "title": ["title", "name"],
    "media_type": ["media_type", "type"],
    "year": ["year", "release_year"],
    "season": ["season", "season_number"],
    "episode": ["episode", "episode_number", "ep"],
    "watched_at": ["watched_at", "date", "watched_date"],
    "rating": ["rating", "your_rating", "user_rating"],
}
FIELDS = list(COLUMN_ALIASES)
REQUIRED_FIELDS = ["title", "media_type", "watched_at"]

MEDIA_TYPE_ALIASES = {
    "movie": MediaType.MOVIE,
    "movies": MediaType.MOVIE,
    "show": MediaType.TV,
    "shows": MediaType.TV,
    "tv": MediaType.TV,
    "anime": MediaType.ANIME,
}

DATE_FORMATS = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]


def open_csv_reader(fileobj):
    """fileobj: a binary file opened for reading. Returns a csv.DictReader
    decoded as utf-8-sig (strips a BOM if present, a no-op otherwise)."""
    text = io.TextIOWrapper(fileobj, encoding="utf-8-sig", newline="")
    return csv.DictReader(text)


def detect_mapping(headers):
    """headers: list of raw CSV header strings. Returns {field: header} for
    every field with a confident alias match; unmatched fields are left out
    so the caller can prompt for a manual pick in the preview UI."""
    lowered = {(h or "").strip().lower(): h for h in headers}
    mapping = {}
    for field, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in lowered:
                mapping[field] = lowered[alias]
                break
    return mapping


def parse_date(raw):
    raw = (raw or "").strip()
    if not raw:
        return None
    for fmt in DATE_FORMATS:
        try:
            dt = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        return timezone.make_aware(dt) if timezone.is_naive(dt) else dt
    return None


def _cell(row, mapping, field):
    header = mapping.get(field)
    if not header:
        return ""
    return (row.get(header) or "").strip()


def parse_rows(dict_reader, mapping, limit=None):
    """Returns (rows, errors). rows are dicts ready for commit_rows(); errors
    are (csv_row_number, reason) for rows that failed to parse — these are
    skipped rather than aborting the whole file. limit caps how many *parsed*
    rows are collected (used for the preview); pass None for the full file."""
    rows, errors = [], []
    for i, raw_row in enumerate(dict_reader, start=2):  # row 1 is the header
        title = _cell(raw_row, mapping, "title")
        media_type_raw = _cell(raw_row, mapping, "media_type").lower()
        watched_raw = _cell(raw_row, mapping, "watched_at")

        if not title:
            errors.append((i, "missing title"))
            continue
        media_type = MEDIA_TYPE_ALIASES.get(media_type_raw)
        if media_type is None:
            errors.append((i, f'unrecognized media_type "{media_type_raw or "(blank)"}"'))
            continue
        watched_at = parse_date(watched_raw)
        if watched_at is None:
            errors.append((i, f'unparseable watched_at "{watched_raw or "(blank)"}"'))
            continue

        year_raw = _cell(raw_row, mapping, "year")
        season_raw = _cell(raw_row, mapping, "season")
        episode_raw = _cell(raw_row, mapping, "episode")
        rating_raw = _cell(raw_row, mapping, "rating")

        rating = None
        if rating_raw:
            try:
                candidate = int(float(rating_raw))
            except ValueError:
                candidate = None
            if candidate is not None and 1 <= candidate <= 10:
                rating = candidate

        rows.append(
            {
                "row": i,
                "title": title,
                "media_type": media_type,
                "year": int(year_raw) if year_raw.isdigit() else None,
                "season": int(season_raw) if season_raw.isdigit() else None,
                "episode": int(episode_raw) if episode_raw.isdigit() else None,
                "watched_at": watched_at,
                "rating": rating,
            }
        )
        if limit and len(rows) >= limit:
            break
    return rows, errors


def _get_or_create_title(media_type, name, year):
    title = Title.objects.filter(media_type=media_type, name__iexact=name, year=year or 0).first()
    if title:
        return title
    from tracker.integrations import tmdb
    from tracker.models import attach_genres

    external_ids = {}
    poster_url = ""
    genre_names = []
    match = tmdb.find_match(media_type, name, year)
    if match:
        external_ids = {"tmdb": str(match["id"]), "tmdb_kind": match["kind"]}
        poster_url = match["poster_url"] or ""
        details = tmdb.get_full_details(match["kind"], match["id"])
        if details:
            genre_names = details["genres"]
    title = Title.objects.create(
        media_type=media_type, name=name, year=year or 0, external_ids=external_ids, poster_url=poster_url
    )
    attach_genres(title, genre_names)
    return title


def commit_rows(profile, rows):
    """rows: parsed dicts from parse_rows(). Returns (imported_count,
    skipped) where skipped is [(csv_row_number, reason), ...] for rows that
    passed parsing but were rejected at the database step."""
    from . import completion, rewatches

    imported = 0
    skipped = []
    touched_movies = set()
    touched_shows = set()
    touched_watch_keys = set()
    for r in rows:
        if r["media_type"] != MediaType.MOVIE and (r["season"] is None or r["episode"] is None):
            skipped.append((r["row"], "TV/anime rows need a season and episode number"))
            continue

        title = _get_or_create_title(r["media_type"], r["title"], r["year"])
        episode = None
        if r["media_type"] != MediaType.MOVIE:
            episode, _ = Episode.objects.get_or_create(title=title, season=r["season"], episode=r["episode"])
            touched_shows.add(title.id)
        else:
            touched_movies.add(title.id)

        already_logged = WatchEvent.objects.filter(
            profile=profile, title=title, episode=episode, watched_at=r["watched_at"]
        ).exists()
        if already_logged:
            skipped.append((r["row"], "already in history"))
            continue

        WatchEvent.objects.create(
            profile=profile, title=title, episode=episode, watched_at=r["watched_at"], user_rating=r["rating"]
        )
        imported += 1
        touched_watch_keys.add((title.id, episode.id if episode else None))

    for title_id, episode_id in touched_watch_keys:
        rewatches.recompute_is_rewatch(
            profile, Title.objects.get(id=title_id), Episode.objects.get(id=episode_id) if episode_id else None
        )

    for title in Title.objects.filter(id__in=touched_movies):
        completion.update_movie_runtime(title)
    for title in Title.objects.filter(id__in=touched_shows):
        completion.sync_show_completion(profile, title)

    return imported, skipped
