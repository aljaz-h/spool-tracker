"""AniFiller (https://github.com/AniraTeam/AniFiller) - a small,
community-curated static dataset of canon/filler episode classifications
for ~180 long-running, manga-adapted anime. Used purely as a fallback for
jikan.py's own filler/recap lookup (see views._apply_anime_filler_flags/
_resolve_mal_id) when Jikan has nothing usable for a title - either
because MAL's own community tagging never covered it, or because Jikan's
search endpoint is having one of its occasional outages (see jikan.py's
own docstring; observed live while investigating this).

Unlike jikan.py, this isn't a live query API - it's a single JSON bundle
published as a GitHub Release asset, fetched whole and cached, then
looked up in memory. Coverage is far narrower than Jikan's in show count
(~180 curated titles vs. whatever MAL has tagged for any anime), but
those 180 are exactly the long-running, source-material-adapted shows
filler is actually a real phenomenon for (Naruto, Bleach, One Piece,
Fairy Tail, Detective Conan, Gintama, the Dragon Ball franchise, ...), so
real-world coverage for this feature is better than the show count alone
suggests.

Deliberately never treated as authoritative on its own - Jikan is tried
first for both MAL id resolution and per-episode filler/recap flags; this
only fills in whatever Jikan came back without. The two sources can
disagree (confirmed live: Black Clover episode 66 is tagged "recap" by
Jikan/MAL but "filler" by AniFiller) - this module never overrides a
Jikan-provided answer, only supplies one where Jikan had none. It also
has no separate "recap" category (just manga-canon/anime-canon/
mixed-manga/filler), so it can only ever contribute a Filler badge, never
a Recap one - that distinction still comes from Jikan alone."""

import logging

import requests

logger = logging.getLogger(__name__)

BUNDLE_URL = "https://github.com/AniraTeam/AniFiller/releases/latest/download/anifiller.min.json"

# A week - same staleness tolerance as jikan.py's own filler-map cache
# (aired episodes' classification doesn't change, and this whole bundle
# is a single static release asset anyway).
_BUNDLE_TTL = 7 * 24 * 3600
_CACHE_KEY = "anifiller:bundle"


def _bundle():
    """The raw list of shows from the latest AniFiller release, cached
    whole - fetched at most once a week regardless of how many titles
    are looked up against it. [] on any fetch failure (never None, so
    callers can iterate it unconditionally)."""
    from django.core.cache import cache

    try:
        shows = cache.get(_CACHE_KEY)
    except Exception:
        logger.warning("AniFiller bundle cache read failed, continuing without cache", exc_info=True)
        shows = None
    if shows is not None:
        return shows

    try:
        resp = requests.get(BUNDLE_URL, timeout=15)
        resp.raise_for_status()
        shows = resp.json()
    except (requests.RequestException, ValueError):
        logger.warning("AniFiller bundle fetch failed", exc_info=True)
        return []

    try:
        cache.set(_CACHE_KEY, shows, _BUNDLE_TTL)
    except Exception:
        logger.warning("AniFiller bundle cache write failed, continuing without cache", exc_info=True)
    return shows


def find_mal_id_by_name(name):
    """Case-insensitive exact match against AniFiller's own show titles -
    only ever consulted when Jikan's own (fuzzy, live-searched) match
    fails, so this deliberately doesn't attempt any fuzzy matching of its
    own: an exact-title miss just means no fallback id, not a wrong one."""
    name_lower = name.strip().lower()
    for show in _bundle():
        if (show.get("title") or "").strip().lower() == name_lower:
            mal_id = (show.get("mappings") or {}).get("mal_id")
            if mal_id is not None:
                return mal_id
    return None


def get_episode_types(mal_id):
    """Returns {episode_number: "manga-canon"|"filler"|"mixed-manga"|
    "anime-canon"} for this MAL id, or {} if this show isn't one of the
    ones AniFiller covers (true for most anime, which are filler-free to
    begin with)."""
    for show in _bundle():
        if (show.get("mappings") or {}).get("mal_id") == mal_id:
            return {ep["episode"]: ep["type"] for ep in show.get("episodes") or [] if ep.get("episode") is not None}
    return {}
