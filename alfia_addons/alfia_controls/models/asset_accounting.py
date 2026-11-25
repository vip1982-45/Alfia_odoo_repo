import calendar

from dateutil.relativedelta import relativedelta

from odoo import Command, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from odoo.addons.alfia_security.flow import workflow

# Odoo 20's finance-manager group does not include the accountant group, so both are named.
ACCOUNTING = 'account.group_account_user,account.group_account_manager'
MANAGER = 'account.group_account_manager'
LOCKED_WHEN_RUNNING = ('cost', 'salvage_value', 'useful_life_months', 'depreciation_start', 'asset_account_id',
                       'accumulated_account_id', 'expense_account_id', 'asset_journal_id')


def _month_end(day):
    return day.replace(day=calendar.monthrange(day.year, day.month)[1])


class AlfiaAsset(models.Model):
    """Fixed-asset accounting on the farm's asset register: straight-line depreciation by month, posted to the ledger,
    and disposal (sale or write-off) with its gain or loss."""
    _inherit = 'alfia.asset'

    state = fields.Selection(selection_add=[('disposed', 'Disposed (sold / written off)')], ondelete={'disposed': 'set default'})
    accounting_state = fields.Selection(
        [('none', 'Not capitalised'), ('running', 'Depreciating'), ('done', 'Fully depreciated'), ('disposed', 'Disposed')],
        default='none', required=True, tracking=True, groups=ACCOUNTING, copy=False)
    cost = fields.Monetary(string='Acquisition cost', currency_field='company_currency_id', groups=ACCOUNTING, tracking=True)
    salvage_value = fields.Monetary(string='Residual value', currency_field='company_currency_id', groups=ACCOUNTING,
                                    help="What the asset is expected to be worth at the end of its useful life; not depreciated.")
    useful_life_months = fields.Integer(string='Useful life (months)', groups=ACCOUNTING, default=60)
    depreciation_start = fields.Date(groups=ACCOUNTING, help="Depreciation runs from the month of this date.")
    asset_account_id = fields.Many2one('account.account', string='Fixed asset account', groups=ACCOUNTING,
                                       domain="[('account_type', 'in', ('asset_fixed', 'asset_non_current'))]")
    accumulated_account_id = fields.Many2one('account.account', string='Accumulated depreciation account', groups=ACCOUNTING,
                                             domain="[('account_type', 'in', ('asset_fixed', 'asset_non_current'))]")
    expense_account_id = fields.Many2one('account.account', string='Depreciation expense account', groups=ACCOUNTING,
                                         domain="[('account_type', 'in', ('expense_depreciation', 'expense'))]")
    asset_journal_id = fields.Many2one('account.journal', string='Journal', groups=ACCOUNTING, domain="[('type', '=', 'general')]")
    company_currency_id = fields.Many2one(related='company_id.currency_id')
    depreciation_line_ids = fields.One2many('alfia.asset.depreciation', 'asset_id', groups=ACCOUNTING)
    accumulated_depreciation = fields.Monetary(compute='_compute_book_value', currency_field='company_currency_id', groups=ACCOUNTING)
    book_value = fields.Monetary(compute='_compute_book_value', currency_field='company_currency_id', groups=ACCOUNTING)
    disposal_move_id = fields.Many2one('account.move', readonly=True, copy=False, groups=ACCOUNTING)
    disposed_by_id = fields.Many2one('res.users', readonly=True, copy=False, groups=ACCOUNTING)

    _alfia_guarded = ('state', 'maintenance_by_id', 'released_by_id', 'accounting_state', 'disposal_move_id', 'disposed_by_id')

    @api.depends('cost', 'depreciation_line_ids.state', 'depreciation_line_ids.amount')
    def _compute_book_value(self):
        for asset in self:
            posted = sum(asset.depreciation_line_ids.filtered(lambda l: l.state == 'posted').mapped('amount'))
            asset.accumulated_depreciation = posted
            asset.book_value = (asset.cost or 0.0) - posted

    @api.constrains('cost', 'salvage_value', 'useful_life_months')
    def _check_amounts(self):
        for asset in self.sudo():
            if asset.cost < 0 or asset.salvage_value < 0:
                raise ValidationError(self.env._("Cost and residual value cannot be negative."))
            if asset.salvage_value > asset.cost:
                raise ValidationError(self.env._("The residual value cannot be more than the cost."))
            if asset.useful_life_months < 0 or asset.useful_life_months > 600:
                raise ValidationError(self.env._("The useful life must be between 1 and 600 months."))

    def write(self, vals):
        if set(LOCKED_WHEN_RUNNING) & set(vals) and not self.env.su:
            if any(a.sudo().accounting_state != 'none' for a in self):
                raise UserError(self.env._("An asset's cost, life and accounts cannot change once it is capitalised. Dispose "
                                           "of it and capitalise a corrected one, so the ledger keeps the history."))
        return super().write(vals)

    # ------------------------------------------------------------------ schedule
    def _check_accounting(self, manager=False):
        groups = [MANAGER] if manager else ACCOUNTING.split(',')
        if not (self.env.su or any(self.env.user.has_group(g) for g in groups)):
            raise AccessError(self.env._("Only finance managers dispose of assets.") if manager
                              else self.env._("Only accounting staff capitalise and depreciate assets."))

    def _schedule(self):
        """[(month_end, amount)] for the whole life; the last month takes the rounding difference."""
        self.ensure_one()
        currency = self.company_currency_id
        months = self.useful_life_months
        base = self.cost - self.salvage_value
        monthly = currency.round(base / months)
        start = _month_end(self.depreciation_start)
        lines = []
        for index in range(months):
            amount = monthly if index < months - 1 else currency.round(base - monthly * (months - 1))
            lines.append((_month_end(start + relativedelta(months=index)), amount))
        return lines

    def action_capitalise(self):
        self._check_accounting()
        for asset in self.sudo():
            if asset.accounting_state != 'none':
                raise UserError(self.env._("%s is already capitalised.", asset.name))
            missing = [label for label, value in ((self.env._('cost'), asset.cost), (self.env._('useful life'), asset.useful_life_months),
                                                  (self.env._('start date'), asset.depreciation_start),
                                                  (self.env._('accounts'), asset.asset_account_id and asset.accumulated_account_id and asset.expense_account_id),
                                                  (self.env._('journal'), asset.asset_journal_id)) if not value]
            if missing:
                raise UserError(self.env._("Fill in %(what)s before capitalising %(asset)s.", what=', '.join(missing), asset=asset.name))
            self.env['alfia.asset.depreciation'].sudo().create(
                [{'asset_id': asset.id, 'date': day, 'amount': amount} for day, amount in asset._schedule()])
            with workflow():
                asset.write({'accounting_state': 'running'})
            asset.message_post(body=self.env._("Capitalised by %(user)s: %(cost)s over %(months)s months.",
                                               user=self.env.user.name, cost=asset.cost, months=asset.useful_life_months))

    def action_post_depreciation(self, up_to=None):
        """Post every planned month up to `up_to` (default today)."""
        self._check_accounting()
        up_to = up_to or fields.Date.context_today(self)
        lines = self.sudo().depreciation_line_ids.filtered(lambda l: l.state == 'planned' and l.date <= up_to)
        lines.sorted('date')._post()
        for asset in self.sudo().filtered(lambda a: a.accounting_state == 'running'
                                         and not a.depreciation_line_ids.filtered(lambda l: l.state == 'planned')):
            with workflow():
                asset.write({'accounting_state': 'done'})
        return lines

    @api.model
    def _cron_post_depreciation(self):
        self.search([('accounting_state', '=', 'running')]).action_post_depreciation()

    def action_retire(self):
        # A capitalised asset leaves the books through a disposal, which also retires it; retiring it on its own would
        # leave its cost and depreciation in the ledger for an asset no longer in use.
        if not self.env.su and any(a.sudo().accounting_state in ('running', 'done', 'disposed') for a in self):
            raise UserError(self.env._("This asset is capitalised. A finance manager disposes of it (Dispose), which also "
                                       "retires it."))
        return super().action_retire()

    def action_open_disposal(self):
        self._check_accounting(manager=True)
        return {'type': 'ir.actions.act_window', 'res_model': 'alfia.asset.disposal', 'view_mode': 'form', 'target': 'new',
                'name': self.env._('Dispose of asset'), 'context': {'default_asset_id': self.id}}


class AlfiaAssetDepreciation(models.Model):
    _name = 'alfia.asset.depreciation'
    _description = 'Asset depreciation month'
    _order = 'asset_id, date'

    asset_id = fields.Many2one('alfia.asset', required=True, ondelete='cascade', index=True)
    date = fields.Date(required=True)
    amount = fields.Monetary(currency_field='currency_id', required=True)
    currency_id = fields.Many2one(related='asset_id.company_currency_id')
    state = fields.Selection([('planned', 'Planned'), ('posted', 'Posted'), ('cancelled', 'Cancelled')], default='planned', required=True)
    move_id = fields.Many2one('account.move', readonly=True)

    def _post(self):
        Move = self.env['account.move'].sudo()
        for line in self:
            asset = line.asset_id.sudo()
            label = self.env._("Depreciation %(asset)s %(month)s", asset=asset.name, month=line.date.strftime('%Y-%m'))
            move = Move.create({
                'move_type': 'entry', 'journal_id': asset.asset_journal_id.id, 'date': line.date, 'ref': label,
                'company_id': asset.company_id.id, 'alfia_asset_id': asset.id,
                'line_ids': [Command.create({'name': label, 'account_id': asset.expense_account_id.id, 'debit': line.amount}),
                             Command.create({'name': label, 'account_id': asset.accumulated_account_id.id, 'credit': line.amount})]})
            move._post()
            line.write({'state': 'posted', 'move_id': move.id})


class AccountMove(models.Model):
    _inherit = 'account.move'

    alfia_asset_id = fields.Many2one('alfia.asset', string='Asset', readonly=True, index='btree_not_null', copy=False)

    def _alfia_is_manual(self):
        # Depreciation and disposal entries are generated from the asset register, not typed in.
        return not self.alfia_asset_id and super()._alfia_is_manual()


class AlfiaAssetDisposal(models.TransientModel):
    """Sell or write off an asset: depreciation is brought up to the disposal date (part of the last month), the cost and
    accumulated depreciation leave the books, and the difference to any proceeds is the gain or loss."""
    _name = 'alfia.asset.disposal'
    _description = 'Dispose of an asset'

    asset_id = fields.Many2one('alfia.asset', required=True, readonly=True)
    date = fields.Date(required=True, default=fields.Date.context_today)
    reason = fields.Char(required=True)
    proceeds = fields.Monetary(string='Sale proceeds', currency_field='currency_id', help="0 for a write-off.")
    proceeds_account_id = fields.Many2one('account.account', string='Proceeds to',
                                          help="Where the sale price is due or was received, for example a receivable or the bank.")
    gain_loss_account_id = fields.Many2one('account.account', string='Gain / loss account', required=True)
    currency_id = fields.Many2one(related='asset_id.company_currency_id')

    def action_confirm(self):
        self.ensure_one()
        self.asset_id._check_accounting(manager=True)
        asset = self.asset_id.sudo()
        if asset.accounting_state not in ('running', 'done'):
            raise UserError(self.env._("Only a capitalised asset that is not yet disposed of can be disposed of."))
        if self.proceeds and not self.proceeds_account_id:
            raise UserError(self.env._("Choose where the sale proceeds go."))
        if self.proceeds < 0:
            raise UserError(self.env._("Proceeds cannot be negative."))
        if self.date < asset.depreciation_start:
            raise UserError(self.env._("The disposal date cannot be before depreciation started."))
        currency = asset.company_currency_id
        planned = asset.depreciation_line_ids.filtered(lambda l: l.state == 'planned').sorted('date')
        # Whole months before the disposal month, then the disposal month pro rata by day.
        planned.filtered(lambda l: l.date < _month_end(self.date).replace(day=1))._post()
        current = planned.filtered(lambda l: l.date == _month_end(self.date) and l.state == 'planned')
        if current:
            days = calendar.monthrange(self.date.year, self.date.month)[1]
            current.amount = currency.round(current.amount * self.date.day / days)
            if not currency.is_zero(current.amount):
                current.date = self.date
                current._post()
            else:
                current.state = 'cancelled'
        asset.depreciation_line_ids.filtered(lambda l: l.state == 'planned').write({'state': 'cancelled'})
        accumulated = sum(asset.depreciation_line_ids.filtered(lambda l: l.state == 'posted').mapped('amount'))
        book = currency.round(asset.cost - accumulated)
        result = currency.round(self.proceeds - book)        # positive = gain
        label = self.env._("Disposal of %(asset)s: %(reason)s", asset=asset.name, reason=self.reason)
        lines = [Command.create({'name': label, 'account_id': asset.asset_account_id.id, 'credit': asset.cost})]
        if accumulated:
            lines.append(Command.create({'name': label, 'account_id': asset.accumulated_account_id.id, 'debit': accumulated}))
        if self.proceeds:
            lines.append(Command.create({'name': label, 'account_id': self.proceeds_account_id.id, 'debit': self.proceeds}))
        if not currency.is_zero(result):
            lines.append(Command.create({'name': label, 'account_id': self.gain_loss_account_id.id,
                                         'credit' if result > 0 else 'debit': abs(result)}))
        move = self.env['account.move'].sudo().create({
            'move_type': 'entry', 'journal_id': asset.asset_journal_id.id, 'date': self.date, 'ref': label,
            'company_id': asset.company_id.id, 'alfia_asset_id': asset.id, 'line_ids': lines})
        move._post()
        with workflow():
            asset.write({'accounting_state': 'disposed', 'disposal_move_id': move.id, 'disposed_by_id': self.env.user.id,
                         'state': 'disposed'})
        asset.message_post(body=self.env._(
            "Disposed of by %(user)s on %(date)s: %(reason)s. Book value %(book)s, proceeds %(proceeds)s, %(kind)s %(result)s.",
            user=self.env.user.name, date=self.date, reason=self.reason, book=book, proceeds=self.proceeds,
            kind=self.env._('gain') if result >= 0 else self.env._('loss'), result=abs(result)))
        return {'type': 'ir.actions.act_window_close'}

