# Alfia add-ons

Custom modules for the Alfia stud farm, kept apart from Odoo's own `addons/` so upstream merges stay clean.

| Module | Purpose |
|---|---|
| `alfia_security` | Segregated accounts by site and function, controlled stock (semen, embryos, medicine), segregation of duties, audit trail, authentication hardening |

## Run

```sh
python3 -m venv venv && venv/bin/pip install -r requirements.txt psycopg2-binary
# PostgreSQL 16 with a role that may create databases
venv/bin/python odoo-bin -d alfia -i stock,purchase,sale_management,account,l10n_qa,alfia_security \
    --addons-path=addons,odoo/addons,alfia_addons
# tests
venv/bin/python odoo-bin -d alfia -u alfia_security --test-tags /alfia_security --stop-after-init
```

Odoo Community already provides the finance, purchasing, sales and inventory applications used here, with a Qatar chart
of accounts (`l10n_qa`). Payroll and budgeting are **not** in Community.
