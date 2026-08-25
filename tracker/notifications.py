"""In-app notification generation - no email/push, just Notification rows
a profile sees in the header bell. tracker/tasks.py's generate_release_notifications
periodic task drives the two release-based kinds, and its own
generate_watchlist_stale_notifications task drives the watchlist-nudge
kind; _run_sync's failure path calls notify_sync_failure directly
(event-driven, no periodic scan needed). Kept out of tracker/tasks.py
itself for the same reason completion.py and
rewatches.py are their own modules - the "who's eligible + what does this
dedupe on" logic is substantial enough to want its own tests without
Celery task machinery in the way.
"""

from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from .models import Notification, Profile, ReleaseSchedule, WatchListItem

# How close to "now" a release has to be to notify on it at all - a
# release more than a day past is stale news, one more than three days
# out isn't a reminder yet. sync_release_schedules runs nightly, so a
# release entering either window is caught well before it expires out
# the other side.
NEW_RELEASE_WINDOW = timedelta(days=1)
UPCOMING_RELEASE_WINDOW = timedelta(days=3)


def _profiles_watching(title):
    """Actively watching - has WatchProgress or any watch history for this
    title. Deliberately narrower than "tracking" (see below): a title
    merely sitting on a watchlist isn't something you're watching yet, so
    it shouldn't trigger a "new episode" alert."""
    return Profile.objects.filter(Q(watch_progress__title=title) | Q(watch_events__title=title)).distinct()


def _profiles_tracking(title):
    """Watching (see _profiles_watching) plus anyone with this title on a
    watchlist visible to them - their own list, or literally every
    profile on the instance if it's on any *shared* list, matching
    selectors.calendar_releases()'s own "shared means everyone's
    calendar" semantics, just inverted (per-title, not per-profile)."""
    eligible_ids = set(_profiles_watching(title).values_list("id", flat=True))
    eligible_ids |= set(
        WatchListItem.objects.filter(title=title, watchlist__is_shared=False).values_list(
            "watchlist__profile_id", flat=True
        )
    )
    if WatchListItem.objects.filter(title=title, watchlist__is_shared=True).exists():
        eligible_ids |= set(Profile.objects.values_list("id", flat=True))
    return Profile.objects.filter(id__in=eligible_ids)


def _release_label(release):
    if release.release_type == ReleaseSchedule.ReleaseType.MOVIE_RELEASE or not release.episode:
        return release.title.name
    if release.release_type == ReleaseSchedule.ReleaseType.SEASON_PREMIERE:
        return f"{release.title.name} — Season {release.episode.season} premiere"
    return f"{release.title.name} — S{release.episode.season}E{release.episode.episode}"


def generate_release_notifications(now=None, labels_out=None):
    """Scans ReleaseSchedule for anything landing in either window and
    creates the matching Notification per eligible profile with that
    source enabled - get_or_create on (profile, kind, release_schedule)
    means re-running this (nightly, alongside sync_release_schedules) is
    idempotent; a release already notified on doesn't notify again.
    Returns the count of newly created rows. labels_out (optional list) -
    same contract as trakt.py/simkl.py/csv_import.py's own labels_out
    param: append a human-readable label for each notification actually
    created, so tasks.generate_release_notifications can log what fired
    to DataLog - skipped entirely (None default) when the caller doesn't
    need it, same as every other labels_out caller."""
    now = now or timezone.now()
    created = 0
    releases = ReleaseSchedule.objects.filter(
        release_date__gte=now - NEW_RELEASE_WINDOW, release_date__lte=now + UPCOMING_RELEASE_WINDOW
    ).select_related("title", "episode")
    for release in releases:
        label = _release_label(release)
        if release.release_date <= now:
            profiles = _profiles_watching(release.title).filter(notify_new_releases=True)
            message = f"Now available: {label}"
            kind = Notification.Kind.NEW_RELEASE
        else:
            # Portable day-of-month formatting - strftime's no-leading-zero
            # flag is spelled differently on Windows (%#d) than everywhere
            # else (%-d), so this composes it by hand instead.
            local_date = timezone.localtime(release.release_date)
            when = f"{local_date.strftime('%b')} {local_date.day}"
            profiles = _profiles_tracking(release.title).filter(notify_upcoming_releases=True)
            message = f"Coming {when}: {label}"
            kind = Notification.Kind.UPCOMING_RELEASE
        for profile in profiles:
            _, made = Notification.objects.get_or_create(
                profile=profile,
                kind=kind,
                release_schedule=release,
                defaults={"title": release.title, "message": message},
            )
            created += made
            if made and labels_out is not None:
                labels_out.append(f"{label} ({profile.display_name})")
    return created


# How long an item sits untouched on the default Watchlist before it's
# eligible for a "still interested?" nudge, and the minimum gap between
# repeat nudges for the same title - long enough that a genuinely-parked
# item doesn't get nagged every night once it clears the age bar, but
# short enough that a years-old item gets a nudge more than once.
WATCHLIST_STALE_AGE = timedelta(days=180)
WATCHLIST_STALE_COOLDOWN = timedelta(days=180)


def generate_watchlist_stale_notifications(now=None, labels_out=None):
    """Nightly beat job (see bootstrap_periodic_tasks.py) - scans every
    profile's default Watchlist (WatchList.is_watchlist=True) for items
    older than WATCHLIST_STALE_AGE and notifies their owner, skipping any
    title already nudged within WATCHLIST_STALE_COOLDOWN. No unique
    constraint to lean on here (unlike the release-based kinds, there's no
    per-notification FK to dedupe against - see the Notification.Kind
    docstring), so this checks directly with a created_at window instead.
    Watching a title removes it from the default Watchlist (see
    completion.py), so anything this finds is still genuinely unwatched.
    Returns the count of newly created rows. labels_out - see
    generate_release_notifications's own docstring for the contract."""
    now = now or timezone.now()
    cooldown_start = now - WATCHLIST_STALE_COOLDOWN
    created = 0
    items = WatchListItem.objects.filter(
        watchlist__is_watchlist=True, added_at__lte=now - WATCHLIST_STALE_AGE
    ).select_related("watchlist__profile", "title")
    for item in items:
        profile = item.watchlist.profile
        already_nudged = Notification.objects.filter(
            profile=profile,
            kind=Notification.Kind.WATCHLIST_STALE,
            title=item.title,
            created_at__gte=cooldown_start,
        ).exists()
        if already_nudged:
            continue
        days = (now - item.added_at).days
        Notification.objects.create(
            profile=profile,
            kind=Notification.Kind.WATCHLIST_STALE,
            title=item.title,
            message=f'"{item.title.name}" has been on your watchlist for {days} days - still interested?',
        )
        created += 1
        if labels_out is not None:
            labels_out.append(f"{item.title.name} ({profile.display_name})")
    return created


def notify_sync_failure(profile, provider, error_message):
    """Called from tasks._run_sync's except block - event-driven, not
    part of the periodic scan above, since a failure is already a single
    concrete event with nothing to dedupe against. No-op (returns None)
    when the profile has this source turned off."""
    if not profile.notify_sync_failures:
        return None
    return Notification.objects.create(
        profile=profile,
        kind=Notification.Kind.SYNC_FAILED,
        message=f"{provider.title()} sync failed: {error_message[:200]}",
    )
