← [Back to README](../README.md)

# Local development (without Docker)

```bash
python -m venv .venv && source .venv/Scripts/activate   # or .venv/bin/activate on Linux/Mac
pip install -r requirements.txt
npm install
npx @tailwindcss/cli -i ./static/src/app.css -o ./static/dist/app.css --watch   # separate terminal
cp .env.example .env   # DATABASE_URL can be left unset to fall back to sqlite
python manage.py migrate
python manage.py bootstrap_admin   # or createsuperuser, see Quick start
python manage.py runserver
```

Celery/Redis-dependent features (Trakt/Simkl sync) won't run without a
Redis instance and a `celery -A spool worker` process alongside the dev
server; everything else works against just `runserver` + sqlite.

Want the app populated with realistic demo data instead of an empty
account (handy for trying features out, or for taking your own
screenshots)? `python manage.py seed_demo` — refuses to touch anything
outside `DEBUG=True` on purpose, see its `--help`.

See [CONTRIBUTING.md](../CONTRIBUTING.md) for what's expected before
opening a PR (tests, Tailwind rebuild, `VERSION`/`CHANGELOG.md`).
