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
