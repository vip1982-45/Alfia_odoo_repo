# Run with:  odoo-bin shell -c <conf> -d <db> < demo/data_fixes.py      (safe to run again)
# Corrections to data created by earlier releases. Each step checks first and changes nothing once applied.
done = []

# 1. The semen tank room sat outside the warehouse stock, so transfers and sales could not see the straws in it.
wh = env['stock.warehouse'].sudo().search([('company_id', '=', env.company.id)], limit=1)
room = env['stock.location'].sudo().search([('name', '=', 'غرفة خزانات السائل المنوي · Semen tank room'), ('usage', '=', 'internal')], limit=1)
if wh and room and not env['stock.location'].sudo().search_count([('id', '=', room.id), ('id', 'child_of', wh.lot_stock_id.id)]):
    room.location_id = wh.lot_stock_id
    done.append('moved the semen tank room under %s' % wh.lot_stock_id.complete_name)

# 2. Contracts were marked Invoiced as soon as a draft invoice existed; they are Invoiced only once it is posted.
if 'alfia.breeding.contract' in env:
    early = env['alfia.breeding.contract'].sudo().search([('state', '=', 'invoiced'), ('invoice_id.state', '!=', 'posted')])
    if early:
        early.write({'state': 'confirmed'})
        done.append('%d contract(s) back to Confirmed until their invoice is posted' % len(early))

# 3. Controlled deliveries leave from the warehouse's output area, which the custodians and dispatch approvers could not
#    see (their scope was only their store), so an open delivery was invisible to the people who must approve it.
if wh:
    out = wh.wh_output_stock_loc_id
    roles = [env.ref(x, raise_if_not_found=False) for x in ('alfia_security.group_alfia_semen_custodian', 'alfia_security.group_alfia_medicine_custodian',
             'alfia_security.group_alfia_dispatch_admin_approver', 'alfia_security.group_alfia_dispatch_vet_approver')]
    roles = [g.id for g in roles if g]
    profiles = env['alfia.access.profile'].sudo().search([('group_ids', 'in', roles), ('location_ids', '!=', False)]).filtered(
        lambda pr: not env['stock.location'].sudo().search_count([('id', '=', out.id), ('id', 'child_of', pr.location_ids.ids)]))
    if out and profiles:
        profiles.write({'location_ids': [(4, out.id)]})
        done.append('%d custodian/approver profile(s) can now see %s' % (len(profiles), out.complete_name))

# 4. Negative stock of a tracked or controlled item: a pick recorded a batch leaving a place it was not in (the trial's
#    semen straw that physically came from a tank). Correct it the way it happened: move the shortfall from where that
#    batch really is, as a documented internal transfer. Nothing is created or written off.
Quant = env['stock.quant'].sudo()
negatives = Quant.search([('quantity', '<', 0), ('location_id.usage', '=', 'internal')]).filtered(
    lambda q: q.product_id.tracking != 'none' or q.product_id.categ_id.alfia_effective_group_id
    or q.product_id.categ_id.alfia_effective_approver_group_ids)
for neg in negatives:
    short = -neg.quantity
    source = Quant.search([('product_id', '=', neg.product_id.id), ('lot_id', '=', neg.lot_id.id or False), ('company_id', '=', neg.company_id.id),
                           ('location_id.usage', '=', 'internal'), ('quantity', '>', 0)], order='quantity desc').filtered(
        lambda q: q.quantity - q.reserved_quantity >= short)[:1]
    if not source:
        done.append('could not correct %s at %s (no free stock of that batch elsewhere)' % (neg.product_id.display_name, neg.location_id.complete_name))
        continue
    origin = 'Correction: %s%s was recorded leaving %s; it came from %s' % (
        neg.product_id.display_name, (' / ' + neg.lot_id.name) if neg.lot_id else '', neg.location_id.complete_name, source.location_id.complete_name)
    move = env['stock.move'].sudo().create({
        'product_id': neg.product_id.id, 'product_uom_qty': short, 'uom_id': neg.product_id.uom_id.id, 'company_id': neg.company_id.id,
        'location_id': source.location_id.id, 'location_dest_id': neg.location_id.id, 'origin': origin})
    move._action_confirm()
    move.write({'move_line_ids': [(0, 0, {'product_id': neg.product_id.id, 'lot_id': neg.lot_id.id or False, 'quantity': short,
                                          'uom_id': neg.product_id.uom_id.id, 'location_id': source.location_id.id,
                                          'location_dest_id': neg.location_id.id, 'picked': True})]})
    move.picked = True
    move._action_done()
    env['alfia.security.audit']._alfia_log('stock.correction', 'stock.move', move.reference or str(move.id), origin)
    done.append(origin)

# 5. Currencies the farm deals in (QAR company): USD and EUR active, each with a dated rate so nothing is converted at 1.
#    The rates are indicative trial values (USD at the 3.64 peg; EUR approximate) for finance to replace with approved ones.
company = env.company
if company.currency_id.name == 'QAR':
    Currency = env['res.currency'].sudo().with_context(active_test=False)
    for code, qar_per_unit in (('USD', 3.64), ('EUR', 4.20)):
        cur = Currency.search([('name', '=', code)], limit=1)
        if not cur:
            continue
        if not cur.active:
            cur.active = True
            done.append('%s activated' % code)
        if not env['res.currency.rate'].sudo().search_count([('currency_id', '=', cur.id), ('company_id', 'in', (False, company.id))]):
            env['res.currency.rate'].sudo().create({'currency_id': cur.id, 'company_id': company.id, 'name': '2026-11-01',
                                                    'rate': round(1 / qar_per_unit, 6)})
            done.append('%s rate from 2026-11-01: 1 %s = %s QAR (indicative, for finance to approve)' % (code, code, qar_per_unit))

# 6. Files uploaded into clinical records (diagnostic images, laboratory reports) were linked to no record, so only the
#    uploader could open them. They now belong to their record.
for model in ('alfia.diag.image', 'alfia.lab.test'):
    if model in env and 'attachment_ids' in env[model]._fields:
        linked = 0
        for rec in env[model].sudo().search([('attachment_ids', '!=', False)]):
            loose = rec.attachment_ids.filtered(lambda a: not a.res_id or a.res_model != model)
            if loose:
                loose.write({'res_model': model, 'res_id': rec.id})
                linked += len(loose)
        if linked:
            done.append('%d file(s) linked to their %s record' % (linked, model))

env.cr.commit()

# 7. Last, as uninstalling reloads the registry: supplier and customer name lookup needs a paid online service (IAP)
#    and failed with an access error.
mod = env['ir.module.module'].sudo().search([('name', '=', 'partner_autocomplete'), ('state', '=', 'installed')])
if mod:
    mod.button_immediate_uninstall()
    done.append('removed the online company lookup (partner_autocomplete)')

print('Data fixes: ' + ('; '.join(done) if done else 'nothing to change'))
