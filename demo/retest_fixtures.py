# Run with:  odoo-bin shell -c <conf> -d <db> < demo/retest_fixtures.py      (safe to run again; trial only)
# TEST records for the retests that the role accounts could not reach: each sits inside the sites of the roles that must
# use it, so no account needs broader rights. Records are found by name and created only once.
import os
from datetime import timedelta

from odoo import fields

made = []
today = fields.Date.context_today(env['res.users'])
Horse = env['alfia.horse'].sudo()
Loc = env['stock.location'].sudo()


def location(fragment):
    return Loc.search([('name', 'ilike', fragment), ('usage', '=', 'internal')], limit=1)


def horse(name, sex, loc):
    h = Horse.search([('name', '=', name)], limit=1)
    if not h and loc:
        h = Horse.create({'name': name, 'sex': sex, 'initial_location_id': loc.id})
        made.append(f'{name} in {loc.complete_name}')
    return h


barn_a, barn_b = location('Barn A'), location('Barn B')
wh = env['stock.warehouse'].sudo().search([], limit=1)

# R6 - a healthy horse inside the training staff's sites (Barn B), entered in an event with no entry rules.
r6 = horse('TEST R6 healthy entry horse', 'female', barn_b)
event = env['alfia.event'].sudo().search([('name', '=', 'TEST R6 event without rules')], limit=1)
if not event:
    event = env['alfia.event'].sudo().create({'name': 'TEST R6 event without rules', 'date_start': today + timedelta(days=7)})
    made.append(f'event {event.name} on {event.date_start}')

# E4 - a mare in Barn A (groom.a's site) with a confirmed pregnancy, ready for a three-role foaling.
e4 = horse('TEST E4 foaling mare', 'female', barn_a)
if e4 and not env['alfia.pregnancy'].sudo().search([('mare_id', '=', e4.id)], limit=1):
    rule = env['alfia.breeding.rule'].sudo()._get_approved(e4.company_id)
    ov = env['alfia.ovulation'].sudo().create({'mare_id': e4.id, 'date': today - timedelta(days=340)})
    ov.action_confirm()
    env['alfia.pregnancy.check'].sudo().create({'pregnancy_id': ov.pregnancy_id.id, 'date': today - timedelta(days=326), 'result': 'positive'})
    made.append(f'confirmed pregnancy {ov.pregnancy_id.display_name}')

# E2 - a mare in Barn A for a frozen-semen service by breeding.manager.
horse('TEST E2 service mare', 'female', barn_a)

# R10 - a feed product counted in kg, to buy in 25 kg bags.
uom_kg = env.ref('uom.product_uom_kgm')
feed = env['product.product'].sudo().search([('name', '=', 'TEST R10 feed (kg)')], limit=1)
if not feed:
    feed = env['product.product'].sudo().create({'name': 'TEST R10 feed (kg)', 'type': 'consu', 'is_storable': True, 'uom_id': uom_kg.id,
                                                 'standard_price': 2.0, 'purchase_ok': True})
    made.append('product TEST R10 feed (kg)')
bag = env.ref('alfia_finance.uom_bag_25kg', raise_if_not_found=False)
if bag and bag not in feed.uom_ids:
    feed.product_tmpl_id.uom_ids = [(4, bag.id)]
    made.append('TEST R10 feed (kg) can be bought in 25 kg bags')

# A sales clerk (sales and receivables) account, so the sales cycle can run under an ordinary sales role.
password = os.environ.get('ALFIA_DEMO_PASSWORD') or os.environ.get('DEMO_PASSWORD')
profile = env.ref('alfia_security.profile_ar_clerk', raise_if_not_found=False)
Users = env['res.users'].sudo().with_context(no_reset_password=True)
if profile and not Users.with_context(active_test=False).search([('login', '=', 'sales.clerk')], limit=1):
    clerk = Users.create({'name': 'موظف المبيعات · Sales clerk (TEST)', 'login': 'sales.clerk',
                          'group_ids': [(6, 0, [env.ref('base.group_user').id])]})
    clerk.write({'alfia_profile_ids': [(6, 0, [profile.id])]})
    if password:
        clerk.write({'password': password})
    clerk._alfia_sync_profile_groups()
    made.append('account sales.clerk (sales and receivables)')

env.cr.commit()
print('Retest fixtures: ' + ('; '.join(made) if made else 'already in place'))
