"""Anime lists from AniList, Kitsu and a MyAnimeList-format XML export.

None of these needs the server owner to register an app: AniList and Kitsu
publish a person's anime list by username (a public profile - a private list
answers "not found"/"private" and says so), and the XML is a file the person
uploads. Each reader returns the same entry shape MyAnimeList's own API
does, so tracker/integrations/myanimelist.normalize_items - the one place
that turns an anime list into Spool history (matching on TMDB, walking a
show's episodes for "watched N") and a watchlist - handles all four sources:

    {"node": {"id": <int>, "provider": "mal" | "anilist" | "kitsu",
              "title": str, "start_season": {"year": int} | None},
     "list_status": {"status": "watching" | "completed" | "on_hold" | "dropped"
                                | "plan_to_watch",
                     "score": 0-10, "num_episodes_watched": int,
                     "updated_at": ISO str | None, "start_date": "YYYY-MM-DD" | None,
                     "finish_date": "YYYY-MM-DD" | None}}

node.provider says which id node.id is. AniList and Kitsu both know an
anime's MyAnimeList id for most titles, and that id is preferred (it is what
the rest of Spool - filler flags, MAL scores, an earlier MAL connection -
keys on); a title without one falls back to the source's own id.

Like every integration here, nothing in this module has been exercised
against the live services from this environment: the request and response
shapes follow AniList's GraphQL schema and Kitsu's JSON:API documentation.
"""

import gzip
import io
import re
from datetime import datetime, timezone as dt_timezone

import requests
from defusedxml import ElementTree as SafeET
from defusedxml.common import DefusedXmlException

ANILIST_URL = "https://graphql.anilist.co"
KITSU_BASE = "https://kitsu.app/api/edge"
USER_AGENT = "Spool/1.0 (+https://github.com/aljaz-h/spool-tracker)"
TIMEOUT = 20
# A guard on how far a list is followed, not a limit anyone should reach: a
# huge list is a few thousand entries (500 per request).
MAX_PAGES = 100
# A MyAnimeList export is a few MB at most; anything much bigger is not one.
MAX_XML_BYTES = 50 * 1024 * 1024

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.\- ]{1,100}$")


class AnimeListError(Exception):
    """A problem worth showing the person as it is written (user not found,
    private list, the service being down, a file that is not a list)."""


def clean_username(raw):
    name = (raw or "").strip().lstrip("@")
    if not USERNAME_RE.match(name):
        raise AnimeListError("That doesn't look like a username - use the name from your profile address.")
    return name


# --------------------------------------------------------------------- AniList

_ANILIST_QUERY = """
query ($userName: String, $chunk: Int, $perChunk: Int) {
  MediaListCollection(userName: $userName, type: ANIME, chunk: $chunk, perChunk: $perChunk) {
    hasNextChunk
    lists {
      entries {
        status
        progress
        score(format: POINT_10)
        updatedAt
        startedAt { year month day }
        completedAt { year month day }
        media { id idMal seasonYear startDate { year } title { romaji english } }
      }
    }
  }
}
"""
_ANILIST_STATUS = {
    "CURRENT": "watching",
    "REPEATING": "watching",
    "COMPLETED": "completed",
    "PAUSED": "on_hold",
    "DROPPED": "dropped",
    "PLANNING": "plan_to_watch",
}


def _fuzzy_date(parts):
    """AniList's {year, month, day} (any of them null) as YYYY-MM-DD, or
    None unless it is a whole date."""
    if not parts or not (parts.get("year") and parts.get("month") and parts.get("day")):
        return None
    return f"{parts['year']:04d}-{parts['month']:02d}-{parts['day']:02d}"


def _anilist_entry(raw):
    media = raw.get("media") or {}
    status = _ANILIST_STATUS.get(raw.get("status"))
    if not media.get("id") or status is None:
        return None
    title = media.get("title") or {}
    node_id, provider = (media["idMal"], "mal") if media.get("idMal") else (media["id"], "anilist")
    year = media.get("seasonYear") or (media.get("startDate") or {}).get("year")
    updated = raw.get("updatedAt")
    return {
        "node": {
            "id": node_id,
            "provider": provider,
            "title": title.get("english") or title.get("romaji") or "",
            "start_season": {"year": year} if year else None,
        },
        "list_status": {
            "status": status,
            "score": int(raw.get("score") or 0),
            "num_episodes_watched": int(raw.get("progress") or 0),
            "updated_at": datetime.fromtimestamp(updated, dt_timezone.utc).isoformat() if updated else None,
            "start_date": _fuzzy_date(raw.get("startedAt")),
            "finish_date": _fuzzy_date(raw.get("completedAt")),
        },
    }


def _anilist_page(username, chunk, per_chunk):
    try:
        resp = requests.post(
            ANILIST_URL,
            json={"query": _ANILIST_QUERY, "variables": {"userName": username, "chunk": chunk, "perChunk": per_chunk}},
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=TIMEOUT,
        )
    except requests.RequestException:
        raise AnimeListError("Couldn't reach AniList - try again in a moment.") from None
    if resp.status_code == 429:
        raise AnimeListError("AniList is rate-limiting requests right now - try again in a minute.")
    try:
        body = resp.json()
    except ValueError:
        raise AnimeListError("AniList sent back something unexpected - try again later.") from None
    errors = body.get("errors") or []
    if errors:
        message = str(errors[0].get("message", "")).lower()
        if "private" in message:
            raise AnimeListError(f'The AniList list for "{username}" is private - make it public to import it.')
        if resp.status_code == 404 or "not found" in message:
            raise AnimeListError(f'AniList has no user called "{username}".')
        raise AnimeListError("AniList refused the request - try again later.")
    if not resp.ok:
        raise AnimeListError("AniList is not answering right now - try again later.")
    return (body.get("data") or {}).get("MediaListCollection") or {}


def fetch_anilist(username):
    """Every anime on the public AniList list of `username`. Raises
    AnimeListError when the user doesn't exist, the list is private, or AniList
    can't be reached."""
    username = clean_username(username)
    entries = []
    chunk = 1
    for _ in range(MAX_PAGES):
        collection = _anilist_page(username, chunk, 500)
        for media_list in collection.get("lists") or []:
            for raw in media_list.get("entries") or []:
                entry = _anilist_entry(raw)
                if entry:
                    entries.append(entry)
        if not collection.get("hasNextChunk"):
            break
        chunk += 1
    return _dedupe(entries)


# ----------------------------------------------------------------------- Kitsu

_KITSU_STATUS = {
    "current": "watching",
    "completed": "completed",
    "on_hold": "on_hold",
    "dropped": "dropped",
    "planned": "plan_to_watch",
}


def _kitsu_get(url, params=None):
    try:
        resp = requests.get(
            url,
            params=params,
            headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.api+json"},
            timeout=TIMEOUT,
        )
    except requests.RequestException:
        raise AnimeListError("Couldn't reach Kitsu - try again in a moment.") from None
    if resp.status_code == 429:
        raise AnimeListError("Kitsu is rate-limiting requests right now - try again in a minute.")
    if not resp.ok:
        raise AnimeListError("Kitsu is not answering right now - try again later.")
    try:
        return resp.json()
    except ValueError:
        raise AnimeListError("Kitsu sent back something unexpected - try again later.") from None


def _kitsu_user_id(username):
    for field in ("slug", "name"):
        data = _kitsu_get(f"{KITSU_BASE}/users", {f"filter[{field}]": username, "fields[users]": "name,slug"})
        users = data.get("data") or []
        if users:
            return users[0]["id"]
    raise AnimeListError(f'Kitsu has no user called "{username}".')


def _kitsu_date(value):
    return value[:10] if value and re.match(r"^\d{4}-\d{2}-\d{2}", value) else None


def _kitsu_entry(raw, included):
    attrs = raw.get("attributes") or {}
    status = _KITSU_STATUS.get(attrs.get("status"))
    anime_ref = ((raw.get("relationships") or {}).get("anime") or {}).get("data")
    anime = included.get(("anime", (anime_ref or {}).get("id")))
    if status is None or anime is None:
        return None
    anime_attrs = anime.get("attributes") or {}
    mal_id = None
    for ref in ((anime.get("relationships") or {}).get("mappings") or {}).get("data") or []:
        mapping = (included.get(("mappings", ref.get("id"))) or {}).get("attributes") or {}
        if mapping.get("externalSite") == "myanimelist/anime" and str(mapping.get("externalId", "")).isdigit():
            mal_id = int(mapping["externalId"])
            break
    year = None
    if (anime_attrs.get("startDate") or "")[:4].isdigit():
        year = int(anime_attrs["startDate"][:4])
    rating = attrs.get("ratingTwenty")
    return {
        "node": {
            "id": mal_id if mal_id else int(anime["id"]),
            "provider": "mal" if mal_id else "kitsu",
            "title": anime_attrs.get("canonicalTitle") or "",
            "start_season": {"year": year} if year else None,
        },
        "list_status": {
            "status": status,
            "score": round(rating / 2) if rating else 0,
            "num_episodes_watched": int(attrs.get("progress") or 0),
            "updated_at": attrs.get("progressedAt") or attrs.get("updatedAt"),
            "start_date": _kitsu_date(attrs.get("startedAt")),
            "finish_date": _kitsu_date(attrs.get("finishedAt")),
        },
    }


def fetch_kitsu(username):
    """Every anime in the public Kitsu library of `username` (its profile
    slug or display name)."""
    username = clean_username(username)
    user_id = _kitsu_user_id(username)
    url = f"{KITSU_BASE}/library-entries"
    params = {
        "filter[user_id]": user_id,
        "filter[kind]": "anime",
        "include": "anime,anime.mappings",
        "fields[libraryEntries]": "status,progress,ratingTwenty,startedAt,finishedAt,progressedAt,updatedAt,anime",
        "fields[anime]": "canonicalTitle,startDate,mappings",
        "fields[mappings]": "externalSite,externalId",
        "page[limit]": 500,
    }
    entries = []
    for _ in range(MAX_PAGES):
        data = _kitsu_get(url, params)
        included = {(item.get("type"), item.get("id")): item for item in data.get("included") or []}
        for raw in data.get("data") or []:
            entry = _kitsu_entry(raw, included)
            if entry:
                entries.append(entry)
        url = (data.get("links") or {}).get("next")
        params = None  # the next link already carries its own query string
        if not url:
            break
    return _dedupe(entries)


# ------------------------------------------------------------------ MAL XML

_XML_STATUS = {
    "watching": "watching", "1": "watching",
    "completed": "completed", "2": "completed",
    "on-hold": "on_hold", "onhold": "on_hold", "on hold": "on_hold", "3": "on_hold",
    "dropped": "dropped", "4": "dropped",
    "plan to watch": "plan_to_watch", "plantowatch": "plan_to_watch", "6": "plan_to_watch",
}


def _xml_date(value):
    value = (value or "").strip()
    return value if re.match(r"^\d{4}-\d{2}-\d{2}$", value) and not value.startswith("0000") and "-00" not in value else None


def parse_mal_xml(data):
    """The anime in a MyAnimeList-format XML export (the file MAL's "Export
    my list" makes, plain or gzipped). Raises AnimeListError for anything
    that is not one."""
    if data[:2] == b"\x1f\x8b":
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as handle:
                data = handle.read(MAX_XML_BYTES + 1)
        except (OSError, EOFError):
            raise AnimeListError("That file looks like a damaged archive.") from None
    if len(data) > MAX_XML_BYTES:
        raise AnimeListError("That file is too large to be an anime list export.")
    # defusedxml refuses entities, external references and DTDs, so a crafted
    # file can't expand into something huge or read a local file.
    try:
        root = SafeET.fromstring(data)
    except DefusedXmlException:
        raise AnimeListError("That file isn't a plain anime list export.") from None
    except SafeET.ParseError:
        raise AnimeListError("That file isn't a valid MyAnimeList XML export.") from None
    if root.tag != "myanimelist":
        raise AnimeListError("That file isn't a MyAnimeList XML export (no <myanimelist> list found).")
    entries = []
    for anime in root.findall("anime"):
        def text(tag):
            node = anime.find(tag)
            return (node.text or "").strip() if node is not None and node.text else ""

        status = _XML_STATUS.get(text("my_status").lower())
        anime_id = text("series_animedb_id")
        if status is None or not anime_id.isdigit():
            continue
        entries.append(
            {
                "node": {"id": int(anime_id), "provider": "mal", "title": text("series_title"), "start_season": None},
                "list_status": {
                    "status": status,
                    "score": int(text("my_score")) if text("my_score").isdigit() else 0,
                    "num_episodes_watched": int(text("my_watched_episodes")) if text("my_watched_episodes").isdigit() else 0,
                    "updated_at": None,
                    "start_date": _xml_date(text("my_start_date")),
                    "finish_date": _xml_date(text("my_finish_date")),
                },
            }
        )
    if not entries:
        raise AnimeListError("That export has no anime in it.")
    return entries


# ---------------------------------------------------------------------- shared


def _dedupe(entries):
    """One entry per (provider, id): a title on two of a person's custom
    lists (AniList) or paged twice must not become two imports."""
    seen = set()
    out = []
    for entry in entries:
        key = (entry["node"]["provider"], entry["node"]["id"])
        if key not in seen:
            seen.add(key)
            out.append(entry)
    return out


def check_user(provider, username):
    """A cheap "does this user exist and is their list readable" - one small
    request instead of the whole list - used when someone connects. Returns
    the cleaned username; raises AnimeListError otherwise."""
    username = clean_username(username)
    if provider == "anilist":
        _anilist_page(username, 1, 1)
    elif provider == "kitsu":
        _kitsu_user_id(username)
    else:
        raise AnimeListError("Unknown anime list service.")
    return username


def fetch_list(provider, username):
    if provider == "anilist":
        return fetch_anilist(username)
    if provider == "kitsu":
        return fetch_kitsu(username)
    raise AnimeListError("Unknown anime list service.")
