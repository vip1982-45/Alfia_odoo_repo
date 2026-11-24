from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.alfia_security.flow import is_trusted, trusted, workflow

LOCK_FIELDS = ('fiscalyear_lock_date', 'tax_lock_date', 'sale_lock_date', 'purchase_lock_date')
PERIOD_CLOSE = 'alfia_period_close'


class AccountMove(models.Model):
    _inherit = 'account.move'

    alfia_manual = fields.Boolean(string='Manual journal entry', compute='_compute_alfia_manual')

    @api.depends('move_type', 'journal_id', 'origin_payment_id', 'statement_line_id')
    def _compute_alfia_manual(self):
        for move in self:
            move.alfia_manual = move._alfia_is_manual()

    def _alfia_is_manual(self):
        """A journal entry a person typed in (Miscellaneous operations), as opposed to one the system generates from a
        document: payments, bank lines, exchange differences, cash basis taxes, stock valuation."""
        self.ensure_one()
        if self.move_type != 'entry' or self.journal_id.type != 'general':
            return False
        if self.origin_payment_id or self.statement_line_id or self.tax_cash_basis_rec_id:
            return False
        if self.journal_id == self.company_id.currency_exchange_journal_id:
            return False
        if 'stock_move_id' in self.line_ids._fields and self.line_ids.stock_move_id:
            return False
        return True

    def _alfia_amount(self):
        return sum(self.line_ids.mapped('debit'))

    def _post(self, soft=True):
        if not self.env.su:
            for move in self.filtered(lambda m: m.state == 'draft' and m.create_uid == self.env.user and m._alfia_is_manual()):
                if move._alfia_sod_applies(move._alfia_amount(), move.company_id):
                    raise UserError(self.env._(
                        "Segregation of duties: you entered the journal entry %s, so another person must review and post it.",
                        move.name if move.name and move.name != '/' else move.ref or ''))
        posted = super()._post(soft=soft)
        for move in posted.filtered(lambda m: m.state == 'posted' and m._alfia_is_manual() and not self.env.su):
            move.message_post(body=self.env._("Reviewed and posted by %s.", self.env.user.name))
        return posted

    def _alfia_check_autopost(self):
        # Scheduled posting runs as the system, so a manual entry set to post itself would skip the second person.
        if self.env.su:
            return
        for move in self:
            if move.auto_post != 'no' and move._alfia_is_manual() and move._alfia_sod_applies(move._alfia_amount(), move.company_id):
                raise UserError(self.env._("A manual journal entry is posted by a second person, so it cannot be set to post "
                                           "automatically."))

    @api.model_create_multi
    def create(self, vals_list):
        moves = super().create(vals_list)
        moves._alfia_check_autopost()
        return moves

    def write(self, vals):
        res = super().write(vals)
        if 'auto_post' in vals:
            self._alfia_check_autopost()
        return res


class ResCompany(models.Model):
    _inherit = 'res.company'

    def write(self, vals):
        # Closing or reopening a period goes through an approved period close, never a direct change of the lock dates.
        if set(LOCK_FIELDS) & set(vals) and not is_trusted(self.env, PERIOD_CLOSE):
            changed = [f for f in LOCK_FIELDS if f in vals and any(c[f] != fields.Date.to_date(vals[f]) for c in self)]
            if changed:
                raise UserError(self.env._("Lock dates are set by an approved period close (Accounting › Period closes)."))
        return super().write(vals)


class AccountLockException(models.Model):
    _inherit = 'account.lock_exception'

    @api.model_create_multi
    def create(self, vals_list):
        if not is_trusted(self.env, PERIOD_CLOSE):
            raise UserError(self.env._("A closed period is reopened through Accounting › Period closes, with a reason and a "
                                       "second person's approval."))
        return super().create(vals_list)


class AlfiaPeriodClose(models.Model):
    """Closing (or reopening) the books up to a date: prepared by one person, approved by another, who sets the lock date."""
    _name = 'alfia.period.close'
    _description = 'Period close'
    _inherit = ['mail.thread', 'alfia.guarded.mixin']
    _order = 'date desc, id desc'
    _alfia_guarded = ('state', 'requested_by_id', 'approved_by_id', 'approved_date', 'previous_lock_date')

    name = fields.Char(compute='_compute_name', store=True)
    kind = fields.Selection([('close', 'Close the period'), ('reopen', 'Reopen the period')], default='close', required=True,
                            tracking=True)
    date = fields.Date(string='Up to and including', required=True, tracking=True)
    lock = fields.Selection([('fiscalyear_lock_date', 'Everything (all users)'), ('tax_lock_date', 'Tax returns only')],
                            default='fiscalyear_lock_date', required=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    reason = fields.Text(help="Why the period is reopened (required to reopen).")
    state = fields.Selection([('draft', 'Draft'), ('submitted', 'Waiting for approval'), ('done', 'Done'), ('rejected', 'Rejected')],
                             default='draft', required=True, tracking=True)
    requested_by_id = fields.Many2one('res.users', default=lambda self: self.env.user, readonly=True)
    approved_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    approved_date = fields.Datetime(readonly=True, copy=False)
    previous_lock_date = fields.Date(readonly=True, copy=False)
    current_lock_date = fields.Date(compute='_compute_checks')
    draft_count = fields.Integer(string='Draft entries in the period', compute='_compute_checks')
    unreconciled_count = fields.Integer(string='Unreconciled bank lines in the period', compute='_compute_checks')
    pending_inspections = fields.Integer(string='Receipts waiting for inspection', compute='_compute_checks')

    _alfia_guarded_create = {'requested_by_id': '*'}

    @api.depends('kind', 'date')
    def _compute_name(self):
        for rec in self:
            label = self.env._('Close') if rec.kind == 'close' else self.env._('Reopen')
            rec.name = f"{label} {rec.date or ''}"

    @api.depends('date', 'company_id', 'lock')
    def _compute_checks(self):
        Move = self.env['account.move'].sudo()
        for rec in self:
            rec.current_lock_date = rec.company_id[rec.lock] if rec.company_id else False
            if not rec.date:
                rec.draft_count = rec.unreconciled_count = rec.pending_inspections = 0
                continue
            rec.draft_count = Move.search_count([('company_id', '=', rec.company_id.id), ('state', '=', 'draft'), ('date', '<=', rec.date)])
            rec.unreconciled_count = self.env['account.bank.statement.line'].sudo().search_count(
                rec.company_id._get_unreconciled_statement_lines_domain(rec.date))
            rec.pending_inspections = self.env['alfia.quality.check'].sudo().search_count(
                [('company_id', '=', rec.company_id.id), ('state', '=', 'pending'), ('picking_id.date_done', '<=', rec.date)])

    def _check_manager(self):
        if not (self.env.su or self.env.user.has_group('account.group_account_manager')):
            raise AccessError(self.env._("Only finance managers close or reopen periods."))

    def action_submit(self):
        self._check_manager()
        for rec in self:
            if rec.state not in ('draft', 'rejected'):
                raise UserError(self.env._("%s has already been submitted.", rec.name))
            if rec.kind == 'reopen' and not rec.reason:
                raise UserError(self.env._("Write why the period must be reopened."))
            if rec.kind == 'close' and rec.current_lock_date and rec.date <= rec.current_lock_date:
                raise UserError(self.env._("The books are already closed up to %s.", rec.current_lock_date))
            if rec.kind == 'reopen' and not (rec.current_lock_date and rec.date < rec.current_lock_date):
                raise UserError(self.env._("To reopen, choose a date before the current lock date (%s).", rec.current_lock_date or '—'))
            with workflow():
                rec.write({'state': 'submitted', 'requested_by_id': self.env.user.id})

    def action_approve(self):
        self._check_manager()
        for rec in self:
            if rec.state != 'submitted':
                raise UserError(self.env._("Only a submitted period close can be approved."))
            if rec.requested_by_id == self.env.user and not self.env.su:
                raise UserError(self.env._("You prepared %s, so another finance manager must approve it.", rec.name))
            if rec.kind == 'close' and rec.draft_count:
                raise UserError(self.env._("%s draft entries are dated in the period. Post or delete them first.", rec.draft_count))
            previous = rec.current_lock_date
            with trusted(PERIOD_CLOSE):
                rec.company_id.sudo().write({rec.lock: rec.date})
            with workflow():
                rec.write({'state': 'done', 'approved_by_id': self.env.user.id, 'approved_date': fields.Datetime.now(),
                           'previous_lock_date': previous})
            rec.message_post(body=self.env._("Approved by %(user)s: books locked up to %(date)s (before: %(prev)s).",
                                             user=self.env.user.name, date=rec.date, prev=previous or '—'))

    def action_reject(self):
        self._check_manager()
        for rec in self:
            if rec.state != 'submitted':
                raise UserError(self.env._("Only a submitted period close can be rejected."))
            with workflow():
                rec.write({'state': 'rejected'})
            rec.message_post(body=self.env._("Rejected by %s.", self.env.user.name))

    def write(self, vals):
        if {'kind', 'date', 'lock', 'company_id', 'reason'} & set(vals) and any(r.state in ('submitted', 'done') for r in self) \
                and not self.env.su:
            raise UserError(self.env._("A period close cannot be changed once it is submitted."))
        return super().write(vals)

    def unlink(self):
        if any(r.state == 'done' for r in self) and not self.env.su:
            raise UserError(self.env._("A completed period close is kept as the record of who closed the books."))
        return super().unlink()
