from odoo import Command, api, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.alfia_security.flow import workflow

FINANCE_GROUPS = 'account.group_account_user,account.group_account_manager'
MANAGER = 'account.group_account_manager'

CLASSES = [('breeding', 'Breeding stock'), ('performance', 'Racing / performance'), ('young', 'Young stock'),
           ('for_sale', 'Held for sale'), ('retired', 'Retired / companion'), ('external', 'Owned by others (not ours)')]
EVENTS = [('acquisition', 'Acquisition'), ('birth', 'Birth'), ('classification', 'Classification change'),
          ('valuation', 'Valuation'), ('disposal', 'Disposal')]


def _finance(env, manager=False):
    if env.su:
        return True
    groups = [MANAGER] if manager else FINANCE_GROUPS.split(',')
    return any(env.user.has_group(g) for g in groups)


class ResCompany(models.Model):
    _inherit = 'res.company'

    alfia_livestock_account_id = fields.Many2one('account.account', string='Livestock (biological assets) account')
    alfia_livestock_gain_account_id = fields.Many2one('account.account', string='Livestock revaluation gain account')
    alfia_livestock_loss_account_id = fields.Many2one('account.account', string='Livestock revaluation loss account')
    alfia_livestock_journal_id = fields.Many2one('account.journal', string='Livestock journal', domain="[('type', '=', 'general')]")


class AlfiaHorse(models.Model):
    """The financial side of each horse: its livestock class, how it came to the farm, its carrying value from the last
    approved valuation, and the history of those events, each with its evidence."""
    _inherit = 'alfia.horse'

    alfia_livestock_class = fields.Selection(CLASSES, string='Livestock class', tracking=True, groups=FINANCE_GROUPS)
    alfia_acquisition_kind = fields.Selection([('purchase', 'Purchased'), ('birth', 'Born on the farm'), ('gift', 'Received as a gift'),
                                               ('other', 'Other')], string='Came to the farm by', groups=FINANCE_GROUPS)
    alfia_acquisition_date = fields.Date(string='Acquired / born on', groups=FINANCE_GROUPS)
    alfia_acquisition_cost = fields.Monetary(string='Acquisition cost', currency_field='alfia_company_currency_id', groups=FINANCE_GROUPS)
    alfia_company_currency_id = fields.Many2one(related='company_id.currency_id')
    alfia_carrying_value = fields.Monetary(string='Carrying value', compute='_compute_alfia_carrying_value',
                                           currency_field='alfia_company_currency_id', groups=FINANCE_GROUPS)
    alfia_last_valuation_id = fields.Many2one('alfia.livestock.valuation', compute='_compute_alfia_carrying_value', groups=FINANCE_GROUPS)
    alfia_livestock_event_ids = fields.One2many('alfia.livestock.event', 'horse_id', groups=FINANCE_GROUPS)
    alfia_valuation_ids = fields.One2many('alfia.livestock.valuation', 'horse_id', groups=FINANCE_GROUPS)

    @api.depends('alfia_valuation_ids.state', 'alfia_valuation_ids.value', 'alfia_valuation_ids.effective_date')
    def _compute_alfia_carrying_value(self):
        Valuation = self.env['alfia.livestock.valuation'].sudo()
        for horse in self:
            last = Valuation.search([('horse_id', '=', horse.id), ('state', '=', 'posted')], order='effective_date desc, id desc', limit=1)
            horse.alfia_last_valuation_id = last
            horse.alfia_carrying_value = last.value if last else 0.0

    def _alfia_log_event(self, kind, date=None, amount=0.0, note=''):
        Event = self.env['alfia.livestock.event'].sudo()
        for horse in self:
            Event.create({'horse_id': horse.id, 'kind': kind, 'date': date or fields.Date.context_today(self), 'amount': amount,
                          'note': note, 'user_id': self.env.user.id})

    @api.model_create_multi
    def create(self, vals_list):
        horses = super().create(vals_list)
        for horse in horses.sudo():
            # A foal is born on the farm; anything else is recorded when finance classifies it.
            today = fields.Date.context_today(self)
            if horse.dam_id and horse.birthdate and (today - horse.birthdate).days <= 366:
                with workflow():
                    horse.write({'alfia_acquisition_kind': 'birth', 'alfia_acquisition_date': horse.birthdate,
                                 'alfia_livestock_class': 'young'})
                horse._alfia_log_event('birth', horse.birthdate, note=self.env._("Foal of %s", horse.dam_id.name))
        return horses

    def write(self, vals):
        old_class = {h.id: h.sudo().alfia_livestock_class for h in self} if 'alfia_livestock_class' in vals else {}
        leaving = self.filtered(lambda h: h.status == 'active') if vals.get('status') in ('sold', 'gifted', 'dead') else self.browse()
        res = super().write(vals)
        for horse in self.filtered(lambda h: h.id in old_class and old_class[h.id] != h.sudo().alfia_livestock_class):
            labels = dict(self._fields['alfia_livestock_class']._description_selection(self.env))
            horse._alfia_log_event('classification', note=self.env._("%(old)s → %(new)s", old=labels.get(old_class[horse.id], '—'),
                                                                     new=labels.get(horse.sudo().alfia_livestock_class, '—')))
        for horse in leaving.sudo():
            horse._alfia_log_event('disposal', note=dict(self._fields['status']._description_selection(self.env)).get(vals['status']))
            if horse.alfia_carrying_value:
                # The carrying value leaves the books when finance approves this draft (method: disposal, value 0).
                valuation = self.env['alfia.livestock.valuation'].sudo().create({
                    'horse_id': horse.id, 'method': 'disposal', 'value': 0.0, 'effective_date': fields.Date.context_today(self),
                    'notes': self.env._("Drafted when %(horse)s left the farm (%(why)s).", horse=horse.name, why=vals['status'])})
                valuation.message_post(body=self.env._("Drafted automatically; finance chooses the counterpart account (for example "
                                                       "cost of horses sold) and approves it."))
        return res


class AlfiaLivestockEvent(models.Model):
    _name = 'alfia.livestock.event'
    _description = 'Livestock register event'
    _inherit = ['mail.thread']
    _order = 'date desc, id desc'

    horse_id = fields.Many2one('alfia.horse', required=True, ondelete='cascade', index=True)
    kind = fields.Selection(EVENTS, required=True)
    date = fields.Date(required=True)
    amount = fields.Monetary(currency_field='currency_id')
    currency_id = fields.Many2one(related='horse_id.company_id.currency_id')
    note = fields.Char()
    user_id = fields.Many2one('res.users', string='Recorded by', readonly=True)
    valuation_id = fields.Many2one('alfia.livestock.valuation', readonly=True)


class AlfiaLivestockValuation(models.Model):
    """A valuation of one horse under a finance-approved method, with evidence and an effective date. Prepared by one
    person and approved by a finance manager who did not prepare it; approval posts the adjustment journal for the
    difference from the carrying value."""
    _name = 'alfia.livestock.valuation'
    _description = 'Livestock valuation'
    _inherit = ['mail.thread', 'alfia.guarded.mixin']
    _order = 'effective_date desc, id desc'
    _alfia_guarded = ('state', 'prepared_by_id', 'approved_by_id', 'approved_date', 'previous_value', 'move_id')
    _alfia_guarded_create = {'prepared_by_id': '*'}

    name = fields.Char(compute='_compute_name', store=True)
    horse_id = fields.Many2one('alfia.horse', required=True, index=True)
    company_id = fields.Many2one(related='horse_id.company_id', store=True)
    currency_id = fields.Many2one(related='company_id.currency_id')
    method = fields.Selection([('cost', 'Cost'), ('cost_impairment', 'Cost less impairment'),
                               ('fair_value', 'Fair value less costs to sell'), ('disposal', 'Disposal (derecognition)')],
                              required=True, default='cost', tracking=True)
    effective_date = fields.Date(required=True, default=fields.Date.context_today, tracking=True)
    value = fields.Monetary(required=True, currency_field='currency_id', tracking=True)
    previous_value = fields.Monetary(currency_field='currency_id', readonly=True)
    difference = fields.Monetary(compute='_compute_difference', currency_field='currency_id')
    counterpart_account_id = fields.Many2one('account.account', string='Counterpart account',
                                             help="Leave empty to use the company's revaluation gain or loss account. For a "
                                                  "first recognition at cost, choose the account the purchase was booked to; "
                                                  "for a disposal, the cost of horses sold.")
    notes = fields.Text(string='Basis and evidence', help="How the value was reached (valuer, comparable sales, insurance). "
                                                          "Attach the evidence. Required to submit.")
    state = fields.Selection([('draft', 'Draft'), ('submitted', 'Waiting for approval'), ('posted', 'Approved and posted'),
                              ('rejected', 'Rejected')], default='draft', required=True, tracking=True)
    prepared_by_id = fields.Many2one('res.users', default=lambda self: self.env.user, readonly=True)
    approved_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    approved_date = fields.Datetime(readonly=True, copy=False)
    move_id = fields.Many2one('account.move', string='Adjustment entry', readonly=True, copy=False)

    _value_positive = models.Constraint('CHECK(value >= 0)', 'A value cannot be negative.')

    @api.depends('horse_id.name', 'effective_date', 'method')
    def _compute_name(self):
        for rec in self:
            rec.name = f"{rec.horse_id.name or ''} {rec.effective_date or ''}"

    @api.depends('value', 'horse_id', 'state', 'previous_value')
    def _compute_difference(self):
        for rec in self:
            base = rec.previous_value if rec.state == 'posted' else rec.horse_id.sudo().alfia_carrying_value
            rec.difference = rec.value - base

    def action_open(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': self.id, 'view_mode': 'form'}

    def write(self, vals):
        if {'horse_id', 'method', 'effective_date', 'value', 'counterpart_account_id', 'notes'} & set(vals) and not self.env.su:
            if any(r.state in ('submitted', 'posted') for r in self):
                raise UserError(self.env._("A submitted or posted valuation cannot be changed. Record a new valuation."))
        return super().write(vals)

    def unlink(self):
        if any(r.state == 'posted' for r in self) and not self.env.su:
            raise UserError(self.env._("A posted valuation is part of the review history and cannot be deleted."))
        return super().unlink()

    def action_submit(self):
        if not _finance(self.env):
            raise AccessError(self.env._("Only finance staff prepare livestock valuations."))
        for rec in self:
            if rec.state not in ('draft', 'rejected'):
                raise UserError(self.env._("%s has already been submitted.", rec.name))
            if not rec.notes:
                raise UserError(self.env._("Describe the basis of the valuation (and attach the evidence) before submitting."))
            if not self.env['ir.attachment'].sudo().search_count([('res_model', '=', rec._name), ('res_id', '=', rec.id)]):
                raise UserError(self.env._("Attach the evidence (valuer's report, invoice, comparable sales) before submitting."))
            if rec.method == 'disposal' and not rec.counterpart_account_id:
                raise UserError(self.env._("Choose the counterpart account of the disposal (for example cost of horses sold) "
                                           "before submitting."))
            with workflow():
                rec.write({'state': 'submitted', 'prepared_by_id': self.env.user.id})

    def action_reject(self):
        if not _finance(self.env, manager=True):
            raise AccessError(self.env._("Only finance managers approve or reject livestock valuations."))
        for rec in self.filtered(lambda r: r.state == 'submitted'):
            with workflow():
                rec.write({'state': 'rejected'})
            rec.message_post(body=self.env._("Rejected by %s.", self.env.user.name))

    def action_approve(self):
        if not _finance(self.env, manager=True):
            raise AccessError(self.env._("Only finance managers approve or reject livestock valuations."))
        for rec in self:
            if rec.state != 'submitted':
                raise UserError(self.env._("Only a submitted valuation can be approved."))
            if rec.prepared_by_id == self.env.user and not self.env.su:
                raise UserError(self.env._("You prepared this valuation, so another finance manager must approve it."))
            later = self.sudo().search([('horse_id', '=', rec.horse_id.id), ('state', '=', 'posted'),
                                        ('effective_date', '>', rec.effective_date)], limit=1)
            if later:
                raise UserError(self.env._("A later valuation (%s) is already posted. Record a new one dated after it.", later.effective_date))
            rec._post_adjustment()

    def _post_adjustment(self):
        self.ensure_one()
        company = self.company_id
        currency = company.currency_id
        previous = self.horse_id.sudo().alfia_carrying_value
        difference = currency.round(self.value - previous)
        move = self.env['account.move']
        if not currency.is_zero(difference):
            asset = company.alfia_livestock_account_id
            counterpart = self.counterpart_account_id or (company.alfia_livestock_gain_account_id if difference > 0
                                                          else company.alfia_livestock_loss_account_id)
            if not (asset and counterpart and company.alfia_livestock_journal_id):
                raise UserError(self.env._("Set the livestock account, the revaluation gain and loss accounts and the livestock "
                                           "journal on the company (Settings › Companies), or choose a counterpart account."))
            label = self.env._("Livestock valuation %(horse)s %(date)s (%(method)s)", horse=self.horse_id.name, date=self.effective_date,
                               method=dict(self._fields['method']._description_selection(self.env))[self.method])
            analytic = self.horse_id.sudo().analytic_account_id
            distribution = {str(analytic.id): 100} if analytic else False
            move = self.env['account.move'].sudo().create({
                'move_type': 'entry', 'journal_id': company.alfia_livestock_journal_id.id, 'date': self.effective_date,
                'ref': label, 'company_id': company.id, 'alfia_livestock_valuation_id': self.id,
                'line_ids': [Command.create({'name': label, 'account_id': asset.id, 'debit': max(difference, 0), 'credit': max(-difference, 0)}),
                             Command.create({'name': label, 'account_id': counterpart.id, 'debit': max(-difference, 0),
                                             'credit': max(difference, 0), 'analytic_distribution': distribution})]})
            move._post()
        with workflow():
            self.write({'state': 'posted', 'approved_by_id': self.env.user.id, 'approved_date': fields.Datetime.now(),
                        'previous_value': previous, 'move_id': move.id})
        self.env['alfia.livestock.event'].sudo().create({
            'horse_id': self.horse_id.id, 'kind': 'disposal' if self.method == 'disposal' else 'valuation', 'date': self.effective_date,
            'amount': self.value, 'valuation_id': self.id, 'user_id': self.env.user.id,
            'note': self.env._("%(prev)s → %(new)s, approved by %(user)s", prev=f"{previous:,.2f}", new=f"{self.value:,.2f}",
                               user=self.env.user.name)})
        self.message_post(body=self.env._("Approved by %(user)s. Carrying value %(prev)s → %(new)s.", user=self.env.user.name,
                                          prev=f"{previous:,.2f}", new=f"{self.value:,.2f}"))


class AccountMove(models.Model):
    _inherit = 'account.move'

    alfia_livestock_valuation_id = fields.Many2one('alfia.livestock.valuation', string='Livestock valuation', readonly=True,
                                                   copy=False, index='btree_not_null')

    def _alfia_is_manual(self):
        # Valuation entries carry their own two-person approval; they are not typed-in manual entries.
        return not self.alfia_livestock_valuation_id and super()._alfia_is_manual()


class AlfiaLivestockClassify(models.TransientModel):
    """Finance records a horse's livestock class and how it came to the farm without needing to edit the horse."""
    _name = 'alfia.livestock.classify'
    _description = 'Classify a horse in the livestock register'

    horse_id = fields.Many2one('alfia.horse', required=True)
    livestock_class = fields.Selection(CLASSES, required=True)
    acquisition_kind = fields.Selection([('purchase', 'Purchased'), ('birth', 'Born on the farm'), ('gift', 'Received as a gift'),
                                         ('other', 'Other')], string='Came to the farm by')
    acquisition_date = fields.Date(string='Acquired / born on')
    acquisition_cost = fields.Monetary(currency_field='currency_id')
    currency_id = fields.Many2one(related='horse_id.company_id.currency_id')
    note = fields.Char(string='Reason / reference')

    @api.model
    def default_get(self, fields_list):
        vals = super().default_get(fields_list)
        horse = self.env['alfia.horse'].browse(vals.get('horse_id') or self.env.context.get('active_id')).sudo().exists()
        if horse:
            vals.update(horse_id=horse.id, livestock_class=horse.alfia_livestock_class, acquisition_kind=horse.alfia_acquisition_kind,
                        acquisition_date=horse.alfia_acquisition_date, acquisition_cost=horse.alfia_acquisition_cost)
        return vals

    def action_confirm(self):
        self.ensure_one()
        if not _finance(self.env):
            raise AccessError(self.env._("Only finance staff classify horses in the livestock register."))
        self.horse_id.check_access('read')
        horse = self.horse_id.sudo()
        first_acquisition = not horse.alfia_acquisition_kind and self.acquisition_kind
        with workflow():
            horse.write({'alfia_livestock_class': self.livestock_class, 'alfia_acquisition_kind': self.acquisition_kind,
                         'alfia_acquisition_date': self.acquisition_date, 'alfia_acquisition_cost': self.acquisition_cost})
        if first_acquisition and self.acquisition_kind != 'birth':
            horse._alfia_log_event('acquisition', self.acquisition_date, self.acquisition_cost, self.note or '')
        if self.note:
            horse.message_post(body=self.env._("Livestock register updated by %(user)s: %(note)s", user=self.env.user.name, note=self.note))
        return {'type': 'ir.actions.act_window_close'}


class AlfiaHorseValuationAction(models.Model):
    _inherit = 'alfia.horse'

    def action_alfia_new_valuation(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': 'alfia.livestock.valuation', 'view_mode': 'form',
                'context': {'default_horse_id': self.id}}
