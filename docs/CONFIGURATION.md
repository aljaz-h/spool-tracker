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
| `TRAKT_CLIENT_ID` / `TRAKT_CLIENT_SECRET` | No | Enables Trakt import — see [Importing your data](IMPORTING.md#connecting-trakt--simkl--nuvio). |
| `SIMKL_CLIENT_ID` / `SIMKL_CLIENT_SECRET` | No | Enables Simkl import — see [Importing your data](IMPORTING.md#connecting-trakt--simkl--nuvio). |
| `TMDB_API_KEY` | No | A free v3 API key from themoviedb.org — enables poster lookup and the Movies & TV / Anime / Calendar pages' live TMDB data. Safe to leave blank; those features just have less to show. |
| `TIME_ZONE` | No (default `Europe/Ljubljana` in `.env.example`) | Used for scheduling (nightly sync jobs, calendar day boundaries) and display. Set to your own household's time zone, e.g. `America/New_York`. |
| `SESSION_COOKIE_SECURE` / `CSRF_COOKIE_SECURE` | No (default `False`) | Set both to `True` once Spool is only ever reached over HTTPS — marks the session/CSRF cookies `Secure` so browsers never send them over plain HTTP. Leave `False` for a plain-HTTP/LAN-only setup, or logins will silently fail (the cookie never comes back). |
| `SECURE_SSL_REDIRECT` | No (default `False`) | Redirects any plain-HTTP request to HTTPS. Only turn this on once HTTPS actually works end-to-end (either terminated by your reverse proxy or directly) — see [SECURITY.md](../SECURITY.md). |
| `SECURE_HSTS_SECONDS` / `SECURE_HSTS_INCLUDE_SUBDOMAINS` / `SECURE_HSTS_PRELOAD` | No (default `0`/off) | Tells browsers to only ever use HTTPS for this host, for the given number of seconds. Only enable once you're confident you won't need to fall back to plain HTTP — browsers cache this aggressively. |
| `SILK_ENABLED` | No (default `False`) | Turns on [django-silk](https://github.com/jazzband/django-silk) request/SQL/Python profiling — a dashboard at `/silk/` (restricted to `is_staff` accounts) showing every recorded request's timing, SQL queries, and (with the bundled Python profiler) call-stack breakdown. Real per-request overhead while on, so the intended use is: set it, let it capture real traffic for a day or two, pull what's slow, then unset it again — not a permanently-on setting. Requires `python manage.py migrate` after enabling (adds Silk's own tables). |

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
