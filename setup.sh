#!/usr/bin/env bash
# One-shot setup: writes a working .env (random secret key + DB password,
# prompts for the hostname you'll browse Spool through) and brings the
# stack up. Safe to re-run - it never overwrites an existing .env.
set -euo pipefail
cd "$(dirname "$0")"

if [ -f .env ]; then
  echo ".env already exists - leaving it alone. Delete it first if you want setup.sh to regenerate it."
else
  cp .env.example .env

  rand_hex() { openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'; }
  SECRET_KEY="$(rand_hex)"
  DB_PASSWORD="$(rand_hex)"

  read -rp "Hostname/IP you'll access Spool through [localhost]: " ALLOWED_HOST
  ALLOWED_HOST="${ALLOWED_HOST:-localhost}"

  # Portable in-place sed (BSD sed on macOS needs -i '', GNU sed needs -i)
  sedi() { sed -i.bak "$1" .env && rm -f .env.bak; }
  sedi "s#^DJANGO_SECRET_KEY=.*#DJANGO_SECRET_KEY=${SECRET_KEY}#"
  sedi "s#^DB_PASSWORD=.*#DB_PASSWORD=${DB_PASSWORD}#"
  sedi "s#^DATABASE_URL=.*#DATABASE_URL=postgres://spool:${DB_PASSWORD}@db:5432/spool#"
  sedi "s#^DJANGO_ALLOWED_HOSTS=.*#DJANGO_ALLOWED_HOSTS=${ALLOWED_HOST}#"

  echo "Wrote .env with a random DJANGO_SECRET_KEY and DB_PASSWORD."
  echo "Edit .env now if you want Trakt/Simkl/TMDB integration, HTTPS via a reverse proxy, or a non-default admin password - see the README's Configuration reference."
fi

echo "Starting Spool (this builds the image and runs migrations - first run can take a minute or two)..."
docker compose up -d --build

echo
echo "Done. Once 'docker compose ps' shows all five services healthy, open http://<the host you entered>:8000"
echo "and sign in with ADMIN_USERNAME/ADMIN_PASSWORD from .env (still the 'changeme' default unless you edited it - see the README's First login section)."
