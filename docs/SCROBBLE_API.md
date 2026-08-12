← [Back to README](../README.md)

# Scrobble API

Trakt/Simkl (OAuth) and Nuvio (reverse-engineered private API) each need
their own bespoke integration to keep Spool in sync with what you're
watching. If your player or setup isn't one of those three, this is the
alternative: a single, generic, documented endpoint any player author —
or you, with a short script — can POST watch events to directly. No
per-app integration to build or wait on.

## Getting a token

Settings → Integrations → **Custom Player** → *Generate token*. This is a
per-profile bearer credential — every scrobble sent with it is recorded
against that profile, and regenerating it immediately invalidates the old
one (update your player's config too, or it'll start getting `401`s).

Treat it like a password: anyone with the token can log watches (and only
watches — it can't read your library, change settings, or act as any
other profile) against that profile.

## Sending a scrobble

```
POST /api/scrobble
Authorization: Bearer <your token>
Content-Type: application/json
```

```json
{
  "action": "start",
  "media_type": "movie",
  "tmdb_id": 27205,
  "progress": 12.5
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `action` | string | yes | `"start"`, `"pause"`, or `"stop"` — see below. |
| `media_type` | string | yes | `"movie"` or `"tv"`. |
| `tmdb_id` | integer | yes | The title's [TMDB](https://www.themoviedb.org/) id. Most players already know this; if yours only has an IMDb id, TMDB's `/find` endpoint converts it (this API doesn't accept IMDb ids directly). |
| `progress` | number | yes | 0–100, percent watched so far. |
| `season` | integer | if `media_type` is `tv` | |
| `episode` | integer | if `media_type` is `tv` | |
| `title` | string | no | Name hint, used only if Spool doesn't already know this title and looking it up on TMDB fails (e.g. no `TMDB_API_KEY` configured). |
| `year` | integer | no | Same fallback role as `title`. |

**`action` semantics** (deliberately the same shape as Trakt's own public
scrobble API, if you've integrated with that before):

- **`start`** — playback began. Upserts an in-progress entry (Dashboard's
  Watching row) at the given `progress`.
- **`pause`** — still watching, progress updated. Same effect as `start`;
  sent repeatedly as playback continues is the normal, expected pattern
  (e.g. every 30–60s, or whenever the player's own state changes).
- **`stop`** — playback ended. If `progress` is **90 or higher**, this
  logs a completed watch (History, streaks, stats) and clears the
  in-progress entry. Below 90, it's recorded as just another progress
  update — closing a player 20 minutes into a movie is "still watching
  it," not "watched it."

A title Spool has never seen before is looked up on TMDB and created
automatically (needs `TMDB_API_KEY` configured — see
[Configuration](CONFIGURATION.md); without it, or if the id doesn't
resolve, a bare entry is created from `title`/`year` instead). A `tv`
scrobble always needs `season`+`episode` — there's no such thing as an
episode-less watch for a show.

## Response

`200` with `{"watch_event_created": bool, "title_id": int}` — `true` only
for a `stop` that crossed the completed threshold. Bad input is a `422`
with a `detail` message (e.g. missing `season`/`episode` for a `tv`
scrobble). An invalid or missing token is a `401`.

## Example: a simple `curl` scrobble

```bash
# Started watching Inception (tmdb:27205)
curl -X POST https://your-spool-instance/api/scrobble \
  -H "Authorization: Bearer $SPOOL_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action": "start", "media_type": "movie", "tmdb_id": 27205, "progress": 0}'

# ...later, finished it
curl -X POST https://your-spool-instance/api/scrobble \
  -H "Authorization: Bearer $SPOOL_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action": "stop", "media_type": "movie", "tmdb_id": 27205, "progress": 95}'
```

## What this doesn't do

- **No pull/history backfill.** This only ever records what you scrobble
  going forward — it's not a way to import existing watch history (see
  [Importing your data](IMPORTING.md) for that).
- **One profile per token**, always. There's no way to scrobble on behalf
  of a household member other than sharing your own token with them,
  which isn't recommended (it also revokes your access whenever they
  need a fresh one).
