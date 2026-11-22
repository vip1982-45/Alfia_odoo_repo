#!/usr/bin/env bash
# Runs the Alfia trial on Azure managed services, with no virtual machine (so no server quota is needed):
#   Container Apps (the application) + PostgreSQL Flexible Server (the database) + Azure Files (attachments)
#   + Container Registry (the image) + Key Vault (passwords) + Log Analytics (logs), all in one resource group.
# Run from the repository root, signed in to Azure (the GitHub workflow does this). Safe to run again:
# existing resources and passwords are kept; a new image is built and the app moves to it.
set -euo pipefail

SUBSCRIPTION="${SUBSCRIPTION:-alfia-platform-nonprod}"
APP="${APP:-alfia}"
ENV="${ENV:-trial}"
OWNER="${OWNER:-it-operations}"
COSTCENTER="${COSTCENTER:-stud-farm}"
WEB_CIDRS="${WEB_CIDRS:-}"                         # office / VPN public ranges allowed to open the system, space separated
REGIONS="${REGIONS:-uaenorth qatarcentral northeurope}"   # tried in order for the database; the app goes in the same region
RG_LOCATION="${RG_LOCATION:-uaenorth}"             # only used if the resource group does not exist yet
PG_SKU="${PG_SKU:-Standard_B2s}"                   # 2 vCore burstable, 4 GB
IMAGE_TAG="${IMAGE_TAG:-$(git rev-parse --short HEAD)}"

[[ -n "$WEB_CIDRS" ]] || { echo "Set WEB_CIDRS (your office / VPN public addresses)." >&2; exit 1; }
RG="rg-${APP}-${ENV}"
TAGS=(app="$APP" env="$ENV" owner="$OWNER" costcenter="$COSTCENTER")

az account set --subscription "$SUBSCRIPTION"
SUB_ID=$(az account show --query id -o tsv)
SUFFIX=$(printf '%s' "$SUB_ID$RG" | sha256sum | cut -c1-6)
KV="kv-${APP}-${ENV}-${SUFFIX}"
PG="psql-${APP}-${ENV}-${SUFFIX}"
ACR="acr${APP}${ENV}${SUFFIX}"
STF="st${APP}files${SUFFIX}"
LAW="law-${APP}-${ENV}"; CAE="cae-${APP}-${ENV}"; UAMI="id-${APP}-${ENV}"
CAPP="ca-${APP}-${ENV}"; JOB="job-${APP}-init"
DB_NAME=alfia_demo; PG_ADMIN=alfiaadmin
echo "Subscription $SUB_ID, resource group $RG"

retry() { local n=0; until "$@"; do n=$((n+1)); [[ $n -ge 10 ]] && return 1; sleep 20; done; }  # new role assignments take a minute

echo "1/10 Resource group"
az group show -n "$RG" -o none 2>/dev/null || az group create -n "$RG" -l "$RG_LOCATION" -o none
az group update -n "$RG" --tags "${TAGS[@]}" -o none
# An earlier server-based attempt may have left a firewall, network and public address: remove them if no server exists.
if [[ -z "$(az vm list -g "$RG" --query '[].name' -o tsv)" ]]; then
  for r in $(az resource list -g "$RG" --query "[?type=='Microsoft.Network/publicIPAddresses' || type=='Microsoft.Network/virtualNetworks' || type=='Microsoft.Network/networkSecurityGroups'].id" -o tsv); do
    echo "   removing leftover $(basename "$r")"; az resource delete --ids "$r" -o none || true
  done
fi
for p in Microsoft.App Microsoft.ContainerRegistry Microsoft.DBforPostgreSQL Microsoft.OperationalInsights Microsoft.KeyVault Microsoft.Storage Microsoft.ManagedIdentity; do
  az provider register -n "$p" -o none 2>/dev/null || true
done

# Who is running this (a person or the workflow's identity), read from the sign-in token.
ME=$(az account get-access-token --query accessToken -o tsv | cut -d. -f2 | tr '_-' '/+' \
  | python3 -c 'import base64,json,sys; s=sys.stdin.read().strip(); print(json.loads(base64.b64decode(s+"="*(-len(s)%4)))["oid"])')
[[ "$(az account show --query user.type -o tsv)" == "servicePrincipal" ]] && ME_TYPE=ServicePrincipal || ME_TYPE=User

echo "2/10 Database (PostgreSQL Flexible Server), first region that accepts it: $REGIONS"
LOC=$(az postgres flexible-server show -g "$RG" -n "$PG" --query location -o tsv 2>/dev/null | tr -d ' ' | tr 'A-Z' 'a-z' || true)
NEW_PG=0
if [[ -z "$LOC" ]]; then
  PG_PASSWORD="$(openssl rand -base64 30 | tr -d '/+=' | cut -c1-28)Aa1"
  for r in $REGIONS; do
    echo "   trying $r"
    if az postgres flexible-server create -g "$RG" -n "$PG" -l "$r" --tier Burstable --sku-name "$PG_SKU" \
         --storage-size 32 --version 16 --admin-user "$PG_ADMIN" --admin-password "$PG_PASSWORD" \
         --public-access 0.0.0.0 --tags "${TAGS[@]}" --yes -o none; then
      LOC="$r"; NEW_PG=1; break
    fi
  done
  [[ -n "$LOC" ]] || { echo "No region in '$REGIONS' accepted the database; see the messages above." >&2; exit 1; }
fi
echo "   database in $LOC; the app goes there too"
az postgres flexible-server parameter set -g "$RG" -s "$PG" -n azure.extensions --value PG_TRGM,UNACCENT -o none
PG_HOST=$(az postgres flexible-server show -g "$RG" -n "$PG" --query fullyQualifiedDomainName -o tsv)

echo "3/10 Key Vault and passwords"
az keyvault show -g "$RG" -n "$KV" -o none 2>/dev/null \
  || az keyvault create -g "$RG" -n "$KV" -l "$LOC" --enable-rbac-authorization true --tags "${TAGS[@]}" -o none
KV_ID=$(az keyvault show -g "$RG" -n "$KV" --query id -o tsv)
az role assignment create --assignee-object-id "$ME" --assignee-principal-type "$ME_TYPE" \
  --role "Key Vault Secrets Officer" --scope "$KV_ID" -o none 2>/dev/null || true
setsecret() { az keyvault secret set --vault-name "$KV" -n "$1" --value "$2" -o none 2>/dev/null; }
if [[ $NEW_PG == 1 ]]; then retry setsecret db-password "$PG_PASSWORD"; fi
for name in master-password demo-password admin-password; do
  az keyvault secret show --vault-name "$KV" -n "$name" -o none 2>/dev/null \
    || retry setsecret "$name" "$(openssl rand -base64 24 | tr -d '/+=')"
done
retry az keyvault secret show --vault-name "$KV" -n db-password -o none

echo "4/10 Identity, registry and logs"
az identity show -g "$RG" -n "$UAMI" -o none 2>/dev/null || az identity create -g "$RG" -n "$UAMI" -l "$LOC" --tags "${TAGS[@]}" -o none
UAMI_ID=$(az identity show -g "$RG" -n "$UAMI" --query id -o tsv)
UAMI_PID=$(az identity show -g "$RG" -n "$UAMI" --query principalId -o tsv)
az acr show -g "$RG" -n "$ACR" -o none 2>/dev/null \
  || az acr create -g "$RG" -n "$ACR" -l "$LOC" --sku Basic --admin-enabled false --tags "${TAGS[@]}" -o none
ACR_ID=$(az acr show -g "$RG" -n "$ACR" --query id -o tsv)
az role assignment create --assignee-object-id "$UAMI_PID" --assignee-principal-type ServicePrincipal \
  --role AcrPull --scope "$ACR_ID" -o none 2>/dev/null || true
az role assignment create --assignee-object-id "$UAMI_PID" --assignee-principal-type ServicePrincipal \
  --role "Key Vault Secrets User" --scope "$KV_ID" -o none 2>/dev/null || true
az monitor log-analytics workspace show -g "$RG" -n "$LAW" -o none 2>/dev/null \
  || az monitor log-analytics workspace create -g "$RG" -n "$LAW" -l "$LOC" --retention-time 30 --tags "${TAGS[@]}" -o none

echo "5/10 Share for attachments (Azure Files)"
az storage account show -g "$RG" -n "$STF" -o none 2>/dev/null \
  || az storage account create -g "$RG" -n "$STF" -l "$LOC" --sku Standard_LRS --kind StorageV2 \
       --min-tls-version TLS1_2 --allow-blob-public-access false --https-only true --tags "${TAGS[@]}" -o none
az storage share-rm create --storage-account "$STF" -n odoo-data --quota 100 -o none 2>/dev/null || true
STF_KEY=$(az storage account keys list -g "$RG" -n "$STF" --query '[0].value' -o tsv)

echo "6/10 Container Apps environment"
if ! az containerapp env show -g "$RG" -n "$CAE" -o none 2>/dev/null; then
  LAW_CID=$(az monitor log-analytics workspace show -g "$RG" -n "$LAW" --query customerId -o tsv)
  LAW_KEY=$(az monitor log-analytics workspace get-shared-keys -g "$RG" -n "$LAW" --query primarySharedKey -o tsv)
  az containerapp env create -g "$RG" -n "$CAE" -l "$LOC" --logs-workspace-id "$LAW_CID" --logs-workspace-key "$LAW_KEY" \
    --tags "${TAGS[@]}" -o none
fi
CAE_ID=$(az containerapp env show -g "$RG" -n "$CAE" --query id -o tsv)
az containerapp env storage set -g "$RG" -n "$CAE" --storage-name odoo-data --azure-file-account-name "$STF" \
  --azure-file-account-key "$STF_KEY" --azure-file-share-name odoo-data --access-mode ReadWrite -o none

echo "7/10 Build and push the image $ACR.azurecr.io/alfia-stud:$IMAGE_TAG"
IMAGE="$ACR.azurecr.io/alfia-stud:$IMAGE_TAG"
if az acr repository show -n "$ACR" --image "alfia-stud:$IMAGE_TAG" -o none 2>/dev/null; then
  echo "   already in the registry"
else
  retry az acr login -n "$ACR"
  docker build -f deploy/Dockerfile -t "$IMAGE" .
  retry docker push "$IMAGE"
fi

# The settings shared by the init job and the app (YAML, so the share can be mounted for the app's user).
KVU="https://$KV.vault.azure.net/secrets"
common_yaml() {  # $1 = INIT_ONLY value
cat <<YAML
    registries:
      - server: $ACR.azurecr.io
        identity: $UAMI_ID
    secrets:
      - {name: db-password, keyVaultUrl: "$KVU/db-password", identity: "$UAMI_ID"}
      - {name: master-password, keyVaultUrl: "$KVU/master-password", identity: "$UAMI_ID"}
      - {name: demo-password, keyVaultUrl: "$KVU/demo-password", identity: "$UAMI_ID"}
      - {name: admin-password, keyVaultUrl: "$KVU/admin-password", identity: "$UAMI_ID"}
YAML
}
container_yaml() {  # $1 = INIT_ONLY value
cat <<YAML
    containers:
      - name: odoo
        image: $IMAGE
        resources: {cpu: 2.0, memory: 4Gi}
        env:
          - {name: DB_HOST, value: "$PG_HOST"}
          - {name: DB_PORT, value: "5432"}
          - {name: DB_USER, value: "$PG_ADMIN"}
          - {name: DB_NAME, value: "$DB_NAME"}
          - {name: LOAD_DEMO_FARM, value: "1"}
          - {name: INIT_ONLY, value: "$1"}
          - {name: DB_PASSWORD, secretRef: db-password}
          - {name: MASTER_PASSWORD, secretRef: master-password}
          - {name: ALFIA_DEMO_PASSWORD, secretRef: demo-password}
          - {name: ALFIA_ADMIN_PASSWORD, secretRef: admin-password}
        volumeMounts:
          - {volumeName: data, mountPath: /var/lib/alfia/data}
YAML
}
volumes_yaml() {
cat <<YAML
    volumes:
      - name: data
        storageType: AzureFile
        storageName: odoo-data
        mountOptions: "uid=10001,gid=10001,dir_mode=0750,file_mode=0640,nobrl,mfsymlinks"
YAML
}
YDIR=$(mktemp -d)

echo "8/10 Build the database once (a one-off job; first time about 15 minutes)"
cat > "$YDIR/job.yaml" <<YAML
location: $LOC
identity:
  type: UserAssigned
  userAssignedIdentities:
    "$UAMI_ID": {}
properties:
  environmentId: $CAE_ID
  configuration:
    triggerType: Manual
    replicaTimeout: 5400
    replicaRetryLimit: 0
    manualTriggerConfig: {parallelism: 1, replicaCompletionCount: 1}
$(common_yaml)
  template:
$(container_yaml 1)
$(volumes_yaml)
YAML
if az containerapp job show -g "$RG" -n "$JOB" -o none 2>/dev/null; then
  retry az containerapp job update -g "$RG" -n "$JOB" --yaml "$YDIR/job.yaml" -o none
else
  retry az containerapp job create -g "$RG" -n "$JOB" --yaml "$YDIR/job.yaml" -o none
fi
EXEC=$(az containerapp job start -g "$RG" -n "$JOB" --query name -o tsv)
echo "   job run $EXEC started"
for i in $(seq 1 120); do
  ST=$(az containerapp job execution show -g "$RG" -n "$JOB" --job-execution-name "$EXEC" --query properties.status -o tsv 2>/dev/null || echo Unknown)
  [[ "$ST" == "Succeeded" || "$ST" == "Failed" || "$ST" == "Stopped" || "$ST" == "Degraded" ]] && break
  sleep 45
done
echo "   job run finished: $ST"
LAW_CID=$(az monitor log-analytics workspace show -g "$RG" -n "$LAW" --query customerId -o tsv)
az monitor log-analytics query -w "$LAW_CID" --analytics-query \
  "ContainerAppConsoleLogs_CL | where ContainerJobName_s == '$JOB' | where Log_s has_any ('First start','Initialisation','Demo farm','ERROR','Traceback') | project TimeGenerated, Log_s | order by TimeGenerated asc | take 30" \
  -o table 2>/dev/null || echo "   (logs not available yet)"
[[ "$ST" == "Succeeded" ]] || { echo "The database job did not succeed ($ST); the logs above say why." >&2; exit 1; }

echo "9/10 The app (one copy, HTTPS, only from $WEB_CIDRS)"
RULES=""; i=0
for c in $WEB_CIDRS; do i=$((i+1)); RULES+="        - {name: office-$i, ipAddressRange: \"$c\", action: Allow}"$'\n'; done
RUNNER_IP=$(curl -fsS https://api.ipify.org 2>/dev/null || true)   # let this run check the page, then removed below
[[ -n "$RUNNER_IP" ]] && RULES+="        - {name: deploy-check, ipAddressRange: \"$RUNNER_IP/32\", action: Allow}"$'\n'
cat > "$YDIR/app.yaml" <<YAML
location: $LOC
identity:
  type: UserAssigned
  userAssignedIdentities:
    "$UAMI_ID": {}
properties:
  environmentId: $CAE_ID
  configuration:
    activeRevisionsMode: Single
    ingress:
      external: true
      targetPort: 8069
      transport: auto
      allowInsecure: false
      ipSecurityRestrictions:
${RULES%$'\n'}
$(common_yaml)
  template:
$(container_yaml 0)
        probes:
          - {type: Startup, tcpSocket: {port: 8069}, initialDelaySeconds: 10, periodSeconds: 30, failureThreshold: 10}
          - {type: Liveness, httpGet: {path: /web/health, port: 8069}, periodSeconds: 60, failureThreshold: 5}
    scale: {minReplicas: 1, maxReplicas: 1}
$(volumes_yaml)
YAML
if az containerapp show -g "$RG" -n "$CAPP" -o none 2>/dev/null; then
  retry az containerapp update -g "$RG" -n "$CAPP" --yaml "$YDIR/app.yaml" -o none
else
  retry az containerapp create -g "$RG" -n "$CAPP" --yaml "$YDIR/app.yaml" -o none
fi
FQDN=$(az containerapp show -g "$RG" -n "$CAPP" --query properties.configuration.ingress.fqdn -o tsv)
for id in $(az containerapp show -g "$RG" -n "$CAPP" --query id -o tsv) $(az containerapp job show -g "$RG" -n "$JOB" --query id -o tsv) "$CAE_ID"; do
  az resource tag --ids "$id" --tags "${TAGS[@]}" -o none || true
done

echo "10/10 Check"
CODE=000
for i in $(seq 1 30); do
  CODE=$(curl -s -o /dev/null -w '%{http_code}' "https://$FQDN/web/login" || true)
  [[ "$CODE" == 200 ]] && break; sleep 20
done
echo "   https://$FQDN/web/login answered $CODE"
if [[ -n "$RUNNER_IP" ]]; then
  az containerapp ingress access-restriction remove -g "$RG" -n "$CAPP" --rule-name deploy-check -o none || true
fi
az lock create -n do-not-delete --lock-type CanNotDelete -g "$RG" -o none
rm -rf "$YDIR"
cat <<SUMMARY

Address        : https://$FQDN   (only from $WEB_CIDRS)
Region         : $LOC
Resource group : $RG  (tags: ${TAGS[*]})
Passwords      : Key Vault $KV  (demo-password for the evaluator accounts, admin-password for admin)
Database       : $PG (PostgreSQL 16, automatic daily backups kept 7 days)
SUMMARY
if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  { echo "### Alfia trial ($IMAGE_TAG)"; echo; echo "https://$FQDN  (region $LOC)"; echo; echo "Login page check: $CODE"; } >> "$GITHUB_STEP_SUMMARY"
fi
[[ "$CODE" == 200 ]] || { echo "The app did not answer yet; see Container App logs in $LAW." >&2; exit 1; }
