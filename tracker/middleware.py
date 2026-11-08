from datetime import timedelta

from django.shortcuts import redirect
from django.urls import Resolver404, resolve
from django.utils import timezone as django_timezone

from .models import Profile

EXEMPT_URL_NAMES = {"change_credentials", "logout"}


class ForceCredentialChangeMiddleware:
    """Redirects a logged-in user whose Profile.must_change_credentials is
    still True to the change-credentials form for every request except
    that form itself and logout - so the account bootstrap_admin creates
    from ADMIN_USERNAME/ADMIN_PASSWORD (see
    management/commands/bootstrap_admin.py) can't be used for anything
    else on this instance until its real username/password are set."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and not request.path.startswith(("/static/", "/media/")):
            profile = Profile.objects.filter(user=request.user).only("must_change_credentials").first()
            if profile is not None and profile.must_change_credentials:
                try:
                    url_name = resolve(request.path).url_name
                except Resolver404:
                    url_name = None
                if url_name not in EXEMPT_URL_NAMES:
                    return redirect("change_credentials")
        return self.get_response(request)


class ProfileTimezoneMiddleware:
    """Activates the logged-in profile's own Settings → Appearance
    timezone (Profile.timezone) for this request, so every naive `{{ dt }}`
    template render and `date`/`time` filter converts to it instead of the
    server's TIME_ZONE - lets household members in different timezones see
    their own local times without changing anything server-wide. Blank
    Profile.timezone (the default) deactivates back to settings.TIME_ZONE,
    same as if this middleware weren't installed at all."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        tzname = None
        if request.user.is_authenticated:
            profile = Profile.objects.filter(user=request.user).only("timezone").first()
            if profile is not None:
                tzname = profile.timezone
        if tzname:
            django_timezone.activate(tzname)
        else:
            django_timezone.deactivate()
        return self.get_response(request)


class LastSeenMiddleware:
    """Stamps Profile.last_seen_at on every authenticated request - the
    topbar's Friends dropdown "Active X ago" badge (context_processors.active_profile)
    reads this directly, so it reflects actual presence in the app rather
    than the profile's most recent WatchEvent. Throttled to once a minute
    per profile (a plain .update(), not a full save - no signals, no risk
    of clobbering a concurrent request's other field changes) so normal
    browsing doesn't turn into a write on every single page load."""

    STALE_AFTER = timedelta(minutes=1)

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and not request.path.startswith(("/static/", "/media/")):
            profile = Profile.objects.filter(user=request.user).only("last_seen_at").first()
            now = django_timezone.now()
            if profile is not None and (profile.last_seen_at is None or now - profile.last_seen_at > self.STALE_AFTER):
                Profile.objects.filter(pk=profile.pk).update(last_seen_at=now)
        return self.get_response(request)


class HtmxFullPageMiddleware:
    """Makes htmx's boosted navigation (the main nav, see base.html) and its
    history-restore refetch look like ordinary page requests to the views.

    Those requests carry `HX-Request` like any htmx request, but unlike one
    they want the whole page back and swap it into <body>. Several views read
    `HX-Request` to mean "send just the fragment" (History, Calendar, a list's
    items, ...), so without this a boosted click on History would swap a
    fragment into the page. Only these two kinds of request are touched; every
    other htmx request (a lazy block, a filter, a form) is left exactly as it
    is."""

    HTMX_HEADERS = (
        "HTTP_HX_REQUEST", "HTTP_HX_TARGET", "HTTP_HX_TRIGGER", "HTTP_HX_TRIGGER_NAME", "HTTP_HX_CURRENT_URL",
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        meta = request.META
        if meta.get("HTTP_HX_BOOSTED") == "true" or meta.get("HTTP_HX_HISTORY_RESTORE_REQUEST") == "true":
            for key in self.HTMX_HEADERS:
                meta.pop(key, None)
        return self.get_response(request)
