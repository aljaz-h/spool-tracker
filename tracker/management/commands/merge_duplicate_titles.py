from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction

from tracker.models import (
    Episode,
    ExternalRating,
    Notification,
    Recommendation,
    ReleaseSchedule,
    Title,
    WatchEvent,
    WatchListItem,
    WatchProgress,
)


class Command(BaseCommand):
    help = (
        "Finds Title rows that share the same media_type and the same "
        "external_ids['tmdb'] value and merges them into one. Each of "
        "trakt.py/simkl.py/nuvio.py's own _get_or_create_title used to skip "
        "checking for an already-tracked title with the same TMDB id before "
        "creating a new one - so a movie/show already synced through one "
        "provider got a second, duplicate Title (with its own WatchEvent) "
        "the first time a *different* provider synced it too. That gap is "
        "fixed, but titles it already duplicated need merging by hand. "
        "Dry run by default - pass --commit to actually merge and delete "
        "the duplicates."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--commit", action="store_true", help="Actually merge and delete duplicates (default is a dry run)."
        )

    def handle(self, *args, **options):
        commit = options["commit"]
        groups = defaultdict(list)
        for title in Title.objects.order_by("id"):
            tmdb_id = title.external_ids.get("tmdb")
            if tmdb_id:
                groups[(title.media_type, tmdb_id)].append(title)

        duplicate_groups = {key: titles for key, titles in groups.items() if len(titles) > 1}
        if not duplicate_groups:
            self.stdout.write(self.style.SUCCESS("No duplicate titles found."))
            return

        self.stdout.write(f"Found {len(duplicate_groups)} title(s) with duplicates:")
        for (_media_type, tmdb_id), titles in duplicate_groups.items():
            canonical, *dupes = titles  # already ordered by id - oldest wins
            self.stdout.write(
                f'  "{canonical.name}" ({canonical.year}) [tmdb:{tmdb_id}] - '
                f"keeping #{canonical.id}, merging {[d.id for d in dupes]}"
            )
            if commit:
                with transaction.atomic():
                    for dupe in dupes:
                        _merge_title(canonical, dupe)

        if not commit:
            self.stdout.write(self.style.WARNING("Dry run - nothing changed. Re-run with --commit to merge."))
        else:
            self.stdout.write(self.style.SUCCESS("Done."))


def _merge_title(canonical, dupe):
    """Moves every row pointing at dupe over to canonical, resolving the
    unique constraints each of those models carries (two Titles that
    should've been one can easily each have their own WatchProgress row
    for the same profile, etc.) rather than letting the reassignment
    crash on the first collision. dupe is deleted once nothing points at
    it anymore."""
    _merge_episodes(canonical, dupe)

    # Movie-level WatchEvents (episode-level ones were already repointed
    # by _merge_episodes). Two providers can easily have logged the exact
    # same watch independently - dedupe after reassigning rather than
    # trying to detect the collision up front.
    WatchEvent.objects.filter(title=dupe).update(title=canonical)
    _dedupe_watch_events(canonical)

    canonical_rating_sources = set(ExternalRating.objects.filter(title=canonical).values_list("source", flat=True))
    for rating in ExternalRating.objects.filter(title=dupe):
        if rating.source in canonical_rating_sources:
            rating.delete()
        else:
            rating.title = canonical
            rating.save(update_fields=["title"])

    canonical_progress_by_profile = {p.profile_id: p for p in WatchProgress.objects.filter(title=canonical)}
    for progress in WatchProgress.objects.filter(title=dupe):
        existing = canonical_progress_by_profile.get(progress.profile_id)
        if existing is None:
            progress.title = canonical
            progress.save(update_fields=["title"])
        elif progress.updated_at > existing.updated_at:
            existing.delete()
            progress.title = canonical
            progress.save(update_fields=["title"])
        else:
            progress.delete()

    canonical_watchlist_ids = set(WatchListItem.objects.filter(title=canonical).values_list("watchlist_id", flat=True))
    for item in WatchListItem.objects.filter(title=dupe):
        if item.watchlist_id in canonical_watchlist_ids:
            item.delete()
        else:
            item.title = canonical
            item.save(update_fields=["title"])

    # Movie-level ReleaseSchedule rows only - episode-level ones were
    # already repointed (or deduped) by _merge_episodes.
    canonical_release_keys = set(
        ReleaseSchedule.objects.filter(title=canonical, episode__isnull=True).values_list("release_type", flat=True)
    )
    for release in ReleaseSchedule.objects.filter(title=dupe, episode__isnull=True):
        if release.release_type in canonical_release_keys:
            release.delete()
        else:
            release.title = canonical
            release.save(update_fields=["title"])

    # No title-scoped unique constraint on Notification - safe to move directly.
    Notification.objects.filter(title=dupe).update(title=canonical)

    canonical_pending_pairs = set(
        Recommendation.objects.filter(title=canonical, status=Recommendation.Status.PENDING).values_list(
            "from_profile_id", "to_profile_id"
        )
    )
    for rec in Recommendation.objects.filter(title=dupe):
        pair = (rec.from_profile_id, rec.to_profile_id)
        if rec.status == Recommendation.Status.PENDING and pair in canonical_pending_pairs:
            rec.delete()
        else:
            rec.title = canonical
            rec.save(update_fields=["title"])

    # dupe's own external_ids (its own provider key/id, and possibly a
    # duplicate "tmdb"/"tmdb_kind" already equal to canonical's) fill in
    # anything canonical doesn't already have - canonical wins on overlap
    # since it's the older, presumably more-linked-to row.
    merged_ids = {**dupe.external_ids, **canonical.external_ids}
    update_fields = []
    if merged_ids != canonical.external_ids:
        canonical.external_ids = merged_ids
        update_fields.append("external_ids")
    if not canonical.poster_url and dupe.poster_url:
        canonical.poster_url = dupe.poster_url
        update_fields.append("poster_url")
    if update_fields:
        canonical.save(update_fields=update_fields)

    dupe.delete()


def _merge_episodes(canonical, dupe):
    canonical_episodes = {(e.season, e.episode): e for e in Episode.objects.filter(title=canonical)}
    for episode in list(Episode.objects.filter(title=dupe)):
        existing = canonical_episodes.get((episode.season, episode.episode))
        if existing is None:
            episode.title = canonical
            episode.save(update_fields=["title"])
            canonical_episodes[(episode.season, episode.episode)] = episode
            continue
        WatchEvent.objects.filter(episode=episode).update(title=canonical, episode=existing)
        WatchProgress.objects.filter(current_episode=episode).update(current_episode=existing)
        ReleaseSchedule.objects.filter(episode=episode).update(title=canonical, episode=existing)
        episode.delete()


def _dedupe_watch_events(title):
    seen = set()
    for event in WatchEvent.objects.filter(title=title).order_by("id"):
        key = (event.profile_id, event.episode_id, event.watched_at)
        if key in seen:
            event.delete()
        else:
            seen.add(key)
