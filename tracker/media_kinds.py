"""Movie or show? - for an imported row.

Spool has three media types (movie/tv/anime) but TMDB has two kinds
(movie/tv), and anime can be either: a series (TMDB tv, watched by
episode) or a film (TMDB movie, watched once - Your Name, Akira). An
imported anime row says which it is either explicitly (a Spool backup's
or Trakt-JSON export's ids.tmdb_kind) or implicitly - an anime row with
no season/episode is an anime film, which is exactly how Spool's own CSV
export writes one.
"""

from tracker.models import MediaType

SHOW_ROW_ERROR = "TV/anime rows need a season and episode number"


def tmdb_kind_for(media_type, season=None, episode=None, explicit=None):
    """"movie" or "tv" - the TMDB kind this row's title should match/be
    created as."""
    if media_type == MediaType.MOVIE:
        return "movie"
    if media_type == MediaType.ANIME:
        if explicit in ("movie", "tv"):
            return explicit
        return "movie" if season is None and episode is None else "tv"
    return "tv"


def is_episodic(media_type, season=None, episode=None, explicit=None):
    """Whether this row is an episode watch (needs an Episode) rather
    than a single watch of a film."""
    return tmdb_kind_for(media_type, season, episode, explicit) == "tv"


def row_error(media_type, season=None, episode=None, explicit=None):
    """The import error for a row whose shape doesn't fit its kind, or None."""
    if is_episodic(media_type, season, episode, explicit) and (season is None or episode is None):
        return SHOW_ROW_ERROR
    return None
