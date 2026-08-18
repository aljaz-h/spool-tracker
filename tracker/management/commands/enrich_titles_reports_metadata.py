import time

from django.core.management.base import BaseCommand

from tracker.integrations import tmdb
from tracker.models import Title, attach_reports_metadata


class Command(BaseCommand):
    help = (
        "Best-effort TMDB country/studio/network/cast/directors/writers "
        "backfill for existing titles - these six fields (see Title's own "
        "field comments) power spool-wrapped's Reports API/Year in Review "
        "report (see api/routers/reports.py), and were never populated by "
        "any import path until they were added alongside this command, so "
        "every title synced before that point has none. Requires a TMDB "
        "id already captured on the title (external_ids['tmdb']) - run "
        "backfill_posters first for titles that don't have one yet. Run "
        "once after upgrading to the version that adds this (see "
        "CHANGELOG); a title's reports metadata is otherwise only ever "
        "set once, at creation time, by whichever import path first "
        "tracked it - same one-shot-at-creation shape as backfill_genres."
    )

    def handle(self, *args, **options):
        # Same "fetch broadly, filter in Python" trade-off backfill_genres
        # makes rather than a JSONField has_key/exact query - this is a
        # manually-run one-off command, not hot-path code, so a bit of
        # extra Python-side work over a full table scan isn't worth a
        # more elaborate query for.
        candidates = [
            t
            for t in Title.objects.all()
            if t.external_ids.get("tmdb") and not (t.country or t.studio or t.network or t.cast or t.directors or t.writers)
        ]
        total = len(candidates)
        if total == 0:
            self.stdout.write("Nothing to backfill - every title either has reports metadata already or no TMDB id yet.")
            return

        self.stdout.write(f"Looking up reports metadata for {total} titles...")
        found = 0
        for i, title in enumerate(candidates, start=1):
            kind = tmdb.media_type_for(title)
            tmdb_id = title.external_ids["tmdb"]
            details = tmdb.get_full_details(kind, tmdb_id)
            if details:
                metadata = tmdb.get_reports_metadata(kind, tmdb_id, details)
                if any(metadata.values()):
                    attach_reports_metadata(title, metadata)
                    found += 1
            if i % 50 == 0:
                self.stdout.write(f"...{i}/{total} checked, {found} got reports metadata so far")
            time.sleep(0.1)  # stay well clear of TMDB's rate limit over a large backfill

        self.stdout.write(self.style.SUCCESS(f"Done: {found}/{total} titles got reports metadata."))
