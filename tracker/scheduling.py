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
    ExternalAccount.Provider.NUVIO: "tracker.tasks.sync_nuvio_history",
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


RELEASE_SYNC_TASK_NAME = "sync-release-schedules"
RELEASE_NOTIFICATIONS_TASK_NAME = "generate-release-notifications"
UPDATE_CHECK_TASK_NAME = "check-for-new-version"
LOG_RETENTION_TASK_NAME = "prune-old-logs"
MDBLIST_REFRESH_TASK_NAME = "queue-due-mdblist-refreshes"
WATCHLIST_STALE_TASK_NAME = "generate-watchlist-stale-notifications"


def ensure_release_sync_task(hour=3, minute=0):
    """(Re)creates the single, non-per-account PeriodicTask that refreshes
    ReleaseSchedule from TMDB - unlike ensure_periodic_task above, this
    isn't tied to a connected ExternalAccount, so there's exactly one of
    these instance-wide rather than one per account."""
    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute=str(minute), hour=str(hour), day_of_week="*", day_of_month="*", month_of_year="*"
    )
    PeriodicTask.objects.update_or_create(
        name=RELEASE_SYNC_TASK_NAME,
        defaults={
            "crontab": schedule,
            "task": "tracker.tasks.sync_release_schedules",
            "args": "[]",
            "enabled": True,
        },
    )


def ensure_release_notifications_task(hour=3, minute=30):
    """(Re)creates the nightly PeriodicTask that turns near-term
    ReleaseSchedule rows into Notification rows - 30 minutes after
    ensure_release_sync_task's own default, so a title's release schedule
    is already refreshed by the time this scans it."""
    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute=str(minute), hour=str(hour), day_of_week="*", day_of_month="*", month_of_year="*"
    )
    PeriodicTask.objects.update_or_create(
        name=RELEASE_NOTIFICATIONS_TASK_NAME,
        defaults={
            "crontab": schedule,
            "task": "tracker.tasks.generate_release_notifications",
            "args": "[]",
            "enabled": True,
        },
    )


def ensure_update_check_task(hour=3, minute=45):
    """(Re)creates the nightly PeriodicTask that checks for a newer Spool
    release (see tracker/update_check.py) - instance-wide, not tied to
    any account, same as the two release-schedule tasks above."""
    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute=str(minute), hour=str(hour), day_of_week="*", day_of_month="*", month_of_year="*"
    )
    PeriodicTask.objects.update_or_create(
        name=UPDATE_CHECK_TASK_NAME,
        defaults={
            "crontab": schedule,
            "task": "tracker.tasks.check_for_new_version",
            "args": "[]",
            "enabled": True,
        },
    )


def ensure_log_retention_task(hour=4, minute=15):
    """(Re)creates the nightly PeriodicTask that prunes SyncLog/DataLog
    rows past InstanceConfig.log_retention_days (see tasks.prune_old_logs)
    - unconditionally scheduled, same as the other instance-wide nightly
    tasks above, regardless of whether retention is actually configured;
    the task itself is a no-op on a night nothing needs pruning."""
    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute=str(minute), hour=str(hour), day_of_week="*", day_of_month="*", month_of_year="*"
    )
    PeriodicTask.objects.update_or_create(
        name=LOG_RETENTION_TASK_NAME,
        defaults={
            "crontab": schedule,
            "task": "tracker.tasks.prune_old_logs",
            "args": "[]",
            "enabled": True,
        },
    )


def ensure_watchlist_stale_task(hour=4, minute=0):
    """(Re)creates the nightly PeriodicTask that nudges profiles about
    long-parked Watchlist items - instance-wide, same shape as the other
    nightly notification-generating tasks above."""
    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute=str(minute), hour=str(hour), day_of_week="*", day_of_month="*", month_of_year="*"
    )
    PeriodicTask.objects.update_or_create(
        name=WATCHLIST_STALE_TASK_NAME,
        defaults={
            "crontab": schedule,
            "task": "tracker.tasks.generate_watchlist_stale_notifications",
            "args": "[]",
            "enabled": True,
        },
    )


def ensure_mdblist_refresh_task():
    """(Re)creates the hourly PeriodicTask that queues only the
    TitleRatingsCache rows whose next_refresh_at has passed (see
    tasks.queue_due_mdblist_refreshes) - hourly rather than nightly like
    the other instance-wide tasks above, since the shortest refresh tier
    (upcoming/newly-released titles) is measured in a couple of days, not
    weeks, so a once-a-day sweep would be too coarse to hit those on time."""
    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute="0", hour="*", day_of_week="*", day_of_month="*", month_of_year="*"
    )
    PeriodicTask.objects.update_or_create(
        name=MDBLIST_REFRESH_TASK_NAME,
        defaults={
            "crontab": schedule,
            "task": "tracker.tasks.queue_due_mdblist_refreshes",
            "args": "[]",
            "enabled": True,
        },
    )
