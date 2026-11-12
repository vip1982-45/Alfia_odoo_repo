#!/usr/bin/env bash
# Creates everything the Alfia stud system trial needs in Azure, in its own resource group.
# Run it from Azure Cloud Shell (Bash) or any machine with the Azure CLI, after `az login`.
# Safe to run again: existing resources are kept and updated, passwords already in Key Vault are kept.
set -euo pipefail

# ---------- Edit these values (or set them in the environment, as the GitHub workflow does) ----------
SUBSCRIPTION="${SUBSCRIPTION:-alfia-platform-nonprod}"   # subscription name or id
LOCATION="${LOCATION:-qatarcentral}"                      # keep the data in Qatar
APP="${APP:-alfia}"
ENV="${ENV:-trial}"
OWNER="${OWNER:-it-operations}"                           # team that owns these resources (tag)
COSTCENTER="${COSTCENTER:-stud-farm}"                     # cost centre code (tag)
WEB_CIDRS="${WEB_CIDRS:-0.0.0.0/32}"                      # office / VPN public ranges allowed to open the system, space separated
ADMIN_CIDRS="${ADMIN_CIDRS:-}"                            # IT addresses allowed to use SSH (empty = no SSH; use Run Command)
SSH_PUBLIC_KEY_FILE="${SSH_PUBLIC_KEY_FILE:-$HOME/.ssh/id_ed25519.pub}"   # missing = a key is generated and kept in Key Vault
DNS_LABEL="${DNS_LABEL:-alfia-stud-trial}"                # gives https://<label>.qatarcentral.cloudapp.azure.com ; must be unique
VM_SIZES="${VM_SIZES:-Standard_D4s_v5 Standard_D4as_v5 Standard_D4s_v4 Standard_D4as_v4 Standard_D4ds_v5 Standard_D4s_v3 Standard_B4ms Standard_E2s_v5}"  # first with free quota wins (4 vCPU / 16 GB, or 2 vCPU / 16 GB)
TEAM_GROUP_ID="${TEAM_GROUP_ID:-}"                        # optional: object id of the Entra group that may manage this app
# -----------------------------------------------------------------------------------------------------

RG="rg-${APP}-${ENV}"
ADMIN_USER="alfiaadmin"
TAGS=(app="$APP" env="$ENV" owner="$OWNER" costcenter="$COSTCENTER")

if [[ -z "$WEB_CIDRS" || "$WEB_CIDRS" == "0.0.0.0/32" ]]; then
  echo "Set WEB_CIDRS first (your office / VPN public addresses)." >&2; exit 1
fi
KEY_DIR=""
if [[ ! -f "$SSH_PUBLIC_KEY_FILE" ]]; then   # no key given: make one; its private half is kept in Key Vault below
  KEY_DIR=$(mktemp -d); ssh-keygen -q -t ed25519 -N "" -C "alfiaadmin@vm-alfia" -f "$KEY_DIR/id"; SSH_PUBLIC_KEY_FILE="$KEY_DIR/id.pub"
fi

az account set --subscription "$SUBSCRIPTION"
SUB_ID=$(az account show --query id -o tsv)
SUFFIX=$(printf '%s' "$SUB_ID$RG" | sha256sum | cut -c1-6)   # makes globally unique names repeatable
KV="kv-${APP}-${ENV}-${SUFFIX}"
ST="st${APP}${ENV}${SUFFIX}"
VNET="vnet-${APP}-${ENV}"; SUBNET="snet-app"; NSG="nsg-${APP}-${ENV}"
PIP="pip-${APP}-${ENV}"; VM="vm-${APP}-${ENV}"; RSV="rsv-${APP}-${ENV}"
echo "Subscription $SUB_ID, resource group $RG in $LOCATION"

# The region must exist for this subscription. LOCATION may also be part of a name (e.g. "saudi"):
# the regions this subscription can use, and those where it can create servers, are searched for it.
if [[ -z "$(az account list-locations --query "[?name=='$LOCATION'].name" -o tsv)" ]]; then
  LOCS=$(mktemp); VMLOCS=$(mktemp)
  az account list-locations -o json > "$LOCS"
  az provider show -n Microsoft.Compute --query "resourceTypes[?resourceType=='virtualMachines'].locations | [0]" -o json > "$VMLOCS"
  FOUND=$(python3 - "$LOCS" "$VMLOCS" "$LOCATION" <<'PY'
import json, sys
locs = json.load(open(sys.argv[1])); vm = json.load(open(sys.argv[2])) or []; want = sys.argv[3].lower().replace(" ", "")
names = {l["name"]: l.get("displayName", "") for l in locs}
for d in vm:
    names.setdefault(d.lower().replace(" ", ""), d)
hits = sorted(n for n, d in names.items() if want in n or want in d.lower().replace(" ", ""))
print("  Regions matching '%s': %s" % (sys.argv[3], ", ".join(f"{n} ({names[n]})" for n in hits) or "none"), file=sys.stderr)
print("  Regions where this subscription can create servers: " + ", ".join(sorted(vm)), file=sys.stderr)
if len(hits) == 1:
    print(hits[0])
PY
)
  rm -f "$LOCS" "$VMLOCS"
  if [[ -z "$FOUND" ]]; then
    echo "Region '$LOCATION' is not available to this subscription (see the lists above)." >&2; exit 1
  fi
  LOCATION="$FOUND"; echo "   Using region $LOCATION"
fi

echo "1/9 Resource group"
OLD_LOC=$(az group show -n "$RG" --query location -o tsv 2>/dev/null || true)
if [[ -n "$OLD_LOC" && "$OLD_LOC" != "$LOCATION" ]]; then
  # A group left in another region by an earlier run: remove it only if it holds nothing but network parts.
  OTHER=$(az resource list -g "$RG" --query "[?!(type=='Microsoft.Network/networkSecurityGroups' || type=='Microsoft.Network/virtualNetworks' || type=='Microsoft.Network/publicIPAddresses')].name" -o tsv)
  if [[ -n "$OTHER" ]]; then
    echo "$RG is in $OLD_LOC and holds more than network parts ($OTHER); move or delete it by hand first." >&2; exit 1
  fi
  echo "   Removing the earlier, empty $RG in $OLD_LOC"
  az group delete -n "$RG" --yes -o none
fi
az group create -n "$RG" -l "$LOCATION" --tags "${TAGS[@]}" -o none

echo "2/9 Network: firewall rules, virtual network, public address"
az network nsg create -g "$RG" -n "$NSG" -l "$LOCATION" --tags "${TAGS[@]}" -o none
# shellcheck disable=SC2086
az network nsg rule create -g "$RG" --nsg-name "$NSG" -n allow-https --priority 100 --direction Inbound --access Allow \
  --protocol Tcp --destination-port-ranges 443 --source-address-prefixes $WEB_CIDRS -o none
# Port 80 stays open to the internet only so the free HTTPS certificate can be issued and renewed; it just redirects to 443.
az network nsg rule create -g "$RG" --nsg-name "$NSG" -n allow-http-certificate --priority 110 --direction Inbound --access Allow \
  --protocol Tcp --destination-port-ranges 80 --source-address-prefixes Internet -o none
if [[ -n "$ADMIN_CIDRS" ]]; then
  # shellcheck disable=SC2086
  az network nsg rule create -g "$RG" --nsg-name "$NSG" -n allow-ssh-it --priority 120 --direction Inbound --access Allow \
    --protocol Tcp --destination-port-ranges 22 --source-address-prefixes $ADMIN_CIDRS -o none
fi
az network vnet create -g "$RG" -n "$VNET" -l "$LOCATION" --address-prefixes 10.20.0.0/16 \
  --subnet-name "$SUBNET" --subnet-prefixes 10.20.1.0/24 --tags "${TAGS[@]}" -o none
az network vnet subnet update -g "$RG" --vnet-name "$VNET" -n "$SUBNET" --network-security-group "$NSG" -o none
az network public-ip create -g "$RG" -n "$PIP" -l "$LOCATION" --sku Standard --allocation-method Static \
  --dns-name "$DNS_LABEL" --tags "${TAGS[@]}" -o none

echo "3/9 Virtual machine (Ubuntu 24.04 with Docker)"
if az vm show -g "$RG" -n "$VM" -o none 2>/dev/null; then
  VM_SIZE=$(az vm show -g "$RG" -n "$VM" --query hardwareProfile.vmSize -o tsv)
else
  # New subscriptions often have no quota for some server families: take the first size that is offered here and fits.
  SKUS=$(mktemp); USAGE=$(mktemp)
  az vm list-skus -l "$LOCATION" --resource-type virtualMachines --all -o json > "$SKUS"
  az vm list-usage -l "$LOCATION" -o json > "$USAGE"
  VM_SIZE=$(python3 - "$SKUS" "$USAGE" $VM_SIZES <<'PY'
import json, sys
skus = {s["name"]: s for s in json.load(open(sys.argv[1]))}
usage = {u["name"]["value"]: u for u in json.load(open(sys.argv[2]))}
def free(name):
    u = usage.get(name)
    return int(u["limit"]) - int(u["currentValue"]) if u else 0
regional = free("cores")
for size in sys.argv[3:]:
    s = skus.get(size)
    if not s:
        print(f"  {size}: not in this region", file=sys.stderr); continue
    # Only a whole-region restriction rules a size out; a zone restriction does not (no zone is asked for).
    if any(r.get("type") == "Location" for r in s.get("restrictions", [])):
        print(f"  {size}: not offered to this subscription here", file=sys.stderr); continue
    cpus = int(next((c["value"] for c in s.get("capabilities", []) if c["name"] == "vCPUs"), 0))
    fam = s.get("family", "")
    print(f"  {size}: family {fam}, free {free(fam)} of {cpus} needed, region free {regional}", file=sys.stderr)
    if cpus and free(fam) >= cpus and regional >= cpus:
        print(size); break
else:
    print("  Quota in this region (family: free of limit):", file=sys.stderr)
    for k, u in sorted(usage.items()):
        if int(u["limit"]):
            print(f"    {k}: {int(u['limit']) - int(u['currentValue'])} of {u['limit']}", file=sys.stderr)
PY
)
  rm -f "$SKUS" "$USAGE"
  if [[ -z "$VM_SIZE" ]]; then
    echo "No server size has free quota in $LOCATION. In the portal: Quotas > Compute > $LOCATION," >&2
    echo "request 4 vCPUs for 'Standard DSv5 Family vCPUs' (and 'Total Regional vCPUs' if it is 0), then run again." >&2
    exit 1
  fi
  echo "   Using $VM_SIZE"
fi
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
# Who is running this (a person, or the GitHub workflow's identity): read from the sign-in token, no directory lookup needed.
ME=$(az account get-access-token --query accessToken -o tsv | cut -d. -f2 | tr '_-' '/+' \
  | python3 -c 'import base64,json,sys; s=sys.stdin.read().strip(); print(json.loads(base64.b64decode(s+"="*(-len(s)%4)))["oid"])')
[[ "$(az account show --query user.type -o tsv)" == "servicePrincipal" ]] && ME_TYPE=ServicePrincipal || ME_TYPE=User
az role assignment create --assignee-object-id "$ME" --assignee-principal-type "$ME_TYPE" \
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

if [[ -n "$KEY_DIR" ]]; then
  az keyvault secret show --vault-name "$KV" -n vm-ssh-private-key -o none 2>/dev/null \
    || az keyvault secret set --vault-name "$KV" -n vm-ssh-private-key --file "$KEY_DIR/id" -o none
  rm -rf "$KEY_DIR"
fi

echo "5/9 Storage account for nightly database copies (kept 30 days)"
az storage account create -g "$RG" -n "$ST" -l "$LOCATION" --sku Standard_LRS --kind StorageV2 \
  --min-tls-version TLS1_2 --allow-blob-public-access false --allow-shared-key-access false --tags "${TAGS[@]}" -o none
az storage container-rm create --storage-account "$ST" -n db-backups -o none 2>/dev/null || true
az storage container-rm create --storage-account "$ST" -n releases -o none 2>/dev/null || true
ST_ID=$(az storage account show -g "$RG" -n "$ST" --query id -o tsv)
POLICY=$(mktemp)
cat > "$POLICY" <<'JSON'
{"rules":[{"enabled":true,"name":"delete-after-30-days","type":"Lifecycle","definition":{"actions":{"baseBlob":{"delete":{"daysAfterModificationGreaterThan":30}}},"filters":{"blobTypes":["blockBlob"],"prefixMatch":["db-backups/","releases/"]}}}]}
JSON
az storage account management-policy create -g "$RG" --account-name "$ST" --policy "@$POLICY" -o none
rm -f "$POLICY"
az role assignment create --assignee-object-id "$VM_IDENTITY" --assignee-principal-type ServicePrincipal \
  --role "Storage Blob Data Contributor" --scope "$ST_ID/blobServices/default/containers/db-backups" -o none 2>/dev/null || true
az role assignment create --assignee-object-id "$VM_IDENTITY" --assignee-principal-type ServicePrincipal \
  --role "Storage Blob Data Reader" --scope "$ST_ID/blobServices/default/containers/releases" -o none 2>/dev/null || true
az role assignment create --assignee-object-id "$ME" --assignee-principal-type "$ME_TYPE" \
  --role "Storage Blob Data Contributor" --scope "$ST_ID/blobServices/default/containers/releases" -o none 2>/dev/null || true

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

Next: follow "Install the system on the server" in deploy/azure/README.md (or install-on-vm.sh does it for you).
SUMMARY
if [[ -n "${GITHUB_ENV:-}" ]]; then   # hand the names to the next workflow step
  { echo "RG=$RG"; echo "VM=$VM"; echo "KV=$KV"; echo "ST=$ST"; echo "FQDN=$FQDN"; } >> "$GITHUB_ENV"
fi
