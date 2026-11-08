from odoo import Command, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .common import BILLING_MANAGERS, lock_after, require_any


class AlfiaHorseSale(models.Model):
    """Sale or gift of a horse. Completing it moves ownership, status and the horse off site in one step."""
    _name = 'alfia.horse.sale'
    _description = 'Horse sale or gift'
    _inherit = ['mail.thread']
    _order = 'date desc, id desc'

    horse_id = fields.Many2one('alfia.horse', required=True, index=True, ondelete='restrict', tracking=True)
    company_id = fields.Many2one('res.company', related='horse_id.company_id', store=True)
    kind = fields.Selection([('sale', 'Sale'), ('gift', 'Gift')], required=True, default='sale')
    buyer_id = fields.Many2one('res.partner', string='Buyer / recipient', required=True)
    date = fields.Date(required=True, default=fields.Date.context_today)
    price = fields.Monetary(currency_field='currency_id')
    currency_id = fields.Many2one('res.currency', default=lambda self: self.env.company.currency_id)
    state = fields.Selection([('draft', 'Draft'), ('approved', 'Approved'), ('done', 'Completed'), ('cancelled', 'Cancelled')], default='draft', required=True, tracking=True)
    created_by_id = fields.Many2one('res.users', default=lambda self: self.env.user, readonly=True, copy=False)
    approved_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    move_id = fields.Many2one('account.move', string='Invoice', readonly=True, copy=False)
    notes = fields.Text()

    @api.constrains('kind', 'price')
    def _check_price(self):
        for sale in self:
            if sale.price < 0 or (sale.kind == 'sale' and not sale.price):
                raise ValidationError(self.env._("A sale needs a price above zero; a gift has none."))

    def action_approve(self):
        require_any(self.env, BILLING_MANAGERS, self.env._("managers"))
        for sale in self:
            if sale.state != 'draft':
                raise UserError(self.env._("Only a draft can be approved."))
            if sale.created_by_id == self.env.user and not self.env.su and not self.env.user.has_group('alfia_security.group_alfia_sod_exempt'):
                raise UserError(self.env._("You prepared this sale, so someone else must approve it."))
            sale.write({'state': 'approved', 'approved_by_id': self.env.user.id})

    def write(self, vals):
        lock_after(self, vals, ('approved', 'done'), ('state', 'move_id', 'notes'), self.env._("An approved sale"))
        return super().write(vals)

    def action_complete(self):
        require_any(self.env, BILLING_MANAGERS, self.env._("managers"))
        for sale in self:
            if sale.state != 'approved':
                raise UserError(self.env._("Approve the sale first."))
            horse = sale.horse_id
            # The approved sale is the authority for what follows; the manager completing it must be able to see the horse,
            # and the ownership, movement and status changes are then made by the system on the sale's behalf.
            horse.check_access('read')
            horse = horse.sudo()
            if horse.status != 'active':
                raise UserError(self.env._("%s is not an active horse.", horse.name))
            if horse.active_pregnancy_id and horse.active_pregnancy_id.state in ('bred', 'pregnant'):
                sale.message_post(body=self.env._("Note: the mare is in an open pregnancy."))
            reason = dict(self._fields['kind']._description_selection(self.env))[sale.kind]
            if horse.ownership_ids:
                horse.ownership_ids.action_transfer_all(sale.buyer_id, sale.date, reason)
            else:
                self.env['alfia.horse.ownership'].sudo().create({'horse_id': horse.id, 'partner_id': sale.buyer_id.id, 'date_from': sale.date, 'reason': reason})
            if horse.on_site:
                self.env['alfia.horse.movement'].sudo().create({'horse_id': horse.id, 'kind': 'departure', 'state': 'planned', 'partner_id': sale.buyer_id.id,
                                                         'reason': self.env._("Sold or gifted")}).action_done()
            horse.write({'status': 'sold' if sale.kind == 'sale' else 'gifted'})
            if sale.kind == 'sale':
                product = self.env.ref('alfia_equine_ops.product_horse_sale')
                sale.move_id = self.env['account.move'].sudo().with_company(sale.company_id).create({
                    'move_type': 'out_invoice', 'partner_id': sale.buyer_id.id, 'invoice_date': sale.date,
                    'invoice_line_ids': [Command.create({'product_id': product.id, 'name': self.env._("Horse: %s", horse.name), 'quantity': 1, 'price_unit': sale.price})]})
            sale.state = 'done'

    def action_cancel(self):
        if any(s.state == 'done' for s in self):
            raise UserError(self.env._("A completed sale cannot be cancelled."))
        self.state = 'cancelled'
