# Contributing to Spool

Thanks for considering a contribution. Spool is a small, self-hosted project,
so keep expectations proportionate — a focused bug fix or a well-scoped
feature is easier to review and merge than a sweeping rewrite.

## Dev setup

See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for running Spool locally
without Docker (venv, Tailwind watch mode, sqlite fallback, seeding demo
data).

## Before opening a PR

- **Run the full test suite** and make sure it's green:
  `python manage.py test tracker -v 1` (use the venv's own Python — a
  global interpreter without this project's dependencies installed will
  fail in confusing ways). Don't add `--parallel` on Windows - Django's
  multiprocessing test runner crashes here with `TypeError: cannot
  pickle 'traceback' object` partway through this suite (a known
  Django/Windows `spawn`-vs-`fork` limitation, not something specific
  to any one test); it may be worth revisiting on Linux/CI. While
  iterating, run just the test class(es) your change touches
  (`manage.py test tracker.tests.YourTestClass`) and save the full run
  for before opening the PR.
- **Rebuild Tailwind** (`npm run build:css`) if you added or changed any
  template class combinations — the compiled CSS only contains classes it
  has actually seen used somewhere in the templates at build time.
- **Bump `VERSION`** (semantic versioning) **and add a dated entry to
  `CHANGELOG.md`** (Keep a Changelog format) for any change to the app
  itself — one entry per logical change, not per commit. Repo-only
  changes (README/docs, CI workflows, `.md` files) don't get a bump.
- Keep changes scoped to what the PR describes. If you spot an unrelated
  issue while working, mention it in the PR description or open a separate
  issue rather than folding an unrelated fix in.

## Reporting bugs / requesting features

Open a GitHub issue. For bugs, include: what you expected, what happened
instead, and enough context to reproduce it (Spool version, browser if
it's a UI issue, relevant log output from `docker compose logs web` if
it's a backend error).

## Reporting a security issue

See [SECURITY.md](SECURITY.md) — please don't open a public issue for
anything that looks like a vulnerability.
