#!/bin/sh

# Run Django database migrations
python manage.py migrate

# Collect all CSS, JavaScript, and Image files for production
python manage.py collectstatic --no-input

# Create a superuser automatically if it doesn't exist
python manage.py createsuperuser --no-input || true

# Hand off control to the Docker CMD instruction (Gunicorn)
exec "$@"
