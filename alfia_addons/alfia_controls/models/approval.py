from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

DOC_TYPES = [
    ('purchase_request', 'Purchase requests'),
    ('purchase', 'Purchase orders'),
    ('bill', 'Vendor bills'),
    ('payment', 'Outgoing payments'),
]


class AlfiaApprovalLimit(models.Model):
    """How much a person (or everyone in a group) may approve, per kind of document, in the company's currency.

    While a company has no limit for a kind of document, that kind is not limited (the other controls still apply).
    Once it has one, only people covered by a limit may approve it, up to their amount."""
    _name = 'alfia.approval.limit'
    _description = 'Approval limit'
    _inherit = ['mail.thread']
    _order = 'document_type, amount'

    document_type = fields.Selection(DOC_TYPES, required=True, tracking=True)
    user_id = fields.Many2one('res.users', string='Person', tracking=True)
    group_id = fields.Many2one('res.groups', string='Or everyone in', tracking=True)
    amount = fields.Monetary(string='Up to', currency_field='currency_id', tracking=True)
    unlimited = fields.Boolean(tracking=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')
    active = fields.Boolean(default=True, tracking=True)

    _check_amount = models.Constraint('CHECK(amount >= 0)', 'An approval limit cannot be negative.')

    @api.constrains('user_id', 'group_id')
    def _check_target(self):
        for rec in self:
            if bool(rec.user_id) == bool(rec.group_id):
                raise ValidationError(self.env._("An approval limit is for one person or for one group."))

    def _check_not_own(self):
        if self.env.su:
            return
        for rec in self:
            if rec.user_id == self.env.user or (rec.group_id and rec.group_id in self.env.user.all_group_ids):
                raise UserError(self.env._("You cannot set an approval limit that covers yourself; another finance manager must."))

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._check_not_own()
        return records

    def write(self, vals):
        self._check_not_own()
        res = super().write(vals)
        self._check_not_own()
        return res

    @api.depends('document_type', 'user_id', 'group_id', 'amount', 'unlimited')
    def _compute_display_name(self):
        types = dict(DOC_TYPES)
        for rec in self:
            who = rec.user_id.name or rec.group_id.display_name or ''
            limit = self.env._('no limit') if rec.unlimited else f"{rec.amount:,.2f} {rec.currency_id.name or ''}"
            rec.display_name = f"{types.get(rec.document_type, '')}: {who} – {limit}"

    # ------------------------------------------------------------------ the check every approval goes through
    @api.model
    def _own_limit(self, user, doc_type, company):
        """(limited, amount): limited is False when the company has no limit for this kind of document."""
        rules = self.sudo().search([('document_type', '=', doc_type), ('company_id', '=', company.id)])
        if not rules:
            return False, 0.0
        mine = rules.filtered(lambda r: r.user_id == user or (r.group_id and r.group_id in user.all_group_ids))
        if any(mine.mapped('unlimited')):
            return True, float('inf')
        return True, max(mine.mapped('amount') or [0.0])

    @api.model
    def _limit_for(self, user, doc_type, company, date=None, creator=None):
        """(limited, own, delegated, delegator): the user's own limit, and the highest limit lent to them by a delegation
        active on `date`. A delegation never counts for a document the user created themselves."""
        limited, own = self._own_limit(user, doc_type, company)
        delegated, delegator = own, self.env['res.users']
        if not limited or (creator and creator == user):
            return limited, own, delegated, delegator
        for delegation in self.env['alfia.approval.delegation'].sudo()._active_for(user, doc_type, company, date):
            _lim, their = self._own_limit(delegation.delegator_id, doc_type, company)
            if their > delegated:
                delegated, delegator = their, delegation.delegator_id
        return limited, own, delegated, delegator

    @api.model
    def _check(self, doc_type, amount_company, company, record_name, date=None, creator=None):
        """Raise when the current user may not approve `amount_company` (company currency). Returns the delegator whose
        limit was needed, if any, so the approval can say so.

        A delegated limit never covers the delegate's own documents (and since 24 Nov 2026 nobody approves a document
        they created at all; this stays as a second line with a clearer message)."""
        users = self.env['res.users']
        if self.env.su:
            return users
        limited, own, delegated, delegator = self._limit_for(self.env.user, doc_type, company, date)
        if not limited or amount_company <= own:
            return users
        currency = company.currency_id
        if amount_company <= delegated:
            if creator and creator == self.env.user:
                raise UserError(self.env._(
                    "%(doc)s is above your own approval limit of %(limit)s, and the limit delegated to you by %(owner)s does "
                    "not cover documents you created yourself. Someone else must approve it.",
                    doc=record_name, limit=f"{own:,.2f} {currency.name}", owner=delegator.name))
            return delegator
        top = max(own, delegated)
        if top:
            raise UserError(self.env._(
                "%(doc)s is %(amount)s, above your approval limit of %(limit)s. Someone with a higher limit must approve it.",
                doc=record_name, amount=f"{amount_company:,.2f} {currency.name}", limit=f"{top:,.2f} {currency.name}"))
        raise UserError(self.env._("You have no approval limit for this kind of document, so you cannot approve %s.", record_name))

    @api.model
    def _allows(self, user, doc_type, amount_company, company, creator=None):
        limited, own, delegated, _d = self._limit_for(user, doc_type, company, creator=creator)
        return not limited or amount_company <= max(own, delegated)


class AlfiaApprovalDelegation(models.Model):
    """While its owner is away, someone else approves with the owner's limit. Dated, with a reason, and never for
    the delegate's own documents (segregation of duties still applies)."""
    _name = 'alfia.approval.delegation'
    _description = 'Approval delegation'
    _inherit = ['mail.thread']
    _order = 'date_from desc'

    delegator_id = fields.Many2one('res.users', string='Delegated by', required=True, default=lambda self: self.env.user, tracking=True)
    delegate_id = fields.Many2one('res.users', string='Approves instead', required=True, tracking=True)
    date_from = fields.Date(required=True, default=fields.Date.context_today, tracking=True)
    date_to = fields.Date(required=True, tracking=True)
    document_types = fields.Char(compute='_compute_document_types', store=True)
    for_purchase_request = fields.Boolean(string='Purchase requests', default=True)
    for_purchase = fields.Boolean(string='Purchase orders', default=True)
    for_bill = fields.Boolean(string='Vendor bills', default=True)
    for_payment = fields.Boolean(string='Outgoing payments', default=True)
    reason = fields.Char(required=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    active = fields.Boolean(default=True, tracking=True)

    @api.depends('for_purchase_request', 'for_purchase', 'for_bill', 'for_payment')
    def _compute_document_types(self):
        for rec in self:
            rec.document_types = ','.join(t for t, _l in DOC_TYPES if rec[f'for_{t}'])

    @api.constrains('delegator_id', 'delegate_id', 'date_from', 'date_to')
    def _check_dates(self):
        for rec in self:
            if rec.delegator_id == rec.delegate_id:
                raise ValidationError(self.env._("A person cannot delegate to themselves."))
            if rec.date_to < rec.date_from:
                raise ValidationError(self.env._("A delegation must end on or after the day it starts."))
            if (rec.date_to - rec.date_from).days > 90:
                raise ValidationError(self.env._("A delegation lasts at most 90 days; give a longer one a new approval limit instead."))

    def _is_manager(self):
        return self.env.su or self.env.user.has_group('account.group_account_manager')

    @api.model_create_multi
    def create(self, vals_list):
        today = fields.Date.context_today(self)
        records = super().create(vals_list)
        if not self._is_manager():
            for rec in records:
                if rec.delegator_id != self.env.user:
                    raise UserError(self.env._("You can only delegate your own approvals."))
                if rec.date_from < today:
                    raise UserError(self.env._("A delegation cannot start in the past."))
        return records

    def write(self, vals):
        if not self._is_manager():
            today = fields.Date.context_today(self)
            for rec in self:
                started = rec.date_from <= today
                # Once it has started, the owner may only end it earlier (or switch it off).
                if started and set(vals) - {'date_to', 'active'}:
                    raise UserError(self.env._("A delegation that has started can only be ended earlier."))
                if started and 'date_to' in vals and fields.Date.to_date(vals['date_to']) > rec.date_to:
                    raise UserError(self.env._("A delegation that has started cannot be extended; create a new one."))
                if 'delegator_id' in vals and vals['delegator_id'] != self.env.user.id:
                    raise UserError(self.env._("You can only delegate your own approvals."))
        return super().write(vals)

    @api.model
    def _active_for(self, user, doc_type, company, date=None):
        date = date or fields.Date.context_today(self)
        return self.search([('delegate_id', '=', user.id), ('company_id', '=', company.id), (f'for_{doc_type}', '=', True),
                            ('date_from', '<=', date), ('date_to', '>=', date)])


# ---------------------------------------------------------------------- where approvals happen
class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    def _approval_allowed(self):
        allowed = super()._approval_allowed()
        return allowed and (self.env.su or self.env['alfia.approval.limit']._allows(
            self.env.user, 'purchase', self._alfia_amount_company(), self.company_id, creator=self.create_uid))

    def button_approve(self, force=False):
        for order in self.filtered(lambda o: o.state in ('draft', 'sent', 'to approve')):
            delegators = self.env['alfia.approval.limit']._check('purchase', order._alfia_amount_company(), order.company_id, order.name,
                                                                 creator=order.create_uid)
            if delegators:
                order.message_post(body=self.env._("Approved by %(user)s on behalf of %(owner)s (delegation).",
                                                   user=self.env.user.name, owner=delegators.name))
        return super().button_approve(force=force)


class AccountMove(models.Model):
    _inherit = 'account.move'

    def _post(self, soft=True):
        for move in self.filtered(lambda m: m.move_type in ('in_invoice', 'in_refund') and m.state == 'draft'):
            amount = abs(move.amount_total_signed)
            delegators = self.env['alfia.approval.limit']._check('bill', amount, move.company_id, move.name or move.ref or self.env._('This bill'),
                                                                 creator=move.create_uid)
            if delegators:
                move.message_post(body=self.env._("Posted by %(user)s on behalf of %(owner)s (delegation).",
                                                  user=self.env.user.name, owner=delegators.name))
        return super()._post(soft=soft)


class AccountPayment(models.Model):
    _inherit = 'account.payment'

    def action_post(self):
        for payment in self.filtered(lambda p: p.payment_type == 'outbound' and p.state == 'draft'):
            amount = payment.currency_id._convert(abs(payment.amount), payment.company_id.currency_id, payment.company_id,
                                                  payment.date or fields.Date.today())
            delegators = self.env['alfia.approval.limit']._check('payment', amount, payment.company_id, payment.name or self.env._('This payment'),
                                                                 creator=payment.create_uid)
            if delegators:
                payment.message_post(body=self.env._("Confirmed by %(user)s on behalf of %(owner)s (delegation).",
                                                     user=self.env.user.name, owner=delegators.name))
        return super().action_post()


class AlfiaPurchaseRequest(models.Model):
    _inherit = 'alfia.purchase.request'

    def action_approve(self):
        for rec in self:
            delegators = self.env['alfia.approval.limit']._check('purchase_request', rec.estimated_total, rec.company_id, rec.name,
                                                                 creator=rec.requested_by_id)
            if delegators:
                rec.message_post(body=self.env._("Approved by %(user)s on behalf of %(owner)s (delegation).",
                                                 user=self.env.user.name, owner=delegators.name))
        return super().action_approve()
