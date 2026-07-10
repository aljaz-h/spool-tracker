import time

from django.core.management.base import BaseCommand

from tracker.integrations import tmdb
from tracker.models import Title


class Command(BaseCommand):
    help = (
        "Best-effort TMDB poster + id lookup for existing titles that have "
        "no poster_url yet - e.g. everything imported before poster lookup "
        "was wired into the Trakt/Simkl/CSV import paths. Also backfills "
        "external_ids['tmdb'], which backfill_completion then needs."
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
            match = tmdb.find_match(title.media_type, title.name, title.year)
            if match:
                if match["poster_url"]:
                    title.poster_url = match["poster_url"]
                    found += 1
                title.external_ids = {
                    **title.external_ids,
                    "tmdb": str(match["id"]),
                    "tmdb_kind": match["kind"],
                }
                title.save(update_fields=["poster_url", "external_ids"])
            if i % 50 == 0:
                self.stdout.write(f"...{i}/{total} checked, {found} found so far")
            time.sleep(0.1)  # stay well clear of TMDB's rate limit over a large backfill

        self.stdout.write(self.style.SUCCESS(f"Done: {found}/{total} titles got a poster."))
