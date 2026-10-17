import os
from datetime import date, timedelta
from io import StringIO

import requests
from celery import shared_task
from celery.utils.log import get_task_logger
from django.conf import settings as django_settings
from django.core.management import call_command
from django.utils import timezone

from . import (
    completion,
    csv_import,
    import_pipeline,
    instance_config,
    notifications,
    release_sync,
    selectors,
    update_check,
    version,
)
from .integrations import mdblist, nuvio, simkl, tmdb, trakt, wetrakr
from .models import (
    DataLog,
    ExternalAccount,
    IMPORTED_TITLES_LOG_CAP,
    ImportSession,
    InstanceConfig,
    Notification,
    NuvioConnection,
    Profile,
    SyncLog,
    Title,
    TitleRatingsCache,
)

logger = get_task_logger(__name__)


def _refresh_account_token(account, provider_module, client_id, client_secret):
    """Attempts one token refresh via the stored refresh_token, saving the
    new tokens on success. Returns False (does nothing) rather than
    raising when refresh isn't possible - no refresh_token stored, or no
    redirect_uri captured (accounts connected before that field existed,
    see models.py) - so the caller falls through to the original 401."""
    refresh_token = account.get_refresh_token()
    if not refresh_token or not account.redirect_uri:
        return False
    token_data = provider_module.refresh_access_token(
        refresh_token, client_id, client_secret, account.redirect_uri
    )
    expires_in = token_data.get("expires_in")
    account.set_access_token(token_data.get("access_token", ""))
    account.set_refresh_token(token_data.get("refresh_token") or refresh_token)
    account.token_expires_at = timezone.now() + timedelta(seconds=expires_in) if expires_in else None
    account.save(update_fields=["encrypted_access_token", "encrypted_refresh_token", "token_expires_at"])
    return True


def _call_with_refresh(account, provider_module, client_id, client_secret, call):
    """Runs call() once; on a 401 (expired/revoked access token), refreshes
    the token and retries call() exactly once more. Any other error, or a
    401 that can't be refreshed away, propagates as-is."""
    try:
        return call()
    except requests.HTTPError as e:
        if e.response is None or e.response.status_code != 401:
            raise
        if not _refresh_account_token(account, provider_module, client_id, client_secret):
            raise
        return call()


def _run_sync(profile, provider, fetch_and_upsert):
    """Wraps a sync call with a SyncLog row - records when it ran,
    whether it succeeded/failed, and (imported_titles, capped at
    models.IMPORTED_TITLES_LOG_CAP) a sample of what was actually
    imported for the Logs tab's collapsible detail view. fetch_and_upsert
    returns (created_count, labels) - a plain int wouldn't tell this
    function what to log beyond the count, and a shared closure-mutated
    list would risk double-counting across _call_with_refresh's retry-
    on-401 (this way each attempt builds its own fresh list). Re-raises
    on failure after recording it, so the sync still shows up as failed
    in Celery's own tracking/logs too, not just silently swallowed."""
    log = SyncLog.objects.create(profile=profile, provider=provider)
    try:
        created, labels = fetch_and_upsert()
    except wetrakr.WeTrakrQuotaError as e:
        # Not a failure - WeTrakr's daily quota was reached mid-sync.
        # PAUSED instead of FAILED so Settings -> Logs doesn't alarm the
        # user over something that resolves itself automatically, and no
        # notify_sync_failure bell ping - a quiet background daily-sync
        # deferral shouldn't page anyone (see sync_wetrakr_history's own
        # docstring for why last_synced_at also isn't advanced for this
        # case). Still re-raised so the caller's own post-_run_sync code
        # (which would otherwise unconditionally advance last_synced_at)
        # doesn't run.
        log.status = SyncLog.Status.PAUSED
        log.error_message = str(e)[:500]
        log.finished_at = timezone.now()
        log.save(update_fields=["status", "error_message", "finished_at"])
        raise
    except Exception as e:
        log.status = SyncLog.Status.FAILED
        log.error_message = str(e)[:500]
        log.finished_at = timezone.now()
        log.save(update_fields=["status", "error_message", "finished_at"])
        notifications.notify_sync_failure(profile, provider, log.error_message)
        raise
    log.status = SyncLog.Status.SUCCESS
    log.item_count = created
    log.imported_titles = labels[:IMPORTED_TITLES_LOG_CAP]
    log.finished_at = timezone.now()
    log.save(update_fields=["status", "item_count", "imported_titles", "finished_at"])
    return created


@shared_task
def sync_trakt_history(profile_id):
    try:
        account = ExternalAccount.objects.select_related("profile").get(
            profile_id=profile_id, provider=ExternalAccount.Provider.TRAKT
        )
    except ExternalAccount.DoesNotExist:
        logger.info("sync_trakt_history: profile %s has no Trakt account connected", profile_id)
        return 0

    # Captured before the fetch, not after - so anything Trakt records
    # between now and this task finishing is still >= this value and gets
    # picked up by the *next* sync rather than falling in a gap.
    sync_start = timezone.now()

    client_id, client_secret = instance_config.get_trakt_credentials()

    def fetch_and_upsert():
        labels = []
        items = trakt.fetch_history(account.get_access_token(), client_id, start_at=account.last_synced_at)
        created = trakt.upsert_history_items(account.profile, items, labels_out=labels)
        if account.import_lists:
            lists_data = trakt.fetch_lists(account.get_access_token(), client_id)
            created += trakt.upsert_lists(account.profile, lists_data, labels_out=labels)
        return created, labels

    def do_sync():
        return _call_with_refresh(account, trakt, client_id, client_secret, fetch_and_upsert)

    created = _run_sync(account.profile, ExternalAccount.Provider.TRAKT, do_sync)
    account.last_synced_at = sync_start
    account.save(update_fields=["last_synced_at"])
    logger.info("sync_trakt_history: profile %s, %d new watch events", profile_id, created)
    return created


@shared_task
def sync_simkl_history(profile_id):
    try:
        account = ExternalAccount.objects.select_related("profile").get(
            profile_id=profile_id, provider=ExternalAccount.Provider.SIMKL
        )
    except ExternalAccount.DoesNotExist:
        logger.info("sync_simkl_history: profile %s has no Simkl account connected", profile_id)
        return 0

    client_id, client_secret = instance_config.get_simkl_credentials()

    def fetch_and_upsert():
        labels = []
        items = simkl.fetch_history(account.get_access_token(), client_id)
        created = simkl.upsert_history_items(account.profile, items, labels_out=labels)
        return created, labels

    def do_sync():
        return _call_with_refresh(account, simkl, client_id, client_secret, fetch_and_upsert)

    created = _run_sync(account.profile, ExternalAccount.Provider.SIMKL, do_sync)
    logger.info("sync_simkl_history: profile %s, %d new watch events", profile_id, created)
    return created


@shared_task
def sync_nuvio_history(profile_id):
    """One Celery invocation per profile, same shape as sync_trakt_history/
    sync_simkl_history above - a task instance never iterates other
    profiles' NuvioConnections, so one profile's sync can't read or write
    another's rows. _run_sync already records/re-raises any failure onto
    this profile's own SyncLog row without affecting any other profile's
    already-independent task run."""
    try:
        connection = NuvioConnection.objects.select_related("profile").get(profile_id=profile_id, sync_enabled=True)
    except NuvioConnection.DoesNotExist:
        logger.info("sync_nuvio_history: profile %s has no active Nuvio connection", profile_id)
        return 0

    def fetch_and_upsert():
        session = nuvio.refresh_access_token(connection.get_refresh_token())
        # Supabase rotates the refresh token on every use (per scrob) -
        # saved before touching anything else so a later failure in the
        # fetch/upsert calls below doesn't strand the connection on a
        # now-invalid refresh token.
        connection.set_refresh_token(session["refresh_token"])
        connection.save(update_fields=["encrypted_refresh_token"])

        watched = nuvio.fetch_watched_items(session["access_token"], connection.nuvio_profile_id)
        progress = nuvio.fetch_watch_progress(session["access_token"], connection.nuvio_profile_id)
        labels = []
        created = nuvio.upsert_history_items(connection.profile, watched, labels_out=labels)
        nuvio.upsert_progress_items(connection.profile, progress)
        return created, labels

    created = _run_sync(connection.profile, ExternalAccount.Provider.NUVIO, fetch_and_upsert)
    connection.last_synced_at = timezone.now()
    connection.save(update_fields=["last_synced_at"])
    logger.info("sync_nuvio_history: profile %s, %d new watch events", profile_id, created)
    return created


# Hard safety bound on sync_wetrakr_history's own in-memory page loop,
# same reasoning as trakt.fetch_history's max_pages - two targets x 500
# pages x 200 items is far beyond any real incremental delta.
_WETRAKR_SYNC_MAX_PAGES = 500


@shared_task
def sync_wetrakr_history(profile_id):
    """Routine incremental sync, same shape as sync_trakt_history above -
    captures sync_start before fetching, loops _fetch_wetrakr_page_quota_checked
    page by page (same budget-checked-before-every-request helper
    _scan_wetrakr_session uses) with start_at to only pull what's new. On
    a WeTrakrQuotaError mid-fetch, deliberately does NOT advance
    last_synced_at (the except branch below returns before reaching that
    save) - the account's own already-scheduled PeriodicTask (see
    scheduling.ensure_periodic_task) just retries the same incremental
    delta on its next normal run, with no new cursor/resume machinery
    needed for this path. A routine sync's delta is normally small for
    an already-synced account, so this simple "just don't advance the
    watermark" approach is enough - contrast with _scan_wetrakr_session
    below, which needs real page-level resumability because a first-time
    full history pull can be thousands of items."""
    try:
        account = ExternalAccount.objects.select_related("profile").get(
            profile_id=profile_id, provider=ExternalAccount.Provider.WETRAKR
        )
    except ExternalAccount.DoesNotExist:
        logger.info("sync_wetrakr_history: profile %s has no WeTrakr account connected", profile_id)
        return 0

    sync_start = timezone.now()
    client_id, client_secret = instance_config.get_wetrakr_credentials()

    def fetch_and_upsert():
        labels = []
        items = []
        cursor = None
        pages_fetched = 0
        while pages_fetched < _WETRAKR_SYNC_MAX_PAGES:
            page_items, cursor = _fetch_wetrakr_page_quota_checked(
                account, client_id, client_secret, cursor, start_at=account.last_synced_at,
            )
            items.extend(page_items)
            pages_fetched += 1
            if cursor is None:
                break
        created = wetrakr.upsert_history_items(account.profile, items, labels_out=labels)
        return created, labels

    try:
        created = _run_sync(account.profile, ExternalAccount.Provider.WETRAKR, fetch_and_upsert)
    except wetrakr.WeTrakrQuotaError:
        logger.info("sync_wetrakr_history: profile %s paused on quota, last_synced_at unchanged", profile_id)
        return 0
    account.last_synced_at = sync_start
    account.save(update_fields=["last_synced_at"])
    logger.info("sync_wetrakr_history: profile %s, %d new watch events", profile_id, created)
    return created


def _scan_trakt_session(session):
    """Fetches this profile's full Trakt history (same trakt.fetch_history
    call routine sync uses, including its own 401-triggers-a-refresh
    handling via _call_with_refresh) and hands the normalized items to
    import_pipeline's source-agnostic scan_normalized_items. Only the
    fetch/credentials/token-refresh part is Trakt-specific - matching,
    duplicate detection, and ImportCandidate creation are the exact same
    code path CSV/JSON/ZIP scanning already uses."""
    account = ExternalAccount.objects.select_related("profile").get(
        profile=session.profile, provider=ExternalAccount.Provider.TRAKT
    )
    client_id, client_secret = instance_config.get_trakt_credentials()
    items = _call_with_refresh(
        account, trakt, client_id, client_secret,
        lambda: trakt.fetch_history(account.get_access_token(), client_id),
    )
    normalized = [trakt.normalize_history_item(item, i) for i, item in enumerate(items, start=1)]
    import_pipeline.scan_normalized_items(session, normalized)


def _scan_simkl_session(session):
    """Same shape as _scan_trakt_session above, for Simkl."""
    account = ExternalAccount.objects.select_related("profile").get(
        profile=session.profile, provider=ExternalAccount.Provider.SIMKL
    )
    client_id, client_secret = instance_config.get_simkl_credentials()
    items = _call_with_refresh(
        account, simkl, client_id, client_secret,
        lambda: simkl.fetch_history(account.get_access_token(), client_id),
    )
    normalized = [simkl.normalize_history_item(item, i) for i, item in enumerate(items, start=1)]
    import_pipeline.scan_normalized_items(session, normalized)


def _scan_nuvio_session(session):
    """Nuvio has no access-token persistence at all (see NuvioConnection's
    own docstring) - every scan/sync starts with a fresh refresh_token
    exchange, and Supabase rotates the refresh token on every use, so the
    new one is saved immediately, before any fetch that could still fail.
    Only watch history goes through Import Review so far - continue-
    watching progress (fetch_watch_progress/upsert_progress_items) stays
    routine-sync-only for now, same as Trakt's own list/watchlist import
    (account.import_lists) hasn't been wired into review yet either; it
    starts showing up via the normal daily sync the day after a first
    reviewed history import, not gated behind this scan at all."""
    connection = NuvioConnection.objects.select_related("profile").get(profile=session.profile)
    auth = nuvio.refresh_access_token(connection.get_refresh_token())
    connection.set_refresh_token(auth["refresh_token"])
    connection.save(update_fields=["encrypted_refresh_token"])

    items = nuvio.fetch_watched_items(auth["access_token"], connection.nuvio_profile_id)
    normalized = [nuvio.normalize_history_item(item, i) for i, item in enumerate(items, start=1)]
    import_pipeline.scan_normalized_items(session, normalized)


def _scan_wetrakr_session(session):
    """Resumable scan - unlike _scan_trakt_session/_scan_simkl_session's
    "fetch everything in one shot" shape, a WeTrakr full history pull can
    outlast one day's request quota (see tracker/integrations/wetrakr.py's
    own module docstring, point 2), so this checkpoints between pages
    instead of fetching everything in one task invocation.

    On entry, resumes from session.source_metadata["wetrakr_cursor"]/
    ["wetrakr_raw_items"] if a previous run already paused mid-pull;
    otherwise starts fresh from page 1. Every page fetched gets appended
    to the accumulated raw-item list and the cursor saved immediately, so
    a crash or a quota pause between any two pages loses nothing already
    fetched (the accumulated list re-saving in full on every page is a
    real but accepted cost - see the plan this shipped from - bounded by
    page count, not item count, so even a very large history is at most
    a few hundred saves, not thousands).

    Only once the very last page is reached (fetch_history_page returns
    next_cursor=None) does this normalize the full accumulated list and
    hand off to the shared scan_normalized_items - exactly like every
    other source's scan function. Nothing downstream of that call (the
    actual matching/candidate-building/commit pipeline) ever needs to
    know a multi-day pause happened in between.

    The plan (free/vip/patron) is force-refreshed from GET /plan-usage
    once, only on a fresh (non-resumed) scan - see _check_wetrakr_plan's
    own docstring for why a resume reuses the already-cached reading
    instead of re-checking it on every resumed page."""
    account = ExternalAccount.objects.select_related("profile").get(
        profile=session.profile, provider=ExternalAccount.Provider.WETRAKR
    )
    client_id, client_secret = instance_config.get_wetrakr_credentials()

    resuming = "wetrakr_cursor" in session.source_metadata or "wetrakr_raw_items" in session.source_metadata
    cursor = session.source_metadata.get("wetrakr_cursor")
    raw_items = session.source_metadata.get("wetrakr_raw_items", [])

    _call_with_refresh(
        account, wetrakr, client_id, client_secret,
        lambda: _check_wetrakr_plan(account, client_id, force=not resuming),
    )

    while True:
        try:
            page_items, next_cursor = _fetch_wetrakr_page_quota_checked(account, client_id, client_secret, cursor)
        except wetrakr.WeTrakrQuotaError as e:
            if e.error_code == "APP_QUOTA_EXCEEDED":
                _saturate_wetrakr_app_quota(InstanceConfig.load())
            session.source_metadata["wetrakr_cursor"] = cursor
            session.source_metadata["wetrakr_raw_items"] = raw_items
            session.source_metadata["wetrakr_pause"] = {
                "reason": e.error_code, "paused_at": timezone.now().isoformat(),
            }
            session.status = ImportSession.Status.PAUSED
            session.processed_items = len(raw_items)
            # A slow multi-day pull shouldn't be swept by expire_import_sessions
            # just because it's outlived the original 7-day IMPORT_SESSION_EXPIRY
            # (views.py) - each pause extends the runway by a fresh grace
            # window rather than disabling expiry altogether, so a session a
            # bug leaves stuck PAUSED forever still eventually gets cleaned up.
            grace = timezone.now() + timedelta(days=3)
            if session.expires_at is None or session.expires_at < grace:
                session.expires_at = grace
            session.save(update_fields=["source_metadata", "status", "processed_items", "expires_at"])
            logger.info(
                "_scan_wetrakr_session: session %s paused (%s), %d items so far",
                session.id, e.error_code, len(raw_items),
            )
            return

        raw_items.extend(page_items)
        cursor = next_cursor
        session.source_metadata["wetrakr_raw_items"] = raw_items
        session.source_metadata["wetrakr_cursor"] = cursor
        session.processed_items = len(raw_items)
        session.save(update_fields=["source_metadata", "processed_items"])
        if cursor is None:
            break

    session.source_metadata.pop("wetrakr_pause", None)
    normalized = [wetrakr.normalize_history_item(item, i) for i, item in enumerate(raw_items, start=1)]
    import_pipeline.scan_normalized_items(session, normalized)


# session.source values with a scan implementation wired in - dispatched
# by scan_import_session below.
SCAN_HANDLERS = {
    ImportSession.Source.TRAKT: _scan_trakt_session,
    ImportSession.Source.SIMKL: _scan_simkl_session,
    ImportSession.Source.NUVIO: _scan_nuvio_session,
    ImportSession.Source.WETRAKR: _scan_wetrakr_session,
}


@shared_task
def scan_import_session(session_id):
    """Import Review's scan step - see import_pipeline.py's own
    docstring for why this never writes to canonical Spool state, and
    for why file sources (scan_file_import) and provider sources (this
    task fetching, then import_pipeline.scan_normalized_items) split the
    fetch/parse step but share everything after it. Dispatched right
    after the session row is created (status=SCANNING) - by views.
    import_csv_commit for a file, or oauth_callback for a first Trakt
    connection; the review page polls until this flips the session to
    READY (or FAILED). Always removes a file import's own backing temp
    file once it's been fully read (kept if scanning raises before
    parsing even starts, so a manual retry could still find it) - a
    no-op for a provider source, which has no temp file at all."""
    session = ImportSession.objects.select_related("profile").get(id=session_id)
    try:
        if session.source in import_pipeline.FILE_SOURCES:
            import_pipeline.scan_file_import(session)
        elif session.source in SCAN_HANDLERS:
            SCAN_HANDLERS[session.source](session)
        else:
            raise NotImplementedError(f"Import scan not yet implemented for source={session.source!r}")
    except Exception as e:
        logger.exception("scan_import_session: session %s failed", session_id)
        session.status = ImportSession.Status.FAILED
        session.error_message = str(e)[:500]
        session.save(update_fields=["status", "error_message"])
        raise
    finally:
        path = session.source_metadata.get("path")
        if path:
            try:
                os.remove(path)
            except OSError:
                pass
    logger.info("scan_import_session: session %s ready, %d candidates", session_id, session.total_items)


@shared_task
def commit_import_session(session_id):
    """Import Review's commit step - writes only status=PENDING,
    selected=True candidates into canonical Spool state (see
    import_pipeline.commit_session's own docstring for the idempotency
    guarantee that makes a retry of this task safe). Logs a DataLog row
    on completion so the import shows up in Settings → Logs the same
    way a direct CSV commit always has."""
    session = ImportSession.objects.select_related("profile").get(id=session_id)
    try:
        import_pipeline.commit_session(session)
    except Exception as e:
        logger.exception("commit_import_session: session %s failed", session_id)
        # The FAILED save is logged (Logs + the user's notification) by
        # transfer_tracking.sync_import_session via ImportSession's post_save.
        session.status = ImportSession.Status.FAILED
        session.error_message = str(e)[:500]
        session.save(update_fields=["status", "error_message"])
        raise
    # The DataLog row and the user's notification for this import were
    # updated by the COMPLETED save itself (transfer_tracking.py).
    logger.info(
        "commit_import_session: session %s, %d imported, %d skipped, %d failed",
        session_id, session.imported_items, session.skipped_items, session.failed_items,
    )


@shared_task
def expire_import_sessions():
    """Nightly beat job (see bootstrap_periodic_tasks.py) - marks any
    SCANNING/READY/PAUSED ImportSession past its own expires_at as
    EXPIRED and discards its staging data (ImportCandidate rows + a file
    import's own temp file - see import_pipeline.discard_session), so a
    review nobody ever came back to doesn't accumulate forever. PAUSED is
    included so a WeTrakr pull stuck paused by a bug (rather than
    resuming normally via resume_paused_wetrakr_imports) still eventually
    gets cleaned up - _scan_wetrakr_session extends expires_at by a fresh
    grace window on every real pause, so a pull that's still actively
    progressing day to day never hits this, only one that's truly stuck.
    Never touches an IMPORTING/COMPLETED/CANCELLED/FAILED session
    (already finished, one way or another) or any canonical Title/
    Episode/WatchEvent data - only this session's own staging rows. Same
    idempotent-per-row pattern as prune_old_logs: safe to run on a
    session more than once, safe to re-run after a crash mid-way."""
    stale = ImportSession.objects.filter(
        status__in=(ImportSession.Status.SCANNING, ImportSession.Status.READY, ImportSession.Status.PAUSED),
        expires_at__lt=timezone.now(),
    )
    count = 0
    for session in stale:
        session.status = ImportSession.Status.EXPIRED
        session.completed_at = timezone.now()
        session.save(update_fields=["status", "completed_at"])
        import_pipeline.discard_session(session)
        count += 1
    logger.info("expire_import_sessions: expired %d stale session(s)", count)
    return count


@shared_task
def resume_paused_wetrakr_imports():
    """Runs at 00:05 UTC (see scheduling.ensure_wetrakr_resume_task) -
    just after WeTrakr's daily quota resets at UTC midnight. Finds every
    ImportSession a WeTrakr pull left PAUSED and re-dispatches
    scan_import_session for each; _scan_wetrakr_session itself detects
    the resume (its cursor/accumulated items are already in
    source_metadata) and picks up exactly where it left off. Dispatched
    directly via .delay() rather than views._dispatch_sync_task_safely's
    bounded-wait-thread helper - that one exists specifically to bound a
    request/response cycle's wait on a broker call, which doesn't apply
    here (this already runs inside a Celery worker). Same unbounded-
    sweep shape as queue_due_mdblist_refreshes, minus that one's [:200]
    cap - a single self-hosted instance isn't going to have hundreds of
    paused WeTrakr sessions at once."""
    paused = ImportSession.objects.filter(
        source=ImportSession.Source.WETRAKR, status=ImportSession.Status.PAUSED,
    )
    count = 0
    for session in paused:
        scan_import_session.delay(session.id)
        count += 1
    logger.info("resume_paused_wetrakr_imports: resumed %d paused session(s)", count)
    return count


@shared_task
def sync_title_release(title_id):
    """Per-title release sync, fanned out by sync_release_schedules
    (below) instead of being called in a loop there - each call does one
    blocking TMDB request (see release_sync.sync_title_releases), so
    fanning out lets Celery's own worker concurrency
    (CELERY_WORKER_CONCURRENCY) process a household's titles in
    parallel instead of serializing every one of them inside a single
    task. Silently no-ops if the title's gone by the time this runs
    (e.g. a rare merge/dedupe race) rather than erroring the task.

    Also re-validates completion (completion.resync_completed_profiles)
    for this exact title - see that function's own docstring for why:
    sync_show_completion only runs on a fresh watch action, so a show
    marked COMPLETED earlier stays marked that way even once TMDB
    reports more episodes later, with nothing to catch that until this
    same nightly pass. titles_needing_release_sync already includes
    every title with any WatchProgress row (COMPLETED included), so
    this doesn't widen what gets touched on a given run, just what
    happens once it's here."""
    try:
        title = Title.objects.get(pk=title_id)
    except Title.DoesNotExist:
        return 0
    touched = release_sync.sync_title_releases(title)
    completion.resync_completed_profiles(title)
    return touched


def _log_system_datalog(action, item_count, imported_titles=None, detail=""):
    """Shared tail of every nightly beat job below that logs its own run
    to DataLog (see _system_profile's own docstring for the "no acting
    profile" attribution) - same shape reclassify_anime_titles already
    uses, factored out once a second/third/fourth/fifth task needed the
    exact same few lines. A no-op (not an error) when there's no owner
    profile yet to attach the row to."""
    profile = _system_profile()
    if profile is None:
        return
    DataLog.objects.create(
        profile=profile,
        action=action,
        status=DataLog.Status.SUCCESS,
        item_count=item_count,
        detail=detail,
        imported_titles=(imported_titles or [])[:IMPORTED_TITLES_LOG_CAP],
    )


@shared_task
def sync_release_schedules():
    """Nightly beat job (see bootstrap_periodic_tasks.py) - queues a
    sync_title_release task for every title anyone in the household is
    watching, has planned/completed, or has on a watchlist, so a renewed
    show or a now-dated movie shows up on Calendar without the user
    re-visiting its detail page. Only dispatches here rather than syncing
    in a loop - a large library making N blocking TMDB calls serially in
    one task could run long enough to threaten the 30-minute gap before
    generate_release_notifications. Instance-wide, not per-account - no
    SyncLog row (that model is Trakt/Simkl-account-shaped), logged to
    DataLog instead (see _log_system_datalog) so Settings > Logs shows
    what got queued, not just the usual task logger."""
    titles = list(selectors.titles_needing_release_sync().values_list("id", "name"))
    for title_id, _name in titles:
        sync_title_release.delay(title_id)
    logger.info("sync_release_schedules: queued %d title(s) for release sync", len(titles))
    _log_system_datalog(DataLog.Action.RELEASE_SYNC, len(titles), [name for _id, name in titles])
    return len(titles)


@shared_task
def generate_release_notifications():
    """Nightly beat job (see bootstrap_periodic_tasks.py), scheduled right
    after sync_release_schedules so a freshly-synced release is already
    in ReleaseSchedule by the time this scans it - see
    tracker/notifications.py for the actual eligibility/dedupe logic."""
    labels = []
    created = notifications.generate_release_notifications(labels_out=labels)
    logger.info("generate_release_notifications: %d notification(s) created", created)
    _log_system_datalog(DataLog.Action.RELEASE_NOTIFICATIONS, created, labels)
    return created


@shared_task
def generate_watchlist_stale_notifications():
    """Nightly beat job (see bootstrap_periodic_tasks.py) - see
    tracker/notifications.py for the actual age/cooldown logic."""
    labels = []
    created = notifications.generate_watchlist_stale_notifications(labels_out=labels)
    logger.info("generate_watchlist_stale_notifications: %d notification(s) created", created)
    _log_system_datalog(DataLog.Action.WATCHLIST_STALE, created, labels)
    return created


@shared_task
def check_for_new_version():
    """Nightly beat job (see bootstrap_periodic_tasks.py) - see
    tracker/update_check.py for the actual fetch/compare logic. Only
    owner profiles get notified (Profile.is_owner / user.is_superuser) -
    a household member has no way to actually perform an upgrade, so
    telling them about one is just noise they can't act on. Notification
    dedupes on its own message text (which embeds the version), so
    re-running this every night while the same newer version remains
    unactioned doesn't spam a fresh row each time."""
    latest = update_check.refresh_latest_version()
    if latest is None:
        _log_system_datalog(DataLog.Action.UPDATE_CHECK, 0, detail=f"Already on the latest (v{version.APP_VERSION}).")
        return 0
    message = f"Spool v{latest} is available (you're on v{version.APP_VERSION})."
    created = 0
    for profile in Profile.objects.filter(user__is_superuser=True):
        _, made = Notification.objects.get_or_create(
            profile=profile, kind=Notification.Kind.SYSTEM_UPDATE, message=message
        )
        created += made
    logger.info("check_for_new_version: v%s available, %d notification(s) created", latest, created)
    _log_system_datalog(DataLog.Action.UPDATE_CHECK, created, detail=f"v{latest} available.")
    return created


@shared_task
def reclassify_anime_titles():
    """Nightly beat job (see bootstrap_periodic_tasks.py) - wraps the
    reclassify_anime_titles management command (see its own docstring)
    so a TV title materialized before its anime status was known gets
    promoted to MediaType.ANIME on its own, instead of staying
    misclassified until an admin re-runs the command by hand. Logged to
    DataLog via _log_system_datalog so the result shows up in Settings >
    Logs the same way every other maintenance action does, not just
    Celery's own worker log."""
    result = {}
    call_command("reclassify_anime_titles", stdout=StringIO(), result_sink=result)
    reclassified = result["reclassified"]
    logger.info("reclassify_anime_titles: %d/%d titles reclassified", len(reclassified), result["total"])
    _log_system_datalog(DataLog.Action.RECLASSIFY_ANIME, len(reclassified), reclassified)
    return result


def _run_backfill_command(data_log_id, command_name):
    """Shared body for run_backfill_posters/genres/completion below - each
    just wraps its own already-existing management command (see
    tracker/management/commands/), dispatched from Settings' Maintenance
    tab (views.run_maintenance_task) instead of run from a shell, because
    each one makes one TMDB call per title with a deliberate throttle and
    can run past a normal request's timeout for a real library. The
    DataLog row is created by the view *before* dispatch (so "started" is
    visible in the Logs tab immediately) - this only ever updates it."""
    log = DataLog.objects.get(pk=data_log_id)
    buf = StringIO()
    try:
        call_command(command_name, stdout=buf)
    except Exception as e:
        log.status = DataLog.Status.FAILED
        log.error_message = str(e)[:500]
        log.save(update_fields=["status", "error_message"])
        raise
    output = buf.getvalue().strip()
    log.status = DataLog.Status.SUCCESS
    log.detail = output.splitlines()[-1][:255] if output else ""
    log.save(update_fields=["status", "detail"])


@shared_task
def run_backfill_posters(data_log_id):
    _run_backfill_command(data_log_id, "backfill_posters")


@shared_task
def run_backfill_genres(data_log_id):
    _run_backfill_command(data_log_id, "backfill_genres")


@shared_task
def run_backfill_completion(data_log_id):
    _run_backfill_command(data_log_id, "backfill_completion")


@shared_task
def run_due_backups():
    """Hourly beat job (see bootstrap_periodic_tasks.py) - queues a
    run_scheduled_backup for every enabled BackupDestination whose next
    daily/weekly/monthly backup is due (backup_destinations.is_due)."""
    from .backup_destinations import is_due
    from .models import BackupDestination

    queued = 0
    now = timezone.now()
    for destination in BackupDestination.objects.filter(enabled=True):
        if is_due(destination, now):
            run_scheduled_backup.delay(destination.id)
            queued += 1
    return queued


@shared_task
def run_scheduled_backup(destination_id, notify=False):
    """One backup to one destination - from run_due_backups, or (notify=
    True) Settings' "Back up now"/first backup, which also gets a
    notification that turns into the result. Shows as running in Logs
    and on the import/export page while it works (transfer_tracking.py)."""
    from . import backup_destinations, transfer_tracking
    from .models import BackupDestination

    destination = BackupDestination.objects.select_related("profile").filter(id=destination_id).first()
    if destination is None:
        return False
    previous_error = destination.last_error
    log, note = transfer_tracking.start_backup(destination, notify=notify)
    try:
        ok, message = backup_destinations.run_backup(destination)
    except Exception as exc:
        ok, message = False, f"Unexpected error: {exc.__class__.__name__}"
        destination.last_error = message
        destination.save(update_fields=["last_error"])
        logger.exception("run_scheduled_backup: destination %s crashed", destination_id)
    transfer_tracking.finish_backup(destination, log, ok, message, note=note, previous_error=previous_error)
    logger.info("run_scheduled_backup: destination %s (%s) -> %s", destination_id, destination.kind, "ok" if ok else message)
    return ok


@shared_task
def prune_old_logs():
    """Nightly beat job (see bootstrap_periodic_tasks.py) - deletes
    SyncLog/DataLog rows older than InstanceConfig.log_retention_days.
    A no-op when that's unset (the default - "keep forever"). Deliberately
    never touches AdminAuditLogEntry - see that field's own comment on
    InstanceConfig for why. Uses started_at/created_at (when the row was
    written), not any completion time, so a long-stuck RUNNING row still
    ages out eventually instead of being retained forever by an
    unreachable finished_at."""
    retention_days = InstanceConfig.load().log_retention_days
    if not retention_days:
        return 0
    cutoff = timezone.now() - timedelta(days=retention_days)
    deleted_sync = SyncLog.objects.filter(started_at__lt=cutoff).delete()[0]
    # Runs after the delete above, so this row itself is never at risk of
    # being swept up by the same pass.
    deleted_data = DataLog.objects.filter(created_at__lt=cutoff).delete()[0]
    total = deleted_sync + deleted_data
    logger.info("prune_old_logs: removed %d log entries older than %d days", total, retention_days)
    _log_system_datalog(
        DataLog.Action.LOG_RETENTION, total,
        detail=f"{deleted_sync} sync + {deleted_data} data log row(s) older than {retention_days}d removed.",
    )
    return total


def _system_profile():
    """Attaches a DataLog row raised from a background task with no
    requesting user (quota-pause/failure rows, the nightly anime
    reclassification summary, ...) to the instance owner - the same "no
    specific user" convention _settings_page_context's own
    other_owner_exists check relies on elsewhere in this app."""
    return Profile.objects.filter(user__is_superuser=True).order_by("pk").first()


def _roll_mdblist_quota(cfg):
    """Rolls InstanceConfig's self-tracked daily counter over to today/0 the
    first time it's checked past UTC midnight - no separate reset job
    needed. Mutates cfg in place; caller saves."""
    today = timezone.now().date()
    if cfg.mdblist_quota_date != today:
        cfg.mdblist_quota_date = today
        cfg.mdblist_quota_count = 0


def _mdblist_quota_ok(cfg):
    """Whether there's still budget for one more request, after rolling the
    counter over. Also treats MDBList's own last-seen X-RateLimit-Remaining
    as a backstop - if that ever reported 0, pause regardless of our own
    count."""
    if cfg.mdblist_rate_limit_remaining == 0:
        return False
    return cfg.mdblist_quota_count < django_settings.MDBLIST_QUOTA_PAUSE_AT


def _log_mdblist_quota_pause(cfg, profile):
    today = timezone.now().date()
    if cfg.mdblist_quota_pause_logged_date == today:
        return
    cfg.mdblist_quota_pause_logged_date = today
    if profile is not None:
        DataLog.objects.create(
            profile=profile,
            action=DataLog.Action.MDBLIST_REFRESH,
            provider="mdblist",
            status=DataLog.Status.FAILED,
            detail=f"Paused for the day - {cfg.mdblist_quota_count}/{django_settings.MDBLIST_QUOTA_PAUSE_AT} requests used.",
        )
    logger.info("fetch_mdblist_ratings: quota paused for the day (%d requests used)", cfg.mdblist_quota_count)


def _classify_next_refresh(release_date_str, vote_count, ratings):
    """Tiered refresh schedule (see the plan/README) - a title's next
    next_refresh_at depends on its release recency and popularity, not a
    flat TTL, so a long-since-settled older title isn't refetched nearly
    as often as an upcoming or newly-released one. Checked in this order
    (not the table's row order) so a title that's both newly released and
    low-vote-count gets the tighter "newly released" cadence, not the
    looser "obscure" one."""
    now = timezone.now()
    if not ratings:
        return now + timedelta(days=django_settings.MDBLIST_REFRESH_NOT_FOUND_DAYS)

    release_date = None
    if release_date_str:
        try:
            release_date = date.fromisoformat(release_date_str)
        except ValueError:
            release_date = None

    if release_date is not None:
        if release_date > now.date():
            return now + timedelta(days=django_settings.MDBLIST_REFRESH_UPCOMING_DAYS)
        if (now.date() - release_date).days <= django_settings.MDBLIST_NEWLY_RELEASED_DAYS:
            return now + timedelta(days=django_settings.MDBLIST_REFRESH_NEW_DAYS)

    if vote_count is not None and vote_count < django_settings.MDBLIST_OBSCURE_VOTE_THRESHOLD:
        return now + timedelta(days=django_settings.MDBLIST_REFRESH_OBSCURE_DAYS)

    return now + timedelta(days=django_settings.MDBLIST_REFRESH_OLDER_DAYS)


@shared_task
def fetch_mdblist_ratings(title_id, force=False):
    """Populates/refreshes TitleRatingsCache for one title - queued lazily
    by views._mdblist_ratings_context the first time a title is actually
    viewed, or by queue_due_mdblist_refreshes once its next_refresh_at has
    passed. Never called synchronously from a request - see that view's
    own comment for why. force=True (the admin "Refresh now" action) skips
    the quota gate but still spends/records a real request."""
    try:
        title = Title.objects.get(pk=title_id)
    except Title.DoesNotExist:
        return

    tmdb_id = title.external_ids.get("tmdb")
    if not tmdb_id:
        return

    cfg = InstanceConfig.load()
    _roll_mdblist_quota(cfg)
    if not force and not _mdblist_quota_ok(cfg):
        _log_mdblist_quota_pause(cfg, _system_profile())
        cfg.save(update_fields=["mdblist_quota_date", "mdblist_quota_count", "mdblist_quota_pause_logged_date"])
        # Still leaves a (fetch_attempted=False) row behind if this title
        # has never been cached at all - views.title_rating_pills_partial
        # self-polls while no row exists yet, and quota being paused for
        # the rest of the day shouldn't turn that into an infinite poll.
        # A short retry (not the full tier) picks it back up once quota
        # allows again, same as the rate_limited/error branch below.
        TitleRatingsCache.objects.get_or_create(
            title=title, defaults={"next_refresh_at": timezone.now() + timedelta(hours=1)}
        )
        return
    cfg.save(update_fields=["mdblist_quota_date", "mdblist_quota_count"])

    tmdb_media_type = tmdb.media_type_for(title)
    details = tmdb.get_full_details(tmdb_media_type, tmdb_id) or {}
    release_date_str = details.get("release_date") or details.get("first_air_date")
    vote_count = details.get("vote_count")

    result = mdblist.fetch_ratings(tmdb_media_type, tmdb_id)

    cfg.mdblist_quota_count += 1
    if result["remaining"] is not None:
        cfg.mdblist_rate_limit_remaining = result["remaining"]
    cfg.save(update_fields=["mdblist_quota_count", "mdblist_rate_limit_remaining"])

    now = timezone.now()
    if result["status"] in ("ok", "not_found"):
        TitleRatingsCache.objects.update_or_create(
            title=title,
            defaults={
                "ratings": result["ratings"],
                "fetch_attempted": True,
                "last_fetched_at": now,
                "next_refresh_at": _classify_next_refresh(release_date_str, vote_count, result["ratings"]),
            },
        )
        return

    # rate_limited/error - record the attempt and retry soon, but don't
    # clobber an existing good payload with an empty one.
    profile = _system_profile()
    if profile is not None:
        DataLog.objects.create(
            profile=profile,
            action=DataLog.Action.MDBLIST_REFRESH,
            provider="mdblist",
            status=DataLog.Status.FAILED,
            detail=f"{result['status']} fetching ratings for {title}"[:255],
        )
    cache, _ = TitleRatingsCache.objects.get_or_create(title=title)
    cache.fetch_attempted = True
    cache.next_refresh_at = now + timedelta(hours=6)
    cache.save(update_fields=["fetch_attempted", "next_refresh_at"])


@shared_task
def queue_due_mdblist_refreshes():
    """Hourly beat job (see scheduling.ensure_mdblist_refresh_task) - only
    ever queues titles whose own next_refresh_at has actually passed,
    never a scan-everything-on-a-timer sweep. Capped per run so one sweep
    can't itself blow the daily quota; whatever's left over gets picked up
    on the next hourly run."""
    due_ids = TitleRatingsCache.objects.filter(next_refresh_at__lte=timezone.now()).values_list(
        "title_id", flat=True
    )[:200]
    count = 0
    for title_id in due_ids:
        fetch_mdblist_ratings.delay(title_id)
        count += 1
    return count


# --------------------------------------------------------------------
# WeTrakr quota bookkeeping
#
# Two independent self-tracked daily counters, same lazy-rollover-on-read
# idiom as _roll_mdblist_quota above - rolled to today/0 the first time
# either is checked past UTC midnight, no dedicated reset job:
#   - ExternalAccount.wetrakr_quota_count: this one connected user's own
#     requests today, checked against their free-plan ceiling.
#   - InstanceConfig.wetrakr_app_quota_count: every connected account's
#     requests today, combined, checked against the whole app's shared
#     ceiling (WeTrakr enforces both independently - see
#     tracker/integrations/wetrakr.py's own module docstring).
# Unlike MDBList, there's no X-RateLimit-Remaining-style header to use as
# a backstop (WeTrakr's API has none) - these proactive checks are the
# only line of defense before a live 429, so _scan_wetrakr_session and
# sync_wetrakr_history both call them before every request, not just on
# a failure.
# --------------------------------------------------------------------


def _roll_wetrakr_account_quota(account):
    """Mutates account in place; caller saves."""
    today = timezone.now().date()
    if account.wetrakr_quota_date != today:
        account.wetrakr_quota_date = today
        account.wetrakr_quota_count = 0


def _roll_wetrakr_app_quota(cfg):
    """Mutates cfg in place; caller saves."""
    today = timezone.now().date()
    if cfg.wetrakr_app_quota_date != today:
        cfg.wetrakr_app_quota_date = today
        cfg.wetrakr_app_quota_count = 0


def _wetrakr_account_budget_ok(account):
    """VIP/patron accounts are never proactively paused here - WeTrakr
    doesn't publish their real daily ceiling, so guessing one and pausing
    against it risks cutting off headroom the account actually has. Only
    a free-plan account (whose 1000/day WeTrakr does publish) gets
    self-paced, and with a safety margin (WETRAKR_FREE_QUOTA_PAUSE_AT)
    below that, not the exact number."""
    if account.wetrakr_plan in ("vip", "patron"):
        return True
    return account.wetrakr_quota_count < django_settings.WETRAKR_FREE_QUOTA_PAUSE_AT


def _wetrakr_app_budget_ok(cfg):
    return cfg.wetrakr_app_quota_count < django_settings.WETRAKR_APP_QUOTA_PAUSE_AT


def _record_wetrakr_request(account, cfg):
    """Call once per actual outbound WeTrakr HTTP request (after rolling
    both counters over) - increments and saves both the per-account and
    the app-wide counter together, since every WeTrakr request this app
    makes spends from both budgets at once. Passed as wetrakr.py's
    on_request callback (see that module's _request docstring) so a
    CONCURRENCY_LIMIT retry - a second real request for what looks like
    one call from here - gets counted too, not just the first attempt."""
    account.wetrakr_quota_count += 1
    account.save(update_fields=["wetrakr_quota_date", "wetrakr_quota_count"])
    cfg.wetrakr_app_quota_count += 1
    cfg.save(update_fields=["wetrakr_app_quota_date", "wetrakr_app_quota_count"])


def _saturate_wetrakr_app_quota(cfg):
    """Called when a live APP_QUOTA_EXCEEDED 429 comes back - pins the
    app-wide counter at its pause threshold immediately, so every other
    connected account's next proactive _wetrakr_app_budget_ok check also
    stops, rather than each one individually re-discovering the same
    shared exhaustion one 429 at a time."""
    _roll_wetrakr_app_quota(cfg)
    cfg.wetrakr_app_quota_count = max(cfg.wetrakr_app_quota_count, django_settings.WETRAKR_APP_QUOTA_PAUSE_AT)
    cfg.save(update_fields=["wetrakr_app_quota_date", "wetrakr_app_quota_count"])


def _ensure_wetrakr_budget(account, cfg):
    """Rolls both counters over if needed, then raises WeTrakrQuotaError
    if either is already exhausted - called right before every real
    WeTrakr request (plan check, each history page) so the proactive
    stop happens before spending the request, not just after discovering
    the account was already over via a live 429."""
    _roll_wetrakr_account_quota(account)
    _roll_wetrakr_app_quota(cfg)
    if not _wetrakr_account_budget_ok(account):
        account.save(update_fields=["wetrakr_quota_date", "wetrakr_quota_count"])
        raise wetrakr.WeTrakrQuotaError("QUOTA_EXCEEDED", "WeTrakr account daily quota reached")
    if not _wetrakr_app_budget_ok(cfg):
        cfg.save(update_fields=["wetrakr_app_quota_date", "wetrakr_app_quota_count"])
        raise wetrakr.WeTrakrQuotaError("APP_QUOTA_EXCEEDED", "WeTrakr app-wide daily quota reached")


def _fetch_wetrakr_page_quota_checked(account, client_id, client_secret, cursor, start_at=None):
    """The one place that actually calls wetrakr.fetch_history_page -
    both _scan_wetrakr_session's resumable loop and sync_wetrakr_history's
    in-memory loop call this per page, so a pre-request budget check and
    accurate post-request accounting (every real request counted,
    including an internal CONCURRENCY_LIMIT retry - see wetrakr.py's
    _request docstring) happen in exactly one place rather than being
    reimplemented - and potentially drifting out of sync - per caller.
    Wrapped in _call_with_refresh for the same 401-triggers-a-refresh
    handling every other WeTrakr/Trakt/Simkl fetch already gets."""
    cfg = InstanceConfig.load()
    _ensure_wetrakr_budget(account, cfg)

    def fetch():
        return wetrakr.fetch_history_page(
            account.get_access_token(), client_id, cursor=cursor, start_at=start_at,
            on_request=lambda: _record_wetrakr_request(account, cfg),
        )

    return _call_with_refresh(account, wetrakr, client_id, client_secret, fetch)


def _check_wetrakr_plan(account, client_id, force=False):
    """Refreshes account.wetrakr_plan from GET /plan-usage. Cached for
    about a day (routine sync just lets the cache ride), but force=True
    (used at the start of every new Import Review scan) always re-checks
    regardless of cache age - the whole point of checking plan at all is
    knowing the real budget before a potentially-large first pull, so a
    stale "free" reading from before the user upgraded (or vice versa)
    would be exactly the wrong moment to trust a day-old cache."""
    if not force and account.wetrakr_plan_checked_at:
        if timezone.now() - account.wetrakr_plan_checked_at < timedelta(days=1):
            return account.wetrakr_plan
    cfg = InstanceConfig.load()
    _ensure_wetrakr_budget(account, cfg)
    usage = wetrakr.get_plan_usage(
        account.get_access_token(), client_id, on_request=lambda: _record_wetrakr_request(account, cfg),
    )
    account.wetrakr_plan = usage.get("plan") or ""
    account.wetrakr_plan_checked_at = timezone.now()
    account.save(update_fields=["wetrakr_plan", "wetrakr_plan_checked_at"])
    return account.wetrakr_plan
