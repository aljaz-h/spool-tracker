← [Back to README](../README.md)

# Reports API

A read-only API for external services to generate reports from your watch
history - built for [spool-wrapped](https://github.com/aljaz-h/spool-wrapped),
a companion app that turns it into "Wrapped"-style recap cards and a
long-form Year in Review report, delivered via Discord/webhook/email. Any
service could use it; spool-wrapped is just the one it was designed
against.

This is a different shape of access than the [Scrobble API](SCROBBLE_API.md):
that one lets a player log watches *as one profile*, and can't read
anything back. This one is read-only, but *across every profile that's
opted in at once* - meant for a trusted server-side service, not a
per-profile credential in a player's config.

## Opting in

Nothing is ever visible through this API by default. Each profile decides
for itself in Settings → Integrations → **Wrapped**:

- **Share watch history with spool-wrapped** - the opt-in flag. A profile
  that hasn't turned this on is completely absent from the API - the
  connected service never learns it exists, let alone what it watched.
  This is also the *only* opt-out mechanism: there's no separate "delete
  my data" step, since nothing was ever shared in the first place.
- **Webhook URL** (optional) - a Spool-side default delivery target.
  spool-wrapped's own per-profile config, once set there, always takes
  priority over this - it's only used as the initial suggestion the
  first time spool-wrapped sees a given profile.
- **Enable email delivery by default** - same "initial suggestion only"
  role as the webhook URL above.

## Getting a key

Unlike Scrobble API tokens (one per profile, self-service), this needs a
**Reports API key** - one per external service, minted by an owner in
Settings → Server Integrations. It authorizes read access to every
opted-in profile at once; the key itself doesn't pick which profiles it
can see; the opt-in flag above does. Revoking a key immediately 401s
anything still using it.

## Endpoints

```
Base:  {your Spool URL}/api/reports/
Auth:  Authorization: Bearer <your Reports API key>
```

### `GET /api/reports/profiles/`

Every profile that's opted in.

```json
{
  "profiles": [
    {
      "id": 1,
      "display_name": "Aljaž",
      "wrapped_webhook_url": "https://discord.com/api/webhooks/...",
      "wrapped_email_enabled": true,
      "timezone": "Europe/Ljubljana"
    }
  ]
}
```

An instance with nobody opted in returns `{"profiles": []}`, not an error.

### `GET /api/reports/profiles/{id}/history/?since=&until=`

Watch history for one profile, restricted to a half-open date range
`[since, until)`. Both are required, `YYYY-MM-DD` - resolved against
*that profile's own* timezone (from `/profiles/` above, blank meaning
UTC), not server-local time, so a "January" report doesn't clip the last
few hours of Jan 31 for someone west of UTC.

```
GET /api/reports/profiles/1/history/?since=2026-01-01&until=2026-02-01
```

```json
{
  "profile_id": 1,
  "since": "2026-01-01",
  "until": "2026-02-01",
  "history": [
    {
      "title": "Oppenheimer",
      "type": "movie",
      "genres": ["Drama", "History", "Thriller"],
      "rating": 9,
      "watched_at": "2026-01-04T21:15:00Z",
      "runtime_minutes": 181,
      "country": "United States",
      "studio": "Universal Pictures",
      "network": null,
      "cast": ["Cillian Murphy", "Emily Blunt", "Robert Downey Jr."],
      "directors": ["Christopher Nolan"],
      "writers": ["Christopher Nolan"]
    }
  ]
}
```

One entry per *completed* watch - a `tv`/`anime` episode is one entry per
episode, not per series, so a binge shows up as many entries. `rating` is
`null` for an unrated watch (still counts toward watch time/streaks, just
excluded from "top-rated title"). `country`/`studio`/`network`/`cast`/
`directors`/`writers` power a Year in Review report specifically -
`studio` is always `null` for `tv`/`anime`, `network` always `null` for a
`movie`, and any of the six can be `null`/`[]` for a title Spool hasn't
enriched yet (see below) without breaking anything else.

An empty range or a profile with no watches in it returns
`{"profile_id": 1, "since": "...", "until": "...", "history": []}` - a
valid, common response, not an error.

**Errors:** `404` if `id` doesn't match any profile (opted in or not -
this endpoint never reveals which ids exist). `422` if `since`/`until`
are missing, not valid dates, or `since` isn't before `until`. `401` for
a missing/invalid/revoked key.

## Backfilling existing titles

`country`/`studio`/`network`/`cast`/`directors`/`writers` are populated
automatically for every title synced *after* this feature was added -
Trakt/Simkl/CSV import/Nuvio/the Scrobble API/Discover's own preview
materialize all attach it at the same time they already look a title up
on TMDB. A title tracked *before* that has none of the six yet; run this
once after upgrading to backfill them (rate-limited, safe to re-run):

```
docker compose exec web python manage.py enrich_titles_reports_metadata
```

## SSO ("Manage Wrapped")

Settings → Server Integrations has a **Manage Wrapped** button for
owners, once spool-wrapped's URL and a shared secret are set there (the
same secret set in spool-wrapped's own config) - it signs a short-lived
(60s), single-use token and redirects straight into spool-wrapped's admin
UI, no separate login. This is a one-time signed redirect, not full OAuth
- see spool-wrapped's own docs for what it does with the token.

## What this doesn't do

- **No write access.** Nothing reachable through this API ever creates or
  modifies anything in Spool.
- **No pagination.** A single profile's monthly/yearly history is bounded
  enough that this hasn't been needed.
- **No poster/backdrop URLs.** Every field here is text/numeric - if you
  need artwork, look it up on TMDB yourself using the title/year.
