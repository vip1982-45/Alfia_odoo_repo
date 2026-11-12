#!/usr/bin/env bash
# Run ON the Azure server from cron, inside deploy/: copies the database and the attachments to the storage account.
# Usage: azure/backup-to-blob.sh <storage-account-name>
# Cron (as alfiaadmin, every night at 01:30):
#   30 1 * * * cd $HOME/alfia/deploy && azure/backup-to-blob.sh <storage-account> >> $HOME/backup.log 2>&1
set -euo pipefail
ST=${1:?storage account name}
STAMP=$(date -u +%Y-%m-%dT%H%MZ)
WORK=$(mktemp -d); trap 'rm -rf "$WORK"' EXIT
docker compose exec -T db pg_dump -U odoo alfia_demo | gzip > "$WORK/db-$STAMP.sql.gz"
docker compose exec -T odoo tar czf - -C /var/lib/alfia/data . > "$WORK/files-$STAMP.tar.gz"
TOKEN=$(curl -sf -H Metadata:true \
  "http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https://storage.azure.com/" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')
for f in "$WORK"/*; do
  curl -sf -X PUT -T "$f" -H "Authorization: Bearer $TOKEN" -H "x-ms-version: 2021-08-06" \
    -H "x-ms-blob-type: BlockBlob" "https://$ST.blob.core.windows.net/db-backups/$(basename "$f")"
  echo "$(date -u +%FT%TZ) uploaded $(basename "$f") ($(du -h "$f" | cut -f1))"
done
