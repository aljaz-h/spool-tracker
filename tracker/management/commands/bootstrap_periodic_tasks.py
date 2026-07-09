from django.core.management.base import BaseCommand
from django_celery_beat.models import CrontabSchedule, PeriodicTask


class Command(BaseCommand):
    help = "Idempotently ensures the daily Trakt/Simkl sync periodic task exists (run by the scheduler service on start)."

    def handle(self, *args, **options):
        schedule, _ = CrontabSchedule.objects.get_or_create(
            minute="0", hour="4", day_of_week="*", day_of_month="*", month_of_year="*"
        )
        _, created = PeriodicTask.objects.update_or_create(
            name="daily-external-sync",
            defaults={
                "crontab": schedule,
                "task": "tracker.tasks.sync_all_connected_accounts",
                "enabled": True,
            },
        )
        verb = "Created" if created else "Confirmed"
        self.stdout.write(self.style.SUCCESS(f"{verb} daily-external-sync periodic task (04:00 daily)."))
