#!/bin/sh
set -eu
python manage.py check
python manage.py migrate --noinput
python manage.py seed_references
python manage.py collectstatic --noinput --verbosity 0
exec "$@"
