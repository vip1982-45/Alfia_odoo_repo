# Alfia livestock accounting and budgets (RFP REQ-BUD-01..03, REQ-LIV-01..03)

Menus: Invoicing › Livestock and budgets (الفوترة › الثروة الحيوانية والموازنات), for finance staff and the auditor.

## Budgets
- A budget covers a company and a period, with lines by account and, optionally, by cost centre (a horse or an
  activity) and department. Amounts are in the company currency (shown as the currency basis).
- One person prepares and submits it; a finance manager who did not prepare it approves it. An approved budget cannot
  be changed: *New version* makes a draft copy (version 2, 3...), and the old one is marked Superseded only when the new
  one is approved.
- **Committed** = confirmed purchase orders in the period not yet billed. **Actual** = posted entries in the period.
  **Forecast** = a figure finance enters, or actual + committed. **Available** and **variance** follow. Each line drills
  through to its entries and its open purchase orders (Budget vs actual).
- **Control rule** per budget, applied when a purchase order is confirmed (commitment) and when a vendor bill not from a
  purchase order is posted (actual): *Report only*, *Warn* (a note on the document), or *Block*. A blocked document
  passes only after a finance manager, other than its creator, records a *Budget override* with a reason.
- A cost goes to the budget line for its cost centre when there is one, otherwise to the account line.

## Livestock register and valuation
- Each horse has a livestock class (breeding, performance, young stock, held for sale, retired, owned by others), how it
  came to the farm (purchase, birth, gift) with date and cost, and its carrying value from the last approved valuation.
  Finance sets these with *Classify* (finance has read-only access to horses). Foals are registered as born on the farm.
- Register events (acquisition, birth, classification change, valuation, disposal) are logged with who and when.
- A valuation records the method (cost, cost less impairment, fair value less costs to sell, disposal), effective date,
  value and the basis with attached evidence. Prepared by one person, approved by a finance manager who did not prepare
  it; approval posts the adjustment entry for the difference from the carrying value (livestock account against the
  revaluation gain or loss account, or a chosen counterpart), allocated to the horse's cost centre. Posted valuations
  are kept as the review history.
- When a horse is sold, gifted or dies, a disposal event is logged and, if it has a carrying value, a draft disposal
  valuation (to 0) is created for finance to complete and approve.
- Set the livestock accounts and journal on the company (Settings › Companies › Livestock accounting).
