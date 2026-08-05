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
