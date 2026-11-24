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
