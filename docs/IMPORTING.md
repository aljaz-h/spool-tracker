← [Back to README](../README.md)

# Importing your data

## Connecting Trakt / Simkl / Nuvio

### Trakt / Simkl

1. Register an application with the provider:
   - Trakt: create an app at Trakt's API settings page.
   - Simkl: create an app at Simkl's developer settings page.
2. Set the app's redirect URI to exactly what Spool will send — scheme,
   host, port, and trailing slash all have to match character-for-character,
   since OAuth2 redirect URIs are matched exactly, not just by origin:
   - **Running behind a reverse proxy with a real domain** (the
     `docker-compose.prod.yml` / Nginx Proxy Manager setup):
     `https://<your-domain>/import/trakt/callback/` (or `/import/simkl/callback/`
     for Simkl). This requires `DJANGO_ALLOWED_HOSTS` to include that domain
     and `DJANGO_CSRF_TRUSTED_ORIGINS=https://<your-domain>` in `.env` — see
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
parsing — especially Simkl's — was written against documentation rather
than a live account, since this project was built without real developer
credentials to test against. Do a small test sync first and spot-check a
few titles/episodes before trusting a full history import.

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

Titles are matched the same way Trakt/Simkl/Nuvio's own sync matches them
(provider id → TMDB id → fuzzy name/year → reuse across providers), so
importing a title already pulled in via Trakt/Simkl sync — or reviewing
the same file twice — won't create duplicates.

## Posters

Trakt's API doesn't serve poster images at all, and CSV exports never do
either, so titles created via Trakt/Simkl sync or CSV import get a
best-effort poster looked up from TMDB at creation time — set
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
