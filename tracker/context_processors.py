from django.db.models import OuterRef, Subquery

from . import update_check
from .models import Notification, Profile, WatchEvent
from .version import APP_VERSION


def app_version(request):
    """Exposes the app's VERSION-file string to every template - see
    tracker/version.py for why it's a hand-bumped file rather than a git
    SHA."""
    return {"app_version": APP_VERSION}


def active_profile(request):
    """Exposes the signed-in user's Profile and household size to every
    template — drives the topbar avatar/name and the Activity nav item's
    visibility (spool-product-spec.md §5: hidden entirely, not just empty,
    on single-profile instances), plus the header bell's unread badge and
    the "new version available" banner (Settings & Import + the bell) -
    the latter only computed for owner profiles, same gating the
    Notification itself uses (tasks.check_for_new_version), since a
    household member has no way to act on an upgrade.

    all_profiles carries a last_active_at annotation (most recent
    WatchEvent, one subquery - not a query per profile) for the topbar's
    Friends dropdown; other_profiles is the same queryset with the
    signed-in profile itself excluded, since that dropdown only ever
    lists everyone *else*."""
    profile = None
    unread_notification_count = 0
    latest_available_version = None
    if request.user.is_authenticated:
        profile = Profile.objects.select_related("user").filter(user=request.user).first()
        if profile is not None:
            unread_notification_count = Notification.objects.filter(profile=profile, read=False).count()
            if profile.is_owner:
                latest_available_version = update_check.available_version()
    latest_watch_at = (
        WatchEvent.objects.filter(profile=OuterRef("pk")).order_by("-watched_at").values("watched_at")[:1]
    )
    all_profiles = Profile.objects.annotate(last_active_at=Subquery(latest_watch_at))
    other_profiles = all_profiles.exclude(pk=profile.pk) if profile is not None else all_profiles
    return {
        "active_profile": profile,
        "all_profiles": all_profiles,
        "other_profiles": other_profiles,
        "show_activity_nav": Profile.objects.count() > 1,
        "unread_notification_count": unread_notification_count,
        "latest_available_version": latest_available_version,
        "changelog_url": update_check.CHANGELOG_URL,
    }
