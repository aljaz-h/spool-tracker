"""Data import: parsing + upsert logic for the Settings → Import Data flow.
Accepts three upload shapes:

- **CSV** — no tracker ships one canonical export schema, so this accepts
  the common ground between Trakt's, Simkl's, and a generic export
  (aliased, case-insensitive headers below) rather than requiring an
  exact format. This is the only shape with a column-mapping step, since
  it's the only one where "which header means what" is ambiguous.
- **JSON** — either Trakt's own history/export item shape
  ({"type": "movie"/"episode", "movie"/"show": {...}, "watched_at": ...},
  the same shape /sync/history returns - see trakt.py's
  upsert_history_items, which this mirrors for the OAuth sync path) or a
  generic flat object using the same field aliases as the CSV columns
  below. Per Trakt's own OAuth integration code (trakt.py), the exact
  field names of a real exported watched-history JSON file are
  unverified against a live export - if a real file doesn't match, rows
  from it show up as parse errors (visible in the preview) rather than
  failing silently, which is the signal to come back and adjust
  _row_from_trakt_item()/_looks_like_trakt_history_item() below.
- **ZIP** — Trakt's own "Export now" (Settings → Data) downloads a zip of
  JSON files rather than one CSV, so a zip is walked for every .csv/.json
  entry inside and each is parsed with the matching parser above,
  concatenating rows/errors across all of them. Entries that are neither
  (images, a README, etc.) are skipped rather than reported as errors.

Title matching prefers a Trakt id when the row carries one (JSON/zip
imports sourced from Trakt) via the same external_ids__trakt lookup
trakt.py's OAuth sync uses, falling back to (name, year, media_type) —
CSV rows and generic JSON rows carry no external id at all, so those
only dedupe against a Trakt-synced title if the name/year happen to
match exactly. That's a real limitation, not an oversight: without a
TMDB/IMDB/Trakt id there's no reliable key to match on.
"""

import csv
import io
import json
import zipfile
from datetime import datetime

from django.utils import timezone
from django.utils.dateparse import parse_datetime

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
# Title.name's own max_length - read off the model rather than a second
# hardcoded 255, so this can't silently drift out of sync with it. An
# import row's title is free text from the uploaded file (unlike
# media_type/year/season/episode/rating, which are all validated against
# a fixed set or parsed as plain integers below) - truncating here means
# an oversized value never reaches Title.objects.create() at all,
# instead of relying on the database to reject the insert outright.
TITLE_MAX_LENGTH = Title._meta.get_field("name").max_length

MEDIA_TYPE_ALIASES = {
    "movie": MediaType.MOVIE,
    "movies": MediaType.MOVIE,
    "show": MediaType.TV,
    "shows": MediaType.TV,
    "tv": MediaType.TV,
    "anime": MediaType.ANIME,
}

DATE_FORMATS = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]

EXTENSION_KINDS = {".csv": "csv", ".json": "json", ".zip": "zip"}


def detect_kind(filename):
    """Returns "csv"/"json"/"zip" from the uploaded filename's extension,
    or None if unrecognized - the only signal available, since browsers
    don't reliably send a useful Content-Type for these."""
    lower = (filename or "").lower()
    for ext, kind in EXTENSION_KINDS.items():
        if lower.endswith(ext):
            return kind
    return None


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
    # Falls back to ISO-8601 (django.utils.dateparse.parse_datetime) for
    # JSON/Trakt-shaped sources, which use "2014-09-01T09:10:11.000Z"
    # rather than either of the plain formats above.
    dt = parse_datetime(raw)
    if dt is None:
        return None
    return timezone.make_aware(dt) if timezone.is_naive(dt) else dt


def _cell(row, mapping, field):
    header = mapping.get(field)
    if not header:
        return ""
    return (row.get(header) or "").strip()


def _parse_rating(rating_raw):
    """Shared by CSV/generic-JSON rows - out-of-range values (not 1-10)
    are dropped rather than treated as a parse error, since a rating is
    never required to import the watch itself."""
    if not rating_raw:
        return None
    try:
        candidate = int(float(rating_raw))
    except ValueError:
        return None
    return candidate if 1 <= candidate <= 10 else None


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

        rows.append(
            {
                "row": i,
                "title": title[:TITLE_MAX_LENGTH],
                "media_type": media_type,
                "year": int(year_raw) if year_raw.isdigit() else None,
                "season": int(season_raw) if season_raw.isdigit() else None,
                "episode": int(episode_raw) if episode_raw.isdigit() else None,
                "watched_at": watched_at,
                "rating": _parse_rating(rating_raw),
            }
        )
        if limit and len(rows) >= limit:
            break
    return rows, errors


def _looks_like_trakt_history_item(obj):
    return isinstance(obj, dict) and obj.get("type") in ("movie", "episode") and ("movie" in obj or "show" in obj)


def _safe_int(value):
    """Trakt-shaped JSON rows (unlike CSV/generic-JSON's own year_raw/
    season_raw/episode_raw, which are already digit-checked strings) come
    straight from the uploaded file with no type guarantee at all - a
    year/season/episode number that isn't actually int-coercible would
    otherwise reach Title/Episode's PositiveSmallIntegerField columns
    unchanged and fail at the database instead of being validated here."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _row_from_trakt_item(item, i):
    """item: one entry from a Trakt-shaped JSON list - either a live
    /sync/history API response (see trakt.py's upsert_history_items,
    which this mirrors) or, best-effort, an entry from Trakt's own
    "Export now" zip (unverified against a real export - see module
    docstring). Returns (row, error), exactly one of which is None."""
    watched_raw = item.get("watched_at") or ""
    watched_at = parse_date(watched_raw)
    if watched_at is None:
        return None, (i, f'unparseable watched_at "{watched_raw or "(blank)"}"')

    if item.get("type") == "movie":
        m = item.get("movie") or {}
        ids = m.get("ids") or {}
        return {
            "row": i,
            "title": (m.get("title") or "")[:TITLE_MAX_LENGTH],
            "media_type": MediaType.MOVIE,
            "year": _safe_int(m.get("year")),
            "season": None,
            "episode": None,
            "watched_at": watched_at,
            "rating": None,
            "trakt_id": ids.get("trakt"),
            "tmdb_id": ids.get("tmdb"),
        }, None

    s = item.get("show") or {}
    e = item.get("episode") or {}
    if "season" not in e or "number" not in e:
        return None, (i, "episode item missing season/number")
    ids = s.get("ids") or {}
    return {
        "row": i,
        "title": (s.get("title") or "")[:TITLE_MAX_LENGTH],
        "media_type": MediaType.TV,
        "year": _safe_int(s.get("year")),
        "season": _safe_int(e.get("season")),
        "episode": _safe_int(e.get("number")),
        "watched_at": watched_at,
        "rating": None,
        "trakt_id": ids.get("trakt"),
        "tmdb_id": ids.get("tmdb"),
    }, None


def _generic_row_from_dict(d, i):
    """d: a JSON object that isn't Trakt-history-shaped - matched against
    the same field aliases as CSV headers (COLUMN_ALIASES), case-
    insensitive on keys instead of headers."""
    lowered = {(k or "").strip().lower(): v for k, v in d.items()}

    def cell(field):
        for alias in COLUMN_ALIASES[field]:
            if alias in lowered and lowered[alias] not in (None, ""):
                return str(lowered[alias]).strip()
        return ""

    title = cell("title")
    if not title:
        return None, (i, "missing title")
    media_type_raw = cell("media_type").lower()
    media_type = MEDIA_TYPE_ALIASES.get(media_type_raw)
    if media_type is None:
        return None, (i, f'unrecognized media_type "{media_type_raw or "(blank)"}"')
    watched_raw = cell("watched_at")
    watched_at = parse_date(watched_raw)
    if watched_at is None:
        return None, (i, f'unparseable watched_at "{watched_raw or "(blank)"}"')

    year_raw = cell("year")
    season_raw = cell("season")
    episode_raw = cell("episode")
    return {
        "row": i,
        "title": title[:TITLE_MAX_LENGTH],
        "media_type": media_type,
        "year": int(year_raw) if year_raw.isdigit() else None,
        "season": int(season_raw) if season_raw.isdigit() else None,
        "episode": int(episode_raw) if episode_raw.isdigit() else None,
        "watched_at": watched_at,
        "rating": _parse_rating(cell("rating")),
        "trakt_id": None,
        "tmdb_id": None,
    }, None


def parse_json_rows(data, limit=None):
    """data: already json.load()-ed content (list or dict). Dict inputs
    are flattened by concatenating every list-valued top-level entry
    (some export tools group items under keys like "movies"/"shows"
    rather than a single flat list). Each item is matched against
    Trakt's history shape first, falling back to a generic flat object -
    see _looks_like_trakt_history_item/_row_from_trakt_item/
    _generic_row_from_dict above. Returns (rows, errors) in the same
    shape as parse_rows()."""
    if isinstance(data, dict):
        items = [item for value in data.values() if isinstance(value, list) for item in value]
    elif isinstance(data, list):
        items = data
    else:
        items = []

    rows, errors = [], []
    for i, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            errors.append((i, "not a JSON object"))
            continue
        if _looks_like_trakt_history_item(item):
            row, error = _row_from_trakt_item(item, i)
        else:
            row, error = _generic_row_from_dict(item, i)
        if error:
            errors.append(error)
            continue
        rows.append(row)
        if limit and len(rows) >= limit:
            break
    return rows, errors


def parse_zip_file(path, limit=None):
    """Walks every .csv/.json entry in the zip at `path`, parsing each
    with the matching parser above and concatenating rows/errors across
    all of them - Trakt's own "Export now" zip is a flat pile of ~90 JSON
    files (see module docstring), most of which aren't watch history at
    all (profile/settings/network/ratings/notes/lists/collection data) -
    only watched-history-*.json turned out, against a real export, to be
    per-play watch events; watched-movies-*.json/watched-shows.json are
    *aggregates* (a play count + last_watched_at, no per-play timestamps,
    so importing them can't reconstruct rewatch history anyway) that
    happen to have no top-level "title" the generic JSON shape looks for.

    A JSON entry that yields zero rows is treated the same way a CSV
    entry with no detected title column already was - skipped entirely,
    errors included - rather than reported, since a Trakt export's ~85
    non-history JSON files would otherwise dump thousands of "missing
    title"/"unparseable watched_at" errors into the preview for files
    that were never watch history to begin with (confirmed against a
    real export zip: every non-watched-history-*.json file produced 0
    rows and only errors, while every watched-history-*.json file
    produced only rows and 0 errors - a clean split). Row numbers in
    genuinely surfaced errors are prefixed with the entry's filename
    since they're no longer unique across the whole zip."""
    rows, errors = [], []
    with zipfile.ZipFile(path) as zf:
        for name in zf.namelist():
            if name.endswith("/"):
                continue
            lower = name.lower()
            if lower.endswith(".csv"):
                with zf.open(name) as f:
                    reader = open_csv_reader(f)
                    mapping = detect_mapping(reader.fieldnames or [])
                    if "title" not in mapping:
                        continue
                    file_rows, file_errors = parse_rows(reader, mapping)
            elif lower.endswith(".json"):
                with zf.open(name) as f:
                    try:
                        data = json.loads(f.read().decode("utf-8-sig"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        continue
                    file_rows, file_errors = parse_json_rows(data)
                if not file_rows:
                    continue
            else:
                continue
            rows.extend(file_rows)
            errors.extend((f"{name}:{n}", reason) for n, reason in file_errors)
            if limit and len(rows) >= limit:
                return rows[:limit], errors
    return rows, errors


def parse_file(path, kind, mapping=None, limit=None):
    """Dispatches to the right parser for kind ("csv"/"json"/"zip") -
    the one place that knows how to turn an uploaded file into
    commit_rows()-ready rows, shared between the request-time preview/
    small-file commit path (views.py's _parse_pending_import) and the
    background run_data_import task (tasks.py) used once a file is too
    large to safely commit inside one request - see
    LARGE_IMPORT_ROW_THRESHOLD in views.py."""
    if kind == "csv":
        with open(path, "rb") as f:
            reader = open_csv_reader(f)
            return parse_rows(reader, mapping or {}, limit=limit)
    if kind == "json":
        with open(path, "rb") as f:
            data = json.loads(f.read().decode("utf-8-sig"))
        return parse_json_rows(data, limit=limit)
    return parse_zip_file(path, limit=limit)


def _get_or_create_title(media_type, name, year, trakt_id=None, tmdb_id=None):
    """trakt_id/tmdb_id: present for rows sourced from Trakt-shaped
    JSON/zip imports - Trakt's own ids.trakt/ids.tmdb (see
    _row_from_trakt_item). tmdb_id is checked first (same
    external_ids__tmdb+tmdb_kind lookup discover_action_context uses to
    match Movies & TV/Anime grid items back to a local Title) and, when
    creating a brand new title, used directly instead of the fuzzy
    name/year find_match() search below - find_match can come up empty
    (a year mismatch between Trakt's and TMDB's own metadata returns
    nothing) or match the wrong TMDB entry, leaving the title untracked
    on the Discover grid even though it's already in History. A title
    found via trakt_id or the name/year fallback that's missing a tmdb
    link gets backfilled with this row's tmdb_id too, same resync-
    backfills-missing-data pattern nuvio.py's upsert_history_items uses
    for its own source marker. CSV rows and generic JSON rows carry
    neither id (see module docstring)."""
    kind = "movie" if media_type == MediaType.MOVIE else "tv"
    if tmdb_id:
        title = Title.objects.filter(external_ids__tmdb=str(tmdb_id), external_ids__tmdb_kind=kind).first()
        if title:
            if trakt_id and title.external_ids.get("trakt") != str(trakt_id):
                title.external_ids = {**title.external_ids, "trakt": str(trakt_id)}
                title.save(update_fields=["external_ids"])
            return title
    if trakt_id:
        # Not filtered by media_type - see the tmdb_id lookup above/
        # nuvio.py's own _get_or_create_title docstring for why a title
        # already reclassified from TV to ANIME must still match here.
        title = Title.objects.filter(external_ids__trakt=str(trakt_id)).first()
        if title:
            if tmdb_id and not title.external_ids.get("tmdb"):
                title.external_ids = {**title.external_ids, "tmdb": str(tmdb_id), "tmdb_kind": kind}
                title.save(update_fields=["external_ids"])
            return title
    title = Title.objects.filter(media_type=media_type, name__iexact=name, year=year or 0).first()
    if title:
        if tmdb_id and not title.external_ids.get("tmdb"):
            title.external_ids = {**title.external_ids, "tmdb": str(tmdb_id), "tmdb_kind": kind}
            title.save(update_fields=["external_ids"])
        return title
    from tracker.integrations import tmdb
    from tracker.models import attach_genres, attach_reports_metadata

    external_ids = {"trakt": str(trakt_id)} if trakt_id else {}
    poster_url = ""
    genre_names = []
    details = None
    resolved_kind, resolved_id = kind, tmdb_id
    if tmdb_id:
        external_ids["tmdb"] = str(tmdb_id)
        external_ids["tmdb_kind"] = kind
        details = tmdb.get_full_details(kind, tmdb_id)
        if details:
            poster_url = details["poster_url"] or ""
            genre_names = details["genres"]
    else:
        match = tmdb.find_match(media_type, name, year)
        if match:
            external_ids["tmdb"] = str(match["id"])
            external_ids["tmdb_kind"] = match["kind"]
            poster_url = match["poster_url"] or ""
            resolved_kind, resolved_id = match["kind"], match["id"]
            details = tmdb.get_full_details(match["kind"], match["id"])
            if details:
                genre_names = details["genres"]
    title = Title.objects.create(
        media_type=media_type, name=name, year=year or 0, external_ids=external_ids, poster_url=poster_url
    )
    attach_genres(title, genre_names)
    if details:
        attach_reports_metadata(title, tmdb.get_reports_metadata(resolved_kind, resolved_id, details))
    return title


def commit_rows(profile, rows, labels_out=None):
    """rows: parsed dicts from parse_rows()/parse_json_rows()/
    parse_zip_file(). Returns (imported_count, skipped) where skipped is
    [(row_number, reason), ...] for rows that passed parsing but were
    rejected at the database step.

    labels_out: same optional label-collecting list as trakt.py's own
    upsert_history_items - views.import_csv_commit/tasks.run_data_import
    save these (capped) onto the DataLog row so the Logs tab can show
    what was actually imported, not just a count."""
    from . import completion, recommendations, rewatches

    imported = 0
    skipped = []
    touched_movies = set()
    touched_shows = set()
    touched_watch_keys = set()
    for r in rows:
        if r["media_type"] != MediaType.MOVIE and (r["season"] is None or r["episode"] is None):
            skipped.append((r["row"], "TV/anime rows need a season and episode number"))
            continue

        title = _get_or_create_title(r["media_type"], r["title"], r["year"], r.get("trakt_id"), r.get("tmdb_id"))
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
        if labels_out is not None:
            labels_out.append(title.name if episode is None else f"{title.name} S{episode.season}E{episode.episode}")
        touched_watch_keys.add((title.id, episode.id if episode else None))

    for title_id, episode_id in touched_watch_keys:
        rewatches.recompute_is_rewatch(
            profile, Title.objects.get(id=title_id), Episode.objects.get(id=episode_id) if episode_id else None
        )

    for title in Title.objects.filter(id__in=touched_movies):
        completion.update_movie_runtime(title)
        completion.sync_watchlist_removal(profile, title)
        recommendations.mark_title_watched(profile, title)
    for title in Title.objects.filter(id__in=touched_shows):
        completion.sync_show_completion(profile, title)
        completion.sync_watchlist_removal(profile, title)
        recommendations.mark_title_watched(profile, title)

    return imported, skipped
