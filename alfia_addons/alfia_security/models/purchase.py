from odoo import fields, models
from odoo.exceptions import UserError


class PurchaseOrder(models.Model):
    _name = 'purchase.order'
    _inherit = ['purchase.order', 'alfia.sod.mixin']

    def _alfia_amount_company(self):
        self.ensure_one()
        return self.currency_id._convert(
            self.amount_total, self.company_id.currency_id, self.company_id, self.date_order or fields.Date.today())

    def _alfia_creator_must_not_approve(self):
        self.ensure_one()
        return self.create_uid == self.env.user and self._alfia_sod_applies(self._alfia_amount_company(), self.company_id)

    def _approval_allowed(self):
        allowed = super()._approval_allowed()
        # The buyer who created the order never approves it: the order is sent for approval instead.
        return allowed and not self._alfia_creator_must_not_approve()

    def button_approve(self, force=False):
        for order in self:
            if order.state in ('draft', 'sent', 'to approve') and order._alfia_creator_must_not_approve():
                raise UserError(self.env._(
                    "Segregation of duties: you created %s, so another authorised person must approve it.", order.name))
        return super().button_approve(force=force)
