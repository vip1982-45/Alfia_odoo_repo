from odoo import fields, models


class AlfiaAsset(models.Model):
    """Farm assets that are not stock: vehicles and trailers, trophies, library items."""
    _name = 'alfia.asset'
    _description = 'Farm asset'
    _inherit = ['mail.thread']
    _order = 'kind, name'

    name = fields.Char(required=True)
    kind = fields.Selection([('vehicle', 'Vehicle / trailer'), ('trophy', 'Trophy'), ('library', 'Library item'), ('equipment', 'Equipment')], required=True, default='vehicle')
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company, required=True)
    reference = fields.Char(string='Plate / serial / shelf')
    responsible_id = fields.Many2one('res.users')
    horse_id = fields.Many2one('alfia.horse', string='Related horse (trophy)')
    acquired = fields.Date()
    value = fields.Float()
    next_service = fields.Date(string='Next service / inspection')
    state = fields.Selection([('available', 'Available'), ('in_use', 'In use / on loan'), ('maintenance', 'In maintenance'), ('retired', 'Retired')], default='available', required=True, tracking=True)
    borrower_id = fields.Many2one('res.partner', string='Borrowed by')
    notes = fields.Text()
    active = fields.Boolean(default=True)
