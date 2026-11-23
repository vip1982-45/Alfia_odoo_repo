from odoo import fields, models
from odoo.exceptions import UserError

from odoo.addons.alfia_security.flow import workflow


class AlfiaAsset(models.Model):
    """Farm assets that are not stock: vehicles and trailers, trophies, library items."""
    _name = 'alfia.asset'
    _description = 'Farm asset'
    _inherit = ['mail.thread', 'alfia.guarded.mixin', 'alfia.sod.mixin']
    _order = 'kind, name'
    # Availability moves only through the buttons: an item in maintenance cannot be put back into use by editing its state.
    _alfia_guarded = ('state', 'maintenance_by_id', 'released_by_id')

    name = fields.Char(required=True)
    kind = fields.Selection([('vehicle', 'Vehicle / trailer'), ('trophy', 'Trophy'), ('library', 'Library item'), ('equipment', 'Equipment')], required=True, default='vehicle')
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company, required=True)
    reference = fields.Char(string='Plate / serial / shelf')
    responsible_id = fields.Many2one('res.users')
    location_id = fields.Many2one('stock.location', string='Site', help="Leave empty for farm-wide assets everyone may see.")
    horse_id = fields.Many2one('alfia.horse', string='Related horse (trophy)')
    acquired = fields.Date()
    value = fields.Float()
    next_service = fields.Date(string='Next service / inspection')
    state = fields.Selection([('available', 'Available'), ('in_use', 'In use / on loan'), ('maintenance', 'In maintenance'), ('retired', 'Retired')], default='available', required=True, tracking=True)
    borrower_id = fields.Many2one('res.partner', string='Borrowed by')
    maintenance_by_id = fields.Many2one('res.users', string='Sent to maintenance by', readonly=True, copy=False)
    repair_notes = fields.Text(string='Repair done', help="What was repaired and how it was checked. Required to release the item.")
    released_by_id = fields.Many2one('res.users', string='Released by', readonly=True, copy=False)
    notes = fields.Text()
    active = fields.Boolean(default=True)

    def _alfia_set_state(self, state, **extra):
        with workflow():
            self.write(dict(extra, state=state))

    def action_assign(self):
        for rec in self:
            if rec.state != 'available':
                raise UserError(self.env._("%s is not available (it may be in maintenance or retired).", rec.name))
        self._alfia_set_state('in_use')

    def action_return(self):
        for rec in self.filtered(lambda r: r.state == 'in_use'):
            rec._alfia_set_state('available', borrower_id=False)

    def action_maintenance(self):
        for rec in self.filtered(lambda r: r.state in ('available', 'in_use')):
            rec._alfia_set_state('maintenance', maintenance_by_id=self.env.user.id, released_by_id=False, repair_notes=False)
            rec.message_post(body=self.env._("Out of service: sent to maintenance by %s.", self.env.user.name))

    def action_release(self):
        """Back into service after a repair: the repair is described and someone other than the person who took it out
        of service releases it."""
        for rec in self:
            if rec.state != 'maintenance':
                raise UserError(self.env._("%s is not in maintenance.", rec.name))
            if not rec.repair_notes:
                raise UserError(self.env._("Describe the repair and how it was checked before releasing %s.", rec.name))
            if rec.maintenance_by_id == self.env.user and rec._alfia_sod_applies():
                raise UserError(self.env._("You took %s out of service, so someone else must release it.", rec.name))
            rec._alfia_set_state('available', released_by_id=self.env.user.id)
            rec.message_post(body=self.env._("Released after repair by %(user)s: %(notes)s", user=self.env.user.name, notes=rec.repair_notes))

    def action_retire(self):
        self._alfia_set_state('retired')
