"""Reconciles a player-reported TV/anime (season, episode) against TMDB's
own season structure for the title before an Episode row is created.

Confirmed live: many anime players (Nuvio included) split a show into
their own "seasons"/cours that TMDB doesn't reflect - "Hell's Paradise"
is one real, verified example: TMDB lists it as a single 25-episode
season 1, while a player reporting "season 2, episode 7" for the same
show is really referring to TMDB's season 1, episode 20. Without this,
that scrobble creates an orphan Episode(season=2, episode=7) row that's
invisible to the title detail page's episode browser (entirely TMDB-
season-driven, see views._episode_panel_context) even though it shows
up correctly in History (which reads WatchEvent.episode directly, no
TMDB dependency) - exactly the mismatch this module exists to close.

Shared by tracker/integrations/nuvio.py and tracker/integrations/
scrobble.py's own write paths - the same reconciliation regardless of
which protocol reported the watch, so it isn't duplicated in both."""

from .integrations import tenrai, tmdb
from .models import MediaType


def resolve_episode_season(title, tmdb_id, season, episode_number):
    """Returns (season, episode_number) - unchanged for the overwhelming
    majority of shows, where the reported season is one TMDB already
    knows about. Remapped only when TMDB reports exactly one real season
    for the show (number_of_seasons == 1) and the reported season is
    higher than that - the specific, confirmed case above. A show TMDB
    already lists as multiple real seasons is deliberately left alone
    even when the reported season exceeds TMDB's own count - that's
    most likely a genuinely new season TMDB just hasn't added yet, not
    a split to reconcile, and guessing there would silently misfile it.

    Anime-only: MyAnimeList (tenrai.py) is the only offset source this
    has, and it only covers anime. A non-anime show with the same kind
    of mismatch (rare, but not impossible for some foreign multi-cour
    series) is left as reported rather than guessed at.

    The offset (how many episodes precede the reported virtual season)
    comes from MAL's own Sequel relation chain - each split-cour season
    is typically its own separate MAL entry with an accurate episode
    count, a stable answer independent of how much of this title Spool
    itself has ingested so far. Falls back to leaving season/episode
    exactly as reported - visible only in History, same as today - when
    MAL has no match for the title or its relation chain doesn't reach
    that far; a wrong absolute-episode guess would be worse than an
    orphan row, not better, so this only ever acts on a real answer."""
    if title.media_type != MediaType.ANIME or season is None or episode_number is None or season <= 1:
        return season, episode_number

    tv_details = tmdb.get_tv_details(tmdb_id) if tmdb_id else None
    if not tv_details:
        return season, episode_number
    real_seasons = {s["season_number"] for s in tv_details["seasons"] if s["season_number"] != 0}
    if season in real_seasons or len(real_seasons) != 1:
        return season, episode_number

    mal_id = tenrai.resolve_mal_id(title)
    if mal_id is None:
        return season, episode_number
    offset = tenrai.get_season_episode_offset(mal_id, season)
    if offset is None:
        return season, episode_number

    target_season = next(iter(real_seasons))
    return target_season, offset + episode_number
