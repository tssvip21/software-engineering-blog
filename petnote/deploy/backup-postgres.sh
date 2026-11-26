#!/bin/bash
set -euo pipefail
umask 077
cd "$(dirname "$0")/.."
project_dir=$(dirname "$PWD")
project=$(basename "$project_dir")
backup_dir="/var/backups/$project"
install -d -m 0700 "$backup_dir"
backup="$backup_dir/$project-$(date -u +%Y%m%dT%H%M%SZ).dump"
docker compose -f compose.production.yaml --env-file "$project_dir/production.env" -p "$project" exec -T db \
    sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$backup.partial"
test -s "$backup.partial"
mv "$backup.partial" "$backup"
sha256sum "$backup" > "$backup.sha256"
printf 'Backup saved: %s\n' "$backup"
