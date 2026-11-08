from odoo import Command, api, fields, models
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

    def _alfia_needs_second_person(self):
        self.ensure_one()
        if self.payment_type != 'outbound':
            return False
        amount = self.currency_id._convert(abs(self.amount), self.company_id.currency_id, self.company_id, self.date or fields.Date.today())
        return self._alfia_sod_applies(amount, self.company_id)

    @api.model_create_multi
    def create(self, vals_list):
        payments = super().create(vals_list)
        # Some paths (e.g. 'Register payment') create payments already confirmed. An outgoing payment that needs a second
        # person always starts as a draft, whichever way it is created.
        for payment in payments:
            if payment.state != 'draft' and payment._alfia_needs_second_person():
                payment.state = 'draft'
                if payment.move_id.state == 'posted':
                    payment.move_id.button_draft()
        return payments

    def action_post(self):
        for payment in self:
            if payment.payment_type == 'outbound' and payment.create_uid == self.env.user:
                amount = payment.currency_id._convert(
                    abs(payment.amount), payment.company_id.currency_id, payment.company_id, payment.date or fields.Date.today())
                if payment._alfia_sod_applies(amount, payment.company_id):
                    raise UserError(self.env._(
                        "Segregation of duties: you created this outgoing payment, so another person must confirm it."))
        return super().action_post()


class AccountPaymentRegister(models.TransientModel):
    """'Register payment' creates and posts in one step. Under segregation of duties an outgoing payment is left as a
    draft linked to its bills, for a second person to confirm; the bills are matched when it is confirmed."""
    _inherit = 'account.payment.register'

    def _alfia_held(self, to_process):
        return [vals for vals in to_process if vals['payment'].state == 'draft' and vals['payment']._alfia_needs_second_person()]

    def _post_payments(self, to_process, edit_mode=False):
        held = self._alfia_held(to_process)
        for vals in held:
            vals['payment'].message_post(body=self.env._("Waiting for a second person to confirm this outgoing payment."))
        return super()._post_payments([v for v in to_process if v not in held], edit_mode=edit_mode)

    def _reconcile_payments(self, to_process, edit_mode=False):
        held = self._alfia_held(to_process)
        for vals in held:
            # Keep the link to the bills so confirming the payment settles them; nothing is reconciled yet.
            payment = vals['payment']
            payment.invoice_ids = [Command.link(m.id) for m in vals['to_reconcile'].move_id]
            vals['to_reconcile'].move_id.matched_payment_ids = [Command.link(payment.id)]
        return super()._reconcile_payments([v for v in to_process if v not in held], edit_mode=edit_mode)


class AccountPaymentSettle(models.Model):
    _inherit = 'account.payment'

    def action_post(self):
        drafts = self.filtered(lambda p: p.state == 'draft')
        res = super().action_post()
        # A payment confirmed by the second person settles the bills it was registered for.
        for payment in drafts.filtered(lambda p: p.state != 'draft' and p.invoice_ids):
            payment_lines = payment.move_id.line_ids.filtered(
                lambda l: l.parent_state == 'posted' and not l.reconciled and l.account_type in ('liability_payable', 'asset_receivable'))
            for account in payment_lines.account_id:
                open_lines = payment.invoice_ids.line_ids.filtered(lambda l: l.account_id == account and not l.reconciled)
                (payment_lines.filtered(lambda l: l.account_id == account) + open_lines).reconcile()
        return res
