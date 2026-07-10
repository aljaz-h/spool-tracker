"""Creates/updates the django-celery-beat PeriodicTask backing a connected
account's configured sync schedule. Called both when the schedule is saved
from Settings (immediate effect) and by bootstrap_periodic_tasks on every
scheduler boot (reconciles anything that drifted, and backfills accounts
connected before this feature existed)."""

from django_celery_beat.models import CrontabSchedule, PeriodicTask

from .models import ExternalAccount

_TASK_NAME = {
    ExternalAccount.Provider.TRAKT: "tracker.tasks.sync_trakt_history",
    ExternalAccount.Provider.SIMKL: "tracker.tasks.sync_simkl_history",
}


def sync_periodic_task_name(account):
    return f"sync-{account.provider}-{account.id}"


def ensure_periodic_task(account):
    """(Re)creates the PeriodicTask matching account's sync_interval_days/
    sync_hour/sync_minute - see the field comments on ExternalAccount for
    the day_of_month=*/N approximation this relies on."""
    day_of_month = "*" if account.sync_interval_days <= 1 else f"*/{account.sync_interval_days}"
    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute=str(account.sync_minute),
        hour=str(account.sync_hour),
        day_of_week="*",
        day_of_month=day_of_month,
        month_of_year="*",
    )
    PeriodicTask.objects.update_or_create(
        name=sync_periodic_task_name(account),
        defaults={
            "crontab": schedule,
            "task": _TASK_NAME[account.provider],
            "args": f"[{account.profile_id}]",
            "enabled": True,
        },
    )


def remove_periodic_task(account):
    PeriodicTask.objects.filter(name=sync_periodic_task_name(account)).delete()
