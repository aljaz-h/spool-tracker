import os
from datetime import date, timedelta
from io import StringIO

import requests
from celery import shared_task
from celery.utils.log import get_task_logger
from django.conf import settings as django_settings
from django.core.management import call_command
from django.utils import timezone

from . import csv_import, instance_config, notifications, release_sync, selectors, update_check, version
from .integrations import mdblist, nuvio, simkl, tmdb, trakt
from .models import (
    DataLog,
    ExternalAccount,
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
    """Wraps a sync call with a SyncLog row - records when it ran and
    whether it succeeded/failed (never what was imported). Re-raises on
    failure after recording it, so the sync still shows up as failed in
    Celery's own tracking/logs too, not just silently swallowed."""
    log = SyncLog.objects.create(profile=profile, provider=provider)
    try:
        created = fetch_and_upsert()
    except Exception as e:
        log.status = SyncLog.Status.FAILED
        log.error_message = str(e)[:500]
        log.finished_at = timezone.now()
        log.save(update_fields=["status", "error_message", "finished_at"])
        notifications.notify_sync_failure(profile, provider, log.error_message)
        raise
    log.status = SyncLog.Status.SUCCESS
    log.item_count = created
    log.finished_at = timezone.now()
    log.save(update_fields=["status", "item_count", "finished_at"])
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
        items = trakt.fetch_history(account.get_access_token(), client_id, start_at=account.last_synced_at)
        created = trakt.upsert_history_items(account.profile, items)
        if account.import_lists:
            lists_data = trakt.fetch_lists(account.get_access_token(), client_id)
            created += trakt.upsert_lists(account.profile, lists_data)
        return created

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
        items = simkl.fetch_history(account.get_access_token(), client_id)
        return simkl.upsert_history_items(account.profile, items)

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
        created = nuvio.upsert_history_items(connection.profile, watched)
        nuvio.upsert_progress_items(connection.profile, progress)
        return created

    created = _run_sync(connection.profile, ExternalAccount.Provider.NUVIO, fetch_and_upsert)
    connection.last_synced_at = timezone.now()
    connection.save(update_fields=["last_synced_at"])
    logger.info("sync_nuvio_history: profile %s, %d new watch events", profile_id, created)
    return created


@shared_task
def run_data_import(log_id, profile_id, path, kind, mapping=None):
    """Settings → Import Data's background path for a file too large to
    commit inside one request - see LARGE_IMPORT_ROW_THRESHOLD in
    views.py. `log_id` is a DataLog row already created (status=RUNNING)
    synchronously by the view before dispatch, same as _run_sync creates
    its SyncLog row up front - so the Logs tab shows the import as
    in-progress immediately rather than only once this task actually
    starts running on a worker. Always removes the temp file at `path`
    (success or failure), since ownership of it passes from the view to
    this task at dispatch time - see import_csv_commit."""
    log = DataLog.objects.get(id=log_id)
    profile = Profile.objects.get(id=profile_id)
    try:
        rows, parse_errors = csv_import.parse_file(path, kind, mapping)
        imported, skipped = csv_import.commit_rows(profile, rows)
    except Exception as e:
        log.status = DataLog.Status.FAILED
        log.error_message = str(e)[:500]
        log.save(update_fields=["status", "error_message"])
        raise
    finally:
        try:
            os.remove(path)
        except OSError:
            pass

    all_skipped = parse_errors + skipped
    log.status = DataLog.Status.SUCCESS
    log.item_count = imported
    log.detail = f"{len(all_skipped)} skipped" if all_skipped else ""
    log.save(update_fields=["status", "item_count", "detail"])
    logger.info("run_data_import: profile %s, %d imported, %d skipped", profile_id, imported, len(all_skipped))
    return imported


@shared_task
def sync_release_schedules():
    """Nightly beat job (see bootstrap_periodic_tasks.py) - refreshes
    ReleaseSchedule for every title anyone in the household is watching,
    has planned/completed, or has on a watchlist, so a renewed show or a
    now-dated movie shows up on Calendar without the user re-visiting its
    detail page. Instance-wide, not per-account - no SyncLog row (that
    model is Trakt/Simkl-account-shaped; nothing in the UI surfaces a
    "last release sync" for this yet), just the usual task logger."""
    titles = list(selectors.titles_needing_release_sync())
    touched = sum(release_sync.sync_title_releases(t) for t in titles)
    logger.info("sync_release_schedules: checked %d title(s), %d release row(s) touched", len(titles), touched)
    return touched


@shared_task
def generate_release_notifications():
    """Nightly beat job (see bootstrap_periodic_tasks.py), scheduled right
    after sync_release_schedules so a freshly-synced release is already
    in ReleaseSchedule by the time this scans it - see
    tracker/notifications.py for the actual eligibility/dedupe logic."""
    created = notifications.generate_release_notifications()
    logger.info("generate_release_notifications: %d notification(s) created", created)
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
        return 0
    message = f"Spool v{latest} is available (you're on v{version.APP_VERSION})."
    created = 0
    for profile in Profile.objects.filter(user__is_superuser=True):
        _, made = Notification.objects.get_or_create(
            profile=profile, kind=Notification.Kind.SYSTEM_UPDATE, message=message
        )
        created += made
    logger.info("check_for_new_version: v%s available, %d notification(s) created", latest, created)
    return created


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
    deleted_data = DataLog.objects.filter(created_at__lt=cutoff).delete()[0]
    total = deleted_sync + deleted_data
    logger.info("prune_old_logs: removed %d log entries older than %d days", total, retention_days)
    return total


def _mdblist_system_profile():
    """Attaches quota-pause/failure DataLog rows (raised from a background
    task with no requesting user) to the instance owner - the same "no
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
        _log_mdblist_quota_pause(cfg, _mdblist_system_profile())
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
    profile = _mdblist_system_profile()
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
