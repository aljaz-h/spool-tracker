import time

from django.core.management.base import BaseCommand

from tracker.integrations import tmdb
from tracker.models import Title


class Command(BaseCommand):
    help = (
        "Best-effort TMDB poster lookup for existing titles that have no "
        "poster_url yet - e.g. everything imported before poster lookup "
        "was wired into the Trakt/Simkl/CSV import paths."
    )

    def handle(self, *args, **options):
        titles = Title.objects.filter(poster_url="")
        total = titles.count()
        if total == 0:
            self.stdout.write("Nothing to backfill - every title already has a poster_url.")
            return

        self.stdout.write(f"Looking up posters for {total} titles...")
        found = 0
        for i, title in enumerate(titles.iterator(), start=1):
            poster_url = tmdb.find_poster_url(title.media_type, title.name, title.year)
            if poster_url:
                title.poster_url = poster_url
                title.save(update_fields=["poster_url"])
                found += 1
            if i % 50 == 0:
                self.stdout.write(f"...{i}/{total} checked, {found} found so far")
            time.sleep(0.1)  # stay well clear of TMDB's rate limit over a large backfill

        self.stdout.write(self.style.SUCCESS(f"Done: {found}/{total} titles got a poster."))
