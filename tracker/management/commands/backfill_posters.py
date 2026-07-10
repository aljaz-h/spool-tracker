import time

from django.core.management.base import BaseCommand

from tracker.integrations import tmdb
from tracker.models import Title


class Command(BaseCommand):
    help = (
        "Best-effort TMDB poster + id lookup for existing titles missing "
        "either a poster_url or a TMDB id - e.g. everything imported "
        "before poster lookup was wired into the Trakt/Simkl/CSV import "
        "paths, AND titles that got a poster from an earlier version of "
        "this command that predates it also capturing external_ids['tmdb'] "
        "(which backfill_completion needs and can't work without)."
    )

    def handle(self, *args, **options):
        # Filtered in Python, not via Title.objects.filter(poster_url="")
        # alone - a title can already have a poster from an older run of
        # this command that didn't yet capture the TMDB id, and would be
        # silently skipped forever by a poster_url-only filter even though
        # it still needs the id. Same reasoning as backfill_completion's
        # own Python-side filter re: JSONField key-existence lookups.
        titles = [t for t in Title.objects.all() if not t.poster_url or not t.external_ids.get("tmdb")]
        total = len(titles)
        if total == 0:
            self.stdout.write("Nothing to backfill - every title already has a poster and a TMDB id.")
            return

        self.stdout.write(f"Looking up posters/ids for {total} titles...")
        posters_found = ids_found = 0
        for i, title in enumerate(titles, start=1):
            match = tmdb.find_match(title.media_type, title.name, title.year)
            if match:
                if match["poster_url"] and not title.poster_url:
                    title.poster_url = match["poster_url"]
                    posters_found += 1
                if not title.external_ids.get("tmdb"):
                    ids_found += 1
                title.external_ids = {
                    **title.external_ids,
                    "tmdb": str(match["id"]),
                    "tmdb_kind": match["kind"],
                }
                title.save(update_fields=["poster_url", "external_ids"])
            if i % 50 == 0:
                self.stdout.write(f"...{i}/{total} checked, {posters_found} posters + {ids_found} ids found so far")
            time.sleep(0.1)  # stay well clear of TMDB's rate limit over a large backfill

        self.stdout.write(
            self.style.SUCCESS(f"Done: {posters_found}/{total} got a poster, {ids_found}/{total} got a TMDB id.")
        )
