FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

# Node is needed only at build time to compile Tailwind; the image stays
# slim because we don't keep node_modules around after collectstatic.
RUN apt-get update && apt-get install -y --no-install-recommends \
    nodejs npm libpq5 && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY package*.json ./
RUN npm ci

COPY . .
RUN npx @tailwindcss/cli -i ./static/src/app.css -o ./static/dist/app.css --minify
RUN DJANGO_SECRET_KEY=build-time-only python manage.py collectstatic --noinput

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
