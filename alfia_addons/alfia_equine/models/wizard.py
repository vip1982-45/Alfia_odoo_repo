from odoo import api, fields, models
from odoo.exceptions import UserError


class AlfiaHorseMerge(models.TransientModel):
    _name = 'alfia.horse.merge'
    _description = 'Merge duplicate horses'

    duplicate_id = fields.Many2one('alfia.horse', string='Duplicate (will be archived)', required=True)
    keep_id = fields.Many2one('alfia.horse', string='Horse to keep', required=True)

    def action_merge(self):
        self.ensure_one()
        dup, keep = self.duplicate_id, self.keep_id
        if dup == keep:
            raise UserError(self.env._("Choose two different horses."))
        (dup | keep).check_access('write')
        if dup.sex != keep.sex:
            raise UserError(self.env._("The two horses have different sexes. Check that they really are the same animal."))
        Fields = self.env['ir.model.fields'].sudo()
        # Free the duplicate's unique identifiers first, then hand over what the kept horse lacks.
        carry = {f: dup[f] for f in ('microchip', 'reg_id', 'passport', 'ueln', 'fei', 'birthdate', 'breed', 'color', 'sire_id', 'dam_id') if dup[f] and not keep[f]}
        dup.sudo().write({'microchip': False})
        moved = 0
        for field in Fields.search([('relation', '=', 'alfia.horse'), ('ttype', '=', 'many2one'), ('store', '=', True)]):
            model = self.env[field.model].sudo().with_context(active_test=False, alfia_merge=True)
            if field.model == 'alfia.horse.merge' or model._abstract or not model._auto:
                continue   # transient, abstract and view-backed models hold no rows of their own
            records = model.search([(field.name, '=', dup.id)])
            if records:
                records.write({field.name: keep.id})
                moved += len(records)
        keep.sudo().write({k: (v.id if hasattr(v, 'id') else v) for k, v in carry.items()})
        for field in ('sire_id', 'dam_id', 'carrier_mare_id'):
            if keep[field] == keep:
                keep.sudo()[field] = False
        dup.sudo().write({'active': False})
        keep.message_post(body=self.env._("Merged duplicate %(dup)s into this record (%(n)d linked record(s) moved) by %(user)s.", dup=dup.name, n=moved, user=self.env.user.name))
        return {'type': 'ir.actions.act_window', 'res_model': 'alfia.horse', 'res_id': keep.id, 'view_mode': 'form'}
