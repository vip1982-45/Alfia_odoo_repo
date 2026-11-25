from odoo import fields, models
from odoo.exceptions import AccessError, UserError

PURCHASE_MANAGER = 'purchase.group_purchase_manager'


class AccountMove(models.Model):
    """A supplier bill line priced above its purchase order line by more than the company's tolerance is not posted
    until a purchase manager, other than the person who entered the bill, approves the difference with a reason."""
    _inherit = 'account.move'

    alfia_price_approved_by_id = fields.Many2one('res.users', string='Price difference approved by', readonly=True, copy=False)
    alfia_price_approval_reason = fields.Char(string='Price difference reason', readonly=True, copy=False)
    alfia_price_variance = fields.Text(string='Prices above the order', compute='_compute_alfia_price_variance')

    def _alfia_price_variances(self):
        """[(line, order unit price, bill unit price in the order's unit, % above)] for lines above the tolerance."""
        self.ensure_one()
        tolerance = (self.company_id.alfia_bill_price_tolerance or 0.0) / 100.0
        found = []
        for line in self.invoice_line_ids.filtered(lambda l: l.purchase_line_id and l.display_type == 'product'):
            po_line = line.purchase_line_id
            ordered = po_line.price_unit * (1 - (po_line.discount or 0.0) / 100.0)
            billed = line.price_unit * (1 - (line.discount or 0.0) / 100.0)
            if line.product_uom_id and po_line.uom_id and line.product_uom_id != po_line.uom_id:
                billed = line.product_uom_id._compute_price(billed, po_line.uom_id)
            if line.currency_id != po_line.currency_id:
                billed = line.currency_id._convert(billed, po_line.currency_id, self.company_id, self.invoice_date or fields.Date.today())
            if ordered > 0 and billed > ordered * (1 + tolerance) + po_line.currency_id.rounding:
                found.append((line, ordered, billed, (billed / ordered - 1) * 100))
        return found

    def _compute_alfia_price_variance(self):
        for move in self:
            rows = move._alfia_price_variances() if move.move_type == 'in_invoice' else []
            move.alfia_price_variance = '\n'.join(
                f"{line.product_id.display_name or line.name}: {ordered:,.2f} → {billed:,.2f} (+{pct:.1f}%)"
                for line, ordered, billed, pct in rows) or False

    def _post(self, soft=True):
        if not self.env.su:
            for move in self.filtered(lambda m: m.move_type == 'in_invoice' and m.state == 'draft'):
                rows = move._alfia_price_variances()
                if rows and not move.alfia_price_approved_by_id:
                    raise UserError(self.env._(
                        "%(bill)s is priced above its purchase order beyond the %(tol)s%% tolerance: %(lines)s. A purchase "
                        "manager must approve the price difference (Approve price difference) with a reason first.",
                        bill=move.name if move.name and move.name != '/' else move.ref or self.env._('This bill'),
                        tol=f"{move.company_id.alfia_bill_price_tolerance:g}",
                        lines='; '.join(f"{(l.product_id.display_name or l.name)} {o:,.2f} → {b:,.2f} (+{p:.1f}%)" for l, o, b, p in rows)))
        return super()._post(soft=soft)

    def action_alfia_approve_price(self):
        self.ensure_one()
        if not (self.env.su or self.env.user.has_group(PURCHASE_MANAGER)):
            raise AccessError(self.env._("Only purchase managers approve a bill's price difference."))
        return {'type': 'ir.actions.act_window', 'res_model': 'alfia.bill.price.approval', 'view_mode': 'form', 'target': 'new',
                'name': self.env._('Approve price difference'), 'context': {'default_move_id': self.id}}


class AlfiaBillPriceApproval(models.TransientModel):
    _name = 'alfia.bill.price.approval'
    _description = 'Approve a supplier bill price difference'

    move_id = fields.Many2one('account.move', required=True, readonly=True)
    variance = fields.Text(related='move_id.alfia_price_variance')
    reason = fields.Char(required=True)

    def action_confirm(self):
        self.ensure_one()
        if not (self.env.su or self.env.user.has_group(PURCHASE_MANAGER)):
            raise AccessError(self.env._("Only purchase managers approve a bill's price difference."))
        move = self.move_id.sudo()
        if move.state != 'draft':
            raise UserError(self.env._("Only a draft bill's price difference can be approved."))
        if move.create_uid == self.env.user:
            raise UserError(self.env._("You entered this bill, so another purchase manager must approve its price difference."))
        move.write({'alfia_price_approved_by_id': self.env.user.id, 'alfia_price_approval_reason': self.reason})
        move.message_post(body=self.env._("Price difference approved by %(user)s: %(reason)s (%(lines)s)", user=self.env.user.name,
                                          reason=self.reason, lines=move.alfia_price_variance or ''))
        return {'type': 'ir.actions.act_window_close'}


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    def write(self, vals):
        # A price changed after the difference was approved needs a new approval.
        if {'price_unit', 'discount', 'quantity', 'product_uom_id'} & set(vals):
            approved = self.move_id.filtered(lambda m: m.state == 'draft' and m.alfia_price_approved_by_id)
            if approved:
                approved.sudo().write({'alfia_price_approved_by_id': False, 'alfia_price_approval_reason': False})
        return super().write(vals)
