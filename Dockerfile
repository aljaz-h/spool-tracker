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
CMD ["gunicorn", "spool.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]
