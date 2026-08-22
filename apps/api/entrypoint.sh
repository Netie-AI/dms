#!/bin/sh
# Appliance boot: migrate then exec the CMD (uvicorn). already-at-head is success.
set -eu
if [ -n "${DATABASE_URL:-}" ]; then
  python -c "from dms_api.migrate import run_migrations; import os; run_migrations(os.environ['DATABASE_URL'])"
fi
exec "$@"
