#!/usr/bin/env bash
# Run ON the Azure server, inside deploy/: writes .env with the passwords from Key Vault.
# The server reads them with its own managed identity, so no password is ever typed or copied.
# Usage: azure/make-env.sh <key-vault-name> <site-address>
set -euo pipefail
KV=${1:?key vault name}; SITE=${2:?site address, e.g. alfia-stud-trial.qatarcentral.cloudapp.azure.com}
TOKEN=$(curl -sf -H Metadata:true \
  "http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https://vault.azure.net" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')
secret() {
  curl -sf -H "Authorization: Bearer $TOKEN" "https://$KV.vault.azure.net/secrets/$1?api-version=7.4" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["value"])'
}
umask 077
cat > .env <<ENV
SITE_ADDRESS=$SITE
DB_PASSWORD=$(secret db-password)
MASTER_PASSWORD=$(secret master-password)
DEMO_PASSWORD=$(secret demo-password)
ADMIN_PASSWORD=$(secret admin-password)
LOAD_DEMO_FARM=1
ENV
echo ".env written for $SITE (readable by you only)"
