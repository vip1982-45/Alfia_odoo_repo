from collections import defaultdict

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from odoo.addons.alfia_security.flow import workflow

FINANCE = ('account.group_account_user', 'account.group_account_manager')
MANAGER = 'account.group_account_manager'


def _is(env, groups):
    return env.su or any(env.user.has_group(g) for g in groups)


def _analytic_ids(distribution):
    """Analytic account ids named in an analytic distribution ({"12": 100} or {"12,15": 100})."""
    ids = set()
    for key in (distribution or {}):
        for part in str(key).split(','):
            if part.strip().isdigit():
                ids.add(int(part))
    return ids


class AlfiaBudget(models.Model):
    """A versioned budget for a company and period. Lines plan amounts by account, and optionally by cost centre (a
    horse or activity) and department. Prepared by one person, approved by a finance manager who did not prepare it;
    a new version replaces the approved one only when it is approved in turn."""
    _name = 'alfia.budget'
    _description = 'Budget'
    _inherit = ['mail.thread', 'alfia.guarded.mixin']
    _order = 'date_from desc, name, version desc'
    _alfia_guarded = ('state', 'prepared_by_id', 'approved_by_id', 'approved_date', 'version', 'previous_id')
    _alfia_guarded_create = {'prepared_by_id': '*', 'version': '*', 'previous_id': '*'}

    name = fields.Char(required=True, tracking=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id', string='Currency basis')
    date_from = fields.Date(required=True, tracking=True)
    date_to = fields.Date(required=True, tracking=True)
    version = fields.Integer(default=1, readonly=True)
    previous_id = fields.Many2one('alfia.budget', string='Replaces version', readonly=True)
    state = fields.Selection([('draft', 'Draft'), ('submitted', 'Waiting for approval'), ('approved', 'Approved'),
                              ('superseded', 'Superseded'), ('rejected', 'Rejected')], default='draft', required=True, tracking=True)
    control = fields.Selection([('report', 'Report only'), ('warning', 'Warn'), ('block', 'Block')], default='warning', required=True,
                               tracking=True,
                               help="What happens when a purchase order or a bill would take a line over its budget: nothing "
                                    "(report only), a warning on the document, or a block that a finance manager can override "
                                    "with a recorded reason.")
    prepared_by_id = fields.Many2one('res.users', default=lambda self: self.env.user, readonly=True)
    approved_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    approved_date = fields.Datetime(readonly=True, copy=False)
    line_ids = fields.One2many('alfia.budget.line', 'budget_id', copy=True)
    planned_total = fields.Monetary(compute='_compute_totals', currency_field='currency_id')
    committed_total = fields.Monetary(compute='_compute_totals', currency_field='currency_id')
    actual_total = fields.Monetary(compute='_compute_totals', currency_field='currency_id')

    @api.depends('line_ids.planned', 'line_ids.committed', 'line_ids.actual')
    def _compute_totals(self):
        for budget in self:
            budget.planned_total = sum(budget.line_ids.mapped('planned'))
            budget.committed_total = sum(budget.line_ids.mapped('committed'))
            budget.actual_total = sum(budget.line_ids.mapped('actual'))

    @api.depends('name', 'version')
    def _compute_display_name(self):
        for budget in self:
            budget.display_name = f"{budget.name or ''} v{budget.version or 1}".strip()

    @api.constrains('date_from', 'date_to')
    def _check_dates(self):
        for budget in self:
            if budget.date_to < budget.date_from:
                raise ValidationError(self.env._("A budget must end on or after the day it starts."))

    def write(self, vals):
        if {'name', 'date_from', 'date_to', 'company_id', 'control', 'line_ids'} & set(vals) and not self.env.su:
            if any(b.state in ('submitted', 'approved', 'superseded') for b in self):
                raise UserError(self.env._("A submitted or approved budget cannot be changed. Create a new version."))
        return super().write(vals)

    def _check_finance(self, manager=False):
        if not _is(self.env, (MANAGER,) if manager else FINANCE):
            raise AccessError(self.env._("Only finance managers approve budgets.") if manager
                              else self.env._("Only finance staff prepare budgets."))

    def action_submit(self):
        self._check_finance()
        for budget in self:
            if budget.state not in ('draft', 'rejected'):
                raise UserError(self.env._("%s has already been submitted.", budget.display_name))
            if not budget.line_ids:
                raise UserError(self.env._("Add budget lines before submitting."))
            with workflow():
                budget.write({'state': 'submitted', 'prepared_by_id': self.env.user.id})

    def action_approve(self):
        self._check_finance(manager=True)
        for budget in self:
            if budget.state != 'submitted':
                raise UserError(self.env._("Only a submitted budget can be approved."))
            if budget.prepared_by_id == self.env.user and not self.env.su:
                raise UserError(self.env._("You prepared %s, so another finance manager must approve it.", budget.display_name))
            with workflow():
                budget.write({'state': 'approved', 'approved_by_id': self.env.user.id, 'approved_date': fields.Datetime.now()})
                if budget.previous_id.state == 'approved':
                    budget.previous_id.write({'state': 'superseded'})
            budget.message_post(body=self.env._("Approved by %s.", self.env.user.name))

    def action_reject(self):
        self._check_finance(manager=True)
        for budget in self.filtered(lambda b: b.state == 'submitted'):
            with workflow():
                budget.write({'state': 'rejected'})
            budget.message_post(body=self.env._("Rejected by %s.", self.env.user.name))

    def action_new_version(self):
        """A draft copy of an approved budget, to revise; the approved one stays in force until the new one is approved."""
        self._check_finance()
        self.ensure_one()
        if self.state != 'approved':
            raise UserError(self.env._("Only an approved budget gets a new version."))
        latest = self.search([('previous_id', '=', self.id), ('state', 'in', ('draft', 'submitted'))], limit=1)
        if latest:
            raise UserError(self.env._("Version %s is already being prepared.", latest.version))
        new = self.copy({'version': self.version + 1, 'previous_id': self.id, 'prepared_by_id': self.env.user.id})
        return {'type': 'ir.actions.act_window', 'res_model': 'alfia.budget', 'res_id': new.id, 'view_mode': 'form'}

    # ------------------------------------------------------------------ control at the document stages
    @api.model
    def _lines_for(self, company, day, account, analytic_ids):
        """The approved budget line a cost belongs to: the line for its cost centre if there is one, else the account line."""
        lines = self.env['alfia.budget.line'].sudo().search([
            ('budget_id.state', '=', 'approved'), ('budget_id.company_id', '=', company.id),
            ('budget_id.date_from', '<=', day), ('budget_id.date_to', '>=', day), ('account_id', '=', account.id)])
        specific = lines.filtered(lambda l: l.analytic_account_id and l.analytic_account_id.id in analytic_ids)
        return specific[:1] or lines.filtered(lambda l: not l.analytic_account_id)[:1]

    @api.model
    def _check_document(self, record, items, stage):
        """`items`: [(account, analytic_ids, amount_company_currency, date)]. Warn or block per the budget's rule; a block
        passes only when a finance manager has recorded an override on the document."""
        if self.env.su:
            return
        totals = defaultdict(float)
        for account, analytic_ids, amount, day in items:
            if account and amount:
                line = self._lines_for(record.company_id, day, account, analytic_ids)
                if line:
                    totals[line] += amount
        warnings, blocks = [], []
        # Read the figures fresh: an earlier document in the same action may have changed them.
        self.env['alfia.budget.line'].browse([l.id for l in totals]).invalidate_recordset(['actual', 'committed'])
        for line, amount in totals.items():
            after = line.actual + line.committed + amount
            if after <= line.planned:
                continue
            text = self.env._("%(line)s: %(after)s after this %(stage)s against a budget of %(planned)s (%(currency)s)",
                              line=line.display_name, after=f"{after:,.2f}", stage=stage, planned=f"{line.planned:,.2f}",
                              currency=line.currency_id.name)
            (blocks if line.budget_id.control == 'block' else warnings if line.budget_id.control == 'warning' else []).append(text)
        if blocks and not record.sudo().alfia_budget_override_by_id:
            raise UserError(self.env._("Over budget: %s. A finance manager can record an override (Budget override) with "
                                       "a reason.", '; '.join(blocks)))
        if blocks:
            record.sudo().message_post(body=self.env._("Over budget, allowed by the override of %(user)s (%(reason)s): %(lines)s",
                                                       user=record.sudo().alfia_budget_override_by_id.name,
                                                       reason=record.sudo().alfia_budget_override_reason, lines='; '.join(blocks)))
        if warnings:
            record.sudo().message_post(body=self.env._("Budget warning: %s", '; '.join(warnings)))


class AlfiaBudgetLine(models.Model):
    _name = 'alfia.budget.line'
    _description = 'Budget line'
    _order = 'budget_id, account_id'

    budget_id = fields.Many2one('alfia.budget', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='budget_id.company_id', store=True)
    currency_id = fields.Many2one(related='budget_id.currency_id')
    account_id = fields.Many2one('account.account', required=True)
    analytic_account_id = fields.Many2one('account.analytic.account', string='Cost centre (horse / activity)')
    department_id = fields.Many2one('hr.department', string='Department')
    planned = fields.Monetary(required=True, currency_field='currency_id')
    committed = fields.Monetary(compute='_compute_figures', currency_field='currency_id',
                                help="Confirmed purchase orders in the period not yet billed.")
    actual = fields.Monetary(compute='_compute_figures', currency_field='currency_id', help="Posted entries in the period.")
    forecast = fields.Monetary(currency_field='currency_id',
                               help="Expected total for the period. Left empty, it is taken as actual plus committed.")
    forecast_total = fields.Monetary(string='Forecast', compute='_compute_figures', currency_field='currency_id')
    available = fields.Monetary(compute='_compute_figures', currency_field='currency_id')
    variance = fields.Monetary(string='Variance (planned - forecast)', compute='_compute_figures', currency_field='currency_id')

    _planned_positive = models.Constraint('CHECK(planned >= 0)', 'A planned amount cannot be negative.')

    @api.depends('account_id', 'analytic_account_id', 'department_id')
    def _compute_display_name(self):
        for line in self:
            parts = [line.account_id.display_name, line.analytic_account_id.name, line.department_id.name]
            line.display_name = ' / '.join(p for p in parts if p)

    def _matches(self, distribution):
        return not self.analytic_account_id or self.analytic_account_id.id in _analytic_ids(distribution)

    def _actual_lines(self):
        self.ensure_one()
        budget = self.budget_id
        lines = self.env['account.move.line'].sudo().search([
            ('parent_state', '=', 'posted'), ('company_id', '=', budget.company_id.id), ('account_id', '=', self.account_id.id),
            ('date', '>=', budget.date_from), ('date', '<=', budget.date_to)])
        return lines.filtered(lambda l: self._matches(l.analytic_distribution))

    def _commitment_lines(self):
        self.ensure_one()
        budget = self.budget_id
        po_lines = self.env['purchase.order.line'].sudo().search([
            ('order_id.state', '=', 'purchase'), ('company_id', '=', budget.company_id.id), ('display_type', '=', False),
            ('order_id.date_order', '>=', budget.date_from), ('order_id.date_order', '<=', fields.Datetime.to_datetime(budget.date_to).replace(hour=23, minute=59, second=59))])
        return po_lines.filtered(lambda l: AlfiaPurchaseOrder._alfia_line_account(l) == self.account_id
                                 and self._matches(l.analytic_distribution) and l.qty_invoiced < l.product_qty)

    @api.depends('planned', 'forecast', 'account_id', 'analytic_account_id', 'budget_id.date_from', 'budget_id.date_to')
    def _compute_figures(self):
        for line in self:
            if not (line.account_id and line.budget_id.date_from and line.budget_id.date_to):
                line.committed = line.actual = line.forecast_total = line.available = line.variance = 0.0
                continue
            line.actual = sum(line._actual_lines().mapped('balance'))
            line.committed = sum(AlfiaPurchaseOrder._alfia_open_amount(l) for l in line._commitment_lines())
            line.forecast_total = line.forecast or (line.actual + line.committed)
            line.available = line.planned - line.actual - line.committed
            line.variance = line.planned - line.forecast_total

    def action_view_actuals(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': self.env._('Actuals: %s', self.display_name), 'res_model': 'account.move.line',
                'view_mode': 'list,form', 'domain': [('id', 'in', self._actual_lines().ids)]}

    def action_view_commitments(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': self.env._('Commitments: %s', self.display_name),
                'res_model': 'purchase.order.line', 'view_mode': 'list,form', 'domain': [('id', 'in', self._commitment_lines().ids)]}


class AlfiaBudgetOverrideMixin(models.AbstractModel):
    _name = 'alfia.budget.override.mixin'
    _description = 'Budget override on a document'

    alfia_budget_override_by_id = fields.Many2one('res.users', string='Budget override by', readonly=True, copy=False)
    alfia_budget_override_reason = fields.Char(string='Budget override reason', readonly=True, copy=False)

    def action_alfia_budget_override(self):
        if not _is(self.env, (MANAGER,)):
            raise AccessError(self.env._("Only finance managers override a budget block."))
        return {'type': 'ir.actions.act_window', 'res_model': 'alfia.budget.override', 'view_mode': 'form', 'target': 'new',
                'name': self.env._('Budget override'), 'context': {'default_res_model': self._name, 'default_res_id': self.id}}


class AlfiaBudgetOverride(models.TransientModel):
    _name = 'alfia.budget.override'
    _description = 'Record a budget override'

    res_model = fields.Char(required=True)
    res_id = fields.Integer(required=True)
    reason = fields.Char(required=True)

    def action_confirm(self):
        self.ensure_one()
        if not _is(self.env, (MANAGER,)):
            raise AccessError(self.env._("Only finance managers override a budget block."))
        if self.res_model not in ('purchase.order', 'account.move'):
            raise UserError(self.env._("A budget override applies to purchase orders and bills."))
        record = self.env[self.res_model].browse(self.res_id).exists()
        record.check_access('read')
        if record.create_uid == self.env.user:
            raise UserError(self.env._("You created this document, so another finance manager must override its budget."))
        record.sudo().write({'alfia_budget_override_by_id': self.env.user.id, 'alfia_budget_override_reason': self.reason})
        record.sudo().message_post(body=self.env._("Budget override recorded by %(user)s: %(reason)s", user=self.env.user.name,
                                                   reason=self.reason))
        return {'type': 'ir.actions.act_window_close'}


class AlfiaPurchaseOrder(models.Model):
    _name = 'purchase.order'
    _inherit = ['purchase.order', 'alfia.budget.override.mixin']

    @staticmethod
    def _alfia_line_account(line):
        accounts = line.product_id.product_tmpl_id.get_product_accounts(fiscal_pos=line.order_id.fiscal_position_id)
        return accounts.get('expense') or line.env['account.account']

    @staticmethod
    def _alfia_open_amount(line):
        order = line.order_id
        open_qty = max(line.product_qty - line.qty_invoiced, 0.0)
        amount = line.price_unit * open_qty * (1 - (line.discount or 0.0) / 100.0)
        return order.currency_id._convert(amount, order.company_id.currency_id, order.company_id,
                                          order.date_order or fields.Date.today())

    def button_confirm(self):
        for order in self.filtered(lambda o: o.state in ('draft', 'sent')):
            items = [(self._alfia_line_account(l), _analytic_ids(l.analytic_distribution), self._alfia_open_amount(l),
                      fields.Date.to_date(order.date_order) or fields.Date.today())
                     for l in order.order_line.filtered(lambda l: not l.display_type)]
            self.env['alfia.budget']._check_document(order, items, self.env._("order"))
        return super().button_confirm()


class AccountMove(models.Model):
    _name = 'account.move'
    _inherit = ['account.move', 'alfia.budget.override.mixin']

    def _post(self, soft=True):
        for move in self.filtered(lambda m: m.move_type == 'in_invoice' and m.state == 'draft'):
            # Bills from a purchase order were checked when the order was confirmed (as a commitment).
            lines = move.invoice_line_ids.filtered(lambda l: l.display_type == 'product' and not l.purchase_line_id)
            items = [(l.account_id, _analytic_ids(l.analytic_distribution), abs(l.balance), move.invoice_date or move.date or fields.Date.today())
                     for l in lines]
            self.env['alfia.budget']._check_document(move, items, self.env._("bill"))
        return super()._post(soft=soft)
