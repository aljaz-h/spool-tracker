"""MyAnimeList (MAL) OAuth2 + anime list sync.

Implemented against MAL's official API v2 (api.myanimelist.net/v2 for data,
myanimelist.net/v1/oauth2/* for auth) - this environment has no real MAL
developer app credentials to exercise it against, so every network-calling
function here is unverified against the live API, same caveat every other
integration module in this codebase carries (see trakt.py's own module
docstring).

Two things MAL does differently from Trakt/Simkl/WeTrakr, both load-bearing
here:

1. PKCE, but MAL only supports code_challenge_method=plain (not S256) - the
   challenge must equal the verifier itself (see generate_pkce_pair). Token
   exchange/refresh are form-encoded (not JSON, unlike Trakt/WeTrakr).
   Access tokens last 1 hour, refresh tokens 1 month - short enough that a
   connection idle for 30+ days genuinely needs reconnecting, not just
   refreshing; _call_with_refresh's existing "retry once after refreshing"
   logic (tasks.py) already surfaces that as a normal sync failure (the
   refresh grant itself fails with a 400, which isn't a 401 the retry path
   catches, so it just propagates as a failed sync - a human sees "sync
   failed" in Logs + a bell notification, same as any other failure).

2. MAL's own list_status.num_episodes_watched is a single cumulative count
   across the whole series (MAL has no per-episode watch log the way
   Trakt/Simkl do) - there's no season/episode number anywhere in the API
   response at all. Turning "47 episodes watched" into actual (season,
   episode) pairs needs the matched show's real TMDB season/episode
   structure, walked the same way views.title_mark_all_seasons_watched
   already walks it for its own bulk "mark all seasons watched" action.
   That walk needs a resolved tmdb_id, which means normalize_items() below
   has to resolve title matches (via tracker.title_matching.
   resolve_title_match - read-only, safe at scan time) and make extra TMDB
   calls (get_full_details/get_season_details) for every watched show -
   unlike every other provider's own normalize/fetch step in this codebase,
   which is pure in-memory reshaping of data the provider already returned
   in final form. This is the one genuinely new kind of work this module
   does that no other integration here needs.

MAL's own 5 list statuses collapse to 3 outcomes on the Spool side:
plan_to_watch -> Watchlist only (via tracker.backup.restore_lists' own
list-item shape, same convention IMDb/Letterboxd/TMDB's watchlist-only
imports already use). watching/on_hold/completed/dropped with
num_episodes_watched > 0 -> one or more WatchEvents (episode-by-episode for
a matched TV series, one single watch for an anime film or an unmatched
show). watching/on_hold/dropped with zero progress -> nothing at all - not
"planning to watch" (that's plan_to_watch's own job) and nothing watched
yet either. MAL's own status distinction beyond that (completed vs.
dropped, say) isn't tracked as a separate concept here - same as every
other provider's own history import, which only ever turns into "how many
WatchEvents", not a status label.
"""

import secrets
from urllib.parse import urlencode

import requests
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from tracker.models import MediaType

AUTHORIZE_URL = "https://myanimelist.net/v1/oauth2/authorize"
TOKEN_URL = "https://myanimelist.net/v1/oauth2/token"
API_BASE = "https://api.myanimelist.net/v2"


def generate_pkce_pair():
    """(code_verifier, code_challenge) - MAL only supports the "plain"
    PKCE method, where the challenge is required to equal the verifier
    itself (no S256 hashing, unlike every other PKCE provider in this
    codebase - see wetrakr.py's own generate_pkce_pair for the normal
    S256 shape this deliberately isn't). The verifier only needs to
    survive this one connect->callback round trip - stashed in the
    session by views.oauth_connect, read back in oauth_callback, never
    persisted anywhere past that."""
    verifier = secrets.token_urlsafe(96)[:128]
    return verifier, verifier


def authorize_url(redirect_uri, state, client_id, code_challenge):
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "plain",
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def exchange_code(code, redirect_uri, client_id, client_secret, code_verifier):
    """MAL's token endpoint is form-encoded (data=), not JSON - unlike
    Trakt/WeTrakr's own exchange_code, which both POST json=."""
    resp = requests.post(
        TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "code_verifier": code_verifier,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def refresh_access_token(refresh_token, client_id, client_secret, redirect_uri):
    """redirect_uri is accepted for signature compatibility with
    trakt.refresh_access_token (so tasks._call_with_refresh works
    against this module unchanged) but isn't actually sent - MAL's
    refresh grant doesn't take or need one."""
    resp = requests.post(
        TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def test_credentials(client_id):
    """Settings' "Test connection" button - a public GET using MAL's
    separate "Client ID" auth method (the X-MAL-CLIENT-ID header, no
    user token needed) against a public ranking endpoint, the same
    "confirms the client id is live, not the secret" contract every
    other test_credentials in this codebase has. Raises
    requests.RequestException on failure."""
    resp = requests.get(
        f"{API_BASE}/anime/ranking",
        params={"ranking_type": "all", "limit": 1},
        headers={"X-MAL-CLIENT-ID": client_id},
        timeout=10,
    )
    resp.raise_for_status()


def _headers(access_token):
    return {"Authorization": f"Bearer {access_token}"}


def fetch_anime_list(access_token, limit=1000):
    """Every anime on this account's list, each a {"node": {...},
    "list_status": {...}} dict (node: id/title/media_type/start_season/
    num_episodes; list_status: status/score/num_episodes_watched/
    updated_at/start_date/finish_date - fields=list_status is required,
    MAL omits list_status entirely without it). Follows MAL's own
    paging.next URL rather than doing offset math here, so a change to
    MAL's own page size can't desync anything on this end. limit: the
    per-page size requested (MAL's documented max is 1000) - every page
    is walked regardless, so this doesn't cap the total returned."""
    items = []
    url = f"{API_BASE}/users/@me/animelist"
    params = {"fields": "list_status", "limit": limit}
    while url:
        resp = requests.get(url, params=params, headers=_headers(access_token), timeout=15)
        resp.raise_for_status()
        data = resp.json()
        items.extend(data.get("data") or [])
        url = (data.get("paging") or {}).get("next")
        params = None  # the next URL already carries its own query string
    return items


def _resolve(mal_id, name, year):
    """One title_matching.resolve_title_match call per anime - read-
    only, same guarantee every other call site of this function has
    (see that module's own docstring). Done here, at normalize time,
    rather than deferred to scan_normalized_items' own match_cache like
    every other provider, because the TV-episode-expansion branch below
    needs a resolved tmdb_id/tmdb_kind *before* it can even attempt to
    build (season, episode) pairs - see this module's own docstring."""
    from tracker import title_matching

    return title_matching.resolve_title_match(MediaType.ANIME, "mal", mal_id, name=name, year=year)


def _episode_specs(tmdb_id):
    """Flat (season, episode_number, name) list in absolute, whole-
    series order - the same per-season TMDB walk views.
    title_mark_all_seasons_watched already does for its own bulk "mark
    all seasons watched" action (tmdb.get_full_details for
    number_of_seasons, then tmdb.get_season_details per season). Reused
    here because that's exactly what turns MAL's single cumulative
    num_episodes_watched count into real (season, episode) pairs: the
    Nth entry in this list is the Nth episode MAL would count toward
    that total, assuming (as every tracker that surfaces a bulk episode
    count does) that MAL's own absolute numbering agrees with TMDB's
    season order - which is the normal case, not a verified one."""
    from tracker.integrations import tmdb as tmdb_integration

    details = tmdb_integration.get_full_details("tv", tmdb_id)
    if not details:
        return []
    specs = []
    for season in range(1, (details["number_of_seasons"] or 0) + 1):
        season_data = tmdb_integration.get_season_details(tmdb_id, season)
        if season_data:
            specs.extend((season, ep["episode_number"], ep["name"]) for ep in season_data["episodes"])
    return specs


def _aware_midnight(d):
    return timezone.make_aware(timezone.datetime.combine(d, timezone.datetime.min.time()))


def _best_date(status_data):
    """finish_date (closest to "the day they actually finished") >
    updated_at (always present - MAL stamps every list change, covers
    watching/on_hold/dropped rows with no finish_date) > start_date (a
    last-resort fallback for the rare row missing even updated_at) -
    same "best available date" reasoning imdb_import.py's own Date
    Rated fallback already uses. None if nothing here parses at all."""
    finish = status_data.get("finish_date")
    if finish:
        d = parse_date(finish)
        if d:
            return _aware_midnight(d)
    updated = status_data.get("updated_at")
    if updated:
        dt = parse_datetime(updated)
        if dt:
            return dt if timezone.is_aware(dt) else timezone.make_aware(dt)
    start = status_data.get("start_date")
    if start:
        d = parse_date(start)
        if d:
            return _aware_midnight(d)
    return None


def _history_item(name, year, season, episode, watched_at, rating, source_external_ids, source_row):
    return {
        "media_type": MediaType.ANIME, "title_name": name, "year": year, "season": season,
        "episode": episode, "watched_at": watched_at, "rating": rating,
        "source_external_ids": source_external_ids, "source_row": source_row, "error": None,
    }


def _error_item(source_row, reason):
    return {
        "media_type": None, "title_name": f"MyAnimeList item {source_row}", "year": None, "season": None,
        "episode": None, "watched_at": None, "rating": None, "source_external_ids": {},
        "source_row": source_row, "error": reason,
    }


def _history_items_for(mal_id, name, year, progress, score, watched_at, source_row):
    """progress: list_status.num_episodes_watched (already confirmed >
    0 by the caller). Branches on the *resolved* TMDB kind, not MAL's
    own node.media_type guess - a MAL "tv"/"ova"/"ona"/"special" that
    TMDB only has catalogued as a movie (or that doesn't match on TMDB
    at all) is treated as a single watch, same as a real anime film,
    since there's no TMDB season/episode structure to expand either
    way. Only a resolved tmdb_kind == "tv" gets the episode-by-episode
    treatment. The rating lands on the single movie-shaped watch, or
    on the *last* episode of a bulk range - mirroring how a single
    external score naturally reads as "my rating as of where I left
    off", not as a letterboxd-style guess at a duplicate per-episode
    rating."""
    match = _resolve(mal_id, name, year)
    source_external_ids = {"mal": str(mal_id)}
    if match.tmdb_id:
        source_external_ids["tmdb"] = match.tmdb_id
        source_external_ids["tmdb_kind"] = match.tmdb_kind
    rating = score if score > 0 else None

    if match.tmdb_id and match.tmdb_kind == "tv":
        specs = _episode_specs(match.tmdb_id)
        if not specs:
            return [_error_item(source_row, f'matched "{name}" on TMDB but couldn\'t read its episode list')]
        # MAL can report more watched than TMDB's own season data
        # covers (a recently-aired episode TMDB hasn't added yet, an
        # OVA/special MAL counts that TMDB doesn't) - the slice just
        # caps at what's actually available rather than erroring, same
        # "import what we can, say nothing about what we can't" spirit
        # as imdb_import.py's own unsupported-title-type handling.
        watched_specs = specs[:progress]
        last_index = len(watched_specs) - 1
        return [
            _history_item(
                name, year, season, episode_number, watched_at,
                rating if i == last_index else None, source_external_ids, source_row,
            )
            for i, (season, episode_number, _ep_name) in enumerate(watched_specs)
        ]

    # Movie-shaped: a real anime film, an OVA/special TMDB only has as
    # a movie, or no TMDB match at all - one single watch, same as
    # IMDb/Trakt's own movie rows.
    return [_history_item(name, year, None, None, watched_at, rating, source_external_ids, source_row)]


def _watchlist_item(mal_id, name, year, position):
    """tracker.backup.restore_lists' own item shape - no tmdb id
    resolved here (unlike _history_items_for), since restore_lists'
    own _get_or_create_title already does the same fuzzy name/year
    fallback a missing id needs, exactly as Letterboxd's own watchlist
    items (which also carry no native id at all) already rely on."""
    return {
        "type": "show", "media_type": MediaType.ANIME, "title": name, "year": year,
        "ids": {"mal": str(mal_id)}, "position": position,
    }


def normalize_items(entries):
    """entries: fetch_anime_list()'s own return list. Returns
    (history_items, watchlist_entry) - history_items in the common
    Import Review normalized shape (see import_pipeline.py's own
    module docstring), ready for both tasks.scan_import_session (via
    import_pipeline.scan_normalized_items) and routine sync (via this
    module's own upsert_normalized_items below - the single shared
    "parse and resolve" step both read from, differing only in whether
    the final title match is resolve_title_match (read-only, scan) or
    apply_title_match (write, routine sync/commit)). watchlist_entry is
    a tracker.backup.restore_lists-shaped list dict (is_watchlist=True)
    for every plan_to_watch anime, or None if there are none - same
    convention IMDb/Letterboxd/TMDB's own watchlist-only imports use.

    See this module's own docstring for how MAL's 5 statuses collapse
    into history items vs. the watchlist vs. nothing at all."""
    history_items = []
    watchlist_items = []
    source_row = 0
    for entry in entries:
        source_row += 1
        node = entry.get("node") or {}
        status_data = entry.get("list_status") or {}
        mal_id = node.get("id")
        name = node.get("title") or ""
        year = (node.get("start_season") or {}).get("year")
        status = status_data.get("status")
        progress = status_data.get("num_episodes_watched") or 0
        score = status_data.get("score") or 0

        if not mal_id or not name:
            history_items.append(_error_item(source_row, "missing MyAnimeList id or title"))
            continue

        if status == "plan_to_watch":
            watchlist_items.append(_watchlist_item(mal_id, name, year, len(watchlist_items)))
            continue

        if status not in ("watching", "on_hold", "completed", "dropped") or progress <= 0:
            continue

        watched_at = _best_date(status_data)
        if watched_at is None:
            history_items.append(_error_item(source_row, "no usable date (missing updated_at/finish_date)"))
            continue

        history_items.extend(_history_items_for(mal_id, name, year, progress, score, watched_at, source_row))

    watchlist_entry = {"is_watchlist": True, "items": watchlist_items} if watchlist_items else None
    return history_items, watchlist_entry


def upsert_normalized_items(profile, history_items, labels_out=None):
    """Routine re-sync's write path for normalize_items()' own
    history_items - the apply_title_match-based counterpart to scan's
    read-only resolve_title_match (used internally by _resolve above,
    then again by import_pipeline.scan_normalized_items's own
    match_cache). Mirrors import_pipeline._commit_one's dedup-by-
    (profile, title, episode, watched_at) and rating-only-on-create
    logic directly, rather than Trakt/Simkl's own upsert_history_items
    shape, since normalize_items already did every bit of parsing and
    resolution this needs - there's no separate raw-provider-item step
    left to duplicate here. Returns the count of newly created
    WatchEvent rows."""
    from tracker import completion, media_kinds, recommendations, rewatches, title_matching
    from tracker.models import Episode, Title, WatchEvent

    created = 0
    touched_movies = set()
    touched_shows = set()
    touched_watch_keys = set()
    for item in history_items:
        if item["error"] or item["watched_at"] is None:
            continue
        mal_id = item["source_external_ids"].get("mal")
        tmdb_id = item["source_external_ids"].get("tmdb")
        kind = media_kinds.tmdb_kind_for(
            item["media_type"], item["season"], item["episode"], item["source_external_ids"].get("tmdb_kind")
        )
        title = title_matching.apply_title_match(
            item["media_type"], "mal", mal_id, name=item["title_name"], year=item["year"],
            tmdb_id=tmdb_id, tmdb_kind=kind,
        )
        episode = None
        is_show = kind == "tv"
        if is_show:
            episode, _ = Episode.objects.get_or_create(title=title, season=item["season"], episode=item["episode"])
            touched_shows.add(title.id)
        else:
            touched_movies.add(title.id)

        existing = WatchEvent.objects.filter(
            profile=profile, title=title, episode=episode, watched_at=item["watched_at"]
        ).first()
        if existing is None:
            WatchEvent.objects.create(
                profile=profile, title=title, episode=episode, watched_at=item["watched_at"],
                user_rating=item["rating"], source=WatchEvent.Source.MAL,
            )
            created += 1
            if labels_out is not None:
                labels_out.append(title.name if episode is None else f"{title.name} S{episode.season}E{episode.episode}")
            touched_watch_keys.add((title.id, episode.id if episode else None))
        elif not existing.source:
            existing.source = WatchEvent.Source.MAL
            existing.save(update_fields=["source"])

    for title_id, episode_id in touched_watch_keys:
        rewatches.recompute_is_rewatch(
            profile, Title.objects.get(id=title_id), Episode.objects.get(id=episode_id) if episode_id else None
        )
    for title in Title.objects.filter(id__in=touched_movies):
        completion.update_movie_runtime(title)
        completion.sync_watchlist_removal(profile, title)
        recommendations.mark_title_watched(profile, title)
    for title in Title.objects.filter(id__in=touched_shows):
        completion.sync_show_completion(profile, title)
        completion.sync_watchlist_removal(profile, title)
        recommendations.mark_title_watched(profile, title)
    return created


def upsert_watchlist(profile, watchlist_entry, labels_out=None):
    """normalize_items()' own watchlist_entry (or None), restored via
    the exact same tracker.backup.restore_lists mechanism the first-
    connect scan path stashes it for - see tmdb_account.upsert_lists
    for the equivalent one-liner on the TMDB side. Returns the count of
    newly added items."""
    from tracker import backup

    if watchlist_entry is None:
        return 0
    _, items_added = backup.restore_lists(profile, {"lists": [watchlist_entry]}, labels_out=labels_out)
    return items_added
