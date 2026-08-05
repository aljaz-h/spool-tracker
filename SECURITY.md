# Security Policy

## Supported Versions

Spool is a rolling-release, self-hosted project — there are no
long-term-support branches. Only the latest `master` (and the
correspondingly latest `ghcr.io/aljaz-h/spool-tracker:latest` image tag)
is supported. If you're running an older version, please update before
reporting an issue, if you're able to.

## Reporting a Vulnerability

Please **do not** open a public GitHub issue for a security
vulnerability. Instead:

- Use GitHub's [private vulnerability reporting](https://github.com/aljaz-h/spool-tracker/security/advisories/new)
  (Security tab → "Report a vulnerability") if it's enabled on the repo, or
- Open a regular issue with minimal detail asking a maintainer to reach
  out for a private channel to share specifics.

Please include: what you found, how to reproduce it, and what you think
the impact is (e.g. auth bypass, data exposure across household profiles,
injection, etc.). This is a hobby-scale project maintained in spare time,
so response times aren't guaranteed, but reports are taken seriously and
addressed as soon as practical.

## Scope Notes

Spool is designed to be run on a trusted home network or behind your own
reverse proxy for a small household, not exposed as a multi-tenant public
service — see [docs/LIMITATIONS.md](docs/LIMITATIONS.md) (profiles
share one database by design; anyone with a login can see shared lists
and the Activity feed). Reports specific to that trust model (e.g. "one
household member can see another's shared list") are expected behavior,
not vulnerabilities — but anything that breaks isolation *between separate
Spool instances*, or lets an unauthenticated request read/write data, is
in scope.

## Deployment Hardening

Most of the real-world security here depends on how you deploy Spool, not
just the code. A few concrete recommendations:

**Don't expose Spool directly to the public internet.** Run it behind a
reverse proxy (Nginx Proxy Manager, Traefik, Caddy, nginx-proxy, ...)
that terminates TLS, and publish the proxy's ports, not `web`'s directly.
Once Spool is reachable only over HTTPS, turn on `SESSION_COOKIE_SECURE`,
`CSRF_COOKIE_SECURE`, and `SECURE_SSL_REDIRECT` in `.env` (all default
`False`, so a plain-HTTP/LAN-only setup isn't broken by an upgrade — see
[docs/CONFIGURATION.md](docs/CONFIGURATION.md#environment-variables-env)
for the full list, including HSTS). `docker-compose.prod.yml`'s
reverse-proxy-network setup already avoids publishing a host port at all
when you go that route.

**Protect `.env`.** It holds `DJANGO_SECRET_KEY`, database credentials,
and (optionally) Trakt/Simkl/TMDB API credentials. Keep its file
permissions restricted to the user running Docker, never commit it to
version control (this repo's `.gitignore` already excludes it), and
don't paste its contents into a public issue or chat when asking for
help.

**Credentials stored in the database** (Trakt/Simkl OAuth tokens, and
any Trakt/Simkl/TMDB credentials entered via Settings → Server instead
of `.env`) are encrypted at rest, not plaintext — derived from
`DJANGO_SECRET_KEY`, so protecting that key protects these too.

**Rotating `DJANGO_SECRET_KEY`.** If it's ever leaked: generate a new
random value, update `.env`, and restart the app. This invalidates every
existing session (everyone is logged out) and CSRF token — expected. It
also makes the encrypted credentials mentioned above undecryptable,
since the encryption key is derived from the same `SECRET_KEY` — after
rotating, reconnect Trakt/Simkl/Nuvio and re-enter any Settings → Server
credentials rather than expecting the old ones to keep working.

**Rotating Trakt/Simkl/TMDB credentials.** Revoke/regenerate the app on
the provider's own developer console, then update the value either in
`.env` (requires a restart) or Settings → Server (owner-only, takes
effect immediately) — a blank field there falls back to `.env`.

**Database access.** Postgres isn't exposed outside the Docker network
by default — keep it that way. For backups or ad-hoc inspection, prefer
`docker compose exec db psql ...` or an SSH tunnel over publishing the
port.
