from odoo import Command, api, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.alfia_security.flow import workflow

INSPECTOR = 'alfia_controls.group_quality_inspector'


class ProductCategory(models.Model):
    _inherit = 'product.category'

    alfia_requires_inspection = fields.Boolean(
        string='Inspect on receipt',
        help="Goods of this category (and its sub-categories) received from a supplier wait for a quality inspection before "
             "they can be put into stock.")
    alfia_effective_inspection = fields.Boolean(compute='_compute_alfia_effective_inspection', store=True, recursive=True)

    @api.depends('alfia_requires_inspection', 'parent_id.alfia_effective_inspection')
    def _compute_alfia_effective_inspection(self):
        for categ in self:
            categ.alfia_effective_inspection = categ.alfia_requires_inspection or categ.parent_id.alfia_effective_inspection


class AlfiaQualityCheck(models.Model):
    """One received line waiting for inspection. Accepting releases it to stock; rejecting moves it to Rejected goods with
    the reason, ready to be returned to the supplier."""
    _name = 'alfia.quality.check'
    _description = 'Receipt quality check'
    _inherit = ['mail.thread', 'alfia.guarded.mixin', 'alfia.sod.mixin']
    _order = 'state, id desc'
    _alfia_guarded = ('state', 'received_by_id', 'inspector_id', 'decided_date', 'move_id', 'picking_id', 'quantity', 'product_id', 'location_id')

    name = fields.Char(compute='_compute_name', store=True)
    picking_id = fields.Many2one('stock.picking', string='Receipt', readonly=True, index=True)
    move_id = fields.Many2one('stock.move', string='Received move', readonly=True, index=True)
    partner_id = fields.Many2one(related='picking_id.partner_id', string='Supplier')
    product_id = fields.Many2one('product.product', readonly=True, required=True)
    lot_names = fields.Char(string='Batches', compute='_compute_lots')
    quantity = fields.Float(readonly=True)
    uom_id = fields.Many2one(related='move_id.uom_id')
    location_id = fields.Many2one('stock.location', string='Goods are in', readonly=True)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)
    state = fields.Selection([('pending', 'Waiting for inspection'), ('passed', 'Accepted'), ('rejected', 'Rejected')],
                             default='pending', required=True, tracking=True)
    findings = fields.Text(help="What was checked and found (packaging, temperature, expiry, documents...). Required to reject.")
    received_by_id = fields.Many2one('res.users', string='Received by', readonly=True)
    inspector_id = fields.Many2one('res.users', readonly=True, copy=False)
    decided_date = fields.Datetime(readonly=True, copy=False)

    @api.depends('picking_id.name', 'product_id')
    def _compute_name(self):
        for rec in self:
            rec.name = f"{rec.picking_id.name or ''} – {rec.product_id.display_name or ''}"

    def _compute_lots(self):
        for rec in self:
            rec.lot_names = ', '.join(rec.move_id.move_line_ids.lot_id.mapped('name'))

    # ------------------------------------------------------------------ creation, from the receipt
    @api.model
    def _alfia_hold_location(self, warehouse, kind):
        """'hold' (waiting for inspection, one-step receipts) or 'rejected' area of a warehouse, created once."""
        Location = self.env['stock.location'].sudo()
        name = {'hold': 'حجز الجودة · Quality hold', 'rejected': 'بضاعة مرفوضة · Rejected goods'}[kind]
        loc = Location.search([('name', '=', name), ('location_id', '=', warehouse.view_location_id.id)], limit=1)
        return loc or Location.create({'name': name, 'usage': 'internal', 'location_id': warehouse.view_location_id.id,
                                       'company_id': warehouse.company_id.id})

    @api.model
    def _alfia_create_for_moves(self, moves):
        for move in moves:
            location = move.location_dest_id
            if not move.move_dest_ids:
                # A one-step receipt put the goods straight into stock: hold them aside until they are inspected.
                warehouse = move.location_dest_id.warehouse_id or move.picking_type_id.warehouse_id
                hold = self._alfia_hold_location(warehouse, 'hold')
                self._alfia_transfer(move, move.location_dest_id, hold)
                location = hold
            with workflow():
                check = self.sudo().create({
                    'picking_id': move.picking_id.id, 'move_id': move.id, 'product_id': move.product_id.id,
                    'quantity': move.quantity, 'location_id': location.id, 'company_id': move.company_id.id,
                    'received_by_id': self.env.user.id})
            move.picking_id.message_post(body=self.env._("%s waits for quality inspection before it can go into stock.",
                                                         move.product_id.display_name))
            check.message_post(body=self.env._("Received in %s; waiting for inspection.", location.complete_name))

    @api.model
    def _alfia_transfer(self, move, src, dst):
        """Move exactly what `move` received (same batches and quantities) from src to dst."""
        new = self.env['stock.move'].sudo().create({
            'product_id': move.product_id.id, 'product_uom_qty': move.quantity, 'uom_id': move.uom_id.id,
            'location_id': src.id, 'location_dest_id': dst.id, 'company_id': move.company_id.id,
            'origin': f"{move.picking_id.name or ''} / quality"})
        new._action_confirm()
        new.write({'move_line_ids': [Command.create({
            'product_id': ml.product_id.id, 'lot_id': ml.lot_id.id, 'quantity': ml.quantity, 'uom_id': ml.uom_id.id,
            'location_id': src.id, 'location_dest_id': dst.id, 'picked': True}) for ml in move.move_line_ids]})
        new.picked = True
        new._action_done()
        return new

    # ------------------------------------------------------------------ decisions
    def _check_inspector(self):
        if not (self.env.su or self.env.user.has_group(INSPECTOR)):
            raise AccessError(self.env._("Only quality inspectors can accept or reject received goods."))
        for rec in self:
            if rec.state != 'pending':
                raise UserError(self.env._("%s has already been decided.", rec.name))
            if rec.received_by_id == self.env.user and rec._alfia_sod_applies(None, rec.company_id):
                raise UserError(self.env._("You received these goods, so another person must inspect them."))

    def action_accept(self):
        self._check_inspector()
        for rec in self:
            if not rec.move_id.move_dest_ids:
                self._alfia_transfer(rec.move_id, rec.location_id, rec.move_id.location_dest_id)
            with workflow():
                rec.write({'state': 'passed', 'inspector_id': self.env.user.id, 'decided_date': fields.Datetime.now(),
                           'location_id': rec.move_id.location_dest_id.id})
            rec.message_post(body=self.env._("Accepted by %(user)s. %(findings)s", user=self.env.user.name, findings=rec.findings or ''))

    def action_reject(self):
        self._check_inspector()
        for rec in self:
            if not rec.findings:
                raise UserError(self.env._("Record what was found before rejecting %s.", rec.name))
            warehouse = rec.location_id.warehouse_id or rec.move_id.picking_type_id.warehouse_id
            rejected = self._alfia_hold_location(warehouse, 'rejected')
            # The put-away waiting for these goods will not happen (cancelled first, so it no longer reserves them).
            rec.move_id.move_dest_ids.filtered(lambda m: m.state not in ('done', 'cancel')).sudo()._action_cancel()
            self._alfia_transfer(rec.move_id, rec.location_id, rejected)
            with workflow():
                rec.write({'state': 'rejected', 'inspector_id': self.env.user.id, 'decided_date': fields.Datetime.now(),
                           'location_id': rejected.id})
            rec.message_post(body=self.env._("Rejected by %(user)s: %(findings)s", user=self.env.user.name, findings=rec.findings))
            rec.picking_id.message_post(body=self.env._("%(product)s was rejected at inspection: %(findings)s",
                                                        product=rec.product_id.display_name, findings=rec.findings))


class StockMove(models.Model):
    _inherit = 'stock.move'

    def _action_done(self, cancel_backorder=False):
        # Goods waiting for inspection (or rejected) are not put into stock.
        waiting = self.env['alfia.quality.check'].sudo().search(
            [('move_id', 'in', self.move_orig_ids.ids), ('state', '!=', 'passed')])
        if waiting:
            raise UserError(self.env._("Waiting for quality inspection: %s. An inspector must accept them before they go into stock.",
                                       ', '.join(waiting.mapped('name'))))
        done = super()._action_done(cancel_backorder=cancel_backorder)
        received = done.filtered(lambda m: m.state == 'done' and m.location_id.usage == 'supplier'
                                 and m.location_dest_id.usage == 'internal' and m.product_id.categ_id.alfia_effective_inspection
                                 and not self.env['alfia.quality.check'].sudo().search_count([('move_id', '=', m.id)]))
        if received:
            self.env['alfia.quality.check']._alfia_create_for_moves(received)
        return done
