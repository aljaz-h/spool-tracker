from django.core.management.base import BaseCommand
from django_celery_beat.models import PeriodicTask

from tracker import scheduling
from tracker.models import ExternalAccount


class Command(BaseCommand):
    help = (
        "Idempotently ensures every connected Trakt/Simkl account has a "
        "PeriodicTask matching its configured sync schedule, plus the single "
        "nightly release-schedule sync task (run by the scheduler service on "
        "start). Superseded the old single daily-external-sync job that fired "
        "for every account at once - each account gets its own schedule now "
        "(see tracker/scheduling.py)."
    )

    def handle(self, *args, **options):
        # Old blanket job from before per-account scheduling existed -
        # remove it so an upgraded install doesn't double-sync (once via
        # this and once via each account's own new PeriodicTask).
        removed, _ = PeriodicTask.objects.filter(name="daily-external-sync").delete()
        if removed:
            self.stdout.write("Removed the old blanket daily-external-sync periodic task.")

        count = 0
        for account in ExternalAccount.objects.all():
            scheduling.ensure_periodic_task(account)
            count += 1
        self.stdout.write(self.style.SUCCESS(f"Confirmed sync schedules for {count} connected account(s)."))

        scheduling.ensure_release_sync_task()
        self.stdout.write(self.style.SUCCESS("Confirmed the nightly release-schedule sync task."))

        scheduling.ensure_release_notifications_task()
        self.stdout.write(self.style.SUCCESS("Confirmed the nightly release-notifications task."))

        scheduling.ensure_update_check_task()
        self.stdout.write(self.style.SUCCESS("Confirmed the nightly update-check task."))

        scheduling.ensure_log_retention_task()
        self.stdout.write(self.style.SUCCESS("Confirmed the nightly log-retention task."))

        scheduling.ensure_mdblist_refresh_task()
        self.stdout.write(self.style.SUCCESS("Confirmed the hourly MDBList ratings-refresh task."))

        scheduling.ensure_watchlist_stale_task()
        self.stdout.write(self.style.SUCCESS("Confirmed the nightly watchlist time-capsule task."))
