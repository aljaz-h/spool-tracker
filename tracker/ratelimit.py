"""Fixed-window rate limiting backed by Django's cache (Redis in
production - see settings.CACHES) for the auth endpoints Django doesn't
protect by default: login and password/credential changes. Not a
general-purpose throttling framework, just enough to blunt credential-
stuffing/brute-force against a self-hosted instance without a new
dependency - the cache backend this needs is already a hard requirement
(Celery/discover-cache both depend on Redis being up)."""

import logging

from django.core.cache import cache

logger = logging.getLogger(__name__)


def _client_ip(request):
    # XFF's first entry is only trustworthy behind a reverse proxy that
    # overwrites (not appends to) this header before forwarding - same
    # trust boundary settings.py's SECURE_PROXY_SSL_HEADER comment
    # documents for X-Forwarded-Proto. Self-hosted without a proxy in
    # front, REMOTE_ADDR is already correct and this branch is unused.
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def is_rate_limited(request, key, limit, window_seconds):
    """key identifies *what* is being limited (e.g. "login") so different
    endpoints don't share a budget - combined with the client IP.
    Increments a per-IP counter on every call and returns True once it
    exceeds limit within window_seconds. Fixed-window rather than
    sliding/token-bucket: a burst straddling the window boundary can let
    through up to ~2x limit in the worst case, an accepted tradeoff for
    staying a single cache.incr() call instead of a sorted-set/Lua
    script - this only needs to blunt automated brute-force, not provide
    precise quota accounting.

    A down/unreachable cache backend degrades to "not rate limited" for
    this request rather than failing the whole login/password-change
    attempt - same reasoning as tmdb.py's own cache.get/set wrapping:
    losing brute-force protection during a cache outage is better than
    losing the ability to log in at all."""
    cache_key = f"ratelimit:{key}:{_client_ip(request)}"
    try:
        try:
            count = cache.incr(cache_key)
        except ValueError:
            # incr() raises when the key doesn't exist yet (nothing to
            # increment) - not a cache miss/expiry to be handled quietly
            # and skipped, so this is the normal "first hit in a new
            # window" path.
            cache.set(cache_key, 1, timeout=window_seconds)
            count = 1
    except Exception:
        logger.warning("Rate-limit cache access failed, allowing the request through", exc_info=True)
        return False
    return count > limit
