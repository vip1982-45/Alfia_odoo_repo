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

English is the working language; Arabic is installed and can be chosen under each user's Preferences (Odoo's own
screens are translated; the Alfia stud-farm screens are not yet).
