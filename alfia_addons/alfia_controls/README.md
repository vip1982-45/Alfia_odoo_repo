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

## Approval limits and delegation (Accounting › Configuration › Approval limits; Alfia › Approval delegations)
- Limits per kind of document (purchase requests, purchase orders, vendor bills, outgoing payments), for a person or a
  group, in the company's currency: a document in another currency is converted at its date, so changing the currency
  does not get round a limit. While a kind has no limit it is not limited; once it has one, only people covered by a
  limit may approve it, up to their amount. Nobody can set a limit that covers themselves.
- A person can delegate their limit to someone else for up to 90 days, with a reason, starting today or later. Once it
  has started it can only be ended earlier. The approval notes "on behalf of". Delegation never lets anyone approve
  their own documents, and does not give functions the delegate does not have.
