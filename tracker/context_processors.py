from .models import Notification, Profile
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
    on single-profile instances), plus the header bell's unread badge."""
    profile = None
    unread_notification_count = 0
    if request.user.is_authenticated:
        profile = Profile.objects.select_related("user").filter(user=request.user).first()
        if profile is not None:
            unread_notification_count = Notification.objects.filter(profile=profile, read=False).count()
    return {
        "active_profile": profile,
        "all_profiles": Profile.objects.all(),
        "show_activity_nav": Profile.objects.count() > 1,
        "unread_notification_count": unread_notification_count,
    }
