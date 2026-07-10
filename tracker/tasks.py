from celery import shared_task
from celery.utils.log import get_task_logger
from django.utils import timezone

from . import instance_config
from .integrations import simkl, trakt
from .models import ExternalAccount, SyncLog

logger = get_task_logger(__name__)


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

    def do_sync():
        client_id, _ = instance_config.get_trakt_credentials()
        items = trakt.fetch_history(account.access_token, client_id, start_at=account.last_synced_at)
        return trakt.upsert_history_items(account.profile, items)

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

    def do_sync():
        client_id, _ = instance_config.get_simkl_credentials()
        items = simkl.fetch_history(account.access_token, client_id)
        return simkl.upsert_history_items(account.profile, items)

    created = _run_sync(account.profile, ExternalAccount.Provider.SIMKL, do_sync)
    logger.info("sync_simkl_history: profile %s, %d new watch events", profile_id, created)
    return created


@shared_task
def sync_all_connected_accounts():
    """Daily beat job (see tracker/management/commands/bootstrap_periodic_tasks.py)
    — fans out to a per-account task per spool-handoff-addendum.md §1's
    "same sync job... on a schedule (e.g. daily)"."""
    dispatched = 0
    for account in ExternalAccount.objects.all():
        if account.provider == ExternalAccount.Provider.TRAKT:
            sync_trakt_history.delay(account.profile_id)
        elif account.provider == ExternalAccount.Provider.SIMKL:
            sync_simkl_history.delay(account.profile_id)
        dispatched += 1
    return dispatched
