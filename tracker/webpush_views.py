"""The browser/app half of push (tracker/web_push.py): subscribe this device,
unsubscribe it, send it a test, list and remove devices. Everything is JSON
from the Settings > Notifications card (static/js/web-push.js) except the
device list, which is an HTML fragment the card refreshes."""

import json

from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from . import web_push
from .models import Profile, PushSubscription

MAX_BODY_BYTES = 8192


def _me(request):
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None:
        raise Http404
    return profile


def _json_body(request):
    if len(request.body) > MAX_BODY_BYTES:
        return None
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _error(message, status=400):
    return JsonResponse({"ok": False, "error": message}, status=status)


@login_required
@require_GET
def push_key(request):
    """The public key this device subscribes against. Made on first use, so
    opening Settings never writes anything - only turning push on does."""
    return JsonResponse({"key": web_push.public_key()})


@login_required
@require_POST
def push_subscribe(request):
    """Saves this device's subscription for the signed-in profile. The same
    browser signing in as someone else moves the row to them."""
    me = _me(request)
    data = _json_body(request)
    if data is None:
        return _error("That request wasn't valid.")
    keys = data.get("keys") if isinstance(data.get("keys"), dict) else {}
    endpoint, p256dh, auth = data.get("endpoint"), keys.get("p256dh"), keys.get("auth")
    if not (isinstance(p256dh, str) and isinstance(auth, str) and 0 < len(p256dh) <= 128 and 0 < len(auth) <= 64):
        return _error("That device didn't send valid push keys.")
    if not web_push.endpoint_allowed(endpoint):
        return _error("That browser's push service isn't supported.")
    existing = PushSubscription.objects.filter(endpoint=endpoint).first()
    if existing is None and me.push_subscriptions.count() >= PushSubscription.MAX_PER_PROFILE:
        return _error(f"You can have up to {PushSubscription.MAX_PER_PROFILE} devices. Remove one first.", 409)
    PushSubscription.objects.update_or_create(
        endpoint=endpoint,
        defaults={
            "profile": me,
            "p256dh": p256dh,
            "auth": auth,
            "user_agent": request.META.get("HTTP_USER_AGENT", "")[:300],
        },
    )
    return JsonResponse({"ok": True})


@login_required
@require_POST
def push_status(request):
    """Whether this browser's subscription belongs to the signed-in profile.
    A browser can still hold a subscription from someone who used it before;
    that one doesn't count as "on" for this profile."""
    data = _json_body(request)
    if data is None or not isinstance(data.get("endpoint"), str):
        return _error("That request wasn't valid.")
    return JsonResponse({"ok": True, "subscribed": _me(request).push_subscriptions.filter(endpoint=data["endpoint"]).exists()})


@login_required
@require_POST
def push_unsubscribe(request):
    """Removes this device's subscription - only the signed-in profile's own."""
    me = _me(request)
    data = _json_body(request)
    if data is None or not isinstance(data.get("endpoint"), str):
        return _error("That request wasn't valid.")
    me.push_subscriptions.filter(endpoint=data["endpoint"]).delete()
    return JsonResponse({"ok": True})


@login_required
@require_POST
def push_test(request):
    """Sends a test notification to every device the profile subscribed."""
    me = _me(request)
    if not me.push_subscriptions.exists():
        return _error("Turn on notifications for this device first.", 409)
    sent = web_push.send_to_profile(
        me, "Spool test notification", "If you can see this, push notifications are working on this device.", url="/", tag="spool-test"
    )
    return JsonResponse({"ok": True, "sent": sent})


@login_required
@require_GET
def push_devices(request):
    """The device list fragment the card swaps in after a change."""
    return render(request, "tracker/partials/push_devices.html", {"devices": _me(request).push_subscriptions.all()})


@login_required
@require_POST
def push_device_remove(request, pk):
    me = _me(request)
    me.push_subscriptions.filter(pk=pk).delete()
    return render(request, "tracker/partials/push_devices.html", {"devices": me.push_subscriptions.all()})
