"""Web push: the browser/installed-app half of Spool's push notifications.

Apprise (tracker/push.py) mirrors notifications to Discord, Telegram and the
like; this module sends the same notifications straight to a phone or
computer that subscribed from Settings > Notifications, through the browser
vendor's push service (the Web Push protocol with VAPID, via pywebpush).
The two are independent channels behind the same Push tick-boxes.

Keys: the server signs every push with a VAPID key pair. It is made on first
use and kept on InstanceConfig (private key encrypted); WEB_PUSH_VAPID_PRIVATE_KEY
in the environment overrides it. Changing the key makes every existing
subscription useless (a browser subscribes against one public key), which is
why the value is generated once and then left alone.

The endpoint is the one thing here a client controls, and the server POSTs to
it, so it is restricted to the browser vendors' own push hosts over https
(the same server-side-request-forgery reasoning as tracker/push.py's scheme
allowlist). It is checked when someone subscribes and again at send time, and
redirects are never followed.
"""

import base64
import json
import logging
from urllib.parse import urlsplit

import requests
from cryptography.hazmat.primitives import serialization
from django.conf import settings
from django.core.cache import cache
from django.templatetags.static import static
from django.utils import timezone
from py_vapid import Vapid

from . import crypto

logger = logging.getLogger(__name__)

# Exact hosts and host suffixes of the push services browsers hand out:
# Chrome/Edge-on-Android/Opera (FCM), Firefox (autopush), Edge (WNS), Safari
# and iOS home-screen apps (Apple).
_EXACT_HOSTS = {"fcm.googleapis.com", "android.googleapis.com", "updates.push.services.mozilla.com", "web.push.apple.com"}
_HOST_SUFFIXES = (".push.services.mozilla.com", ".notify.windows.com", ".push.apple.com")

TITLE_LIMIT = 100
BODY_LIMIT = 300  # a push payload is capped near 4 KB, this stays far under it
TTL_SECONDS = 24 * 60 * 60
SEND_TIMEOUT = 10


def endpoint_allowed(endpoint):
    """True for an https URL on a known push service, default port, no
    credentials in it."""
    if not isinstance(endpoint, str) or len(endpoint) > 1000:
        return False
    try:
        parts = urlsplit(endpoint)
        port = parts.port
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or parts.username or parts.password or port not in (None, 443) or not host:
        return False
    return host in _EXACT_HOSTS or host.endswith(_HOST_SUFFIXES)


def _b64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _public_key_of(vapid):
    return _b64url(vapid.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))


def _load_private(text):
    """A Vapid from a PEM, a base64 DER or a raw base64url key."""
    text = text.strip().replace("\\n", "\n")
    if "BEGIN" in text:
        return Vapid.from_pem(text.encode())
    try:
        return Vapid.from_string(text)
    except Exception:
        return Vapid.from_raw(text.encode())


def _config():
    """InstanceConfig with a key pair, making one if there is none yet. The
    web workers and the Celery worker can all get here at the same moment, so
    the write is conditional on the field still being empty (the first one
    wins) and the row is read back, so everyone ends up with the same pair."""
    from .models import InstanceConfig

    config = InstanceConfig.load()
    if config.vapid_public_key and config.encrypted_vapid_private_key:
        return config
    vapid = Vapid()
    vapid.generate_keys()
    InstanceConfig.objects.get_or_create(pk=1)
    InstanceConfig.objects.filter(pk=1, vapid_public_key="").update(
        vapid_public_key=_public_key_of(vapid),
        encrypted_vapid_private_key=crypto.encrypt(vapid.private_pem().decode()),
    )
    try:
        cache.delete(InstanceConfig._CACHE_KEY)  # .update() skips save(), which is what clears it
    except Exception:
        logger.warning("InstanceConfig cache clear failed", exc_info=True)
    return InstanceConfig.objects.get(pk=1)


def _vapid():
    env_key = getattr(settings, "WEB_PUSH_VAPID_PRIVATE_KEY", "")
    if env_key:
        return _load_private(env_key)
    return _load_private(crypto.decrypt(_config().encrypted_vapid_private_key))


def public_key():
    """The application server key a browser subscribes with (raw uncompressed
    P-256 point, base64url, no padding)."""
    if getattr(settings, "WEB_PUSH_VAPID_PRIVATE_KEY", ""):
        return _public_key_of(_vapid())
    return _config().vapid_public_key


def subject():
    """The VAPID `sub` claim: a contact for the push service. Apple's is
    picky about it, so it is the instance's own https address rather than a
    placeholder."""
    configured = getattr(settings, "WEB_PUSH_SUBJECT", "")
    if configured:
        return configured
    for origin in settings.CSRF_TRUSTED_ORIGINS:
        if origin.startswith("https://") and "*" not in origin:
            return origin
    for host in settings.ALLOWED_HOSTS:
        if host and host not in ("localhost", "127.0.0.1", "[::1]") and not host.startswith(".") and "*" not in host:
            return f"https://{host}"
    return "https://localhost"


def safe_click_url(url):
    """Only a path on this site: the push opens it when tapped."""
    if isinstance(url, str) and url.startswith("/") and not url.startswith("//") and "\\" not in url:
        return url
    return "/"


def _clip(text, limit):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def build_payload(title, body, url=None, tag=None):
    payload = {
        "title": _clip(title, TITLE_LIMIT) or "Spool",
        "body": _clip(body, BODY_LIMIT),
        "url": safe_click_url(url),
        "icon": static("img/pwa-icon-192.png"),
    }
    if tag:
        payload["tag"] = str(tag)[:60]
    return payload


def _session():
    session = requests.Session()
    session.max_redirects = 0  # a redirect is an error, never followed
    return session


def send_to_profile(profile, title, body, url=None, tag=None):
    """Sends one notification to every device the profile subscribed. Returns
    how many accepted it. A device whose push service says it is gone (404 or
    410) is removed; any other failure is logged and skipped, so one dead
    device never blocks the rest and the bell row is there either way."""
    from pywebpush import WebPushException, webpush

    subscriptions = list(profile.push_subscriptions.all())
    if not subscriptions:
        return 0
    data = json.dumps(build_payload(title, body, url, tag))
    vapid = _vapid()
    claims_sub = subject()
    session = _session()
    delivered = 0
    for sub in subscriptions:
        if not endpoint_allowed(sub.endpoint):
            sub.delete()
            continue
        try:
            webpush(
                {"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}},
                data,
                vapid_private_key=vapid,
                vapid_claims={"sub": claims_sub},
                ttl=TTL_SECONDS,
                timeout=SEND_TIMEOUT,
                requests_session=session,
            )
        except WebPushException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (404, 410):
                sub.delete()
            else:
                logger.warning("web push to subscription %s failed (status %s)", sub.pk, status)
            continue
        except Exception:
            logger.warning("web push to subscription %s failed", sub.pk, exc_info=True)
            continue
        delivered += 1
        sub.last_used_at = timezone.now()
        sub.save(update_fields=["last_used_at"])
    return delivered
