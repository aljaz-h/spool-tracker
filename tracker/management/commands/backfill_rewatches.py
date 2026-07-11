from django.core.management.base import BaseCommand

from tracker import rewatches
from tracker.models import WatchEvent


class Command(BaseCommand):
    help = (
        "Recomputes WatchEvent.is_rewatch for every (profile, title, "
        "episode) group - covers history imported before rewatch marking "
        "existed (nothing set is_rewatch=True during import until now, "
        "even though rewatches themselves were always correctly stored as "
        "separate WatchEvent rows). Safe to re-run."
    )

    def handle(self, *args, **options):
        # One query for a representative event per (profile, title,
        # episode) group, reusing select_related's already-fetched
        # profile/title/episode objects rather than re-querying per group.
        seen = {}
        for event in WatchEvent.objects.select_related("profile", "title", "episode").order_by("id"):
            key = (event.profile_id, event.title_id, event.episode_id)
            seen.setdefault(key, event)

        total = len(seen)
        if total == 0:
            self.stdout.write("Nothing to check - no watch history yet.")
            return

        self.stdout.write(f"Checking {total} (profile, title, episode) groups...")
        for i, event in enumerate(seen.values(), start=1):
            rewatches.recompute_is_rewatch(event.profile, event.title, event.episode)
            if i % 200 == 0:
                self.stdout.write(f"...{i}/{total}")

        self.stdout.write(self.style.SUCCESS(f"Done: checked {total} groups."))
