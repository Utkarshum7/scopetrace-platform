#!/usr/bin/env sh
# ScopeTrace API container entrypoint.
# Runs migrations + static collection on start, optionally seeds bootstrap data,
# then execs the container command (gunicorn by default).
set -e

echo "[entrypoint] Applying database migrations..."
python manage.py migrate --noinput

echo "[entrypoint] Collecting static files..."
python manage.py collectstatic --noinput

if [ "${BOOTSTRAP_DATA:-false}" = "true" ]; then
  echo "[entrypoint] Seeding bootstrap data..."
  python manage.py bootstrap_data
fi

echo "[entrypoint] Launching: $*"
exec "$@"
