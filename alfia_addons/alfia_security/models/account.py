from odoo import fields, models
from odoo.exceptions import UserError


class AccountMove(models.Model):
    _name = 'account.move'
    _inherit = ['account.move', 'alfia.sod.mixin']

    def _post(self, soft=True):
        for move in self:
            if move.move_type in ('in_invoice', 'in_refund') and move.create_uid == self.env.user:
                amount = move.currency_id._convert(
                    abs(move.amount_total), move.company_id.currency_id, move.company_id, move.date or fields.Date.today())
                if move._alfia_sod_applies(amount, move.company_id):
                    raise UserError(self.env._(
                        "Segregation of duties: you entered this vendor bill, so another person must post it."))
        return super()._post(soft=soft)


class AccountPayment(models.Model):
    _name = 'account.payment'
    _inherit = ['account.payment', 'alfia.sod.mixin']

    def action_post(self):
        for payment in self:
            if payment.payment_type == 'outbound' and payment.create_uid == self.env.user:
                amount = payment.currency_id._convert(
                    abs(payment.amount), payment.company_id.currency_id, payment.company_id, payment.date or fields.Date.today())
                if payment._alfia_sod_applies(amount, payment.company_id):
                    raise UserError(self.env._(
                        "Segregation of duties: you created this outgoing payment, so another person must confirm it."))
        return super().action_post()
