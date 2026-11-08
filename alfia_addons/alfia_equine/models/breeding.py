from dateutil.relativedelta import relativedelta

from odoo import Command, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

PARAMETERS = ('check_offsets', 'gestation_days', 'prep_days', 'vaccine_months', 'season_start_month', 'season_start_day',
              'season_end_month', 'season_end_day', 'teasing_pregnant_until_day')


def _require_group(env, xmlid, what):
    if not env.su and not env.user.has_group(xmlid):
        raise AccessError(env._("Only %s can do this.", what))


class AlfiaBreedingRule(models.Model):
    """A versioned, vet-approved set of breeding schedule parameters. Nothing is scheduled from an unapproved set."""
    _name = 'alfia.breeding.rule'
    _description = 'Breeding schedule rule set'
    _inherit = ['mail.thread']
    _order = 'version desc, id desc'

    name = fields.Char(required=True, tracking=True)
    version = fields.Integer(default=1, required=True)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company, required=True)
    state = fields.Selection([('draft', 'Draft'), ('approved', 'Approved'), ('retired', 'Retired')], default='draft', required=True, tracking=True)
    check_offsets = fields.Char(string='Pregnancy check days after ovulation', default='14,18,21,28,35,45,60,75,90,120', required=True)
    gestation_days = fields.Integer(string='Expected foaling: days after ovulation', default=340, required=True)
    prep_days = fields.Integer(string='Foaling preparation reminder: days before', default=30, required=True)
    vaccine_months = fields.Char(string='Pregnancy vaccination months', default='3,5,7,9')
    season_start_month = fields.Integer(default=10)
    season_start_day = fields.Integer(default=1)
    season_end_month = fields.Integer(default=6)
    season_end_day = fields.Integer(default=15)
    teasing_pregnant_until_day = fields.Integer(string='Tease pregnant mares until day', default=120)
    day_zero_notes = fields.Text(string='Conventions', help="Day-zero convention, month calculation, timezone and handling of past-due tasks, as signed off.")
    approved_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    approved_date = fields.Datetime(readonly=True, copy=False)

    @api.constrains(*PARAMETERS)
    def _check_parameters(self):
        for rule in self:
            offsets = rule._offsets()
            if not offsets:
                raise ValidationError(self.env._("Enter at least one pregnancy check day."))
            if offsets != sorted(set(offsets)) or offsets[0] <= 0:
                raise ValidationError(self.env._("Check days must be positive, ascending and without repeats."))
            if rule.gestation_days <= 0 or rule.prep_days < 0 or rule.prep_days >= rule.gestation_days:
                raise ValidationError(self.env._("Gestation length and the preparation reminder do not make sense together."))
            if not (1 <= rule.season_start_month <= 12 and 1 <= rule.season_end_month <= 12 and 1 <= rule.season_start_day <= 31 and 1 <= rule.season_end_day <= 31):
                raise ValidationError(self.env._("The season dates are not valid."))
            for month in rule._vaccine_months():
                if month <= 0:
                    raise ValidationError(self.env._("Vaccination months must be positive."))

    def _ints(self, text):
        try:
            return [int(x) for x in (text or '').replace(';', ',').split(',') if x.strip()]
        except ValueError:
            raise ValidationError(self.env._("'%s' must be a comma-separated list of whole numbers.", text))

    def _offsets(self):
        self.ensure_one()
        return self._ints(self.check_offsets)

    def _vaccine_months(self):
        self.ensure_one()
        return self._ints(self.vaccine_months)

    @api.model
    def _get_approved(self, company=None):
        company = company or self.env.company
        rule = self.sudo().search([('state', '=', 'approved'), ('company_id', '=', company.id)], limit=1)
        if not rule:
            raise UserError(self.env._("No approved breeding schedule exists. A resident veterinarian must approve a rule set before work is scheduled from it."))
        return rule

    def action_approve(self):
        _require_group(self.env, 'alfia_security.group_alfia_vet_manager', self.env._("the resident veterinarian"))
        for rule in self:
            if rule.state != 'draft':
                raise UserError(self.env._("Only a draft rule set can be approved."))
            others = self.search([('state', '=', 'approved'), ('company_id', '=', rule.company_id.id), ('id', '!=', rule.id)])
            others.write({'state': 'retired'})
            rule.write({'state': 'approved', 'approved_by_id': self.env.user.id, 'approved_date': fields.Datetime.now()})
            rule.message_post(body=self.env._("Approved by %s. It now drives new pregnancies.", self.env.user.name))

    def action_new_version(self):
        self.ensure_one()
        return self.copy({'name': self.name, 'version': self.version + 1, 'state': 'draft'})

    def write(self, vals):
        if set(vals) & set(PARAMETERS) and any(r.state != 'draft' for r in self):
            raise UserError(self.env._("An approved rule set cannot be changed. Create a new version instead."))
        return super().write(vals)

    def season_contains(self, day):
        """Whether `day` falls in the breeding season, which may cross the new year."""
        self.ensure_one()
        start = (self.season_start_month, self.season_start_day)
        end = (self.season_end_month, self.season_end_day)
        mmdd = (day.month, day.day)
        return (start <= mmdd or mmdd <= end) if start > end else (start <= mmdd <= end)


class AlfiaHorseBreeding(models.Model):
    _inherit = 'alfia.horse'

    pregnancy_ids = fields.One2many('alfia.pregnancy', 'mare_id', string='Pregnancies carried')
    reproductive_state = fields.Selection([('open', 'Open (barren)'), ('bred', 'Bred'), ('pregnant', 'Pregnant')], compute='_compute_reproductive_state', store=True)
    active_pregnancy_id = fields.Many2one('alfia.pregnancy', compute='_compute_reproductive_state', store=True)
    teasing_due = fields.Boolean(compute='_compute_teasing_due', search='_search_teasing_due')

    @api.depends('pregnancy_ids.state', 'sex')
    def _compute_reproductive_state(self):
        for horse in self:
            active = horse.pregnancy_ids.filtered(lambda p: p.state in ('bred', 'pregnant')).sorted('ovulation_date')[-1:]
            horse.active_pregnancy_id = active
            horse.reproductive_state = active.state if active else ('open' if horse.sex == 'female' else False)

    def _is_teasing_due(self, day, rules=None):
        self.ensure_one()
        if self.sex != 'female' or self.status != 'active' or not self.on_site:
            return False
        if rules is None:
            rules = {}
        if self.company_id.id not in rules:
            rules[self.company_id.id] = self.env['alfia.breeding.rule'].sudo().search(
                [('state', '=', 'approved'), ('company_id', '=', self.company_id.id)], limit=1)
        rule = rules[self.company_id.id]
        if not rule:
            return False
        if self.reproductive_state == 'open':
            return rule.season_contains(day)
        pregnancy = self.active_pregnancy_id
        return bool(pregnancy) and (day - pregnancy.ovulation_date).days <= rule.teasing_pregnant_until_day

    def _compute_teasing_due(self):
        today = fields.Date.context_today(self)
        rules = {}
        for horse in self:
            horse.teasing_due = horse._is_teasing_due(today, rules)

    def _search_teasing_due(self, operator, value):
        today = fields.Date.context_today(self)
        rules = {}
        candidates = self.sudo().search([('sex', '=', 'female'), ('status', '=', 'active'), ('on_site', '=', True)])
        mares = candidates.filtered(lambda h: h._is_teasing_due(today, rules))
        if operator in ('=', '!='):
            wanted = bool(value) == (operator == '=')
        elif operator in ('in', 'not in'):
            wanted = (True in value) == (operator == 'in')
        else:
            raise UserError(self.env._("Unsupported search on 'to tease today'."))
        return [('id', 'in' if wanted else 'not in', mares.ids)]


class AlfiaTeasingSymbol(models.Model):
    _name = 'alfia.teasing.symbol'
    _description = 'Teasing chart symbol'
    _order = 'sequence, id'

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    is_heat = fields.Boolean(string='Means the mare is in heat')

    _code_uniq = models.Constraint('unique(code)', 'Symbol codes must be unique.')


class AlfiaTeasing(models.Model):
    _name = 'alfia.teasing'
    _description = 'Teasing observation'
    _order = 'date desc, id desc'

    mare_id = fields.Many2one('alfia.horse', string='Mare', required=True, ondelete='cascade', index=True, domain="[('sex', '=', 'female')]")
    company_id = fields.Many2one('res.company', related='mare_id.company_id', store=True)
    date = fields.Date(required=True, default=fields.Date.context_today)
    symbol_id = fields.Many2one('alfia.teasing.symbol', required=True)
    is_heat = fields.Boolean(related='symbol_id.is_heat', store=True)
    observer_id = fields.Many2one('res.users', string='Observer', default=lambda self: self.env.user, readonly=True, copy=False)
    notes = fields.Char()

    @api.constrains('mare_id')
    def _check_mare(self):
        for rec in self:
            if rec.mare_id.sex != 'female':
                raise ValidationError(self.env._("Teasing is recorded for mares."))

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records.filtered('is_heat'):
            pregnancy = rec.mare_id.active_pregnancy_id
            if pregnancy:
                # Apparent heat in a pregnant mare alerts the vet at once. It never closes the pregnancy: only a vet's confirmed result does.
                self.env['alfia.task']._alfia_upsert(
                    f"heat:{rec.id}", rec.mare_id, rec.date, self.env._("Heat observed in pregnant mare %s: vet to examine", rec.mare_id.name),
                    'heat_alert', urgent=True, group=self.env.ref('alfia_security.group_alfia_vet_user', raise_if_not_found=False))
                pregnancy.sudo().message_post(body=self.env._("Apparent heat observed by %s. The vet was alerted. The pregnancy remains open until a vet confirms the result.", self.env.user.name))
        return records


class AlfiaBreedingService(models.Model):
    _name = 'alfia.breeding.service'
    _description = 'Breeding service (attempt)'
    _inherit = ['mail.thread']
    _order = 'date desc, id desc'

    mare_id = fields.Many2one('alfia.horse', string='Mare', required=True, domain="[('sex', '=', 'female')]", index=True, tracking=True)
    stallion_id = fields.Many2one('alfia.horse', string='Stallion', domain="[('sex', '=', 'male')]", tracking=True)
    stallion_name = fields.Char(string='Stallion (external)')
    company_id = fields.Many2one('res.company', related='mare_id.company_id', store=True)
    date = fields.Date(string='Service date', required=True, default=fields.Date.context_today, tracking=True)
    method = fields.Selection([('natural', 'Natural cover'), ('ai_fresh', 'AI - fresh'), ('ai_cooled', 'AI - cooled'), ('ai_frozen', 'AI - frozen'),
                               ('embryo', 'Embryo transfer')], required=True, default='natural')
    semen_lot_id = fields.Many2one('stock.lot', string='Semen batch')
    state = fields.Selection([('planned', 'Planned'), ('done', 'Done'), ('cancelled', 'Cancelled')], default='planned', required=True, tracking=True)
    season = fields.Char(compute='_compute_season', store=True)
    notes = fields.Text()
    ovulation_ids = fields.One2many('alfia.ovulation', 'service_id')
    pregnancy_ids = fields.One2many('alfia.pregnancy', 'service_id')

    @api.depends('date')
    def _compute_season(self):
        for rec in self:
            if not rec.date:
                rec.season = False
                continue
            rule = self.env['alfia.breeding.rule'].sudo().search([('state', '=', 'approved'), ('company_id', '=', rec.company_id.id)], limit=1)
            start_month = rule.season_start_month if rule else 10
            end_month = rule.season_end_month if rule else 6
            year = rec.date.year
            rec.season = f"{year}/{year + 1}" if (start_month > end_month and rec.date.month >= start_month) else (
                f"{year - 1}/{year}" if start_month > end_month else str(year))

    @api.constrains('mare_id', 'stallion_id')
    def _check_sexes(self):
        for rec in self:
            if rec.mare_id.sex != 'female':
                raise ValidationError(self.env._("The mare of a service must be a mare."))
            if rec.stallion_id and rec.stallion_id.sex != 'male':
                raise ValidationError(self.env._("The stallion of a service must be a stallion."))

    def action_done(self):
        self.state = 'done'

    def action_cancel(self):
        self.state = 'cancelled'


class AlfiaOvulation(models.Model):
    _name = 'alfia.ovulation'
    _description = 'Ovulation'
    _inherit = ['mail.thread']
    _order = 'date desc, id desc'

    mare_id = fields.Many2one('alfia.horse', string='Mare', required=True, domain="[('sex', '=', 'female')]", index=True)
    company_id = fields.Many2one('res.company', related='mare_id.company_id', store=True)
    service_id = fields.Many2one('alfia.breeding.service', domain="[('mare_id', '=', mare_id)]")
    date = fields.Date(string='Ovulation date', required=True, default=fields.Date.context_today, tracking=True)
    state = fields.Selection([('draft', 'Recorded'), ('confirmed', 'Confirmed by vet')], default='draft', required=True, tracking=True)
    confirmed_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    confirmed_date = fields.Datetime(readonly=True, copy=False)
    pregnancy_id = fields.Many2one('alfia.pregnancy', readonly=True, copy=False)
    notes = fields.Text()

    @api.constrains('mare_id')
    def _check_mare(self):
        for rec in self:
            if rec.mare_id.sex != 'female':
                raise ValidationError(self.env._("Ovulation is recorded for mares."))

    def action_confirm(self):
        """Vet confirms the ovulation: this is day zero of the pregnancy schedule. Confirming twice changes nothing."""
        _require_group(self.env, 'alfia_security.group_alfia_vet_user', self.env._("veterinary staff"))
        for rec in self:
            if rec.state == 'confirmed':
                continue
            rule = self.env['alfia.breeding.rule']._get_approved(rec.company_id)
            service = rec.service_id
            pregnancy = self.env['alfia.pregnancy'].create({
                'mare_id': rec.mare_id.id, 'genetic_dam_id': rec.mare_id.id, 'service_id': service.id or False,
                'sire_id': service.stallion_id.id or False, 'ovulation_id': rec.id, 'ovulation_date': rec.date, 'rule_id': rule.id})
            rec.write({'state': 'confirmed', 'confirmed_by_id': self.env.user.id, 'confirmed_date': fields.Datetime.now(), 'pregnancy_id': pregnancy.id})
            pregnancy._generate_tasks()
        return True

    def action_correct_date(self, new_date, reason):
        """Correct a confirmed ovulation date. Pending work moves; completed work and results stay as they were."""
        _require_group(self.env, 'alfia_security.group_alfia_vet_user', self.env._("veterinary staff"))
        if not reason:
            raise UserError(self.env._("Enter a reason for the correction."))
        for rec in self:
            old = rec.date
            rec.with_context(alfia_ovulation_correction=True).write({'date': new_date})
            rec.message_post(body=self.env._("Date corrected from %(old)s to %(new)s by %(user)s: %(reason)s", old=old, new=new_date, user=self.env.user.name, reason=reason))
            if rec.pregnancy_id:
                rec.pregnancy_id.ovulation_date = new_date
                moved = rec.pregnancy_id._generate_tasks()
                rec.pregnancy_id.message_post(body=self.env._("Ovulation date corrected. %d pending work item(s) were re-dated; completed work was kept.", moved))

    def write(self, vals):
        if 'date' in vals and any(r.state == 'confirmed' for r in self) and not self.env.context.get('alfia_ovulation_correction'):
            raise UserError(self.env._("A confirmed ovulation date is corrected with 'Correct date', which keeps the history."))
        return super().write(vals)


class AlfiaReproductiveExam(models.Model):
    _name = 'alfia.reproductive.exam'
    _description = 'Reproductive examination'
    _order = 'date desc, id desc'

    mare_id = fields.Many2one('alfia.horse', string='Mare', required=True, domain="[('sex', '=', 'female')]", index=True, ondelete='cascade')
    company_id = fields.Many2one('res.company', related='mare_id.company_id', store=True)
    date = fields.Date(required=True, default=fields.Date.context_today)
    vet_id = fields.Many2one('res.users', string='Examined by', default=lambda self: self.env.user, readonly=True, copy=False)
    left_follicle_mm = fields.Float(string='Left ovary: largest follicle (mm)')
    right_follicle_mm = fields.Float(string='Right ovary: largest follicle (mm)')
    edema = fields.Selection([('0', '0'), ('1', '1'), ('2', '2'), ('3', '3'), ('4', '4'), ('5', '5')], string='Uterine edema')
    cervix = fields.Selection([('open', 'Open'), ('relaxed', 'Relaxed'), ('closed', 'Closed')])
    findings = fields.Text()
    next_action = fields.Char()
    service_id = fields.Many2one('alfia.breeding.service', domain="[('mare_id', '=', mare_id)]")

    @api.constrains('left_follicle_mm', 'right_follicle_mm')
    def _check_sizes(self):
        for rec in self:
            if rec.left_follicle_mm < 0 or rec.right_follicle_mm < 0:
                raise ValidationError(self.env._("Follicle sizes cannot be negative."))


class AlfiaPregnancy(models.Model):
    _name = 'alfia.pregnancy'
    _description = 'Pregnancy'
    _inherit = ['mail.thread']
    _order = 'ovulation_date desc, id desc'

    name = fields.Char(compute='_compute_name', store=True)
    mare_id = fields.Many2one('alfia.horse', string='Carrier mare', required=True, domain="[('sex', '=', 'female')]", index=True, tracking=True)
    genetic_dam_id = fields.Many2one('alfia.horse', string='Genetic dam', domain="[('sex', '=', 'female')]",
                                     help="The biological mother. Differs from the carrier mare after an embryo transfer.")
    sire_id = fields.Many2one('alfia.horse', string='Sire', domain="[('sex', '=', 'male')]")
    company_id = fields.Many2one('res.company', related='mare_id.company_id', store=True)
    service_id = fields.Many2one('alfia.breeding.service')
    ovulation_id = fields.Many2one('alfia.ovulation')
    ovulation_date = fields.Date(required=True, tracking=True)
    rule_id = fields.Many2one('alfia.breeding.rule', string='Rule set used', required=True, ondelete='restrict')
    expected_foaling = fields.Date(compute='_compute_expected_foaling', store=True)
    state = fields.Selection([('bred', 'Bred, awaiting result'), ('pregnant', 'Confirmed pregnant'), ('lost', 'Lost'),
                              ('negative', 'Not pregnant'), ('foaled', 'Foaled')], default='bred', required=True, tracking=True, index=True)
    check_ids = fields.One2many('alfia.pregnancy.check', 'pregnancy_id')
    foaling_ids = fields.One2many('alfia.foaling', 'pregnancy_id')
    task_ids = fields.One2many('alfia.task', compute='_compute_task_ids')
    day_count = fields.Integer(string='Days since ovulation', compute='_compute_day_count')

    @api.depends('mare_id.name', 'ovulation_date')
    def _compute_name(self):
        for rec in self:
            rec.name = f"{rec.mare_id.name or ''} – {rec.ovulation_date or ''}"

    @api.depends('ovulation_date', 'rule_id.gestation_days')
    def _compute_expected_foaling(self):
        for rec in self:
            rec.expected_foaling = rec.ovulation_date + relativedelta(days=rec.rule_id.gestation_days) if rec.ovulation_date and rec.rule_id else False

    def _compute_day_count(self):
        today = fields.Date.context_today(self)
        for rec in self:
            rec.day_count = (today - rec.ovulation_date).days if rec.ovulation_date else 0

    def _compute_task_ids(self):
        for rec in self:
            rec.task_ids = self.env['alfia.task'].search(self.env['alfia.task']._alfia_key_domain(f"preg:{rec.id}"))

    @api.constrains('genetic_dam_id', 'mare_id')
    def _check_dam(self):
        for rec in self:
            if rec.genetic_dam_id and rec.genetic_dam_id.sex != 'female':
                raise ValidationError(self.env._("The genetic dam must be a mare."))

    def _generate_tasks(self):
        """Schedule every item of the rule set this pregnancy was created under. Safe to repeat: items are matched by key, so
        nothing is duplicated, pending items follow a corrected date, and completed or cancelled items are left alone."""
        Task = self.env['alfia.task']
        vet = self.env.ref('alfia_security.group_alfia_vet_user', raise_if_not_found=False)
        moved = 0
        for rec in self:
            if rec.state not in ('bred', 'pregnant'):
                continue
            rule, base, mare = rec.rule_id, rec.ovulation_date, rec.mare_id
            specs = [(f"preg:{rec.id}:check:{n}", base + relativedelta(days=n), self.env._("Pregnancy check, day %d", n), 'pregnancy_check') for n in rule._offsets()]
            specs += [(f"preg:{rec.id}:vacc:{m}", base + relativedelta(months=m), self.env._("Pregnancy vaccination, month %d", m), 'pregnancy_vaccine') for m in rule._vaccine_months()]
            specs.append((f"preg:{rec.id}:prep", rec.expected_foaling - relativedelta(days=rule.prep_days), self.env._("Foaling preparation"), 'foaling_prep'))
            for key, due, name, kind in specs:
                _task, outcome = Task._alfia_upsert(key, mare, due, name, kind, rule=rule, group=vet)
                moved += outcome == 'moved'
        return moved

    def _close(self, state, reason):
        """End a pregnancy: the mare is open again, pending work is cancelled, history is kept."""
        for rec in self:
            rec.state = state
            cancelled = self.env['alfia.task']._alfia_cancel_pending(f"preg:{rec.id}:", reason)
            rec.message_post(body=self.env._("%(reason)s %(count)d pending work item(s) were cancelled; results and history are kept.", reason=reason, count=len(cancelled)))


class AlfiaPregnancyCheck(models.Model):
    _name = 'alfia.pregnancy.check'
    _description = 'Pregnancy check result'
    _order = 'date desc, id desc'

    pregnancy_id = fields.Many2one('alfia.pregnancy', required=True, ondelete='cascade', index=True)
    mare_id = fields.Many2one('alfia.horse', related='pregnancy_id.mare_id', store=True, index=True)
    company_id = fields.Many2one('res.company', related='pregnancy_id.company_id', store=True)
    task_id = fields.Many2one('alfia.task', string='Scheduled check', domain="[('horse_id', '=', mare_id)]")
    date = fields.Date(required=True, default=fields.Date.context_today)
    result = fields.Selection([('positive', 'Positive'), ('negative', 'Negative'), ('inconclusive', 'Inconclusive'), ('loss', 'Pregnancy loss')], required=True)
    days_after_ovulation = fields.Integer(compute='_compute_days', store=True)
    next_check_date = fields.Date(string='Next check (chosen by the vet)')
    vet_id = fields.Many2one('res.users', string='Recorded by', default=lambda self: self.env.user, readonly=True, copy=False)
    notes = fields.Text()

    @api.depends('date', 'pregnancy_id.ovulation_date')
    def _compute_days(self):
        for rec in self:
            rec.days_after_ovulation = (rec.date - rec.pregnancy_id.ovulation_date).days if rec.date and rec.pregnancy_id.ovulation_date else 0

    @api.constrains('result', 'next_check_date', 'date')
    def _check_next(self):
        for rec in self:
            if rec.result == 'inconclusive' and not rec.next_check_date:
                raise ValidationError(self.env._("An inconclusive result needs the next check date. The vet chooses it; no interval is assumed."))
            if rec.next_check_date and rec.next_check_date <= rec.date:
                raise ValidationError(self.env._("The next check must be after this one."))

    @api.model_create_multi
    def create(self, vals_list):
        _require_group(self.env, 'alfia_security.group_alfia_vet_user', self.env._("veterinary staff (staff observations never close a pregnancy)"))
        records = super().create(vals_list)
        for rec in records:
            rec._apply()
        return records

    def _apply(self):
        self.ensure_one()
        pregnancy = self.pregnancy_id
        if pregnancy.state not in ('bred', 'pregnant'):
            raise UserError(self.env._("This pregnancy is already %s; results can no longer be added.", dict(pregnancy._fields['state']._description_selection(pregnancy.env))[pregnancy.state].lower()))
        if self.task_id and self.task_id.state in ('todo', 'deferred'):
            self.task_id.action_done()
        if self.result == 'positive':
            pregnancy.state = 'pregnant'
        elif self.result == 'negative':
            pregnancy._close('negative', self.env._("Negative result confirmed by %s.", self.vet_id.name))
        elif self.result == 'loss':
            pregnancy._close('lost', self.env._("Pregnancy loss confirmed by %s.", self.vet_id.name))
        else:
            self.env['alfia.task']._alfia_upsert(
                f"preg:{pregnancy.id}:uncertain:{self.id}", pregnancy.mare_id, self.next_check_date, self.env._("Re-check after inconclusive result"),
                'pregnancy_check', rule=pregnancy.rule_id, group=self.env.ref('alfia_security.group_alfia_vet_user', raise_if_not_found=False))


class AlfiaFoaling(models.Model):
    """Birth in three steps: stable staff record it, the vet confirms it, administration completes the foal's identity."""
    _name = 'alfia.foaling'
    _description = 'Foaling'
    _inherit = ['mail.thread']
    _order = 'date desc, id desc'

    pregnancy_id = fields.Many2one('alfia.pregnancy', required=True, ondelete='restrict', index=True)
    mare_id = fields.Many2one('alfia.horse', related='pregnancy_id.mare_id', store=True, index=True)
    company_id = fields.Many2one('res.company', related='pregnancy_id.company_id', store=True)
    date = fields.Date(string='Birth date', required=True, default=fields.Date.context_today, tracking=True)
    outcome = fields.Selection([('live', 'Live foal'), ('stillborn', 'Stillborn'), ('aborted', 'Aborted')], required=True, default='live', tracking=True)
    foal_sex = fields.Selection([('filly', 'Filly'), ('colt', 'Colt')])
    foal_name = fields.Char()
    complications = fields.Char()
    notes = fields.Text()
    state = fields.Selection([('draft', 'Recorded by stable staff'), ('vet_confirmed', 'Confirmed by vet'), ('completed', 'Identity completed')],
                             default='draft', required=True, tracking=True)
    recorded_by_id = fields.Many2one('res.users', default=lambda self: self.env.user, readonly=True, copy=False)
    confirmed_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    foal_id = fields.Many2one('alfia.horse', string='Foal', readonly=True, copy=False)

    _one_per_pregnancy = models.Constraint('unique(pregnancy_id)', 'A birth is already recorded for this pregnancy.')

    def action_vet_confirm(self):
        _require_group(self.env, 'alfia_security.group_alfia_vet_user', self.env._("veterinary staff"))
        for rec in self:
            if rec.state != 'draft':
                raise UserError(self.env._("Only a recorded birth can be confirmed."))
            rec.write({'state': 'vet_confirmed', 'confirmed_by_id': self.env.user.id})

    def action_complete(self):
        _require_group(self.env, 'alfia_security.group_alfia_breeding_manager', self.env._("the breeding manager"))
        for rec in self:
            if rec.state == 'completed':
                continue  # idempotent: never a second foal record
            if rec.state != 'vet_confirmed':
                raise UserError(self.env._("The vet must confirm the birth before its identity is completed."))
            preg = rec.pregnancy_id
            if rec.outcome == 'live':
                if not rec.foal_name or not rec.foal_sex:
                    raise UserError(self.env._("Enter the foal's name and sex."))
                carrier = preg.mare_id
                dam = preg.genetic_dam_id or carrier
                foal = self.env['alfia.horse'].create({
                    'name': rec.foal_name, 'sex': 'female' if rec.foal_sex == 'filly' else 'male', 'birthdate': rec.date,
                    'sire_id': preg.sire_id.id or False, 'dam_id': dam.id, 'carrier_mare_id': carrier.id if carrier != dam else False,
                    'breed': dam.breed, 'initial_location_id': carrier.location_id.id or False})
                rec.foal_id = foal
                preg.state = 'foaled'
            elif rec.outcome == 'aborted':
                preg._close('lost', self.env._("The pregnancy ended in abortion."))
            else:
                preg.state = 'foaled'
            rec.state = 'completed'
            preg.task_ids.filtered(lambda t: t.state in ('todo', 'deferred')).action_cancel()
            rec.message_post(body=self.env._("Identity completed by %s.", self.env.user.name))


class AlfiaEmbryo(models.Model):
    _name = 'alfia.embryo'
    _description = 'Embryo'
    _inherit = ['mail.thread']
    _order = 'flush_date desc, id desc'

    name = fields.Char(string='Embryo code', required=True, copy=False, default=lambda self: self.env['ir.sequence'].next_by_code('alfia.embryo'))
    donor_id = fields.Many2one('alfia.horse', string='Donor mare (genetic dam)', required=True, domain="[('sex', '=', 'female')]", index=True)
    sire_id = fields.Many2one('alfia.horse', string='Sire', domain="[('sex', '=', 'male')]")
    company_id = fields.Many2one('res.company', related='donor_id.company_id', store=True)
    flush_date = fields.Date(required=True, default=fields.Date.context_today)
    stage = fields.Char()
    grade = fields.Char()
    state = fields.Selection([('collected', 'Collected'), ('stored', 'Frozen in storage'), ('transferred', 'Transferred'), ('discarded', 'Discarded')],
                             default='collected', required=True, tracking=True)
    tank_id = fields.Many2one('stock.location', string='Storage location', domain="[('usage', '=', 'internal')]")
    pregnancy_id = fields.Many2one('alfia.pregnancy', readonly=True, copy=False)
    notes = fields.Text()

    _name_uniq = models.Constraint('unique(name)', 'Embryo codes must be unique.')

    def action_store(self):
        for rec in self:
            if not rec.tank_id:
                raise UserError(self.env._("Choose where the embryo is stored."))
            rec.state = 'stored'

    def action_discard(self):
        _require_group(self.env, 'alfia_security.group_alfia_vet_user', self.env._("veterinary staff"))
        for rec in self:
            if rec.state == 'transferred':
                raise UserError(self.env._("A transferred embryo cannot be discarded."))
        self.state = 'discarded'

    def action_transfer(self, pregnancy):
        """Place the embryo in a recipient's pregnancy: the recipient stays the carrier; the donor becomes the genetic dam."""
        _require_group(self.env, 'alfia_security.group_alfia_vet_user', self.env._("veterinary staff"))
        for rec in self:
            if rec.state not in ('collected', 'stored'):
                raise UserError(self.env._("This embryo is %s and cannot be transferred.", dict(rec._fields['state']._description_selection(rec.env))[rec.state].lower()))
            if pregnancy.state not in ('bred', 'pregnant'):
                raise UserError(self.env._("The recipient's pregnancy is not active."))
            if pregnancy.mare_id == rec.donor_id:
                raise UserError(self.env._("The recipient must be a different mare from the donor."))
            pregnancy.write({'genetic_dam_id': rec.donor_id.id, 'sire_id': rec.sire_id.id or pregnancy.sire_id.id})
            rec.write({'state': 'transferred', 'pregnancy_id': pregnancy.id})
            pregnancy.message_post(body=self.env._("Embryo %(code)s from donor %(donor)s transferred to carrier %(carrier)s.", code=rec.name, donor=rec.donor_id.name, carrier=pregnancy.mare_id.name))


class AlfiaSemenCollection(models.Model):
    _name = 'alfia.semen.collection'
    _description = 'Semen collection'
    _inherit = ['mail.thread']
    _order = 'date desc, id desc'

    name = fields.Char(copy=False, readonly=True, default='New')
    stallion_id = fields.Many2one('alfia.horse', string='Stallion', required=True, domain="[('sex', '=', 'male')]", index=True)
    company_id = fields.Many2one('res.company', related='stallion_id.company_id', store=True)
    date = fields.Date(required=True, default=fields.Date.context_today)
    volume_ml = fields.Float(string='Volume (ml)')
    concentration = fields.Float(string='Concentration (M/ml)')
    motility = fields.Float(string='Motility (%)')
    morphology = fields.Float(string='Normal morphology (%)')
    doses = fields.Integer(string='Doses produced')
    product_id = fields.Many2one('product.product', string='Semen product', default=lambda self: self.env.ref('alfia_equine.product_frozen_semen', raise_if_not_found=False))
    tank_id = fields.Many2one('stock.location', string='Tank / storage location', domain="[('usage', '=', 'internal')]")
    state = fields.Selection([('draft', 'Collected'), ('released', 'Released for use'), ('rejected', 'Rejected')], default='draft', required=True, tracking=True)
    released_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    lot_id = fields.Many2one('stock.lot', string='Batch', readonly=True, copy=False)
    notes = fields.Text()

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('alfia.semen.collection') or 'New'
        return super().create(vals_list)

    @api.constrains('motility', 'morphology', 'concentration', 'doses')
    def _check_values(self):
        for rec in self:
            if not (0 <= rec.motility <= 100 and 0 <= rec.morphology <= 100):
                raise ValidationError(self.env._("Motility and morphology are percentages from 0 to 100."))
            if rec.concentration < 0 or rec.doses < 0:
                raise ValidationError(self.env._("Quantities cannot be negative."))

    def action_release(self):
        """A vet releases the batch: a lot is created and the doses enter the tank as controlled stock, with a stock move for traceability."""
        _require_group(self.env, 'alfia_security.group_alfia_vet_user', self.env._("veterinary staff"))
        for rec in self:
            if rec.state != 'draft':
                raise UserError(self.env._("Only a collected batch can be released."))
            if not (rec.doses > 0 and rec.concentration > 0 and rec.motility > 0):
                raise UserError(self.env._("Enter doses, concentration and motility before the batch can be released."))
            if not rec.tank_id or not rec.product_id:
                raise UserError(self.env._("Choose the semen product and the tank location."))
            self.env.user._alfia_check_location(rec.tank_id, self.env._("semen tank"))
            product = rec.product_id
            lot = self.env['stock.lot'].sudo().create({'name': f"{rec.name}-{rec.stallion_id.id}", 'product_id': product.id, 'company_id': rec.company_id.id})
            source = self.env['stock.location'].sudo().search([('usage', '=', 'inventory'), ('company_id', 'in', (False, rec.company_id.id))], limit=1)
            move = self.env['stock.move'].sudo().create({
                'product_id': product.id, 'product_uom_qty': rec.doses, 'uom_id': product.uom_id.id, 'company_id': rec.company_id.id,
                'location_id': source.id, 'location_dest_id': rec.tank_id.id, 'origin': rec.name, 'picked': True,
                'move_line_ids': [Command.create({'product_id': product.id, 'lot_id': lot.id, 'quantity': rec.doses,
                                                  'location_id': source.id, 'location_dest_id': rec.tank_id.id, 'company_id': rec.company_id.id})]})
            move._action_confirm()
            move._action_done()
            rec.write({'state': 'released', 'lot_id': lot.id, 'released_by_id': self.env.user.id})

    def action_reject(self):
        _require_group(self.env, 'alfia_security.group_alfia_vet_user', self.env._("veterinary staff"))
        self.filtered(lambda r: r.state == 'draft').write({'state': 'rejected'})

    def unlink(self):
        if any(r.state == 'released' for r in self):
            raise UserError(self.env._("A released batch is stock and cannot be deleted."))
        return super().unlink()
