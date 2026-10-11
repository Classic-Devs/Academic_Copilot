#!/bin/sh
set -e

# Apply database migrations
python manage.py migrate --no-input

# Collect static files for WhiteNoise
python manage.py collectstatic --no-input

# Create a superuser if DJANGO_SUPERUSER_USERNAME/EMAIL/PASSWORD are set
if [ -n "$DJANGO_SUPERUSER_USERNAME" ]; then
    python manage.py createsuperuser --no-input || true
fi

# Hand off control to the Docker CMD instruction (Gunicorn)
exec "$@"
