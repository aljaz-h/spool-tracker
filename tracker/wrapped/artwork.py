"""Artwork for the Year in Review and All Time pages: backdrops for the cover
mosaic, the first/last play banners and the top-titles blocks, and a
portrait for the featured person on each people card. Posters already come
with the watch history; backdrops and portraits are looked up on TMDB (the
TMDB layer caches them), a handful of requests per page. Anything that
can't be found is left blank and the page falls back to a gradient or an
initial."""

import logging

from tracker.integrations import tmdb

logger = logging.getLogger(__name__)


def _kind(item_type):
    return "movie" if item_type == "movie" else "tv"


def _backdrop(item_type, tmdb_id):
    if not tmdb_id:
        return ""
    try:
        details = tmdb.get_full_details(_kind(item_type), tmdb_id)
    except Exception:
        logger.warning("Wrapped backdrop lookup failed for %s %s", item_type, tmdb_id)
        return ""
    return (details or {}).get("backdrop_url") or ""


def _enabled():
    """No TMDB key (or a TMDB problem) just means no extra artwork."""
    try:
        return bool(tmdb._api_key())
    except Exception:
        return False


def fill_backdrops(items):
    """Sets .backdrop_url on each item (TitleAgg / PlayMoment) that has a TMDB
    id but no backdrop yet. Looked up once per TMDB title."""
    if not _enabled():
        return
    seen = {}
    for item in items:
        if not item or item.backdrop_url or not item.tmdb_id:
            continue
        key = (_kind(item.type), item.tmdb_id)
        if key not in seen:
            seen[key] = _backdrop(item.type, item.tmdb_id)
        item.backdrop_url = seen[key]


def fill_portraits(people_lists):
    """Gives the first person of each list a photo_url (TMDB person search)."""
    if not _enabled():
        return
    for people in people_lists:
        if not people:
            continue
        try:
            people[0].photo_url = tmdb.get_person_photo(people[0].name)
        except Exception:
            logger.warning("Wrapped portrait lookup failed for %s", people[0].name)


def decorate_review(stats):
    """Everything artwork-shaped the Year in Review needs."""
    titles = list(stats.collage) + [stats.first_play, stats.last_play]
    for section in (stats.shows, stats.movies):
        titles.append(section.top_title)
    fill_backdrops(titles)
    fill_portraits(
        [stats.most_watched_actors, stats.most_watched_actresses, stats.most_watched_directors, stats.most_watched_writers]
    )


def decorate_all_time(stats):
    titles = list(stats.collage)
    for section in (stats.shows, stats.movies):
        titles.extend(section.top_titles[:1])
    fill_backdrops(titles)
    fill_portraits(
        [stats.most_watched_actors, stats.most_watched_actresses, stats.most_watched_directors, stats.most_watched_writers]
    )
