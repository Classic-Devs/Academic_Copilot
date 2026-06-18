#!/bin/sh

# Run Django database migrations
python manage.py migrate

# Hand off control to the Docker CMD instruction (Gunicorn)
exec "$@"