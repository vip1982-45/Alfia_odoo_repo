from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare


class ResCompany(models.Model):
    _inherit = 'res.company'

    alfia_receipt_tolerance = fields.Float(
        string='Receipt tolerance (%)', default=5.0,
        help="A goods receipt may exceed its purchase order line by up to this percentage. Anything more is refused unless "
             "a purchase manager validates the receipt (and the excess is recorded on the order).")


class StockMove(models.Model):
    _inherit = 'stock.move'

    def _action_done(self, cancel_backorder=False):
        self._alfia_check_receipt_tolerance()
        return super()._action_done(cancel_backorder=cancel_backorder)

    def _alfia_check_receipt_tolerance(self):
        """More received than ordered (beyond the company's tolerance) needs a purchase manager."""
        incoming = self.filtered(lambda m: m.purchase_line_id and m.state != 'done' and m.location_id.usage == 'supplier')
        if not incoming:
            return
        manager = self.env.su or self.env.user.has_group('purchase.group_purchase_manager')
        for line, moves in incoming.grouped('purchase_line_id').items():
            ordered = line.product_qty                                 # in the order line's unit
            already = line.qty_received
            now = sum(m.uom_id._compute_quantity(m.quantity, line.uom_id, round=False) for m in moves)
            tolerance = (line.company_id or self.env.company).alfia_receipt_tolerance or 0.0
            limit = ordered * (1 + tolerance / 100.0)
            total = already + now
            if float_compare(total, limit, precision_digits=4) <= 0:
                continue
            if not manager:
                raise UserError(self.env._(
                    "%(order)s, %(product)s: receiving %(total)g against %(ordered)g ordered exceeds the %(tol)g%% tolerance. "
                    "Receive only what was ordered, or ask a purchase manager to validate the excess.",
                    order=line.order_id.name, product=line.product_id.display_name, total=total, ordered=ordered, tol=tolerance))
            line.order_id.message_post(body=self.env._(
                "%(user)s accepted a receipt of %(total)g against %(ordered)g ordered for %(product)s (above the %(tol)g%% tolerance).",
                user=self.env.user.name, total=total, ordered=ordered, product=line.product_id.display_name, tol=tolerance))


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    @api.depends('type')
    def _compute_invoice_policy(self):
        # Goods are invoiced for what was delivered; services (boarding, nominations) on what was ordered.
        super()._compute_invoice_policy()
        for template in self:
            if template.type == 'consu':
                template.invoice_policy = 'delivery'
