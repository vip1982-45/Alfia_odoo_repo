# Alfia Equine Operations

Builds on `alfia_equine` and Odoo accounting/sales. Every screen sits under the **Stud** menu and obeys the `alfia_security` site scope and functional groups.

| Area | What it does |
|---|---|
| Horse groups | Fixed members plus rules (sex, breed, age, location, reproductive state). Used by recurring charges. |
| Charges and recurring plans | A charge is approved by a manager, then invoiced by accounting staff, once per charge. Plans (one horse, a barn, or a group; weekly or monthly) generate charges idempotently, daily by cron. |
| Sales and gifts | Prepared by one person, approved by another. Completing it transfers ownership, sets the status, records the departure and invoices a sale. |
| Boarding | Agreement that creates its own recurring charge; ending the stay ends the plan. |
| Feed plans | Dated plans per horse; the daily-needs pivot totals feed by product. |
| Quarantine | One open quarantine per horse; a work item reminds the vet; only vet staff release. |
| Medicine issue / return | Moves real stock to and from the inventory-adjustment location; only the medicine custodian can issue or return, and stock must cover it. |
| Breeding contracts | Fee invoice; credit note only under a live-foal guarantee and when the pregnancy ended without a live foal. |
| Events and entries | Restriction rules (age, sex, fitness, withdrawal, pregnancy, quarantine). Approval needs the vet's confirmation and a competition manager. |
| Fleet, trophies, library | Simple asset register with service dates and loans. |
| Reports | Breeding performance by stallion and season (SQL view with pivot/graph), charges pivot/graph, calendar of work items and events. |
| Horse import | CSV (name, sex, birthdate, breed); bad rows are reported, good rows are kept. |

Not included: payroll/WPS (Enterprise-only), integrations with outside systems, mobile notifications.

Regenerate views, menus and access rows with `python3 tools/gen_views.py`.
