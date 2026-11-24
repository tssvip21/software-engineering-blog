#!/bin/sh
set -eu
export POSTGRES_PASSWORD="$(cat /run/dbkey/password)"
python scripts/prepare_phase7_demo.py
python manage.py collectstatic --noinput --verbosity 0
exec python deploy/serve.py
