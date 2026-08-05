← [Back to README](../README.md)

# Quick start (Docker Compose)

This is the whole install — there's no separate app-server setup.

```bash
git clone https://github.com/aljaz-h/spool-tracker.git
cd spool-tracker
```

**Fastest path** — `setup.sh` (Linux/Mac) or `setup.ps1` (Windows) writes a
working `.env` for you (random `DJANGO_SECRET_KEY`/`DB_PASSWORD`, prompts
for the hostname you'll browse Spool through) and brings the stack up:

```bash
./setup.sh
```
```powershell
.\setup.ps1
```

It's safe to re-run — it never overwrites an existing `.env`. Skip ahead to
[First login](#first-login) once it finishes.

**Or configure manually:**

```bash
cp .env.example .env
```

Edit `.env`:
- Set `DJANGO_SECRET_KEY` to a random string.
- Set `DB_PASSWORD` (and match it inside `DATABASE_URL`).
- Set `DJANGO_ALLOWED_HOSTS` to the hostname(s)/IP you'll access Spool
  through (e.g. `DJANGO_ALLOWED_HOSTS=spool.example.com` or your VPS's IP).
- If you're serving over HTTPS behind a reverse proxy, set
  `DJANGO_CSRF_TRUSTED_ORIGINS=https://spool.example.com`.
- Set `ADMIN_USERNAME` / `ADMIN_PASSWORD` / `ADMIN_DISPLAY_NAME` — the web
  service creates this account automatically on first boot (see
  [First login](#first-login) below).

See [Configuration](CONFIGURATION.md) for the full variable reference.

Then:

```bash
docker compose up -d --build
```

This builds the image, starts Postgres/Redis, runs migrations, bootstraps
the admin account, and starts the app, worker, and scheduler. Check
`docker compose ps` — all five services (`db`, `redis`, `web`, `worker`,
`scheduler`) should report healthy/running within about a minute, and
`curl http://localhost:8000/healthz` (or whatever `WEB_PORT` you set) should
return `ok`. `/healthz` actually probes the database and Redis connections,
so a non-`ok` response means one of them is genuinely unreachable, not just
that the process hasn't started yet.

## First login

Log in with the `ADMIN_USERNAME`/`ADMIN_PASSWORD` you set in `.env`. This
account is created automatically the first time the `web` service starts
with no profile yet in the database — it's idempotent, so it's safe to
leave in `docker compose`'s startup command permanently; it no-ops on every
restart once a profile exists.

If you'd rather not put a real password in `.env` at all, leave
`ADMIN_PASSWORD` blank and create the account yourself instead:

```bash
docker compose exec web python manage.py createsuperuser
docker compose exec web python manage.py shell -c "
from django.contrib.auth.models import User
from tracker.models import Profile
Profile.objects.create(user=User.objects.get(username='<the username you just created>'), display_name='<display name>')
"
```

Additional household members can be added afterward from Settings & Import →
Profiles → "+ Add profile", no shell access needed.

## Deploying without cloning the repo (pre-built image)

If you're adding Spool to a host that already runs other Docker Compose
stacks — e.g. behind an existing Nginx Proxy Manager, Traefik, or
nginx-proxy setup — you don't need the repo at all. Every push to `master`
publishes a ready-to-run image to `ghcr.io/aljaz-h/spool-tracker:latest`
via GitHub Actions (`.github/workflows/docker-publish.yml`), so a new
folder just needs two files:

```bash
mkdir spool && cd spool
curl -o docker-compose.yml https://raw.githubusercontent.com/aljaz-h/spool-tracker/master/docker-compose.prod.yml
curl -o .env https://raw.githubusercontent.com/aljaz-h/spool-tracker/master/.env.example
```

Edit `.env` as described above, then in `docker-compose.yml`:

- If you're running behind a containerized reverse proxy on a shared
  Docker network (Nginx Proxy Manager, Traefik, nginx-proxy), replace
  `CHANGE_ME_TO_YOUR_NPM_NETWORK_NAME` under `networks: proxy:` with that
  network's actual name — find it with
  `docker inspect <your-proxy-container> --format '{{json .NetworkSettings.Networks}}'`
  or `docker network ls`. In your proxy's UI/config, point it at
  `spool-web:8000` (container name : internal port — not the host port).
- If you'd rather reach Spool directly on a host port instead (no
  containerized proxy in the mix), uncomment the `ports:` line under the
  `web` service and remove the `networks:` block instead.

Then:

```bash
docker compose up -d
```

No Dockerfile, no Node/npm, no build step on the VPS — just pulls the
published image.

**GHCR image visibility:** the first time the workflow runs, the package
it creates may default to private, which means `docker compose up -d`
will fail to pull with an auth error. Go to the repo on GitHub → the
"Packages" link in the right sidebar → the `spool-tracker` package →
Package settings → change visibility to Public (there's no secret code in
the image, so this is safe). Alternatively, keep it private and
`docker login ghcr.io` on the VPS with a personal access token that has
`read:packages` scope.
