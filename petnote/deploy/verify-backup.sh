#!/bin/bash
set -euo pipefail
cd /opt/taily/app
compose=(docker compose --env-file /opt/taily/production.env -p taily)
backup=$(find /var/backups/taily -maxdepth 1 -type f -name 'taily-*.dump' | sort | tail -n 1)
test -n "$backup"
sha256sum -c "$backup.sha256"
check_db="restore_check_$(date -u +%Y%m%d%H%M%S)"
"${compose[@]}" exec -T db createdb -U taily "$check_db"
# Only this newly created database is used; the production database is untouched.
"${compose[@]}" exec -T db pg_restore -U taily -d "$check_db" --exit-on-error < "$backup"
"${compose[@]}" exec -T db psql -U taily -d "$check_db" -v ON_ERROR_STOP=1 -c 'SELECT COUNT(*) AS restored_migrations FROM django_migrations; SELECT COUNT(*) AS restored_references FROM care_reference;'
"${compose[@]}" exec -T db dropdb -U taily "$check_db"
printf 'Backup restored and checked in an isolated database.\n'
