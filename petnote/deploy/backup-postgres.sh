#!/bin/bash
set -euo pipefail
umask 077
cd /opt/taily/app
install -d -m 0700 /var/backups/taily
backup="/var/backups/taily/taily-$(date -u +%Y%m%dT%H%M%SZ).dump"
docker compose --env-file /opt/taily/production.env -p taily exec -T db \
    sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$backup.partial"
test -s "$backup.partial"
mv "$backup.partial" "$backup"
sha256sum "$backup" > "$backup.sha256"
printf 'Backup saved: %s\n' "$backup"
