← [Back to README](../README.md)

# Importing your data

## Connecting Trakt / Simkl / WeTrakr / Nuvio / TMDB / MyAnimeList

### Trakt / Simkl / WeTrakr

1. Register an application with the provider:
   - Trakt: create an app at Trakt's API settings page.
   - Simkl: create an app at Simkl's developer settings page.
   - WeTrakr: get an app at `wetrakr.com/profile/settings/api` (apps are
     added on request during their testing phase - ask on their Discord). It
     lists your `client_id` and `client_secret`. A new app may only have
     `urn:ietf:wg:oauth:2.0:oob` as its redirect URI, which Spool cannot use:
     edit the app and add Spool's callback below as well.
2. Set the app's redirect URI to exactly what Spool will send — scheme,
   host, port, and trailing slash all have to match character-for-character,
   since OAuth2 redirect URIs are matched exactly, not just by origin:
   - **Running behind a reverse proxy with a real domain** (the
     `docker-compose.prod.yml` / Nginx Proxy Manager setup):
     `https://<your-domain>/import/trakt/callback/` (`/import/simkl/callback/`
     for Simkl, `/import/wetrakr/callback/` for WeTrakr). This requires
     `DJANGO_ALLOWED_HOSTS` to include that domain and
     `DJANGO_CSRF_TRUSTED_ORIGINS=https://<your-domain>` in `.env` — see
     [Configuration](CONFIGURATION.md). Spool trusts the
     proxy's `X-Forwarded-Proto` header to know the original request was
     HTTPS even though the proxy forwards to it over plain HTTP internally;
     without that, it would generate an `http://` redirect_uri that won't
     match what you registered.
   - **No reverse proxy, accessing directly by host:port**:
     `http://<your-host>:<WEB_PORT>/import/trakt/callback/`. `localhost` and
     `127.0.0.1` are different strings to the provider even though they're
     the same machine — use whichever one you'll actually browse Spool
     through, consistently.
3. Put the client ID/secret in `.env` and restart the web service:
   `docker compose up -d --force-recreate web`
4. Settings & Import → Connect. A daily background sync (04:00 server time)
   keeps a connected account's history up to date afterward. The first
   connection doesn't import anything by itself — it scans your history
   and takes you to **Import Review** (the same page CSV/JSON/ZIP imports
   use) to select what to bring in before anything's written. Reconnecting
   an already-connected account skips review and syncs immediately, same
   as it always has; **Review full import** (next to **Sync now** once
   connected) runs another reviewed import on demand without touching the
   scheduled sync.

**Caveat:** the OAuth flow itself (authorization redirect, token exchange)
follows each provider's documented API shape, but the history-sync response
parsing — especially Simkl's and WeTrakr's — was written against
documentation rather than a live account, since this project was built
without real developer credentials to test against (WeTrakr's was last
checked against its published API reference on 29 October 2026). Do a small
test sync first and spot-check a few titles/episodes before trusting a full
history import.

**WeTrakr's daily quota:** WeTrakr's free plan caps a connected account at
1000 API requests/day (reset at midnight UTC) — VIP accounts get 50,000. On
top of that your **app key** has its own daily budget shared by every account
connected to your Spool: a develop key is small (1,000-2,000 requests a day,
whichever your app's page on WeTrakr shows), a production key gets 300 per
connected user (minimum 2,000) and is granted on request. Spool paces itself
to 900 a day by default; once you have a production key, raise
`WETRAKR_APP_QUOTA_PAUSE_AT` in `.env` to match. Spool checks which plan an account is on right before
a scan starts, and self-paces to stay under a free account's limit rather
than racing it. If a first-time import is large enough that it can't finish
within one day's quota, it **pauses automatically partway through** — Import
Review shows "Paused — waiting for quota" with how much has been checked so
far, and a background job resumes it right after the quota resets, continuing
from exactly where it left off rather than starting over. Nothing is lost
and no action is needed; this can take a few days in a row for a very large
free-tier history. A routine (already-connected, incremental) daily sync
that hits the quota mid-run simply retries the same small delta on its next
scheduled run, the same as if nothing had happened.

### Nuvio

Nuvio has no public developer API or OAuth flow, so this works differently
from Trakt/Simkl above: there's no server-owner setup step at all — every
profile connects with its own Nuvio email/password directly from Settings
& Import → Connect. If the account has more than one Nuvio profile, you'll
be asked to pick one right after signing in.

Nuvio's password is only ever used for that one sign-in request and is
never stored; only the resulting refresh token is, encrypted at rest
(derived from `DJANGO_SECRET_KEY`, no separate key to manage - no access
token is ever persisted at all, since Nuvio's own backend rotates the
refresh token on every use and a fresh access token is requested at the
start of every sync/scan). Same as Trakt/Simkl above, a daily background
sync keeps history/continue-watching progress up to date, and the first
connection goes through **Import Review** before anything's written -
only watch history is reviewable so far; continue-watching progress
stays routine-sync-only for now, so it starts appearing the day after a
first reviewed import via the normal daily sync, not gated behind it.

**Caveat:** Nuvio's sync API (`api.nuvio.tv`) is undocumented and
reverse-engineered — this integration (`tracker/integrations/nuvio.py`) is
built from a third-party open-source reference implementation
([github.com/ellite/scrob](https://github.com/ellite/scrob)), not official
docs, and unverified against a live account from this environment. It
could change or break without notice; a failed sync shows up in
Settings & Import → Logs with whatever error the API actually returned.

### TMDB

TMDB works differently from Trakt/Simkl/WeTrakr above: there's no separate
app/redirect URI to register at all. If `TMDB_API_KEY` is already set (the
same key the Movies & TV / Anime / Calendar pages already use — see
[Configuration](CONFIGURATION.md)), connecting your TMDB account from
Settings & Import → Connect just works — TMDB's own auth flow takes a
callback URL as a plain request parameter rather than a pre-registered one,
so there's nothing to configure on themoviedb.org beyond the API key you
probably already have.

TMDB has no watched-history data of its own — only favorites and a
watchlist (movies and TV). Connecting syncs both into Spool the same way a
backup's own lists restore: new items are added, nothing already on a list
is touched, and items already in your library are matched by TMDB id
directly (no fuzzy matching needed, since every item already carries one).
A daily background sync keeps both lists up to date afterward; the first
connection reviews what's about to be added before anything's written, the
same **Import Review** page every other source uses.

### MyAnimeList

1. Register an application at
   [myanimelist.net/apiconfig](https://myanimelist.net/apiconfig).
2. Set the app's redirect URI to exactly what Spool will send (see the
   Trakt/Simkl/WeTrakr redirect URI notes above for the reverse-proxy vs.
   direct-host distinction — it's `/import/mal/callback/` here).
3. Put the client ID/secret in `.env` as `MAL_CLIENT_ID` / `MAL_CLIENT_SECRET`
   and restart the web service: `docker compose up -d --force-recreate web`
4. Settings & Import → Connect.

MyAnimeList's anime list carries a status (watching/completed/on
hold/dropped/plan to watch), a score, and a cumulative episodes-watched
count — but not *which* episodes, unlike Trakt/Simkl. Spool fills in the
earliest episodes in season/episode order up to that count, matched against
TMDB; a show that can't be matched to a TMDB series at all is skipped rather
than guessed at. "Plan to watch" goes on the Watchlist instead of becoming
watch history. MAL's access tokens last an hour and refresh tokens a month —
if a connection sits completely idle for longer than that, it'll need
reconnecting.

**Caveat:** same caveat as Nuvio above — MyAnimeList's own API documentation
pages weren't reachable while building this (they returned an error to
direct requests), so the OAuth flow and list-endpoint shape were built
against third-party reference clients rather than the primary source. Do a
small test sync first and spot-check a few titles before trusting a full
import.

## Importing a CSV, JSON, or ZIP file

Settings & Import → CSV file. Works with a `.csv`, `.json`, or `.zip` file
(up to 50MB) — Trakt's or Simkl's own CSV export, Trakt's "Export now" zip,
or a generic CSV/JSON. CSV headers are matched case-insensitively with
common aliases (`title`/`name`, `type`/`media_type`, `date`/`watched_date`,
etc.); upload takes you to a preview where you can correct any column the
auto-detection guessed wrong.

Nothing is written to Spool yet at this point — confirming the preview
stages the parsed file as an **Import Review** session instead of
importing directly. The review page shows every row the file contained,
broken down by New/Existing/Duplicate/Error, with filters and search
(server-side, so a 10,000-row export never renders as one giant page) and
selection controls (by content type or by bucket, plus per-row checkboxes)
before anything is committed. Rows that fail to parse or fail validation
(bad date, unknown media type, missing title, a TV/anime row missing its
season/episode) show up under **Error** and are never selected automatically.
A row that would just duplicate a watch you've already logged shows up
under **Duplicate**, also unselected by default — importing it would have
no effect. Only what you leave selected when you click **Import** actually
gets written; **Cancel** discards the staged review data and imports
nothing.

Titles are matched the same way Trakt/Simkl/WeTrakr/Nuvio's own sync
matches them (provider id → TMDB id → fuzzy name/year → reuse across
providers), so importing a title already pulled in via Trakt/Simkl/WeTrakr
sync — or reviewing the same file twice — won't create duplicates.

## Importing from IMDb, Letterboxd, or TV Time

None of the three has a public API Spool can sync against, so these all
work by uploading an export file you download yourself — same Import
Review flow as above (nothing written until you confirm), just with
dedicated buttons in Settings & Import since none of their formats are
the generic CSV shape.

**IMDb** — imdb.com/list/ratings for your ratings export, imdb.com/watchlist
for your watchlist export; each has its own **Export** button. Upload
either CSV under **Import & Export**'s IMDb card — which one it is gets
detected from its columns automatically, no need to pick.

IMDb's ratings export is the only "watched" signal it has at all — there's
no separate watched-but-not-rated history to import, because IMDb doesn't
track or export one. A rating's own "Date Rated" is used as the watched
date (the closest thing available; it's when you rated it, not necessarily
when you watched it). An individual episode rating is skipped, not guessed
at — the export has no season/episode number to place it correctly.
Watchlist rows go onto your Spool Watchlist, matched by their IMDb id when
available for an accurate TMDB/poster match, name+year otherwise.

**Letterboxd** — Settings → Import & Export → **Export your data**
downloads one zip containing `watched.csv`, `ratings.csv`, `diary.csv` and
`watchlist.csv`. Upload the whole zip under the Letterboxd card. `watched.csv`
(real watch history — every film marked watched, rated or not) is merged
with `ratings.csv` by title+year to attach a rating where one exists
(Letterboxd's 0.5–5★ scale is doubled onto Spool's 1–10); `watchlist.csv`
goes onto your Spool Watchlist. Letterboxd is films-only, so everything
imports as a movie. None of Letterboxd's own export rows carry a stable,
public film id (`Letterboxd URI` is a private per-account short link, not
one), so matching is always by title+year — same fuzzy-match limitation a
generic CSV import already has.

**TV Time** — TV Time itself shut down on 2026-07-15, so this is for
migrating a historical export, not an ongoing sync. TV Time never had its
own data-export feature; the export comes from
[Liberator](https://github.com/Hobo-Ware/tv-time-liberator), a third-party
browser extension (add it, sign into TV Time, click its icon, download the
CSV). Upload that CSV under the TV Time card.

Liberator's export carries real per-episode watch history (exact season
and episode numbers, not a guess) and per-movie/episode ratings, plus
which shows and movies you're still mid-way through — those go onto your
Spool Watchlist, the rest become History. Matched by IMDb id when the
export has one for a row, name+year otherwise — Liberator also exports a
TVDB id, but Spool has nothing to match a TVDB id against, so that column
is unused.

## Posters

Trakt's (and WeTrakr's) API doesn't serve poster images at all, and CSV
exports never do either, so titles created via Trakt/Simkl/WeTrakr sync or
CSV import get a best-effort poster looked up from TMDB at creation time — set
`TMDB_API_KEY` (a free v3 API key from themoviedb.org) in `.env` and
restart `web`/`worker` for this to take effect. It's matched by title +
year search, so an unusual title or a wrong year in the source data can
occasionally miss; titles with no match just keep the gradient-placeholder
fallback rather than erroring.

If you connected Trakt/Simkl (or ran a CSV import) *before* setting
`TMDB_API_KEY`, those titles were created without a poster and won't
retroactively get one — backfill them once the key's in place:

```bash
docker compose exec web python manage.py backfill_posters
```

This looks up every title that currently has no `poster_url`, so it's
safe to re-run any time (already-illustrated titles are skipped).

## Duplicate titles from multiple sync sources

Trakt/Simkl/Nuvio sync each used to only check their *own* provider id
for an already-tracked title before creating a new one — so a movie or
show already synced through one provider got a second, duplicate Title
(with its own WatchEvent) the first time a *different* provider synced
it too. Symptoms: a title showing "not watched" on Movies & TV/Anime
despite History showing it watched, or the same watch appearing twice in
History at the exact same timestamp. Fixed going forward (all three now
reuse an existing Title matched by TMDB id first), but any duplicates
already created need a one-time cleanup:

```bash
docker compose exec web python manage.py merge_duplicate_titles
```

Dry run by default — prints what it would merge without changing
anything. Add `--commit` to actually merge (moves watch history/progress/
ratings/list entries onto the older of the two Titles, deduping any
exact-duplicate watches in the process, then deletes the duplicate).
