#!/usr/bin/env bash
# Installs (or updates) the Alfia system on the trial server without SSH: the code goes up through the
# storage account and Azure Run Command runs the installation on the server. Run after create-trial.sh,
# from the repository root, signed in to Azure. Needs: RG, VM, ST, KV, FQDN (create-trial.sh prints them).
set -euo pipefail
: "${RG:?}" "${VM:?}" "${ST:?}" "${KV:?}" "${FQDN:?}"
SHA=$(git rev-parse --short HEAD)
PKG=$(mktemp -d)/alfia-$SHA.tar.gz
echo "Packing commit $SHA"
git archive --format=tar HEAD | gzip -1 > "$PKG"
echo "Uploading $(du -h "$PKG" | cut -f1) to $ST/releases"
for try in 1 2 3 4 5 6 7 8 9 10; do   # the upload permission may take a minute to apply
  az storage blob upload --auth-mode login --account-name "$ST" -c releases -n "alfia-$SHA.tar.gz" -f "$PKG" --overwrite -o none && break
  [[ $try == 10 ]] && { echo "Upload failed" >&2; exit 1; }
  sleep 30
done
rm -f "$PKG"

REMOTE=$(mktemp)
cat > "$REMOTE" <<SCRIPT
#!/usr/bin/env bash
set -euo pipefail
exec 2>&1
H=/home/alfiaadmin
cloud-init status --wait > /dev/null || true
command -v docker > /dev/null || { echo "Docker is not installed yet"; exit 1; }
TOKEN=\$(curl -sf -H Metadata:true "http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https://storage.azure.com/" | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')
mkdir -p \$H/releases
curl -sf -H "Authorization: Bearer \$TOKEN" -H "x-ms-version: 2021-08-06" -o \$H/releases/alfia-$SHA.tar.gz "https://$ST.blob.core.windows.net/releases/alfia-$SHA.tar.gz"
NEW=\$H/releases/alfia-$SHA
rm -rf \$NEW && mkdir -p \$NEW && tar xzf \$H/releases/alfia-$SHA.tar.gz -C \$NEW && rm -f \$H/releases/alfia-$SHA.tar.gz
if [[ -f \$H/alfia/deploy/.env ]]; then cp -p \$H/alfia/deploy/.env \$NEW/deploy/.env; fi
ln -sfn \$NEW \$H/alfia
cd \$H/alfia/deploy
[[ -f .env ]] || bash azure/make-env.sh "$KV" "$FQDN"
chown -R alfiaadmin:alfiaadmin \$H/releases \$H/alfia
echo "Building and starting (first time about 15 minutes)"
docker compose up -d --build > \$H/compose-up.log 2>&1 || { tail -40 \$H/compose-up.log; exit 1; }
for i in \$(seq 1 120); do
  docker compose logs odoo 2>&1 | grep -q "HTTP service" && break
  sleep 15
done
docker compose logs odoo 2>&1 | grep -E "Demo farm ready|HTTP service|ERROR|Traceback" | tail -8
( crontab -u alfiaadmin -l 2>/dev/null | grep -v backup-to-blob || true
  echo "30 1 * * * cd \$H/alfia/deploy && bash azure/backup-to-blob.sh $ST >> \$H/backup.log 2>&1" ) | crontab -u alfiaadmin -
echo "Nightly copy scheduled at 01:30"
DEMO=\$(grep ^DEMO_PASSWORD= .env | cut -d= -f2-)
docker compose exec -T -e DEMO="\$DEMO" odoo python3 - <<'PY'
import os, xmlrpc.client
c = xmlrpc.client.ServerProxy("http://localhost:8069/xmlrpc/2/common"); m = xmlrpc.client.ServerProxy("http://localhost:8069/xmlrpc/2/object")
for login in ("groom.a", "groom.b"):
    uid = c.authenticate("alfia_demo", login, os.environ["DEMO"], {})
    print("CHECK", login, "horses", m.execute_kw("alfia_demo", uid, os.environ["DEMO"], "alfia.horse", "search_count", [[]]) if uid else "LOGIN FAILED")
PY
for i in \$(seq 1 20); do
  CODE=\$(curl -s -o /dev/null -w '%{http_code}' --resolve "$FQDN:443:127.0.0.1" "https://$FQDN/web/login" || true)
  [[ "\$CODE" == 200 ]] && break; sleep 15
done
echo "CHECK https certificate and page: \$CODE"
echo INSTALL_OK $SHA
SCRIPT
echo "Installing on $VM through Run Command (the output appears when it finishes)"
OUT=$(az vm run-command invoke -g "$RG" -n "$VM" --command-id RunShellScript --scripts @"$REMOTE" --query 'value[0].message' -o tsv)
rm -f "$REMOTE"
echo "$OUT"
if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  { echo "### Alfia trial: commit $SHA"; echo; echo "https://$FQDN"; echo; echo '```'; echo "$OUT" | grep -E "CHECK|INSTALL_OK|Demo farm|ERROR" || true; echo '```'; } >> "$GITHUB_STEP_SUMMARY"
fi
grep -q "INSTALL_OK" <<< "$OUT" || { echo "Installation did not finish; see the output above." >&2; exit 1; }
