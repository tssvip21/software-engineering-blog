#!/bin/sh
set -eu
export POSTGRES_PASSWORD="$(cat /run/dbkey/password)"
python manage.py migrate --noinput
python manage.py seed_references
python manage.py seed_demo
python manage.py collectstatic --noinput --verbosity 0
exec python deploy/serve.py
