"""Shared title-matching logic for Trakt/Simkl/Nuvio's routine sync AND
the Universal Import Review pipeline (tracker/import_pipeline.py) -
extracted from what were three separately-duplicated (byte-for-byte
identical between Trakt and Simkl) _get_or_create_title functions, so
review (preview) and commit are guaranteed to agree on what a candidate
will match against - the "preview and commit must use the same matching
rules" requirement. CSV/JSON/ZIP import (tracker/csv_import.py) keeps
its own separate matching for now - it has a different priority order
(TMDB id before provider id) already pinned down by its own tests, and
is unified into this module once the file-import path itself moves onto
the review pipeline.

Split into two layers on purpose:

- resolve_title_match() is PURELY READ-ONLY - no DB writes, ever (not
  even backfilling a missing id onto an already-matched Title). This is
  what the Import Review scan step calls, since preview must never
  mutate canonical data, full stop - see its own docstring.
- apply_title_match() wraps it with the actual create-or-backfill
  write, and is what routine sync (trakt.py/simkl.py/nuvio.py's own
  upsert_history_items) and Import Review's commit step call.

Matching order (identical to what Trakt/Simkl/Nuvio's own prior
duplicated logic already did):

1. An existing Title already keyed by this exact provider_id under
   this provider's own external_ids key (resync dedup) - not filtered
   by media_type, since a title may have been reclassified TV<->ANIME
   since it was first created (see reclassify_anime_titles) and is
   still the right row to reuse, not a mismatch to fork a duplicate
   over.
2. Resolve a TMDB match: tmdb_id given directly (exact - the caller's
   own provider already resolved it, preferred over any fuzzy search)
   > imdb_id given, no tmdb_id (tmdb.find_by_imdb_id) > a fuzzy
   name+year search (tmdb.find_match), only possible when a name is
   available.
3. Cross-provider reuse: an existing Title already keyed by that same
   resolved tmdb id + tmdb_kind, from ANY provider - reused rather
   than forking a duplicate (the exact bug each of the three modules'
   own docstrings describe hitting against a real account before this
   existed: a title already tracked via one provider got a second,
   provider-specific duplicate the first time a different provider
   synced it too).
4. No match at all - the caller creates a bare Title with just this
   provider's own id (apply_title_match does this; resolve_title_match
   just reports it as "no existing title").
"""

from dataclasses import dataclass


@dataclass
class TitleMatch:
    """Everything a caller needs to either reuse an existing Title or
    create a new one - computed once, read-only, safe to show in a
    review UI before anything is written."""

    existing_title: object | None  # Title or None
    needs_provider_id_backfill: bool  # existing_title found but missing this provider's own id
    needs_tmdb_backfill: bool  # existing_title found but missing a tmdb id this match resolved
    tmdb_id: str | None
    tmdb_kind: str | None
    resolved_name: str
    resolved_year: int | None
    poster_url: str
    genre_names: list
    details: dict | None  # tmdb.get_full_details' own return shape, or None


def resolve_title_match(
    media_type, provider, provider_id, name=None, year=None, tmdb_id=None, imdb_id=None,
    prefer_resolved_name=False, require_details_for_tmdb_id=False,
):
    """Read-only - see module docstring. name/year are a hint - Trakt/
    Simkl always have a real one and it's always kept as-is (never
    overwritten by TMDB's own title/year, even when a TMDB match is
    found - that's simply what Trakt/Simkl's own routine sync has
    always done). Nuvio often has no name at all, just a content_id, so
    it passes prefer_resolved_name=True to prefer TMDB's own
    name/year over its hint - but only for a match resolved via a
    direct id (tmdb_id/imdb_id given), never for one resolved via a
    fuzzy name search, matching nuvio.py's own prior matching exactly
    (its fuzzy-match branch never overwrote the hint it searched with)."""
    from tracker.integrations import tmdb as tmdb_integration
    from tracker.models import MediaType, Title

    tmdb_kind = "movie" if media_type == MediaType.MOVIE else "tv"

    # Step 1 - this exact provider id, already tracked.
    existing = Title.objects.filter(**{f"external_ids__{provider}": str(provider_id)}).first()
    if existing:
        return TitleMatch(
            existing_title=existing,
            needs_provider_id_backfill=False,
            needs_tmdb_backfill=bool(tmdb_id) and not existing.external_ids.get("tmdb"),
            tmdb_id=str(tmdb_id) if tmdb_id else existing.external_ids.get("tmdb"),
            tmdb_kind=tmdb_kind if tmdb_id else existing.external_ids.get("tmdb_kind"),
            resolved_name=existing.name,
            resolved_year=existing.year,
            poster_url=existing.poster_url,
            genre_names=[],
            details=None,
        )

    # Step 2 - resolve a TMDB match: a cascade, not a pick-one-strategy
    # choice - tmdb_id given directly is tried first, then imdb_id, then
    # a fuzzy name+year search, each only attempted if the previous one
    # didn't produce a usable match (imdb_id resolving to nothing, say,
    # still falls through to the fuzzy search when a name is available).
    # resolved_via_direct_id tracks whether the eventual match came from
    # the tmdb_id/imdb_id branches specifically, not the fuzzy one - see
    # prefer_resolved_name's own use below.
    match = None
    details = None
    resolved_via_direct_id = False

    if tmdb_id:
        details = tmdb_integration.get_full_details(tmdb_kind, tmdb_id)
        # require_details_for_tmdb_id: Nuvio (only) discards a tmdb_id
        # match entirely - not just the details, the id link too - if
        # TMDB has nothing for it, rather than linking a Title to a
        # tmdb id whose own details it was never able to fetch. Trakt/
        # Simkl keep the id either way (default False).
        if details or not require_details_for_tmdb_id:
            match = {"id": tmdb_id, "kind": tmdb_kind, "poster_url": None}
            resolved_via_direct_id = True
        else:
            details = None
    if not match and imdb_id:
        candidate = tmdb_integration.find_by_imdb_id(imdb_id, media_type)
        if candidate:
            match = candidate
            details = tmdb_integration.get_full_details(candidate["kind"], candidate["id"])
            resolved_via_direct_id = True
    if not match and name:
        candidate = tmdb_integration.find_match(media_type, name, year)
        if candidate:
            match = candidate
            details = tmdb_integration.get_full_details(candidate["kind"], candidate["id"])

    resolved_name = name or "Untitled"
    resolved_year = year
    poster_url = ""
    genre_names = []
    matched_tmdb_id = None
    matched_tmdb_kind = None

    if match:
        matched_tmdb_id = str(match["id"])
        matched_tmdb_kind = match["kind"]
        if details:
            if prefer_resolved_name and resolved_via_direct_id:
                resolved_name = details["name"]
                resolved_year = details["year"]
            poster_url = match["poster_url"] or details.get("poster_url") or ""
            genre_names = details["genres"]
        else:
            poster_url = match["poster_url"] or ""

        # Step 3 - cross-provider reuse.
        existing = Title.objects.filter(
            external_ids__tmdb=matched_tmdb_id, external_ids__tmdb_kind=matched_tmdb_kind
        ).first()
        if existing:
            return TitleMatch(
                existing_title=existing,
                needs_provider_id_backfill=existing.external_ids.get(provider) != str(provider_id),
                needs_tmdb_backfill=False,
                tmdb_id=matched_tmdb_id,
                tmdb_kind=matched_tmdb_kind,
                resolved_name=existing.name,
                resolved_year=existing.year,
                poster_url=existing.poster_url,
                genre_names=[],
                details=None,
            )

    # Step 4 - nothing existing matches.
    return TitleMatch(
        existing_title=None,
        needs_provider_id_backfill=False,
        needs_tmdb_backfill=False,
        tmdb_id=matched_tmdb_id,
        tmdb_kind=matched_tmdb_kind,
        resolved_name=resolved_name,
        resolved_year=resolved_year,
        poster_url=poster_url,
        genre_names=genre_names,
        details=details,
    )


def apply_title_match(
    media_type, provider, provider_id, name=None, year=None, tmdb_id=None, imdb_id=None,
    prefer_resolved_name=False, require_details_for_tmdb_id=False,
):
    """resolve_title_match() plus the actual write - reuses (backfilling
    a missing provider/tmdb id onto it if needed) or creates a Title.
    Used by routine sync (trakt.py/simkl.py/nuvio.py) and Import
    Review's commit step - never by preview/scan."""
    from tracker.models import Title, attach_genres, attach_reports_metadata
    from tracker.integrations import tmdb as tmdb_integration

    match = resolve_title_match(
        media_type, provider, provider_id, name=name, year=year, tmdb_id=tmdb_id, imdb_id=imdb_id,
        prefer_resolved_name=prefer_resolved_name, require_details_for_tmdb_id=require_details_for_tmdb_id,
    )

    if match.existing_title:
        title = match.existing_title
        update_fields = []
        external_ids = dict(title.external_ids)
        if match.needs_provider_id_backfill:
            external_ids[provider] = str(provider_id)
            update_fields.append("external_ids")
        if match.needs_tmdb_backfill and match.tmdb_id:
            external_ids["tmdb"] = str(match.tmdb_id)
            external_ids["tmdb_kind"] = match.tmdb_kind
            update_fields.append("external_ids")
        if update_fields:
            title.external_ids = external_ids
            title.save(update_fields=["external_ids"])
        return title

    external_ids = {provider: str(provider_id)}
    if match.tmdb_id:
        external_ids["tmdb"] = match.tmdb_id
        external_ids["tmdb_kind"] = match.tmdb_kind
    title = Title.objects.create(
        media_type=media_type,
        name=match.resolved_name,
        # resolved_year can come straight from tmdb.get_full_details'
        # own "year" field, which is a string (see tmdb.py's own
        # date[:4] slice) - cast, not just "or 0", so Title.year (a
        # PositiveSmallIntegerField) ends up a real int in memory, not
        # a string that only happens to look right until the next DB
        # round-trip normalizes it.
        year=int(match.resolved_year) if match.resolved_year else 0,
        external_ids=external_ids,
        poster_url=match.poster_url,
    )
    attach_genres(title, match.genre_names)
    if match.details and match.tmdb_id:
        attach_reports_metadata(
            title, tmdb_integration.get_reports_metadata(match.tmdb_kind, int(match.tmdb_id), match.details)
        )
    return title
