from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

MANAGERS = ('alfia_security.group_alfia_vet_manager', 'alfia_security.group_alfia_breeding_manager')


class AlfiaHorseDeath(models.TransientModel):
    """Record a horse's death: date, cause and evidence. The horse becomes Dead; if it was on the farm a departure is
    recorded; its recurring charges stop and, when it has a carrying value, a draft disposal valuation is created."""
    _name = 'alfia.horse.death'
    _description = 'Record the death of a horse'

    horse_id = fields.Many2one('alfia.horse', required=True)
    date = fields.Datetime(required=True, default=fields.Datetime.now)
    cause = fields.Selection([('illness', 'Illness'), ('injury', 'Injury or accident'), ('euthanasia', 'Euthanasia'),
                              ('foaling', 'Foaling complication'), ('unknown', 'Unknown'), ('other', 'Other')],
                             required=True, default='illness')
    notes = fields.Text(required=True, help="What happened, who certified it (vet), and any report reference.")

    @api.model
    def default_get(self, fields_list):
        vals = super().default_get(fields_list)
        if not vals.get('horse_id') and self.env.context.get('active_model') == 'alfia.horse':
            vals['horse_id'] = self.env.context.get('active_id')
        return vals

    def action_confirm(self):
        self.ensure_one()
        if not (self.env.su or any(self.env.user.has_group(g) for g in MANAGERS)):
            raise AccessError(self.env._("Only the vet manager or the breeding manager records a death."))
        self.horse_id.check_access('read')
        horse = self.horse_id.sudo()
        if horse.status == 'dead':
            raise UserError(self.env._("%s is already recorded as dead.", horse.name))
        cause = dict(self._fields['cause']._description_selection(self.env))[self.cause]
        reason = self.env._("Death (%s)", cause)
        if horse.on_site:
            move = self.env['alfia.horse.movement'].sudo().create({'horse_id': horse.id, 'kind': 'departure', 'date': self.date,
                                                                   'reason': reason})
            move.action_done()
        horse.write({'status': 'dead'})
        horse.message_post(body=self.env._("Death recorded by %(user)s on %(date)s: %(cause)s. %(notes)s", user=self.env.user.name,
                                           date=fields.Datetime.context_timestamp(self, self.date).strftime('%Y-%m-%d %H:%M'),
                                           cause=cause, notes=self.notes))
        return {'type': 'ir.actions.act_window_close'}
