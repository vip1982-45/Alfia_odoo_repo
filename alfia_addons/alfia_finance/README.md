# Alfia Finance and Supply Chain

Builds on Odoo's Invoicing, Purchase, Sales and Inventory and on the Alfia stud modules.

| Area | What it gives |
|---|---|
| General ledger, trial balance, P&L, balance sheet | Accounting → Reporting. Posted journal items by account (pivot and list), with date, journal and partner filters. Odoo Community has no financial statements; these fill the gap. |
| Aged receivables and payables | Open customer and vendor balances by days overdue (not yet due, 1–30, 31–60, 61–90, over 90), per partner. |
| Cost allocation per horse | Every horse gets a cost centre (analytic account in the "Horses" plan). Charges invoiced to clients and medicine issued to a horse land on it; vendor bills and purchase lines can be allocated to horses or to stud sites. Report: Costs and income per horse. |
| Perpetual stock valuation | Average cost, valued in real time; cost of goods sold is booked with the customer invoice (Anglo-Saxon). Landed costs (freight, customs) spread over received goods and update their cost. |
| Warehouse | Receipts in two steps (Input, then Stock) and deliveries in two (pick list, then shipment), with reservations; multiple locations, routes and batches switched on. |
| Purchasing | Purchase agreements (blanket orders) feeding purchase orders; second-person approval of orders, bills and payments (alfia_security). |

Payments: "Register payment" leaves an outgoing payment as a draft linked to its bills when segregation of duties
applies; a second person confirms it, which settles the bills.

On a new database, set the company's country to Qatar before installing accounting so the Qatar chart of accounts is
used (`demo/set_country.py` does this for the demo).

Tests (`tests/test_finance.py`) run purchase-to-pay, order-to-cash, landed costs, purchase agreements, stock counts,
internal transfers, horse cost centres, aged balances and the finance profiles, and check that the trial balance balances.
