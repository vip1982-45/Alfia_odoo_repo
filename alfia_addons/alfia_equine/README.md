# Alfia Equine

The horse functions, rebuilt as an Odoo 20 application on top of `alfia_security` (roles, site scope, controlled stock) and
Odoo's own stock, purchasing, sales and accounting.

## What is in it

| Area | What it does |
|---|---|
| Horse | Identity (chip, passport, studbook), genetic parents and **carrier mare**, pedigree with inbreeding coefficient, effective-dated ownership, status history, duplicate-chip protection, **merge duplicates** |
| Location | Movements (arrival, internal, departure; planned → done, never edited afterwards) drive the horse's current stock location |
| Daily care | Weight and height, temperature and vitals, notes, farrier by foot, work items (workboard) |
| Health and vet | Health records and vaccinations, vet visits, treatments with **prescription kept apart from doses actually given** and withdrawal date, lab tests with vet review, dental, diagnostic images |
| Training | Sessions, competitions, assessments, and **clinical fitness (vet) separate from event eligibility (competition manager)**; no record means *pending*, never *cleared* |
| Breeding | Teasing chart with configurable symbols and season rules, services (each attempt kept), **service and ovulation as separate events**, reproductive exams, pregnancies, results, **loss handling**, foaling in three steps, embryos and transfer |
| Schedule rules | A **versioned rule set approved by the resident vet**: pregnancy checks at 14, 18, 21, 28, 35, 45, 60, 75, 90 and 120 days after the confirmed ovulation, expected foaling at day 340, preparation reminder 30 days before, vaccinations at months 3, 5, 7 and 9, teasing 1 Oct – 15 Jun and pregnant mares to day 120. Nothing is scheduled until a rule set is approved |
| Semen | Collections with quality values; a vet releases a batch, which creates a lot and a stock move into the tank. It is **controlled stock** (custodians only, dual approval to dispatch) through `alfia_security` |

## Rules worth knowing

* Confirming an ovulation creates the pregnancy and exactly the scheduled work, once. Confirming again, or regenerating, creates nothing new.
* **Correcting** an ovulation date re-dates only pending work and says how many items moved; completed work and results stay.
* Only a vet can record a pregnancy result. A **loss** closes that pregnancy only, cancels its pending work, keeps all history and makes the mare open for a new attempt. An **inconclusive** result needs the vet's own next-check date.
* Apparent heat in a pregnant mare creates an urgent item for the vets and a note on the pregnancy. It never ends the pregnancy.
* A birth is recorded by stable staff, confirmed by a vet and completed by the breeding manager, which creates the foal exactly once with the genetic dam, the carrier mare, the sire and the mother's location.
* Completed work items cannot be deleted, cancelled or reopened.

## Who can do what

Access follows the functions defined in `alfia_security` (breeding, training, veterinary, stable) and the site scope of each user's profile.
Stable staff cannot see veterinary records at all; breeding staff can read but not write them; only the resident vet approves rule sets
and deletes clinical records; only managers edit the horse record. Every horse-linked record follows the **location of its horse**:
a user scoped to Barn A sees only the horses in Barn A and their records. A horse with no location is visible only to users who are not scoped.

`tools/gen_views.py` regenerates the list/form/search views, menus and the access matrix from one table.

## Not ported yet

Reports and dashboards, dynamic groups, calendar view, recurring charges, public share link, contacts-by-role views, the Crio data import,
feed planning against stock, breeding contracts and nominations, horse sale and gift workflow, quarantine, and mobile notifications.
Invoicing, purchasing, stock and accounting come from Odoo itself. Arabic output has not been configured.
