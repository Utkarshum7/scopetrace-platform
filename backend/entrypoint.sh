#!/usr/bin/env sh
# ScopeTrace container entrypoint — shared by the `api` and `worker` (Phase 5)
# images/services, since they're the same build with a different CMD.
#
# Migrations, static collection, and seeding are release-phase concerns and
# must run exactly once — not once per service replica. Set RUN_MIGRATIONS=false
# on any service that isn't the single migration owner (the `worker` service in
# docker-compose.yml) to skip straight to exec'ing the container command.
set -e

if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
  echo "[entrypoint] Applying database migrations..."
  python manage.py migrate --noinput

  echo "[entrypoint] Collecting static files..."
  python manage.py collectstatic --noinput

  if [ "${BOOTSTRAP_DATA:-false}" = "true" ]; then
    echo "[entrypoint] Seeding bootstrap data..."
    python manage.py bootstrap_data
    echo "[entrypoint] Seeding carbon reference data + factors..."
    python manage.py seed_carbon
  fi
else
  echo "[entrypoint] RUN_MIGRATIONS=false — skipping migrate/collectstatic/seed."
fi

echo "[entrypoint] Launching: $*"
exec "$@"
