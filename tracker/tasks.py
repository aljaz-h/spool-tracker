from celery import shared_task
from celery.utils.log import get_task_logger

from . import instance_config
from .integrations import simkl, trakt
from .models import ExternalAccount

logger = get_task_logger(__name__)


@shared_task
def sync_trakt_history(profile_id):
    try:
        account = ExternalAccount.objects.select_related("profile").get(
            profile_id=profile_id, provider=ExternalAccount.Provider.TRAKT
        )
    except ExternalAccount.DoesNotExist:
        logger.info("sync_trakt_history: profile %s has no Trakt account connected", profile_id)
        return 0
    client_id, _ = instance_config.get_trakt_credentials()
    items = trakt.fetch_history(account.access_token, client_id)
    created = trakt.upsert_history_items(account.profile, items)
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
    client_id, _ = instance_config.get_simkl_credentials()
    items = simkl.fetch_history(account.access_token, client_id)
    created = simkl.upsert_history_items(account.profile, items)
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
