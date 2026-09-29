"""Universal Import Review pipeline.

The core rule this module exists to enforce: an initial/full import from
any source (Trakt/Simkl/Nuvio/CSV/JSON/ZIP) never writes anything into
canonical Spool state (Title/Episode/WatchEvent/WatchProgress/
WatchListItem/ratings) until the user has reviewed what would be
imported and explicitly confirmed it. Routine scheduled sync (the
existing per-provider tasks in tasks.py) is untouched by this module and
stays automatic.

Two phases, kept strictly separate:

- **scan** - read-only. Fetches/parses the source's raw data (file
  parsing lives here via scan_file_import; a provider's own network
  fetch - with credentials/token-refresh - stays owned by tasks.py,
  which then calls the source-agnostic scan_normalized_items with its
  own normalize_<source>_item()-shaped list), resolves title matches via
  tracker.title_matching.resolve_title_match (never apply_title_match -
  scan must not write), detects duplicates against existing WatchEvents,
  and bulk-creates ImportCandidate rows describing what commit *would*
  do. Never creates a Title/Episode/WatchEvent, never touches a rating/
  progress/watchlist, never triggers recommendation/completion/rewatch
  recalculation.
- **commit** (`commit_session`) - processes only status=PENDING,
  selected=True candidates, reusing tracker.title_matching.
  apply_title_match (the same matching rules scan used, so a candidate
  shown as "new" during review is guaranteed to actually create a new
  Title, never silently merge into one that scan didn't know about).
  Flips each candidate's own status as it's processed - the idempotency
  mechanism a retried/resumed commit relies on (see commit_session's own
  docstring). One implementation, shared by every source, since every
  source's candidates end up as the same ImportCandidate shape.

Wired for CSV/JSON/ZIP file imports (via tracker.csv_import's existing
parsers) and Trakt (via tracker.integrations.trakt.fetch_history +
normalize_history_item) - Simkl/Nuvio scan adapters land in a later
stage, reusing scan_normalized_items/commit_session rather than a
parallel implementation. tasks.scan_import_session is the single place
that dispatches a scan by session.source.
"""

import os

from django.utils import timezone

from . import csv_import, title_matching
from .models import Episode, ImportCandidate, ImportSession, MediaType, Title, WatchEvent

# Large real-world exports (a Trakt "Export now" zip) can carry 10k+
# rows - update the session's own progress counter periodically during a
# scan/commit rather than once at the very end, so the review page's
# polling status bar has something to show mid-run instead of sitting at
# 0 until the whole file is done.
PROGRESS_UPDATE_INTERVAL = 200
COMMIT_CHUNK_SIZE = 200

FILE_SOURCES = (ImportSession.Source.CSV, ImportSession.Source.JSON, ImportSession.Source.ZIP)
KNOWN_PROVIDERS = (ImportSession.Source.TRAKT, ImportSession.Source.SIMKL, ImportSession.Source.NUVIO)


def _external_id_provider(source_external_ids):
    """A candidate's source_external_ids can carry a native provider id
    even for a file import (a Trakt-shaped JSON/zip row - see
    normalize_file_rows) - whichever known provider key is actually
    present wins, so scan and commit both match/create against that
    provider's own external_ids key rather than a generic "csv" one
    whenever a real id is available. Falls back to ("csv", None) - no
    native id at all, matching commit's own long-standing fallback."""
    for provider in KNOWN_PROVIDERS:
        if source_external_ids.get(provider):
            return provider, source_external_ids[provider]
    return "csv", None


# Per-provider extra kwargs for title_matching.resolve_title_match/
# apply_title_match, mirroring what each provider's own _get_or_create_title
# wrapper (trakt.py/simkl.py/nuvio.py) already hardcodes for every call -
# a fixed per-provider choice, never a per-item one. Nuvio prefers TMDB's
# own resolved name/year over its own (often just a content_id-derived)
# hint, and discards a tmdb_id match entirely if TMDB has no details for
# it, rather than linking to an id it could never enrich - see
# title_matching.py's own module docstring. Trakt/Simkl/CSV use the
# defaults (both False).
PROVIDER_MATCH_OPTIONS = {ImportSession.Source.NUVIO: {"prefer_resolved_name": True, "require_details_for_tmdb_id": True}}


def _match_options(provider):
    return PROVIDER_MATCH_OPTIONS.get(provider, {})


def commit_session(session):
    """Dispatches to the commit implementation. Every source's
    candidates end up as plain ImportCandidate rows with the same
    shape, so - unlike scan - there is currently only one commit
    implementation, shared by every source once it's wired in."""
    return _commit_candidates(session)


# --------------------------------------------------------------------
# File imports (CSV/JSON/ZIP)
# --------------------------------------------------------------------


def normalize_file_rows(rows, parse_errors):
    """rows/parse_errors: csv_import.parse_file()'s own return shape.
    Returns one normalized dict per row PLUS one per parse error, so
    every line of the source file is represented somewhere in the
    review UI - a row that failed to parse shows up under the Error
    filter instead of silently vanishing."""
    normalized = []
    for row in rows:
        source_external_ids = {}
        if row.get("trakt_id"):
            source_external_ids["trakt"] = str(row["trakt_id"])
        if row.get("tmdb_id"):
            source_external_ids["tmdb"] = str(row["tmdb_id"])
        normalized.append(
            {
                "media_type": row["media_type"],
                "title_name": row["title"],
                "year": row["year"],
                "season": row["season"],
                "episode": row["episode"],
                "watched_at": row["watched_at"],
                "rating": row["rating"],
                "source_external_ids": source_external_ids,
                "source_row": row["row"],
                "error": None,
            }
        )
    for row_num, reason in parse_errors:
        normalized.append(
            {
                "media_type": None,
                "title_name": f"Row {row_num}",
                "year": None,
                "season": None,
                "episode": None,
                "watched_at": None,
                "rating": None,
                "source_external_ids": {},
                "source_row": row_num,
                "error": reason,
            }
        )
    return normalized


def scan_file_import(session):
    """Parses the uploaded file (path/mapping stashed on the session at
    creation - see views.import_csv_commit) and normalizes every row,
    then hands off to scan_normalized_items for the (source-agnostic)
    matching/candidate-building work shared with provider sources."""
    path = session.source_metadata.get("path")
    mapping = session.import_options.get("mapping")
    rows, parse_errors = csv_import.parse_file(path, session.source, mapping)
    scan_normalized_items(session, normalize_file_rows(rows, parse_errors))


def scan_normalized_items(session, normalized):
    """The source-agnostic half of scanning, shared by every source once
    it has its own normalize_<source>_item()-shaped list ready (see this
    module's own docstring for the common shape). Resolves title/
    duplicate matches in batches - one title-match lookup per unique
    (media_type, name, year, trakt_id, tmdb_id) key, one Episode/
    WatchEvent prefetch per distinct matched Title, not one query per
    item - before bulk-creating ImportCandidate rows. Never calls
    apply_title_match, get_or_create, or WatchEvent.objects.create -
    purely read-only against canonical data."""
    session.total_items = len(normalized)
    session.save(update_fields=["total_items"])

    match_cache = {}
    prepared = []  # [(item, TitleMatch|None, error|None), ...]
    for item in normalized:
        error = item["error"]
        if not error and item["media_type"] != MediaType.MOVIE and (item["season"] is None or item["episode"] is None):
            error = "TV/anime rows need a season and episode number"
        if error:
            prepared.append((item, None, error))
            continue

        provider, provider_id = _external_id_provider(item["source_external_ids"])
        tmdb_id = item["source_external_ids"].get("tmdb")
        imdb_id = item["source_external_ids"].get("imdb")
        key = (item["media_type"], item["title_name"].strip().lower(), item["year"], provider, provider_id, tmdb_id, imdb_id)
        if key not in match_cache:
            match_cache[key] = title_matching.resolve_title_match(
                item["media_type"], provider, provider_id, name=item["title_name"], year=item["year"],
                tmdb_id=tmdb_id, imdb_id=imdb_id, **_match_options(provider),
            )
        prepared.append((item, match_cache[key], None))

    matched_title_ids = {match.existing_title.id for _, match, _ in prepared if match and match.existing_title}
    episode_lookup = {}
    existing_events = set()
    if matched_title_ids:
        for ep_id, title_id, season, ep_num in Episode.objects.filter(title_id__in=matched_title_ids).values_list(
            "id", "title_id", "season", "episode"
        ):
            episode_lookup[(title_id, season, ep_num)] = ep_id
        for title_id, episode_id, watched_at in WatchEvent.objects.filter(
            profile=session.profile, title_id__in=matched_title_ids
        ).values_list("title_id", "episode_id", "watched_at"):
            existing_events.add((title_id, episode_id, watched_at))

    candidates = []
    for i, (item, match, error) in enumerate(prepared, start=1):
        candidates.append(_build_candidate(item, match, error, episode_lookup, existing_events))
        if i % PROGRESS_UPDATE_INTERVAL == 0:
            session.processed_items = i
            session.save(update_fields=["processed_items"])

    ImportCandidate.objects.bulk_create(
        [ImportCandidate(import_session=session, **fields) for fields in candidates], batch_size=500
    )
    _finalize_scan(session)


def _build_candidate(item, match, error, episode_lookup, existing_events):
    base = dict(
        category=ImportCandidate.Category.HISTORY,
        media_type=item["media_type"] or MediaType.MOVIE,
        title_name=item["title_name"],
        year=item["year"],
        season=item["season"],
        episode=item["episode"],
        watched_at=item["watched_at"],
        rating=item["rating"],
        source_external_ids=item["source_external_ids"],
        normalized_payload={"row": item["source_row"]},
    )
    if error:
        return {**base, "status": ImportCandidate.Status.FAILED, "selected": False, "error": error[:255]}

    resolved_external_ids = {}
    if match.tmdb_id:
        resolved_external_ids = {"tmdb": match.tmdb_id, "tmdb_kind": match.tmdb_kind}

    existing_title = match.existing_title
    action = ImportCandidate.Action.CREATE_EVENT
    warning = ""
    selected = True
    if existing_title:
        if item["media_type"] == MediaType.MOVIE:
            already = (existing_title.id, None, item["watched_at"]) in existing_events
        else:
            episode_id = episode_lookup.get((existing_title.id, item["season"], item["episode"]))
            already = episode_id is not None and (existing_title.id, episode_id, item["watched_at"]) in existing_events
        if already:
            action = ImportCandidate.Action.NOOP_DUPLICATE
            warning = "Already in your history — importing this would have no effect."
            selected = False

    return {
        **base,
        "resolved_external_ids": resolved_external_ids,
        "matched_title": existing_title,
        "action": action,
        "status": ImportCandidate.Status.PENDING,
        "selected": selected,
        "warning": warning,
    }


def _finalize_scan(session):
    """Computes the review page's own summary header counts and flips
    the session to READY. new/existing/duplicate/error are mutually
    exclusive buckets over every candidate; conflict is tracked
    separately (always 0 for file imports today - no source wired in
    yet produces conflict_type - kept in the summary shape so the
    review template doesn't need a source-specific branch once
    Trakt/Simkl/Nuvio start populating it)."""
    # A scan can take a while on a large file - if the user cancelled
    # the session while it was still running, don't resurrect it by
    # flipping it back to READY and leaving the candidates this scan
    # just bulk-created behind (cancel must mean "imports nothing").
    session.refresh_from_db(fields=["status"])
    if session.status == ImportSession.Status.CANCELLED:
        session.candidates.all().delete()
        return

    candidates = session.candidates
    total = candidates.count()
    error_count = candidates.filter(status=ImportCandidate.Status.FAILED).count()
    duplicate_count = candidates.filter(action=ImportCandidate.Action.NOOP_DUPLICATE).count()
    conflict_count = candidates.exclude(conflict_type="").count()
    new_count = candidates.filter(
        status=ImportCandidate.Status.PENDING, matched_title__isnull=True, conflict_type=""
    ).count()
    existing_count = total - error_count - duplicate_count - conflict_count - new_count

    session.summary = {
        "total": total,
        "new": new_count,
        "existing": existing_count,
        "duplicate": duplicate_count,
        "conflict": conflict_count,
        "error": error_count,
    }
    session.total_items = total
    session.selected_items = candidates.filter(selected=True).count()
    session.processed_items = total
    session.status = ImportSession.Status.READY
    session.save(
        update_fields=["summary", "total_items", "selected_items", "processed_items", "status"]
    )


# --------------------------------------------------------------------
# Commit (shared by every source)
# --------------------------------------------------------------------


class CommitResult:
    __slots__ = ("imported", "skipped", "failed", "touched_movies", "touched_shows", "touched_watch_keys")

    def __init__(self):
        self.imported = 0
        self.skipped = 0
        self.failed = 0
        self.touched_movies = set()
        self.touched_shows = set()
        self.touched_watch_keys = set()


def _commit_candidates(session):
    """Processes status=PENDING, selected=True candidates in chunks,
    flipping each row's own status as it's written - a retried/resumed
    commit (worker crash, task retry) only ever re-queries status=
    PENDING, so a candidate that already made it to IMPORTED/SKIPPED/
    FAILED is never processed twice, even across separate commit_session
    calls for the same session. Downstream effects (rewatch/completion/
    watchlist/recommendations) are batched once per distinct Title
    touched across the whole commit, not once per candidate - same
    pattern csv_import.commit_rows/trakt.py/simkl.py/nuvio.py already
    use for their own routine-sync commits."""
    session.status = ImportSession.Status.IMPORTING
    if not session.started_at:
        session.started_at = timezone.now()
    session.save(update_fields=["status", "started_at"])

    result = CommitResult()
    pending = session.candidates.filter(status=ImportCandidate.Status.PENDING, selected=True).order_by("id")
    processed = 0
    while True:
        chunk = list(pending[:COMMIT_CHUNK_SIZE])
        if not chunk:
            break
        for candidate in chunk:
            _commit_one(session, candidate, result)
            processed += 1
        # One save per chunk (COMMIT_CHUNK_SIZE rows) is enough to keep
        # the review page's polling status bar moving on a large import
        # without a save-per-row cost.
        session.processed_items = processed
        session.save(update_fields=["processed_items"])

    _sync_downstream(session.profile, result)

    # Cumulative totals across every run of this session's commit, not
    # just this call's own result - a resumed commit (worker crash mid-
    # way, a retried task) only processes the candidates still PENDING,
    # so result only reflects what THIS run touched. Recounting from the
    # candidates themselves is what makes the final imported/skipped/
    # failed counts correct after a resume instead of clobbering an
    # earlier run's counts with a partial number.
    session.imported_items = session.candidates.filter(status=ImportCandidate.Status.IMPORTED).count()
    session.skipped_items = session.candidates.filter(status=ImportCandidate.Status.SKIPPED).count()
    # selected=True narrows this to candidates commit actually attempted
    # (and failed) - a candidate FAILED at scan time (bad season/episode,
    # an unparseable row) is always selected=False and was never queued
    # for commit at all, so it must not be counted as a commit failure.
    session.failed_items = session.candidates.filter(status=ImportCandidate.Status.FAILED, selected=True).count()
    session.processed_items = session.total_items
    session.status = ImportSession.Status.COMPLETED
    session.completed_at = timezone.now()
    session.save(
        update_fields=[
            "imported_items", "skipped_items", "failed_items", "processed_items", "status", "completed_at",
        ]
    )
    return result


# WatchEvent's own Source badge (History's "came from an external app"
# marker - see WatchEvent.Source's own docstring) for a candidate
# created by a provider-sourced ImportSession. File imports (csv/json/
# zip) leave it blank, same as a direct CSV commit always has - even a
# Trakt-shaped JSON/zip row's own commit provider is resolved from
# source_external_ids (see _external_id_provider), independent of this.
WATCH_EVENT_SOURCE_BY_PROVIDER = {
    ImportSession.Source.TRAKT: WatchEvent.Source.TRAKT,
    ImportSession.Source.SIMKL: WatchEvent.Source.SIMKL,
    ImportSession.Source.NUVIO: WatchEvent.Source.NUVIO,
}


def _commit_one(session, candidate, result):
    profile = session.profile
    if candidate.category != ImportCandidate.Category.HISTORY:
        # Not reachable yet (every wired-in source so far only produces
        # HISTORY candidates) - kept as an explicit, safe no-op rather
        # than an assertion so a future source/category that reuses
        # this same commit loop before it's finished wiring can't
        # silently mis-write canonical data.
        candidate.status = ImportCandidate.Status.SKIPPED
        candidate.save(update_fields=["status"])
        result.skipped += 1
        return

    try:
        provider, provider_id = _external_id_provider(candidate.source_external_ids)
        title = title_matching.apply_title_match(
            candidate.media_type,
            provider,
            provider_id,
            name=candidate.title_name,
            year=candidate.year,
            tmdb_id=candidate.source_external_ids.get("tmdb"),
            imdb_id=candidate.source_external_ids.get("imdb"),
            **_match_options(provider),
        )
        episode = None
        is_show = candidate.media_type != MediaType.MOVIE
        if is_show:
            episode, _ = Episode.objects.get_or_create(title=title, season=candidate.season, episode=candidate.episode)
    except Exception as e:
        candidate.status = ImportCandidate.Status.FAILED
        candidate.error = str(e)[:255]
        candidate.save(update_fields=["status", "error"])
        result.failed += 1
        return

    (result.touched_shows if is_show else result.touched_movies).add(title.id)

    already = WatchEvent.objects.filter(
        profile=profile, title=title, episode=episode, watched_at=candidate.watched_at
    ).exists()
    if already:
        candidate.status = ImportCandidate.Status.SKIPPED
        candidate.matched_title = title
        candidate.save(update_fields=["status", "matched_title"])
        result.skipped += 1
        return

    WatchEvent.objects.create(
        profile=profile, title=title, episode=episode, watched_at=candidate.watched_at, user_rating=candidate.rating,
        source=WATCH_EVENT_SOURCE_BY_PROVIDER.get(session.source, ""),
    )
    candidate.status = ImportCandidate.Status.IMPORTED
    candidate.matched_title = title
    candidate.save(update_fields=["status", "matched_title"])
    result.imported += 1
    result.touched_watch_keys.add((title.id, episode.id if episode else None))


def _sync_downstream(profile, result):
    """Exactly the batched downstream-effects pass csv_import.
    commit_rows already ran for a direct CSV commit - moved here
    unchanged so Import Review's commit path preserves the same
    rewatch/completion/recommendation/watchlist behavior."""
    from . import completion, recommendations, rewatches

    for title_id, episode_id in result.touched_watch_keys:
        rewatches.recompute_is_rewatch(
            profile, Title.objects.get(id=title_id), Episode.objects.get(id=episode_id) if episode_id else None
        )
    for title in Title.objects.filter(id__in=result.touched_movies):
        completion.update_movie_runtime(title)
        completion.sync_watchlist_removal(profile, title)
        recommendations.mark_title_watched(profile, title)
    for title in Title.objects.filter(id__in=result.touched_shows):
        completion.sync_show_completion(profile, title)
        completion.sync_watchlist_removal(profile, title)
        recommendations.mark_title_watched(profile, title)


# --------------------------------------------------------------------
# Cleanup
# --------------------------------------------------------------------


def discard_session(session):
    """Deletes a session's own staging data (ImportCandidate rows +
    backing temp file, for a file import) without ever touching
    canonical Title/Episode/WatchEvent data - used by both explicit
    cancel (import_review_cancel) and the nightly expiry sweep
    (tasks.expire_import_sessions). Safe to call more than once."""
    path = session.source_metadata.get("path")
    if path:
        try:
            os.remove(path)
        except OSError:
            pass
    session.candidates.all().delete()
