#!/bin/sh
set -eu
test "$CARE_PUBLIC_DEMO" = "1"
test "$CARE_DEMO_MODE" = "1"
test "$DJANGO_DEBUG" = "0"
test -z "${POSTGRES_HOST:-}"
test -r /app/demo-seed.sqlite3
if [ ! -f /app/data/demo.sqlite3 ]; then
    cp /app/demo-seed.sqlite3 /app/data/demo.sqlite3
    chmod 0600 /app/data/demo.sqlite3
fi
python manage.py check
python manage.py migrate --noinput
python manage.py seed_demo --upgrade-public-seed
exec "$@"
