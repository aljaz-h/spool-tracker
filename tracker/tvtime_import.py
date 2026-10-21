"""TV Time import - a CSV produced by the "Liberator" browser extension
(github.com/Hobo-Ware/tv-time-liberator, third-party, not an official TV
Time feature - TV Time itself shut down on 2026-07-15, so this is for
migrating an already-downloaded historical export, not an ongoing sync).

Header: imdb_id,tvdb_id,type,title,season,episode,is_special,is_watched,
watched_at,status,is_watchlisted,rating - one file, three row `type`s
mixed together:

- "movie": is_watched/watched_at/rating as expected. is_watchlisted is
  literally `watched_at == null` in Liberator's own serializer (see its
  src/core/serializer/toCsv.ts) - an unwatched movie IS the watchlist
  signal for a movie row, there's no separate flag.
- "episode": only ever present when is_watched=true - there is no row at
  all for an unwatched episode, so season/episode numbers here are
  always real, precise, and already watched; no bulk-progress-guessing
  problem the way MyAnimeList's cumulative episode count was. `status`
  on an episode row is actually the owning show's own status string,
  redundant with that show's own "show" row - ignored here. Its own
  imdb_id is the EPISODE's own IMDb entry, not the show's - confirmed
  against Liberator's real fixture data (Station Eleven's show row
  carries tt10574236, its S1E1 row carries tt10579918 - two genuinely
  different ids) - passing an episode-level id into title matching
  (which expects a show-level id for TMDB's /find?external_source=
  imdb_id, "tv") would just silently fail to match, so it's never used
  for that; see _show_imdb_ids below for where the real show-level id
  actually comes from.
- "show": one per followed show, independent of watch progress - no
  watch data of its own, just `status` (TV Time's label for the user's
  relationship to the show - informational only, Spool derives its own
  watched state from the episode rows, not this string) and
  is_watchlisted, which here means `hasUnwatchedEpisode` - "still has
  something left to watch", not a separate watchlist flag. A show with
  unwatched episodes goes on Spool's Watchlist; one with none doesn't,
  since there's nothing left to watch. Its own imdb_id IS the show-level
  one every one of that show's episode rows should match against.

imdb_id can be the literal string "-1" when TV Time/TVDB has no real
mapping for that row; treated as no id at all, the same way a blank
imdb_id is. tvdb_id is given on every row too but Spool has no TVDB
integration to match against, so it's ignored entirely.
"""

import csv
import io

from django.utils.dateparse import parse_datetime

from .models import MediaType

_REQUIRED_COLUMNS = {"type", "title", "season", "episode", "is_watched", "watched_at", "is_watchlisted"}


def looks_like_tvtime_export(headers):
    """Whether an uploaded CSV's header row matches TV Time Liberator's
    fixed column set closely enough to be worth trying to parse."""
    lowered = {(h or "").strip().lower() for h in headers}
    return _REQUIRED_COLUMNS <= lowered


def _reader(fileobj):
    text = io.TextIOWrapper(fileobj, encoding="utf-8-sig", newline="")
    return csv.DictReader(text)


def _clean_imdb_id(raw):
    value = (raw or "").strip()
    return value if value and value != "-1" else None


def _bool(raw):
    return (raw or "").strip().lower() == "true"


def _title_key(title):
    return title.strip().lower()


def _show_imdb_ids(rows):
    """title (lowercased) -> that show's own imdb_id, from every "show"
    row that has one - built before any episode row is normalized, so
    it doesn't matter that Liberator's real output orders episode rows
    before the show rows that describe them."""
    ids = {}
    for row in rows:
        if (row.get("type") or "").strip().lower() != "show":
            continue
        title = (row.get("title") or "").strip()
        imdb_id = _clean_imdb_id(row.get("imdb_id"))
        if title and imdb_id:
            ids[_title_key(title)] = imdb_id
    return ids


def parse_csv(fileobj):
    """Returns (normalized, watchlist_entry) - normalized is
    scan_normalized_items-shaped history items built from "movie" and
    "episode" rows; watchlist_entry is a tracker.backup.restore_lists-
    shaped list dict built from "show" rows with hasUnwatchedEpisode
    (and any unwatched "movie" row), or None if nothing qualifies."""
    rows = list(_reader(fileobj))
    show_imdb_ids = _show_imdb_ids(rows)

    normalized = []
    watchlist_items = []
    position = 0

    for i, row in enumerate(rows, start=2):
        row_type = (row.get("type") or "").strip().lower()
        title = (row.get("title") or "").strip()
        imdb_id = _clean_imdb_id(row.get("imdb_id"))
        is_watched = _bool(row.get("is_watched"))
        is_watchlisted = _bool(row.get("is_watchlisted"))
        watched_at_raw = (row.get("watched_at") or "").strip()
        rating_raw = (row.get("rating") or "").strip()

        if not title:
            continue

        rating = None
        if rating_raw:
            try:
                rating = int(float(rating_raw))
            except ValueError:
                rating = None
            if rating is not None and not (1 <= rating <= 10):
                rating = None

        if row_type == "movie":
            if is_watched:
                error = None
                watched_at = None
                if not watched_at_raw:
                    error = "missing watched_at"
                else:
                    watched_at = parse_datetime(watched_at_raw)
                    if watched_at is None:
                        error = f'unrecognized date "{watched_at_raw}"'
                normalized.append(
                    {
                        "media_type": MediaType.MOVIE,
                        "title_name": title,
                        "year": None,
                        "season": None,
                        "episode": None,
                        "watched_at": watched_at,
                        "rating": rating,
                        "source_external_ids": {"imdb": imdb_id} if imdb_id else {},
                        "source_row": i,
                        "error": error,
                    }
                )
            elif is_watchlisted:
                position += 1
                watchlist_items.append(
                    {
                        "type": "movie",
                        "media_type": MediaType.MOVIE,
                        "title": title,
                        "year": None,
                        "ids": {"imdb": imdb_id} if imdb_id else {},
                        "position": position,
                    }
                )

        elif row_type == "episode":
            # Liberator never emits an unwatched episode row - every row
            # that reaches here already has is_watched=true in practice,
            # but the season/episode fields are trusted either way since
            # there's nothing to guess about them.
            season_raw = (row.get("season") or "").strip()
            episode_raw = (row.get("episode") or "").strip()
            error = None
            season = episode = None
            watched_at = None
            if not season_raw.isdigit() or not episode_raw.isdigit():
                error = "missing season/episode number"
            elif not watched_at_raw:
                error = "missing watched_at"
            else:
                season, episode = int(season_raw), int(episode_raw)
                watched_at = parse_datetime(watched_at_raw)
                if watched_at is None:
                    error = f'unrecognized date "{watched_at_raw}"'

            show_imdb_id = show_imdb_ids.get(_title_key(title))
            normalized.append(
                {
                    "media_type": MediaType.TV,
                    "title_name": title,
                    "year": None,
                    "season": season,
                    "episode": episode,
                    "watched_at": watched_at,
                    "rating": rating,
                    "source_external_ids": {"imdb": show_imdb_id} if show_imdb_id else {},
                    "source_row": i,
                    "error": error,
                }
            )

        elif row_type == "show":
            if is_watchlisted:
                position += 1
                watchlist_items.append(
                    {
                        "type": "show",
                        "media_type": MediaType.TV,
                        "title": title,
                        "year": None,
                        "ids": {"imdb": imdb_id} if imdb_id else {},
                        "position": position,
                    }
                )

    watchlist_entry = {"is_watchlist": True, "items": watchlist_items} if watchlist_items else None
    return normalized, watchlist_entry
