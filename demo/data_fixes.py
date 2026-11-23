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

env.cr.commit()

# 4. Last, as uninstalling reloads the registry: supplier and customer name lookup needs a paid online service (IAP)
#    and failed with an access error.
mod = env['ir.module.module'].sudo().search([('name', '=', 'partner_autocomplete'), ('state', '=', 'installed')])
if mod:
    mod.button_immediate_uninstall()
    done.append('removed the online company lookup (partner_autocomplete)')

print('Data fixes: ' + ('; '.join(done) if done else 'nothing to change'))
