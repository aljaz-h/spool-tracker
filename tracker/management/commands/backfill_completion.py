import time

from django.core.management.base import BaseCommand

from tracker import completion
from tracker.models import MediaType, Profile, Title, WatchEvent


class Command(BaseCommand):
    help = (
        "Backfills movie/episode runtime and WatchProgress completion "
        "status from TMDB, for titles that already have a TMDB id (see "
        "backfill_posters, which populates that id) - covers everything "
        "imported before this feature existed. Safe to re-run."
    )

    def handle(self, *args, **options):
        # Filtered in Python rather than via a JSONField key-existence
        # lookup - "does external_ids have a non-empty 'tmdb' key" behaves
        # differently enough between SQLite (dev) and Postgres (prod) that
        # a plain iterate-and-check is the safer bet for a one-off command
        # over what's realistically a personal-library-sized table.
        titles = [t for t in Title.objects.all() if t.external_ids.get("tmdb")]
        total = len(titles)
        if total == 0:
            self.stdout.write("Nothing to backfill - no titles have a TMDB id yet (run backfill_posters first).")
            return

        self.stdout.write(f"Checking {total} titles against TMDB...")
        movies_done = shows_done = 0
        for i, title in enumerate(titles, start=1):
            profile_ids = WatchEvent.objects.filter(title=title).values_list("profile_id", flat=True).distinct()
            if title.media_type == MediaType.MOVIE:
                completion.update_movie_runtime(title)
                for profile in Profile.objects.filter(id__in=profile_ids):
                    completion.sync_watchlist_removal(profile, title)
                movies_done += 1
            else:
                for profile in Profile.objects.filter(id__in=profile_ids):
                    completion.sync_show_completion(profile, title)
                    completion.sync_watchlist_removal(profile, title)
                shows_done += 1
            if i % 50 == 0:
                self.stdout.write(f"...{i}/{total} checked")
            time.sleep(0.1)  # stay well clear of TMDB's rate limit over a large backfill

        self.stdout.write(
            self.style.SUCCESS(f"Done: checked {movies_done} movies and {shows_done} shows/anime.")
        )
