# Stage 1: compiles Tailwind's CSS output only. Node/npm here pull in
# Debian's own nodejs/npm packaging, which drags in a large tree of old
# vendored JS tooling (eslint, babel, etc.) with its own known CVEs -
# none of that, nor node/npm themselves, are needed once static/dist/
# app.css exists, so this whole stage (and everything apt/npm installed
# into it) is discarded rather than shipped in the runtime image below.
FROM node:20-slim AS css-builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npx @tailwindcss/cli -i ./static/src/app.css -o ./static/dist/app.css --minify

# Stage 2: the actual runtime image - Python/Django plus the one CSS
# artifact stage 1 produced. No nodejs/npm/node_modules here at all.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

# `apt-get upgrade` picks up whatever Debian security patches (e.g.
# openssl) have shipped since python:3.12-slim's own layer was last
# rebuilt - the base image tag doesn't refresh on every point-release, so
# without this the image can carry known-fixed CVEs indefinitely between
# base-image bumps. libpq5 is the actual runtime dependency (psycopg
# needs it to talk to Postgres) - unlike node/npm above, this one stays.
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
    libpq5 && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=css-builder /app/static/dist/app.css ./static/dist/app.css
# SILK_ENABLED=True here only for this collectstatic run - unrelated to
# whether the runtime .env actually turns Silk on. Silk is meant to be
# toggled on/off at runtime via .env without a rebuild (see its own
# comment in settings.py), but collectstatic only ever runs here, at
# build time - if Silk weren't in INSTALLED_APPS for this one step, its
# own CSS/JS/fonts would never make it into the static manifest, and
# enabling SILK_ENABLED later would 500 on /silk/ with "Missing
# staticfiles manifest entry" no matter what, until the next rebuild.
# Collecting its files into every image unconditionally is harmless when
# SILK_ENABLED is actually False at runtime - they just sit unused,
# since silk's own URLs are only ever registered when it's on.
RUN DJANGO_SECRET_KEY=build-time-only SILK_ENABLED=True python manage.py collectstatic --noinput

EXPOSE 8000
# Shell form (not exec-form CMD) so GUNICORN_WORKERS/GUNICORN_THREADS can
# come from the environment (docker-compose.yml's env_file: .env already
# passes anything set there through) - `exec` still hands off to gunicorn
# as PID 1 so it gets SIGTERM directly for a graceful shutdown, same as
# exec-form CMD would, instead of leaving a shell wrapper in between.
# --threads lets one worker serve another request while the current one
# is blocked on an external call (TMDB/Trakt/Simkl/Gemini) instead of
# tying up an entire worker process for that call's whole latency.
CMD exec gunicorn spool.wsgi:application --bind 0.0.0.0:8000 --workers ${GUNICORN_WORKERS:-3} --threads ${GUNICORN_THREADS:-2}
