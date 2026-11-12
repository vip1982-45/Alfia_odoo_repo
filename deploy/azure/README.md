# Alfia trial on Azure

One script creates the trial's own resource group in the `alfia-platform-nonprod` subscription, with
everything inside it, tagged so its cost shows on its own and locked against deletion. Then the system is
installed on the server with the same Docker package as `deploy/`.

## What gets created

All of it goes in **`rg-alfia-trial`**, in **Qatar Central**, tagged `app=alfia env=trial owner=… costcenter=…`.

| Resource | Name | Purpose |
|---|---|---|
| Resource group | `rg-alfia-trial` | Holds only this app; a delete lock protects it |
| Network firewall | `nsg-alfia-trial` | HTTPS only from your office/VPN; SSH only from IT; port 80 open for the certificate |
| Virtual network | `vnet-alfia-trial` (10.20.0.0/16) | Private network of this app only |
| Public address | `pip-alfia-trial` | Fixed IP and the name `alfia-stud-trial.qatarcentral.cloudapp.azure.com` |
| Server | `vm-alfia-trial` | Ubuntu 24.04, 4 vCPU, 16 GB RAM, 64 GB SSD, Docker installed |
| Key Vault | `kv-alfia-trial-xxxxxx` | The four generated passwords; the server reads them itself |
| Storage account | `stalfiatrialxxxxxx` | Nightly copies of the database and attachments, deleted after 30 days |
| Backup vault | `rsv-alfia-trial` | Daily backup of the whole server, kept 30 days |

Nobody needs a storage key or password file: the server signs in to Key Vault and Storage with its own
Azure identity (managed identity), and only to this app's vault and backup container.

## Option A: let GitHub do it (no password or secret anywhere)

The workflow `.github/workflows/azure-trial.yml` runs both scripts below for you: `create-trial.sh` makes the
resources, `install-on-vm.sh` uploads the code through the storage account and installs it with Azure Run
Command (no SSH needed). It signs in with a federated identity that Azure trusts only for this repository's
branch `claude/alfia-hardening`.

1. Entra ID > **App registrations** > **New registration**: name `alfia-trial-deployer`, single tenant.
   Note the **Application (client) ID** and **Directory (tenant) ID**.
2. In that app: **Certificates & secrets** > **Federated credentials** > **Add credential** >
   scenario **GitHub Actions deploying Azure resources**: organization `vip1982-45`, repository
   `Alfia_odoo_repo` (capital A, exactly as GitHub shows it: Azure compares it letter by letter), entity **Branch**, branch `claude/alfia-hardening`, name `alfia-trial`.
3. Subscription `alfia-platform-nonprod` > **Access control (IAM)** > **Add role assignment** > **Owner**
   (Privileged administrator roles tab) > members: `alfia-trial-deployer` > Conditions: **Allow user to assign
   all roles except privileged administrative roles** > Review + assign.
4. Fill in `deploy/azure/trial.env` (client id, tenant id, subscription id, office/VPN addresses) on branch
   `claude/alfia-hardening` and push: the workflow starts. Its summary shows the address and the checks.

Run it again any time (Actions > Azure trial > Run workflow, once the workflow is on the default branch, or
another change to `trial.env`) to install the latest code; data and passwords are kept.
When the trial is over, remove the role assignment or delete the app registration.

## Option B: run it yourself in Cloud Shell

## 1. Before you start (once)

- You have the **Owner** role on the subscription (needed to give the server and the team their permissions).
- You know the **public IP addresses** of the office and VPN (for HTTPS) and of IT (for SSH).
- Open **Azure Cloud Shell** (the `>_` icon in the portal), choose **Bash**. Create an SSH key if you have none:
  `ssh-keygen -t ed25519` (press Enter at each question).

## 2. Create the resources (about 10 minutes)

In Cloud Shell:

    git clone <alfia_odoo_repo URL> alfia && cd alfia/deploy/azure
    nano create-trial.sh        # edit the values at the top: WEB_CIDRS, ADMIN_CIDRS, OWNER, COSTCENTER,
                                # DNS_LABEL, and TEAM_GROUP_ID if the app team should manage it
    ./create-trial.sh

At the end it prints the server's address, the Key Vault name and the storage account name. Keep them.

## 3. Install the system on the server (about 20 minutes)

Wait 3 minutes after step 2 (the server finishes installing Docker), then:

    ssh alfiaadmin@alfia-stud-trial.qatarcentral.cloudapp.azure.com

On the server:

    ssh-keygen -t ed25519 -f ~/.ssh/github -N ""; cat ~/.ssh/github.pub
    # In GitHub: repository > Settings > Deploy keys > Add (read-only), paste the key shown.
    GIT_SSH_COMMAND="ssh -i ~/.ssh/github" git clone git@github.com:<owner>/alfia_odoo_repo.git alfia
    cd alfia && git checkout 20.0 && cd deploy
    azure/make-env.sh <key-vault-name> alfia-stud-trial.qatarcentral.cloudapp.azure.com
    docker compose up -d --build
    docker compose logs -f odoo          # wait for "Demo farm ready", then Ctrl+C

Open `https://alfia-stud-trial.qatarcentral.cloudapp.azure.com` from the office and sign in.
The demo and admin passwords are in Key Vault (`demo-password`, `admin-password`): in the portal open the
vault, **Secrets**, or run `az keyvault secret show --vault-name <vault> -n demo-password --query value -o tsv`.

## 4. Nightly copy to storage

On the server, `crontab -e` and add (one line):

    30 1 * * * cd $HOME/alfia/deploy && azure/backup-to-blob.sh <storage-account> >> $HOME/backup.log 2>&1

Run it once by hand to check: `cd ~/alfia/deploy && azure/backup-to-blob.sh <storage-account>`.

## 5. Cost

- Portal > **Cost Management** > **Cost analysis**: group by **Tag: app** (or by resource group) to see Alfia's
  cost apart from other apps.
- Portal > **Cost Management** > **Budgets** > Add, scope `rg-alfia-trial`, a monthly amount and an email
  alert at 80% and 100%.

## Later

- **Your own address** (e.g. `stud-trial.company.qa`): create a DNS CNAME to the cloudapp name, put the new name
  in `SITE_ADDRESS` in `.env`, and run `docker compose up -d`.
- **Removing the trial**: delete the lock (`az lock delete -n do-not-delete -g rg-alfia-trial`), then the resource
  group. Everything in it goes with it, and nothing of other apps is touched.
- **Another app** in the same subscription: copy the script, change `APP` and `ENV`; it gets its own group,
  network, vault, storage, tags and lock.
