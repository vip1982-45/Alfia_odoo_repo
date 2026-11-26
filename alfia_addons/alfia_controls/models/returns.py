"""A customer return credits what came back: from a validated return, accounting drafts a credit note for exactly the
returned quantities (the sale already knows them), linked to the return, instead of reversing the whole invoice."""
from odoo import fields, models
from odoo.exceptions import AccessError, UserError

INVOICING = ('account.group_account_invoice', 'account.group_account_user', 'account.group_account_manager')


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    alfia_is_customer_return = fields.Boolean(compute='_compute_alfia_is_customer_return')
    alfia_return_credit_ids = fields.Many2many('account.move', string='Credit notes for this return', copy=False, readonly=True,
                                               relation='alfia_picking_return_credit_rel')

    def _compute_alfia_is_customer_return(self):
        for picking in self:
            picking.alfia_is_customer_return = bool(
                picking.state == 'done' and picking.picking_type_code == 'incoming' and picking.return_id
                and 'sale_id' in picking._fields and picking.sale_id)

    def action_alfia_return_credit_note(self):
        self.ensure_one()
        if not (self.env.su or any(self.env.user.has_group(g) for g in INVOICING)):
            raise AccessError(self.env._("Only accounting staff prepare credit notes."))
        if not self.alfia_is_customer_return:
            raise UserError(self.env._("A credit note is made from a validated return of goods sold."))
        lines = self.move_ids.filtered(lambda m: m.state == 'done').sale_line_id.sudo()
        to_credit = lines.filtered(lambda l: l.qty_to_invoice < 0)
        if not to_credit:
            raise UserError(self.env._(
                "Nothing to credit for %s: the returned goods were not invoiced yet (the next invoice bills only what was "
                "kept), or a credit note for them already exists.", self.name))
        order = to_credit.order_id
        moves = order.with_context(alfia_invoice_line_ids=to_credit.ids)._create_invoices(final=True)
        credits = moves.filtered(lambda m: m.move_type == 'out_refund')
        if not credits:
            raise UserError(self.env._("No credit note was needed for %s.", self.name))
        credits.write({'ref': self.env._("Return %s", self.name)})
        self.sudo().alfia_return_credit_ids = [(4, c.id) for c in credits]
        credits.message_post(body=self.env._("Credit note for the goods returned on %(ret)s (sale %(sale)s).", ret=self.name,
                                             sale=order.name))
        self.message_post(body=self.env._("Credit note %s drafted for the returned goods.", ', '.join(credits.mapped('display_name'))))
        return {'type': 'ir.actions.act_window', 'res_model': 'account.move', 'res_id': credits[:1].id, 'view_mode': 'form'}


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def _get_invoiceable_lines(self, final=False):
        lines = super()._get_invoiceable_lines(final)
        only = self.env.context.get('alfia_invoice_line_ids')
        if only:
            lines = lines.filtered(lambda l: l.id in only)
        return lines
