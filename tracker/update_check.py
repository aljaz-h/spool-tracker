"""Checks whether a newer Spool release exists than the one currently
running, so a self-hosted install gets told to upgrade instead of quietly
falling behind. This repo doesn't cut GitHub Releases/tags - the VERSION
file + CHANGELOG.md on master are already the versioning source of truth
everywhere else in this codebase (see tracker/version.py), so that's what
gets checked here too, via raw.githubusercontent.com rather than the
GitHub API - no auth/rate-limit concerns for fetching one small file,
unlike the API's per-IP limits."""

import logging

import requests

from .version import APP_VERSION

logger = logging.getLogger(__name__)

REPO_VERSION_URL = "https://raw.githubusercontent.com/aljaz-h/spool-tracker/master/VERSION"
REPO_URL = "https://github.com/aljaz-h/spool-tracker"
CHANGELOG_URL = f"{REPO_URL}/blob/master/CHANGELOG.md"


def _version_tuple(version_string):
    """"0.22.0" -> (0, 22, 0); a segment that isn't a plain integer (a
    stray pre-release suffix, an empty/malformed VERSION file) falls back
    to 0 rather than raising - a bad comparison should just come out
    "not newer", not break the check."""
    parts = []
    for segment in (version_string or "").strip().split("."):
        try:
            parts.append(int(segment))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def refresh_latest_version():
    """Fetches the repo's current VERSION file; if it's newer than
    APP_VERSION, saves it to InstanceConfig and returns it. Returns None
    on any failure (network error, unreachable, empty/unparseable
    response) or when already caught up - same "never blocks/breaks
    anything" contract every other network-calling module in this
    codebase follows."""
    from .models import InstanceConfig

    try:
        resp = requests.get(REPO_VERSION_URL, timeout=10)
        resp.raise_for_status()
    except requests.RequestException:
        logger.warning("Spool version check failed", exc_info=True)
        return None
    remote_version = resp.text.strip()
    if not remote_version or _version_tuple(remote_version) <= _version_tuple(APP_VERSION):
        return None
    cfg = InstanceConfig.load()
    cfg.latest_known_version = remote_version
    cfg.save(update_fields=["latest_known_version"])
    return remote_version


def available_version():
    """The latest version InstanceConfig has on record from the last
    check, but only while it's still actually newer than what's running
    right now - self-correcting once an upgrade lands, without needing
    the stored value cleared on deploy. None if never checked, the last
    check failed, or already caught up."""
    from .models import InstanceConfig

    latest = InstanceConfig.load().latest_known_version
    if not latest or _version_tuple(latest) <= _version_tuple(APP_VERSION):
        return None
    return latest
