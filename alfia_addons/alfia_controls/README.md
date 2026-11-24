# Alfia procurement and finance controls

## Purchase requests (Purchase › Purchase requests; also in the Alfia menu for all staff)
- Anyone can ask to buy something. A request is submitted, then approved or rejected (with a reason) by a purchase
  manager who is not the requester. It cannot be edited after it is submitted.
- Purchasing turns an approved request into requests for quotation, one per vendor (the line's vendor, else the
  product's first supplier). Each order line points back to its request line.

## Receipt quality control (Inventory › Transfers › Receipt inspections)
- Tick **Inspect on receipt** on a product category; it applies to its sub-categories too.
- When such goods are received from a supplier, an inspection is opened. With one-step receipts the goods are moved
  to *Quality hold*; with two-step receipts they stay in Input and the put-away cannot be validated.
- A quality inspector (group *Alfia: receipt quality inspector*) who did not receive the goods accepts them (into
  stock) or rejects them with findings (moved to *Rejected goods*, put-away cancelled, noted on the receipt).
- Inspectors see the inspections of the sites on their access profile, like storekeepers: give the *Receipt quality
  inspector* profile its warehouse(s) (Settings › Alfia access profiles). Without a site the list is empty.

## Approval limits and delegation (Accounting › Configuration › Approval limits; Alfia › Approval delegations)
- Limits per kind of document (purchase requests, purchase orders, vendor bills, outgoing payments), for a person or a
  group, in the company's currency: a document in another currency is converted at its date, so changing the currency
  does not get round a limit. While a kind has no limit it is not limited; once it has one, only people covered by a
  limit may approve it, up to their amount. Nobody can set a limit that covers themselves.
- A person can delegate their limit to someone else for up to 90 days, with a reason, starting today or later. Once it
  has started it can only be ended earlier. The approval notes "on behalf of". A delegated limit never covers a
  document the delegate created (or requested). Since 24 Nov 2026 nobody approves a document they created in any case:
  the former segregation-of-duties exemption group no longer waives anything. Delegation does not give
  functions the delegate does not have.

## Journal approval and period close (Accounting › Accounting › Period closes)
- A manual journal entry (Miscellaneous operations, not one generated from a payment, bank line, exchange difference,
  cash basis tax or stock valuation) is posted by a second person, when segregation of duties is on for the company.
  It cannot be set to post itself automatically.
- Lock dates change only through a period close: one finance manager prepares it, another approves it, which sets the
  lock date. The form shows draft entries, unreconciled bank lines and pending inspections in the period; drafts block
  approval and Odoo itself refuses unreconciled bank lines. Reopening is the same, with a reason, and is recorded.
  Odoo's lock-date exceptions are refused: reopen through a period close instead.

## Bank statements (Accounting › Accounting › Import bank statement / Bank reconciliation)
- Import a CSV from online banking: a Date column and an Amount column (or Debit/Credit), optionally Description,
  Reference and Partner; comma, semicolon or tab separated; dates as YYYY-MM-DD or DD/MM/YYYY. Each line gets a
  fingerprint, so lines already imported are skipped when exports overlap (two identical lines in one file both count).
- "Match automatically" settles a line when exactly one open item has its amount (and partner) and, if several do, the
  line's text names one of them. Otherwise "Match…" lets the accountant pick the items, with an account for any
  difference such as bank charges. "Undo" reverses a match. Lines in a foreign currency use Odoo's standard way.
