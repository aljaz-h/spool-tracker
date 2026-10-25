← [Back to README](../README.md)

# Updating, rolling back & backups

## Updating

```bash
git pull
docker compose up -d --build
```

Migrations run automatically as part of the `web` service's startup
command — no separate migrate step needed.

## Rolling back

Every push to `master` publishes a Docker image tagged with that commit's
version number (from the `VERSION` file — see [CHANGELOG.md](../CHANGELOG.md)
for what changed in each one), alongside the usual `:latest`. Those tagged
images aren't deleted when a newer one ships, so going back to a
known-good version is just pinning to it instead of `:latest`:

- **Pre-built image** (`docker-compose.prod.yml`): change the `image:`
  line for `web`, `worker`, and `scheduler` from
  `ghcr.io/aljaz-h/spool-tracker:latest` to e.g.
  `ghcr.io/aljaz-h/spool-tracker:0.65.1`, then `docker compose up -d`.
- **Built from source** (`docker-compose.yml`): there's no git tag for
  each version, just a VERSION-file bump commit — find it in `git log`
  (`git log -- VERSION`) or the commit history on GitHub, cross-checked
  against [CHANGELOG.md](../CHANGELOG.md)'s dated entries, then
  `git checkout <that commit>` and `docker compose up -d --build`.

Move back to `:latest` (or `git checkout master`) once a fix ships.

**Caveat:** this rolls back the application code, not the database. If
the version you're rolling back from already ran a migration that
changed the schema, an older version's code may not agree with it - for
a pure UI/logic bug this is a non-issue, but if you're unsure, restore
the [database backup](#backups) taken before you upgraded too.

## Backups

- `db_data` (Postgres) is the one that matters — it holds all watch
  history, titles, lists, and accounts.
- `media_data` currently only holds transient CSV-import temp files that
  are deleted right after each import completes; nothing long-lived lives
  there yet.
- `redis_data` is cache/broker state, safe to lose.

```bash
docker compose exec db pg_dump -U spool spool > backup.sql
```

## Finding slow queries

**Settings → Maintenance → Query performance** (owner only) lists the
slowest database queries since statistics were last reset - by total time,
by average time, or by how often they run - with how many of their reads
came from cache, plus the tables Postgres mostly reads start to finish
instead of through an index (probable missing indexes). Anything averaging
over 100 ms is flagged. It reads Postgres's own `pg_stat_statements`
extension, so it also covers background tasks and the nightly sync, not just
web requests. "Reset statistics" clears the counters, handy right after a fix
to see whether it helped.

**Turning it on for an existing install:** the compose files now start the
database with `shared_preload_libraries=pg_stat_statements` (it has to be
loaded when Postgres starts), and a migration creates the extension. So
after `git pull` / a redeploy, `docker compose up -d` recreates the
database container once. That is a few seconds of downtime while Postgres
restarts; your data lives in the `db_data` volume and is untouched, but it
is a good moment to take a [backup](#backups) first. Collection costs
roughly 1-3% of database time. The panel says what is missing if it is not
working yet.

On SQLite (the no-Docker dev setup) there is nothing to read; set
`SILK_ENABLED` (see [CONFIGURATION.md](CONFIGURATION.md)) to profile
requests and their SQL instead.
