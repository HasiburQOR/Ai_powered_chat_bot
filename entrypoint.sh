#!/bin/sh
# Container entrypoint: waits for Postgres, then (web service only) applies
# migrations and collects static files before handing off to the service command.
set -e

echo "Waiting for the database..."
until python manage.py showmigrations >/dev/null 2>&1; do
  sleep 2
done

if [ "$RUN_MIGRATIONS" = "1" ]; then
  echo "Applying database migrations..."
  python manage.py migrate --noinput

  echo "Collecting static files..."
  python manage.py collectstatic --noinput
fi

exec "$@"
