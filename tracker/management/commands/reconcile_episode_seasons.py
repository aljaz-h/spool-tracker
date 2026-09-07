from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count

from tracker import episode_matching
from tracker.models import Episode, ReleaseSchedule, Title, WatchEvent, WatchProgress


class Command(BaseCommand):
    help = (
        "Finds existing Episode rows whose (season, episode) doesn't match "
        "what episode_matching.resolve_episode_season would resolve them to "
        "today, and remaps them - the one-time backfill for data affected "
        "by the same player/TMDB season-numbering mismatch that function "
        "now prevents going forward (see its own docstring for the "
        "confirmed real case, Hell's Paradise: TMDB lists it as one "
        "25-episode season, a player's own 'season 2' scrobbles created "
        "orphan Episode rows invisible to the title detail page's episode "
        "browser even though they show up correctly in History). Dry run "
        "by default - pass --commit to actually remap them."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--commit", action="store_true", help="Actually remap the affected episodes (default is a dry run)."
        )

    def handle(self, *args, **options):
        commit = options["commit"]

        # Cheap first pass: only a title with more than one distinct
        # local season number among its own episodes could possibly have
        # anything to reconcile - resolve_episode_season's own early
        # returns (non-anime, season already known to TMDB, TMDB already
        # reports >1 real season) make checking every remaining episode
        # cheap too, so this isn't filtered any further (e.g. by
        # media_type) - a title still classified plain TV at backfill
        # time (reclassify_anime_titles hasn't run yet) would otherwise
        # be silently skipped even though it's exactly the case this
        # command exists to catch once it *does* get reclassified.
        season_counts = (
            Episode.objects.values("title_id")
            .annotate(num_seasons=Count("season", distinct=True))
            .filter(num_seasons__gt=1)
        )
        title_ids = [row["title_id"] for row in season_counts]

        fixes = []
        for title in Title.objects.filter(id__in=title_ids).order_by("id"):
            tmdb_id = title.external_ids.get("tmdb")
            if not tmdb_id:
                continue
            for episode in Episode.objects.filter(title=title).order_by("season", "episode"):
                target_season, target_episode = episode_matching.resolve_episode_season(
                    title, tmdb_id, episode.season, episode.episode
                )
                if (target_season, target_episode) != (episode.season, episode.episode):
                    fixes.append((title, episode, target_season, target_episode))

        if not fixes:
            self.stdout.write(self.style.SUCCESS("No mismatched episode seasons found."))
            return

        self.stdout.write(f"Found {len(fixes)} episode(s) to reconcile:")
        for title, episode, target_season, target_episode in fixes:
            self.stdout.write(
                f'  "{title.name}" ({title.year}) S{episode.season}E{episode.episode} '
                f"-> S{target_season}E{target_episode}"
            )
            if commit:
                with transaction.atomic():
                    _reconcile_episode(title, episode, target_season, target_episode)

        if not commit:
            self.stdout.write(self.style.WARNING("Dry run - nothing changed. Re-run with --commit to apply."))
        else:
            self.stdout.write(self.style.SUCCESS("Done."))


def _reconcile_episode(title, episode, target_season, target_episode):
    """Moves everything pointing at `episode` onto the real
    (target_season, target_episode) row - created first if nothing's
    synced it from TMDB yet - same collision-safe repoint pattern
    merge_duplicate_titles._merge_episodes already uses for a cross-title
    merge, just within one title here. WatchEvent has no uniqueness
    constraint to collide with (unlike a cross-title merge, there's no
    risk of two independently-synced rows landing on the exact same
    profile/episode/watched_at here - these all came from the same
    orphan episode to begin with), so no post-move dedupe pass is
    needed."""
    target, _ = Episode.objects.get_or_create(
        title=title, season=target_season, episode=target_episode,
        defaults={"runtime_minutes": episode.runtime_minutes},
    )

    WatchEvent.objects.filter(episode=episode).update(episode=target)
    WatchProgress.objects.filter(current_episode=episode).update(current_episode=target)

    existing_release_types = set(
        ReleaseSchedule.objects.filter(title=title, episode=target).values_list("release_type", flat=True)
    )
    for release in ReleaseSchedule.objects.filter(episode=episode):
        if release.release_type in existing_release_types:
            release.delete()
        else:
            release.episode = target
            release.save(update_fields=["episode"])

    episode.delete()
