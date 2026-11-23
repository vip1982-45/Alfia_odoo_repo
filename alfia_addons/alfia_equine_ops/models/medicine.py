from odoo import Command, api, fields, models
from odoo.exceptions import UserError

from odoo.addons.alfia_security.flow import is_trusted, workflow

G = 'alfia_security.group_alfia_'


class AlfiaMedicineIssue(models.Model):
    """Medicine taken from stock for one horse. Issue and return both move real stock, so the stock card stays true."""
    _name = 'alfia.medicine.issue'
    _description = 'Medicine issue'
    _inherit = ['alfia.horse.record', 'mail.thread', 'alfia.guarded.mixin']
    _order = 'date desc, id desc'
    _alfia_guarded = ('state', 'returned_qty', 'move_ids')

    date = fields.Date(required=True, default=fields.Date.context_today)
    product_id = fields.Many2one('product.product', string='Medicine', required=True, domain="[('is_storable', '=', True)]")
    quantity = fields.Float(required=True, default=1.0)
    returned_qty = fields.Float(readonly=True, copy=False)
    location_id = fields.Many2one('stock.location', string='Take from', required=True, domain="[('usage', '=', 'internal')]")
    treatment_id = fields.Many2one('alfia.treatment', domain="[('horse_id', '=', horse_id)]")
    state = fields.Selection([('draft', 'Draft'), ('issued', 'Issued'), ('returned', 'Returned (partly or fully)')], default='draft', required=True, tracking=True)
    move_ids = fields.Many2many('stock.move', readonly=True, copy=False)

    _qty_positive = models.Constraint('check(quantity > 0)', 'The quantity must be above zero.')

    def _move(self, qty, src, dst):
        self.ensure_one()
        move = self.env['stock.move'].sudo().create({
            'product_id': self.product_id.id, 'product_uom_qty': qty, 'quantity': qty, 'uom_id': self.product_id.uom_id.id,
            'location_id': src.id, 'location_dest_id': dst.id, 'picked': True, 'company_id': self.company_id.id,
            'origin': f"{self.horse_id.name} / medicine issue {self.id}"})
        move._action_confirm()
        move._action_done()
        with workflow():
            self.move_ids = [Command.link(move.id)]
        return move

    def write(self, vals):
        # Once issued, what was taken and from where is fixed: a return puts back exactly that, never more or elsewhere.
        if not is_trusted(self.env) and {'product_id', 'quantity', 'location_id', 'horse_id'} & set(vals) and any(r.state != 'draft' for r in self):
            raise UserError(self.env._("An issued medicine record cannot be changed. Record a new issue instead."))
        return super().write(vals)

    def action_issue(self):
        if not (self.env.su or self.env.user.has_group(G + 'medicine_custodian')):
            raise UserError(self.env._("Only the medicine custodian can issue medicine."))
        for rec in self.filtered(lambda r: r.state == 'draft'):
            self.env.user._alfia_check_location(rec.location_id, self.env._("medicine store"))
            available = rec.product_id.with_context(location=rec.location_id.id).qty_available
            if available < rec.quantity:
                raise UserError(self.env._("Only %(have)s of %(product)s is available there.", have=available, product=rec.product_id.display_name))
            rec._move(rec.quantity, rec.location_id, rec.product_id.property_stock_inventory)
            with workflow():
                rec.state = 'issued'

    def action_return(self, qty=None):
        if not (self.env.su or self.env.user.has_group(G + 'medicine_custodian')):
            raise UserError(self.env._("Only the medicine custodian can take medicine back."))
        for rec in self.filtered(lambda r: r.state in ('issued', 'returned')):
            qty_back = qty if qty is not None else rec.quantity - rec.returned_qty
            if qty_back <= 0 or qty_back > rec.quantity - rec.returned_qty + 1e-9:
                raise UserError(self.env._("You can return at most what is still out (%s).", rec.quantity - rec.returned_qty))
            self.env.user._alfia_check_location(rec.location_id, self.env._("medicine store"))
            rec._move(qty_back, rec.product_id.property_stock_inventory, rec.location_id)
            with workflow():
                rec.write({'returned_qty': rec.returned_qty + qty_back, 'state': 'returned'})
