# Alfia Security

Segregation by **site**, **function** and **sensitive stock**, enforced by the server on every read and write.
Written for Odoo 20, where access rules are one model, `ir.access` (group rows grant, group-less rows restrict).

## The model in one minute

* **Access profile** = functions (groups) + where the person works (stock locations, warehouses, accounting journals).
  "Breeding staff – Stud A", "Semen custodian – tank room", "AP clerk – purchase journal" are profiles.
* **Three separate duties.** System administrators *design* profiles. Security officers *assign* them and create users, but
  cannot edit groups directly, change their own access, touch administrators or other officers, or make a profile.
  Auditors read the trail. Officers do **not** hold Odoo's *Access Rights* group (which can edit groups, access rules,
  external identifiers and any user's password); groups, access rules, external identifiers and the segregation-of-duties
  setting can be changed by system administrators only, whoever else holds that group.
* **Default deny.** A user with no scope sees nothing in scoped models. Roles that must see everything get a scope-bypass
  group (`All sites`, `All warehouses`, `All journals`); system administrators have all three.
* **Controlled stock.** Set *Restrict stock to group* on a product category (sub-categories inherit): only that group sees
  the stock, moves, lots and transfers of those products. Switch on *Dual approval before dispatch* and pick approver
  groups: an outgoing transfer then needs one approval from each, by different people, and the person dispatching
  cannot be an approver. The same applies to any transfer that takes the stock out of the farm's own locations (to a
  customer, supplier, loss or production location), whatever its operation type. Scrapping controlled stock needs an
  inventory-adjustment approver. Changing the transfer after approval clears the approvals. Approvers get read-only
  sight of the transfer.
* **Segregation of duties** (company setting, with an optional amount threshold):
  the creator of a purchase order cannot approve it; the entrant of a vendor bill cannot post it; the creator of an outgoing
  payment cannot confirm it; counting stock and applying the count to the books are different rights.
  System automation (sudo) and the explicit *exempt* group are not blocked.
* **Audit trail.** Profile, group, user, access-rule, category-restriction and SoD-setting changes are recorded with who and
  what changed. Entries cannot be edited or deleted through the application.
* **Hardening.** Two-factor (TOTP / passkey) is available; password length 12; session lock after 30 minutes idle and 12 hours
  absolute. *Alfia Security → Users without two-factor* lists privileged users who have not enrolled.

## Setting up a farm

1. Create stock locations per barn / tank room; mark restricted categories (Semen, Embryos, Medicine) and their approver groups.
2. Adjust the starter profiles (Settings → Alfia Security → Access profiles) or add yours, with locations / warehouses / journals.
3. As a security officer, create each user and pick their profile(s). Nothing else is needed.

## Known limits

* Two-factor is available but **not enforced**: Odoo Community has no enforcement policy. The report lists who is missing it.
* Location *names* are visible to all internal users (Odoo's purchasing and transfer flows read them); stock in other
  locations is not. System administrators do not automatically see controlled stock; add them to the group (audited).
* A purchasing or sales user with no warehouse scope, or an accountant with no journal scope, sees nothing until a profile gives one.
* The equine functional groups (breeding, training, veterinary, stable) exist but protect no models until the equine
  application is added.
* Code that moves stock with system rights on a person's behalf (semen release, medicine issue) checks the person's site
  scope itself with `res.users._alfia_check_location`; use it in any new code of that kind.
* Single sign-on with Microsoft Entra ID is possible with Odoo's `auth_oauth` and is not configured here.
* Odoo's own test suites were not re-run with this module installed.
