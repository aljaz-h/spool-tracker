import time

from django.core.management.base import BaseCommand

from tracker.integrations import tmdb
from tracker.models import Title, attach_genres


class Command(BaseCommand):
    help = (
        "Best-effort TMDB genre lookup for existing titles with no Genre "
        "rows attached - genre-fetching was never wired into any import "
        "path (Trakt/Simkl/CSV) until it was added alongside this command, "
        "so every title synced before that point has none. Requires a "
        "TMDB id already captured on the title (external_ids['tmdb']) - "
        "run backfill_posters first for titles that don't have one yet."
    )

    def handle(self, *args, **options):
        titles = [t for t in Title.objects.filter(genres__isnull=True).distinct() if t.external_ids.get("tmdb")]
        total = len(titles)
        if total == 0:
            self.stdout.write("Nothing to backfill - every title either has genres already or no TMDB id yet.")
            return

        self.stdout.write(f"Looking up genres for {total} titles...")
        found = 0
        for i, title in enumerate(titles, start=1):
            details = tmdb.get_full_details(tmdb.media_type_for(title), title.external_ids["tmdb"])
            if details and details["genres"]:
                attach_genres(title, details["genres"])
                found += 1
            if i % 50 == 0:
                self.stdout.write(f"...{i}/{total} checked, {found} got genres so far")
            time.sleep(0.1)  # stay well clear of TMDB's rate limit over a large backfill

        self.stdout.write(self.style.SUCCESS(f"Done: {found}/{total} titles got genre data."))
