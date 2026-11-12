#!/usr/bin/env bash
# Creates everything the Alfia stud system trial needs in Azure, in its own resource group.
# Run it from Azure Cloud Shell (Bash) or any machine with the Azure CLI, after `az login`.
# Safe to run again: existing resources are kept and updated, passwords already in Key Vault are kept.
set -euo pipefail

# ---------- Edit these values ----------
SUBSCRIPTION="alfia-platform-nonprod"      # subscription name or id
LOCATION="qatarcentral"                    # keep the data in Qatar
APP="alfia"
ENV="trial"
OWNER="it-operations"                      # team that owns these resources (tag)
COSTCENTER="stud-farm"                     # cost centre code (tag)
WEB_CIDRS="0.0.0.0/32"                     # office / VPN public ranges allowed to open the system, space separated
ADMIN_CIDRS="0.0.0.0/32"                   # IT addresses allowed to connect with SSH, space separated
SSH_PUBLIC_KEY_FILE="$HOME/.ssh/id_ed25519.pub"
DNS_LABEL="alfia-stud-trial"               # gives https://<label>.qatarcentral.cloudapp.azure.com ; must be unique
VM_SIZE="Standard_D4s_v5"                  # 4 vCPU, 16 GB RAM
TEAM_GROUP_ID=""                           # optional: object id of the Entra group that may manage this app
# ---------------------------------------

RG="rg-${APP}-${ENV}"
ADMIN_USER="alfiaadmin"
TAGS=(app="$APP" env="$ENV" owner="$OWNER" costcenter="$COSTCENTER")

if [[ "$WEB_CIDRS" == "0.0.0.0/32" || "$ADMIN_CIDRS" == "0.0.0.0/32" ]]; then
  echo "Edit WEB_CIDRS and ADMIN_CIDRS at the top of this script first (your office / VPN addresses)." >&2; exit 1
fi
[[ -f "$SSH_PUBLIC_KEY_FILE" ]] || { echo "No SSH public key at $SSH_PUBLIC_KEY_FILE (create one with: ssh-keygen -t ed25519)" >&2; exit 1; }

az account set --subscription "$SUBSCRIPTION"
SUB_ID=$(az account show --query id -o tsv)
SUFFIX=$(printf '%s' "$SUB_ID$RG" | sha256sum | cut -c1-6)   # makes globally unique names repeatable
KV="kv-${APP}-${ENV}-${SUFFIX}"
ST="st${APP}${ENV}${SUFFIX}"
VNET="vnet-${APP}-${ENV}"; SUBNET="snet-app"; NSG="nsg-${APP}-${ENV}"
PIP="pip-${APP}-${ENV}"; VM="vm-${APP}-${ENV}"; RSV="rsv-${APP}-${ENV}"
echo "Subscription $SUB_ID, resource group $RG in $LOCATION"

echo "1/9 Resource group"
az group create -n "$RG" -l "$LOCATION" --tags "${TAGS[@]}" -o none

echo "2/9 Network: firewall rules, virtual network, public address"
az network nsg create -g "$RG" -n "$NSG" -l "$LOCATION" --tags "${TAGS[@]}" -o none
# shellcheck disable=SC2086
az network nsg rule create -g "$RG" --nsg-name "$NSG" -n allow-https --priority 100 --direction Inbound --access Allow \
  --protocol Tcp --destination-port-ranges 443 --source-address-prefixes $WEB_CIDRS -o none
# Port 80 stays open to the internet only so the free HTTPS certificate can be issued and renewed; it just redirects to 443.
az network nsg rule create -g "$RG" --nsg-name "$NSG" -n allow-http-certificate --priority 110 --direction Inbound --access Allow \
  --protocol Tcp --destination-port-ranges 80 --source-address-prefixes Internet -o none
# shellcheck disable=SC2086
az network nsg rule create -g "$RG" --nsg-name "$NSG" -n allow-ssh-it --priority 120 --direction Inbound --access Allow \
  --protocol Tcp --destination-port-ranges 22 --source-address-prefixes $ADMIN_CIDRS -o none
az network vnet create -g "$RG" -n "$VNET" -l "$LOCATION" --address-prefixes 10.20.0.0/16 \
  --subnet-name "$SUBNET" --subnet-prefixes 10.20.1.0/24 --tags "${TAGS[@]}" -o none
az network vnet subnet update -g "$RG" --vnet-name "$VNET" -n "$SUBNET" --network-security-group "$NSG" -o none
az network public-ip create -g "$RG" -n "$PIP" -l "$LOCATION" --sku Standard --allocation-method Static \
  --dns-name "$DNS_LABEL" --tags "${TAGS[@]}" -o none

echo "3/9 Virtual machine (Ubuntu 24.04 with Docker)"
CLOUD_INIT=$(mktemp)
cat > "$CLOUD_INIT" <<CLOUD
#cloud-config
package_update: true
packages: [docker.io, docker-compose-v2, git, curl, python3]
runcmd:
  - systemctl enable --now docker
  - usermod -aG docker $ADMIN_USER
CLOUD
if ! az vm show -g "$RG" -n "$VM" -o none 2>/dev/null; then
  az vm create -g "$RG" -n "$VM" -l "$LOCATION" --image Canonical:ubuntu-24_04-lts:server:latest --size "$VM_SIZE" \
    --admin-username "$ADMIN_USER" --ssh-key-values "$SSH_PUBLIC_KEY_FILE" --authentication-type ssh \
    --vnet-name "$VNET" --subnet "$SUBNET" --public-ip-address "$PIP" --nsg "" \
    --os-disk-size-gb 64 --storage-sku Premium_LRS --custom-data "$CLOUD_INIT" \
    --assign-identity --tags "${TAGS[@]}" -o none
fi
rm -f "$CLOUD_INIT"
VM_IDENTITY=$(az vm show -g "$RG" -n "$VM" --query identity.principalId -o tsv)

echo "4/9 Key Vault with generated passwords"
az keyvault show -g "$RG" -n "$KV" -o none 2>/dev/null \
  || az keyvault create -g "$RG" -n "$KV" -l "$LOCATION" --enable-rbac-authorization true --tags "${TAGS[@]}" -o none
KV_ID=$(az keyvault show -g "$RG" -n "$KV" --query id -o tsv)
ME=$(az ad signed-in-user show --query id -o tsv)
az role assignment create --assignee-object-id "$ME" --assignee-principal-type User \
  --role "Key Vault Secrets Officer" --scope "$KV_ID" -o none 2>/dev/null || true
az role assignment create --assignee-object-id "$VM_IDENTITY" --assignee-principal-type ServicePrincipal \
  --role "Key Vault Secrets User" --scope "$KV_ID" -o none 2>/dev/null || true
for name in db-password master-password demo-password admin-password; do
  for try in 1 2 3 4 5 6 7 8 9 10; do   # a new role assignment can take a minute to apply
    if az keyvault secret show --vault-name "$KV" -n "$name" -o none 2>/dev/null; then break; fi
    if az keyvault secret set --vault-name "$KV" -n "$name" --value "$(openssl rand -base64 24 | tr -d '/+=')" -o none 2>/dev/null; then break; fi
    [[ $try == 10 ]] && { echo "Could not write secret $name to $KV" >&2; exit 1; }
    sleep 15
  done
done

echo "5/9 Storage account for nightly database copies (kept 30 days)"
az storage account create -g "$RG" -n "$ST" -l "$LOCATION" --sku Standard_LRS --kind StorageV2 \
  --min-tls-version TLS1_2 --allow-blob-public-access false --allow-shared-key-access false --tags "${TAGS[@]}" -o none
az storage container-rm create --storage-account "$ST" -n db-backups -o none 2>/dev/null || true
ST_ID=$(az storage account show -g "$RG" -n "$ST" --query id -o tsv)
POLICY=$(mktemp)
cat > "$POLICY" <<'JSON'
{"rules":[{"enabled":true,"name":"delete-after-30-days","type":"Lifecycle","definition":{"actions":{"baseBlob":{"delete":{"daysAfterModificationGreaterThan":30}}},"filters":{"blobTypes":["blockBlob"],"prefixMatch":["db-backups/"]}}}]}
JSON
az storage account management-policy create -g "$RG" --account-name "$ST" --policy "@$POLICY" -o none
rm -f "$POLICY"
az role assignment create --assignee-object-id "$VM_IDENTITY" --assignee-principal-type ServicePrincipal \
  --role "Storage Blob Data Contributor" --scope "$ST_ID/blobServices/default/containers/db-backups" -o none 2>/dev/null || true

echo "6/9 Daily backup of the whole server (kept 30 days)"
az backup vault create -g "$RG" -n "$RSV" -l "$LOCATION" --tags "${TAGS[@]}" -o none
az backup protection enable-for-vm -g "$RG" --vault-name "$RSV" --vm "$VM" --policy-name DefaultPolicy -o none 2>/dev/null \
  || echo "   (server backup already enabled)"

echo "7/9 Team access"
if [[ -n "$TEAM_GROUP_ID" ]]; then
  az role assignment create --assignee-object-id "$TEAM_GROUP_ID" --assignee-principal-type Group \
    --role Contributor --scope "$(az group show -n "$RG" --query id -o tsv)" -o none 2>/dev/null || true
  echo "   Contributor on $RG given to group $TEAM_GROUP_ID (they cannot touch other resource groups)"
else
  echo "   Skipped (set TEAM_GROUP_ID to give an app team access to this resource group only)"
fi

echo "8/9 Delete lock on the resource group"
az lock create -n do-not-delete --lock-type CanNotDelete -g "$RG" -o none

echo "9/9 Done"
FQDN=$(az network public-ip show -g "$RG" -n "$PIP" --query dnsSettings.fqdn -o tsv)
cat <<SUMMARY

Resource group : $RG   (tags: ${TAGS[*]})
Server         : $VM   ssh $ADMIN_USER@$FQDN
Web address    : https://$FQDN
Key Vault      : $KV   (passwords: db-password, master-password, demo-password, admin-password)
Storage        : $ST   (container db-backups)
Backup vault   : $RSV

Next: follow "Install the system on the server" in deploy/azure/README.md.
SUMMARY
