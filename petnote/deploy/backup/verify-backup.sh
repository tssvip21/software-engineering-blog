#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/../.."
project_dir=$(dirname "$PWD")
project=$(basename "$project_dir")
backup_dir="/var/backups/$project"
compose=(docker compose -f compose.production.yaml --env-file "$project_dir/production.env" -p "$project")
backup=$(find "$backup_dir" -maxdepth 1 -type f -name "$project-*.dump" | sort | tail -n 1)
test -n "$backup"
sha256sum -c "$backup.sha256"
check_db="restore_check_$(date -u +%Y%m%d%H%M%S)_$$"
db_user=$("${compose[@]}" exec -T db printenv POSTGRES_USER)
"${compose[@]}" exec -T db createdb -U "$db_user" "$check_db"
cleanup() { "${compose[@]}" exec -T db dropdb -U "$db_user" --if-exists "$check_db"; }
trap cleanup EXIT
# Only this newly created database is used; the production database is untouched.
"${compose[@]}" exec -T db pg_restore -U "$db_user" -d "$check_db" --exit-on-error < "$backup"
"${compose[@]}" exec -T db psql -U "$db_user" -d "$check_db" -v ON_ERROR_STOP=1 -c 'SELECT COUNT(*) AS restored_migrations FROM django_migrations; SELECT COUNT(*) AS restored_references FROM care_reference;'
printf 'Backup restored and checked in an isolated database.\n'
