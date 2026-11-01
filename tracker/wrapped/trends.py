"""Show Trends / Movie Trends on the Year in Review: the most popular show
and movie that premiered in each month of the year (TMDB), with the ones
this profile has watched ticked. The lists are cached by tmdb.get_popular_
premieres; the matching against watch history is a single query per kind."""

import calendar
import logging

from tracker.integrations import tmdb

logger = logging.getLogger(__name__)

_KINDS = (("shows", "tv", ("tv", "anime")), ("movies", "movie", ("movie",)))


def trends_for(profile, year):
    """{"shows": {...}, "movies": {...}}, each {"items": [{"month", "name",
    "poster_url", "watched"}...], "watched": int, "total": int}. A kind
    TMDB gave nothing for is left out, and any failure just means no Trends
    sections - the rest of the page never depends on them."""
    from tracker.models import WatchEvent

    out = {}
    try:
        api_key = tmdb._api_key()
        for key, media, local_types in _KINDS:
            premieres = tmdb.get_popular_premieres(media, year, api_key=api_key)
            ids = [p["tmdb_id"] for p in premieres if p]
            if not ids:
                continue
            candidates = [str(i) for i in ids] + [int(i) for i in ids]
            watched = {
                str(v)
                for v in WatchEvent.objects.filter(
                    profile=profile, title__media_type__in=local_types, title__external_ids__tmdb__in=candidates
                ).values_list("title__external_ids__tmdb", flat=True)
            }
            items = []
            for month, premiere in enumerate(premieres, start=1):
                if not premiere:
                    continue
                items.append(
                    {
                        "month": calendar.month_abbr[month].upper(),
                        "name": premiere["name"],
                        "poster_url": premiere["poster_url"],
                        "watched": str(premiere["tmdb_id"]) in watched,
                    }
                )
            out[key] = {"items": items, "watched": sum(1 for i in items if i["watched"]), "total": len(items)}
    except Exception:
        logger.exception("Wrapped trends failed for profile %s (%s)", profile.id, year)
        return {}
    return out
