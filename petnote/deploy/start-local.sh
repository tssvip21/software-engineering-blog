#!/bin/sh
set -eu
# This initializer is limited to the isolated local demonstration database.
test "${DJANGO_DEBUG:-}" = "1"
test "${CARE_DEMO_MODE:-}" = "1"
test "${PETNOTE_LOCAL_POSTGRES:-}" = "1"
test "${POSTGRES_HOST:-}" = "db"
test "${POSTGRES_DB:-}" = "petnote_demo"
export POSTGRES_PASSWORD="$(cat /run/dbkey/password)"
python manage.py check
python manage.py migrate --noinput
python manage.py seed_references
python manage.py seed_demo
python manage.py collectstatic --noinput --verbosity 0
exec python deploy/serve.py
