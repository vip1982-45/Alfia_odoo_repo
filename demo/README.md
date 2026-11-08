# Alfia demonstration system

A complete stud farm for end users to try every function: 26 horses with pedigrees, a breeding season in progress,
health and treatment history, quarantine, semen and embryo storage, billing, contracts, events, stock and purchasing.
Every name and number is invented.

## Build it

    demo/build_demo.sh <odoo-config-file> [database-name]     # default database: alfia_demo; an existing one is dropped

Then start Odoo with `-d alfia_demo --db-filter='^alfia_demo$'` and open `/web/login`.

## Accounts

All accounts use the password `AlfiaDemo-2026`. Change it (or delete the accounts) before the system holds real data.

| Login | Role | Sees |
|---|---|---|
| admin | System administrator | everything |
| officer | Security officer | creates users, assigns profiles |
| auditor | Internal auditor | read-only, audit trail |
| resident.vet / vet | Resident vet / staff vet | all barns, tank room, medicine store |
| breeding.manager / breeding | Breeding manager / assistant | all barns |
| groom.a / groom.b | Stable staff | Barn A only / Barn B only |
| trainer / training.manager | Training staff / competition manager | Barn B and the training yard |
| semen.custodian | Semen and embryo custodian | tank room |
| medicine.custodian | Medicine custodian | medicine store |
| approver.admin / approver.vet | Dispatch approvers | the semen dispatch awaiting approval |
| storekeeper / buyer / purchase.approver | Stores and procurement | feed and medicine stores |
| accountant / finance | Accounting | all journals and horses |

## Arabic

The demo accounts open in Arabic, right to left; English is one setting away (avatar → Preferences → Language). Every
Alfia screen is translated (`alfia_addons/*/i18n/ar.po`), horses carry their Arabic name beside the studbook name, and
the demo data is in Arabic.

Right-to-left layout needs the `rtlcss` tool on the server, which Odoo uses to build its right-to-left styles. Without it
Arabic text still appears but screens stay left to right. Install it once (Node.js required):

    npm install -g rtlcss@4.3.0

`build_demo.sh` warns when it is missing.
