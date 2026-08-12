<p align="center">
  <img src="docs/images/banner.png" alt="Spool" width="640">
</p>

<p align="center">
  <a href="https://github.com/aljaz-h/spool-tracker/actions/workflows/docker-publish.yml"><img src="https://github.com/aljaz-h/spool-tracker/actions/workflows/docker-publish.yml/badge.svg" alt="Docker image build status"></a>
  <img src="https://img.shields.io/badge/Django-5.2%20LTS-0C4B33?logo=django&logoColor=white" alt="Django 5.2 LTS">
  <img src="https://img.shields.io/badge/docker-compose-2496ED?logo=docker&logoColor=white" alt="Docker Compose">
</p>

<p align="center">
  Track what your household watches — movies, TV, and anime — on your own
  server, not someone else's. A self-hosted Trakt/Simkl
  alternative: Django + Postgres + Redis/Celery, server-rendered with HTMX,
  shipped as a five-container Docker Compose stack you can be running in
  under five minutes.
</p>

> **Why not just use Trakt or Simkl?** Your watch history stays on your
> own server instead of a third party's — no account limits, no ads, no
> paywalled features, and multiple household members can share one
> instance without separate paid accounts each.

## Screenshots

Every screenshot below is a real render of the app against seeded demo
data (`manage.py seed_demo`) — nothing mocked up.

|  |  |
|---|---|
| ![Dashboard](docs/images/screenshots/dashboard.png) Dashboard — streak, up next, watching, watchlist | ![Movies & TV](docs/images/screenshots/discover.png) Movies & TV — trending/popular/upcoming/top rated, live from TMDB |
| ![Title detail](docs/images/screenshots/title-detail.png) Title detail — cast, lists, recommend-to-a-housemate | ![Stats](docs/images/screenshots/stats.png) Stats — streaks, genre breakdown, watch time |

<p align="center"><img src="docs/images/screenshots/calendar.png" alt="Calendar" width="720"><br>Calendar — episodes, season premieres, and movie releases from what you're watching and your watchlists</p>

A household member can each get their own profile, watch history, lists,
and stats on one instance. History can be logged manually, imported from
a CSV export, or synced automatically from a connected Trakt or Simkl
account.

## Features

- Dashboard: continue watching, up next, recently added to lists, quick stats
- Movies & TV / Anime library views with Watching / Watchlist / History tabs
- Full watch history with filters, pagination, and per-item removal
- Calendar of upcoming episodes/season premieres/movie releases, synced nightly from TMDB for anything you're watching, have watchlisted, or have watch history for
- Shared and private lists (shared lists are creator-only for edit/delete)
- Stats: watch streaks, genre breakdown, year breakdown, activity heatmap
- Activity feed across profiles (only shown once a second profile exists)
- CSV import with column-mapping and a preview-before-commit step
- Trakt / Simkl OAuth connect, Nuvio email/password connect, + a daily background sync job

## Get started

```bash
git clone https://github.com/aljaz-h/spool-tracker.git
cd spool-tracker
./setup.sh          # or .\setup.ps1 on Windows — writes a working .env and brings the stack up
```

Already running other Docker Compose stacks and don't want to clone the
repo? See [Quick start → deploying the pre-built image](docs/QUICKSTART.md#deploying-without-cloning-the-repo-pre-built-image)
instead.

## Documentation

| Guide | |
|---|---|
| 🚀 **[Quick start](docs/QUICKSTART.md)** | Docker Compose install (from source or the pre-built image), first login. |
| ⚙️ **[Configuration](docs/CONFIGURATION.md)** | Every `.env` variable and `docker-compose.yml`/`docker-compose.prod.yml` setting. |
| 🔗 **[Importing your data](docs/IMPORTING.md)** | Connecting Trakt/Simkl/Nuvio, CSV import, posters, duplicate-title cleanup. |
| 📡 **[Scrobble API](docs/SCROBBLE_API.md)** | A generic webhook for any player or script to report watches directly - no per-app integration needed. |
| 🛠️ **[Updating & backups](docs/MAINTENANCE.md)** | Updating, rolling back a bad release, backing up. |
| ⚠️ **[Known limitations](docs/LIMITATIONS.md)** | What's unverified, out of scope, or not built yet. |
| 💻 **[Local development](docs/DEVELOPMENT.md)** | Running without Docker. |
| 🤝 **[Contributing](CONTRIBUTING.md)** | Dev setup expectations, PR checklist. |
| 📝 **[Changelog](CHANGELOG.md)** | What shipped in each version. |

Bug reports and feature requests are welcome as
[GitHub issues](https://github.com/aljaz-h/spool-tracker/issues) — see
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for community guidelines and
[SECURITY.md](SECURITY.md) for reporting a vulnerability.
