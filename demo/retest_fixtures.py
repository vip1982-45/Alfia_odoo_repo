# Run with:  odoo-bin shell -c <conf> -d <db> < demo/retest_fixtures.py      (safe to run again; trial only)
# TEST records for the retests that the role accounts could not reach: each sits inside the sites of the roles that must
# use it, so no account needs broader rights. Records are found by name and created only once.
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

# N1 - a dispatch from the output area (visible to the custodian and both approvers) whose line takes semen batch
# SEM-00001-5 from STA/Output, where it is not: validating it must be refused, naming Tank 1.
if wh and not env['stock.picking'].sudo().search([('origin', '=', 'TEST N1 wrong-location dispatch')], limit=1):
    product = env.ref('alfia_equine.product_frozen_semen', raise_if_not_found=False)
    lot = env['stock.lot'].sudo().search([('name', '=', 'SEM-00001-5')], limit=1)
    out, customers = wh.wh_output_stock_loc_id, env.ref('stock.stock_location_customers')
    if product and lot and out:
        pick = env['stock.picking'].sudo().create({
            'picking_type_id': wh.out_type_id.id, 'location_id': out.id, 'location_dest_id': customers.id, 'origin': 'TEST N1 wrong-location dispatch',
            'move_ids': [(0, 0, {'product_id': product.id, 'product_uom_qty': 1, 'uom_id': product.uom_id.id,
                                 'location_id': out.id, 'location_dest_id': customers.id})]})
        pick.action_confirm()
        move = pick.move_ids
        move.move_line_ids.unlink()
        move.write({'move_line_ids': [(0, 0, {'product_id': product.id, 'lot_id': lot.id, 'quantity': 1, 'uom_id': product.uom_id.id,
                                              'location_id': out.id, 'location_dest_id': customers.id, 'picked': True})]})
        made.append(f'dispatch {pick.name} (TEST N1)')

# R10 - a feed product counted in kg, to buy in 25 kg bags.
uom_kg = env.ref('uom.product_uom_kgm')
if not env['product.product'].sudo().search([('name', '=', 'TEST R10 feed (kg)')], limit=1):
    env['product.product'].sudo().create({'name': 'TEST R10 feed (kg)', 'type': 'consu', 'is_storable': True, 'uom_id': uom_kg.id,
                                          'standard_price': 2.0, 'purchase_ok': True})
    made.append('product TEST R10 feed (kg)')

env.cr.commit()
print('Retest fixtures: ' + ('; '.join(made) if made else 'already in place'))
