← [Back to README](../README.md)

# Configuration

Everything is configured through `.env` (read by both Compose files) and,
for a couple of deployment-shape choices, the `docker-compose.yml` /
`docker-compose.prod.yml` file itself. `.env.example` has the same
variables with inline comments — this page is the fuller reference.

## Environment variables (`.env`)

| Variable | Required? | Purpose |
|---|---|---|
| `WEB_PORT` | No (default `8000`) | Host port the `web` service is published on. Only meaningful in `docker-compose.yml` — `docker-compose.prod.yml`'s `ports:` block is commented out by default (see below). |
| `DJANGO_SECRET_KEY` | **Yes** | Django's cryptographic signing key (sessions, CSRF tokens, password reset). No safe default — generate a random string and never commit a real one. |
| `DJANGO_ALLOWED_HOSTS` | **Yes** | Comma-separated hostnames/IPs you'll actually browse Spool through, e.g. `spool.example.com` or a bare IP. Django rejects requests with a `Host` header not on this list. |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Only behind HTTPS reverse proxy | Full origin (`https://spool.example.com`), not just a hostname. Required whenever you're serving over HTTPS through a proxy — without it, form submissions/logins fail CSRF validation. |
| `DEBUG` | No (default `False`) | Leave `False` in any deployment reachable by more than just you — Django's debug pages leak settings/stack traces. Only relevant for [local development](DEVELOPMENT.md). |
| `DATABASE_URL` | **Yes** | Postgres connection string. Must agree with `DB_PASSWORD` below (the password appears in both). Can be left unset for local dev to fall back to SQLite. |
| `DB_PASSWORD` | **Yes** (with Postgres) | Also sets Postgres's own `POSTGRES_PASSWORD` in the `db` service — must match the password embedded in `DATABASE_URL`. |
| `REDIS_URL` | **Yes** | Redis connection for Celery's broker/result backend defaults and general cache use. |
| `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` | **Yes** | Usually the same Redis instance as `REDIS_URL`, on database index 0. |
| `CACHE_URL` | **Yes** | A separate Redis database index (default `2`, i.e. `redis://redis:6379/2`) from the Celery ones above — caches TMDB discovery results (Movies & TV / Anime pages) across gunicorn's worker processes. |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` / `ADMIN_DISPLAY_NAME` | No | First-run bootstrap account — created automatically if no profile exists yet. Leave `ADMIN_PASSWORD` blank to skip and create an account yourself instead (see [Quick start → First login](QUICKSTART.md#first-login)). |
| `TRAKT_CLIENT_ID` / `TRAKT_CLIENT_SECRET` | No | Enables Trakt import — see [Importing your data](IMPORTING.md#connecting-trakt--simkl--wetrakr--nuvio--tmdb--myanimelist). |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`, `DROPBOX_APP_KEY` / `DROPBOX_APP_SECRET` | No | Lets profiles schedule automatic backups to Google Drive / Dropbox (also settable under Server Integrations) — see [Backups](BACKUPS.md). |
| `BACKUP_ROOT` | No | Where "Server folder" backups are written (default `/app/backups`; mount a NAS share there) — see [Backups](BACKUPS.md). |
| `SIMKL_CLIENT_ID` / `SIMKL_CLIENT_SECRET` | No | Enables Simkl import — see [Importing your data](IMPORTING.md#connecting-trakt--simkl--wetrakr--nuvio--tmdb--myanimelist). |
| `WETRAKR_CLIENT_ID` / `WETRAKR_CLIENT_SECRET` | No | Enables WeTrakr import — see [Importing your data](IMPORTING.md#connecting-trakt--simkl--wetrakr--nuvio--tmdb--myanimelist). WeTrakr's free plan caps a connected account at 1000 API requests/day, and a develop app key has its own small daily budget (Spool paces itself to 900/day by default - raise `WETRAKR_APP_QUOTA_PAUSE_AT` once you have a production key); a large first import that can't finish in one day pauses automatically and resumes the next day without losing progress — see that same section. |
| `MAL_CLIENT_ID` / `MAL_CLIENT_SECRET` | No | Enables connecting a MyAnimeList account to sync your anime list — see [Importing your data](IMPORTING.md#connecting-trakt--simkl--wetrakr--nuvio--tmdb--myanimelist). |
| `TMDB_API_KEY` | No | A free v3 API key from themoviedb.org — enables poster lookup, the Movies & TV / Anime / Calendar pages' live TMDB data, and connecting a TMDB account to sync favorites/watchlist (no separate credential needed for that — see [Importing your data](IMPORTING.md#connecting-trakt--simkl--wetrakr--nuvio--tmdb--myanimelist)). Safe to leave blank; those features just have less to show. |
| `FANART_API_KEY` | No | A free personal API key from [fanart.tv/get-an-api-key](https://fanart.tv/get-an-api-key/) — adds transparent clearlogos/clearart and better backdrops to a title's own page, over TMDB's plain poster/backdrop. Purely cosmetic; TMDB images are always the fallback. Safe to leave blank. |
| `FANART_REFRESH_DAYS` | No (default `30`) | How long a title's fetched Fanart artwork is trusted before re-checking for anything new — artwork is near-static once uploaded, so this doesn't need to be short. |
| `TIME_ZONE` | No (default `Europe/Ljubljana` in `.env.example`) | Used for scheduling (nightly sync jobs, calendar day boundaries) and display. Set to your own household's time zone, e.g. `America/New_York`. |
| `SESSION_COOKIE_AGE` | No (default `1209600`, 14 days) | Idle timeout in seconds — a signed-in session only goes stale after this many seconds with *no* requests at all, not a flat countdown from login (using the site keeps resetting the clock). Shorten this on a shared/public machine. |
| `SESSION_COOKIE_SECURE` / `CSRF_COOKIE_SECURE` | No (default `False`) | Set both to `True` once Spool is only ever reached over HTTPS — marks the session/CSRF cookies `Secure` so browsers never send them over plain HTTP. Leave `False` for a plain-HTTP/LAN-only setup, or logins will silently fail (the cookie never comes back). |
| `SECURE_SSL_REDIRECT` | No (default `False`) | Redirects any plain-HTTP request to HTTPS. Only turn this on once HTTPS actually works end-to-end (either terminated by your reverse proxy or directly) — see [SECURITY.md](../SECURITY.md). |
| `SECURE_HSTS_SECONDS` / `SECURE_HSTS_INCLUDE_SUBDOMAINS` / `SECURE_HSTS_PRELOAD` | No (default `0`/off) | Tells browsers to only ever use HTTPS for this host, for the given number of seconds. Only enable once you're confident you won't need to fall back to plain HTTP — browsers cache this aggressively. |
| `SILK_ENABLED` | No (default `False`) | Turns on [django-silk](https://github.com/jazzband/django-silk) request/SQL/Python profiling — a dashboard at `/silk/` (restricted to `is_staff` accounts) showing every recorded request's timing, SQL queries, and (with the bundled Python profiler) call-stack breakdown. Real per-request overhead while on, so the intended use is: set it, let it capture real traffic for a day or two, pull what's slow, then unset it again — not a permanently-on setting. Requires `python manage.py migrate` after enabling (adds Silk's own tables). |
| `SENTRY_DSN` | No | Sends unhandled errors (and, with `SENTRY_TRACES_SAMPLE_RATE` above 0, performance traces) to [Sentry](https://sentry.io). Leave blank to disable entirely — nothing is sent anywhere, no extra dependency cost. Get a DSN by creating a free account/project (Platform: Django) at sentry.io. Covers gunicorn, the Celery worker, and Celery beat alike. |
| `SENTRY_ENVIRONMENT` | No (default `production`) | Tags every event with this environment name — useful if you also run a staging/test instance and want them to show up separately in Sentry. |
| `SENTRY_TRACES_SAMPLE_RATE` | No (default `0.0`) | Fraction (0–1) of requests/tasks to capture a full performance trace for — view time plus a waterfall of DB query spans, which is what actually shows *what's* slow, not just *that* something was slow. `0` means errors only. Counts against Sentry's quota, so start low (e.g. `0.1`–`0.2`) rather than `1.0` on a real production site. |
| `SENTRY_SEND_DEFAULT_PII` | No (default `False`) | Set `True` to let Sentry capture request details like cookies/IPs/usernames alongside errors — off by default since that's personal data leaving your server for a third-party service. |

## `docker-compose.yml` vs `docker-compose.prod.yml`

Two Compose files, same five services (`db`, `redis`, `web`, `worker`,
`scheduler`), different image source:

- **`docker-compose.yml`** — `build: .`, used by the [Quick start](QUICKSTART.md)
  flow when you've cloned the repo. Every `docker compose up -d --build`
  rebuilds the image from your local checkout.
- **`docker-compose.prod.yml`** — `image: ghcr.io/aljaz-h/spool-tracker:latest`
  for all three app services, used by the
  [pre-built-image deployment](QUICKSTART.md#deploying-without-cloning-the-repo-pre-built-image).
  No Dockerfile, no Node/npm, no build step — just pulls. Pin this to a
  specific version instead of `:latest` to [roll back](MAINTENANCE.md#rolling-back).

Settings that live in the Compose file itself rather than `.env`:

- **Host port** (`docker-compose.yml` only) — `ports: - "${WEB_PORT:-8000}:8000"`
  on the `web` service, driven by `.env`'s `WEB_PORT`.
  `docker-compose.prod.yml` comments this out by default (see next point).
- **Reverse proxy network** (`docker-compose.prod.yml` only) — the `web`
  service joins an external Docker network named `proxy`, so a
  containerized reverse proxy (Nginx Proxy Manager, Traefik, nginx-proxy)
  on that same network can reach it by container name (`spool-web:8000`)
  without a published host port at all. Replace
  `CHANGE_ME_TO_YOUR_NPM_NETWORK_NAME` under `networks: proxy:` with your
  proxy's actual network name. If you don't run a containerized proxy,
  uncomment the `ports:` line under `web` instead and delete the
  `networks:` block — see [Quick start](QUICKSTART.md#deploying-without-cloning-the-repo-pre-built-image).
- **Volumes** — `db_data`, `redis_data`, `media_data`, identical in both
  files. See [Backups](MAINTENANCE.md#backups) for what each one holds.
- **Image tag** (`docker-compose.prod.yml` only) — `:latest` by default;
  pin to a specific version to control exactly what's running, or to
  [roll back](MAINTENANCE.md#rolling-back) after a bad release.
