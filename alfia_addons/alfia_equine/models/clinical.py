from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


class AlfiaHorseRecord(models.AbstractModel):
    """Common ground of every record that belongs to one horse."""
    _name = 'alfia.horse.record'
    _description = 'Horse record'
    _order = 'id desc'

    horse_id = fields.Many2one('alfia.horse', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one('res.company', related='horse_id.company_id', store=True)
    recorded_by_id = fields.Many2one('res.users', string='Recorded by', default=lambda self: self.env.user, readonly=True, copy=False)

    def _alfia_task_specs(self):
        """Work items this record implies: [{'key', 'date', 'name', 'kind'}]. An empty date withdraws the item."""
        return []

    def _alfia_sync_tasks(self):
        Task = self.env['alfia.task']
        for rec in self:
            for spec in rec._alfia_task_specs():
                key = f"{rec._name}:{rec.id}:{spec['key']}"
                if spec['date']:
                    Task._alfia_upsert(key, rec.horse_id, spec['date'], spec['name'], spec['kind'],
                                       group=self.env.ref('alfia_security.group_alfia_vet_user', raise_if_not_found=False))
                else:
                    Task._alfia_cancel_pending(key, self.env._("The date was removed from the source record."))

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._alfia_sync_tasks()
        return records

    def write(self, vals):
        res = super().write(vals)
        self._alfia_sync_tasks()
        return res


class AlfiaHealthRecord(models.Model):
    _name = 'alfia.health.record'
    _description = 'Health record'
    _inherit = ['alfia.horse.record']
    _order = 'date desc, id desc'

    date = fields.Date(required=True, default=fields.Date.context_today)
    type = fields.Selection([('vaccine', 'Vaccination'), ('deworming', 'Deworming'), ('illness', 'Illness'), ('injury', 'Injury'),
                             ('surgery', 'Surgery'), ('checkup', 'Check-up'), ('other', 'Other')], required=True, default='vaccine')
    title = fields.Char(required=True)
    vet_id = fields.Many2one('res.partner', string='Vet')
    description = fields.Text()
    cost = fields.Float()
    next_due = fields.Date()

    def _alfia_task_specs(self):
        self.ensure_one()
        return [{'key': 'next_due', 'date': self.next_due, 'kind': 'health_due',
                 'name': self.env._("%(type)s due: %(title)s", type=dict(self._fields['type'].selection)[self.type], title=self.title)}]

    @api.constrains('date', 'next_due')
    def _check_dates(self):
        for rec in self:
            if rec.next_due and rec.next_due < rec.date:
                raise ValidationError(self.env._("The next due date is before the record date."))


class AlfiaVetVisit(models.Model):
    _name = 'alfia.vet.visit'
    _description = 'Vet visit'
    _inherit = ['alfia.horse.record']
    _order = 'date desc, id desc'

    date = fields.Date(required=True, default=fields.Date.context_today)
    vet_id = fields.Many2one('res.partner', string='Vet')
    reason = fields.Char(required=True)
    diagnosis = fields.Text()
    treatment_given = fields.Text()
    cost = fields.Float()
    follow_up = fields.Date()

    def _alfia_task_specs(self):
        self.ensure_one()
        return [{'key': 'follow_up', 'date': self.follow_up, 'kind': 'vet_follow_up', 'name': self.env._("Vet follow-up: %s", self.reason)}]


class AlfiaTreatment(models.Model):
    _name = 'alfia.treatment'
    _description = 'Treatment (prescription)'
    _inherit = ['alfia.horse.record']
    _order = 'start_date desc, id desc'
    _rec_name = 'drug'

    start_date = fields.Date(required=True, default=fields.Date.context_today)
    end_date = fields.Date()
    drug = fields.Char(string='Drug / treatment', required=True)
    dose = fields.Char(string='Prescribed dose')
    route = fields.Selection([('oral', 'Oral'), ('iv', 'IV'), ('im', 'IM'), ('sc', 'SC'), ('topical', 'Topical'), ('other', 'Other')])
    frequency = fields.Char()
    vet_id = fields.Many2one('res.partner', string='Prescribed by')
    withdrawal_days = fields.Integer(string='Withdrawal period (days)')
    withdrawal_until = fields.Date(compute='_compute_withdrawal_until', store=True)
    notes = fields.Text()
    dose_ids = fields.One2many('alfia.treatment.dose', 'treatment_id', string='Doses actually given')
    given_count = fields.Integer(compute='_compute_given_count')

    @api.depends('end_date', 'start_date', 'withdrawal_days')
    def _compute_withdrawal_until(self):
        for rec in self:
            last = rec.end_date or rec.start_date
            rec.withdrawal_until = fields.Date.add(last, days=rec.withdrawal_days) if last and rec.withdrawal_days else False

    @api.depends('dose_ids.missed')
    def _compute_given_count(self):
        for rec in self:
            rec.given_count = len(rec.dose_ids.filtered(lambda d: not d.missed))


class AlfiaTreatmentDose(models.Model):
    """What was actually given, kept apart from what was prescribed."""
    _name = 'alfia.treatment.dose'
    _description = 'Dose administered'
    _order = 'given_at desc, id desc'

    treatment_id = fields.Many2one('alfia.treatment', required=True, ondelete='cascade', index=True)
    horse_id = fields.Many2one('alfia.horse', related='treatment_id.horse_id', store=True, index=True)
    given_at = fields.Datetime(required=True, default=fields.Datetime.now)
    given_by_id = fields.Many2one('res.users', default=lambda self: self.env.user, readonly=True, copy=False)
    dose_given = fields.Char()
    missed = fields.Boolean(help="The dose was due but not given.")
    notes = fields.Char()


class AlfiaLabTest(models.Model):
    _name = 'alfia.lab.test'
    _description = 'Laboratory test'
    _inherit = ['alfia.horse.record']
    _order = 'date desc, id desc'
    _rec_name = 'test'

    date = fields.Date(required=True, default=fields.Date.context_today)
    test = fields.Char(required=True)
    lab = fields.Char(string='Laboratory')
    vet_id = fields.Many2one('res.partner', string='Vet')
    result = fields.Char()
    reference_range = fields.Char()
    notes = fields.Text()
    state = fields.Selection([('new', 'Awaiting review'), ('reviewed', 'Reviewed by vet')], default='new', required=True)
    reviewed_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    reviewed_date = fields.Datetime(readonly=True, copy=False)

    def action_review(self):
        if not self.env.user.has_group('alfia_security.group_alfia_vet_user'):
            raise AccessError(self.env._("Only veterinary staff can review laboratory results."))
        self.write({'state': 'reviewed', 'reviewed_by_id': self.env.user.id, 'reviewed_date': fields.Datetime.now()})


class AlfiaDiagnosticImage(models.Model):
    _name = 'alfia.diag.image'
    _description = 'Diagnostic image'
    _inherit = ['alfia.horse.record']
    _order = 'date desc, id desc'
    _rec_name = 'body_part'

    date = fields.Date(required=True, default=fields.Date.context_today)
    modality = fields.Selection([('xray', 'X-ray'), ('ultrasound', 'Ultrasound'), ('mri', 'MRI'), ('ct', 'CT'), ('endoscopy', 'Endoscopy'),
                                 ('thermography', 'Thermography'), ('other', 'Other')], required=True)
    body_part = fields.Char()
    vet_id = fields.Many2one('res.partner', string='Vet')
    findings = fields.Text()
    attachment_ids = fields.Many2many('ir.attachment', string='Images / reports')


class AlfiaOdontology(models.Model):
    _name = 'alfia.odontology'
    _description = 'Dental exam'
    _inherit = ['alfia.horse.record']
    _order = 'date desc, id desc'
    _rec_name = 'date'

    date = fields.Date(required=True, default=fields.Date.context_today)
    vet_id = fields.Many2one('res.partner', string='Dentist / vet')
    findings = fields.Text()
    floated = fields.Boolean(string='Teeth floated')
    extractions = fields.Char()
    next_date = fields.Date(string='Next check')
    cost = fields.Float()

    def _alfia_task_specs(self):
        self.ensure_one()
        return [{'key': 'next', 'date': self.next_date, 'kind': 'dental', 'name': self.env._("Dental check due")}]


class AlfiaFarrierVisit(models.Model):
    _name = 'alfia.farrier.visit'
    _description = 'Farrier visit'
    _inherit = ['alfia.horse.record']
    _order = 'date desc, id desc'
    _rec_name = 'date'

    SHOE = [('barefoot', 'Barefoot'), ('trim', 'Trimmed'), ('shod', 'Shod'), ('corrective', 'Corrective shoe'), ('reset', 'Reset')]

    date = fields.Date(required=True, default=fields.Date.context_today)
    farrier_id = fields.Many2one('res.partner', string='Farrier')
    lf_shoe = fields.Selection(SHOE, string='Left fore')
    rf_shoe = fields.Selection(SHOE, string='Right fore')
    lh_shoe = fields.Selection(SHOE, string='Left hind')
    rh_shoe = fields.Selection(SHOE, string='Right hind')
    findings = fields.Text(string='Findings (cracks, bruising, abscess, soreness)')
    materials = fields.Char()
    cost = fields.Float()
    next_date = fields.Date(string='Next visit')

    def _alfia_task_specs(self):
        self.ensure_one()
        return [{'key': 'next', 'date': self.next_date, 'kind': 'farrier', 'name': self.env._("Farrier visit due")}]


class AlfiaMeasurement(models.Model):
    _name = 'alfia.measurement'
    _description = 'Weight and height'
    _inherit = ['alfia.horse.record']
    _order = 'date desc, id desc'
    _rec_name = 'date'

    date = fields.Date(required=True, default=fields.Date.context_today)
    weight = fields.Float(string='Weight (kg)')
    height = fields.Float(string='Height (cm)')
    girth = fields.Float(string='Girth (cm)')
    notes = fields.Char()

    @api.constrains('weight', 'height', 'girth')
    def _check_positive(self):
        for rec in self:
            if rec.weight < 0 or rec.height < 0 or rec.girth < 0:
                raise ValidationError(self.env._("Measurements cannot be negative."))


class AlfiaTemperature(models.Model):
    _name = 'alfia.temperature'
    _description = 'Temperature and vitals'
    _inherit = ['alfia.horse.record']
    _order = 'taken_at desc, id desc'
    _rec_name = 'taken_at'

    taken_at = fields.Datetime(required=True, default=fields.Datetime.now)
    temperature = fields.Float(string='Temperature (°C)', required=True, digits=(4, 1))
    pulse = fields.Integer(string='Pulse (bpm)')
    respiration = fields.Integer(string='Respiration (/min)')
    notes = fields.Char()

    @api.constrains('temperature')
    def _check_range(self):
        for rec in self:
            if not 30.0 <= rec.temperature <= 45.0:
                raise ValidationError(self.env._("A temperature of %s °C is not plausible. Check the reading.", rec.temperature))


class AlfiaHorseNote(models.Model):
    _name = 'alfia.horse.note'
    _description = 'Horse note'
    _inherit = ['alfia.horse.record']
    _order = 'date desc, id desc'
    _rec_name = 'content'

    date = fields.Date(required=True, default=fields.Date.context_today)
    content = fields.Text(required=True)


class AlfiaScoring(models.Model):
    _name = 'alfia.scoring'
    _description = 'Assessment'
    _inherit = ['alfia.horse.record']
    _order = 'date desc, id desc'
    _rec_name = 'date'

    date = fields.Date(required=True, default=fields.Date.context_today)
    assessor_id = fields.Many2one('res.partner', string='Assessor')
    score = fields.Float(string='Overall score', required=True)
    movement = fields.Float()
    conformation = fields.Float()
    temperament = fields.Float()
    notes = fields.Text()


class AlfiaCompetition(models.Model):
    _name = 'alfia.competition'
    _description = 'Competition result'
    _inherit = ['alfia.horse.record']
    _order = 'date desc, id desc'
    _rec_name = 'event'

    date = fields.Date(required=True, default=fields.Date.context_today)
    event = fields.Char(required=True)
    location = fields.Char()
    discipline = fields.Char()
    class_name = fields.Char(string='Class / level')
    rider_id = fields.Many2one('res.partner', string='Rider')
    result = fields.Char(string='Placing / result')
    score = fields.Float()
    prize_money = fields.Float()
    notes = fields.Text()


class AlfiaTrainingSession(models.Model):
    _name = 'alfia.training.session'
    _description = 'Training session'
    _inherit = ['alfia.horse.record']
    _order = 'date desc, id desc'
    _rec_name = 'program'

    date = fields.Date(required=True, default=fields.Date.context_today)
    program = fields.Char(string='Program / discipline')
    trainer_id = fields.Many2one('res.partner', string='Trainer')
    gait = fields.Selection([('walk', 'Walk'), ('trot', 'Trot'), ('canter', 'Canter'), ('gallop', 'Gallop'), ('swim', 'Swimming'), ('other', 'Other')])
    distance_km = fields.Float(string='Distance (km)')
    duration_min = fields.Integer(string='Duration (min)')
    effort = fields.Integer(string='Effort (1-10)')
    horse_weight = fields.Float(string='Horse weight (kg)')
    rest_day = fields.Boolean()
    entry_mode = fields.Selection([('manual', 'Entered by hand'), ('device', 'From a device')], default='manual')
    notes = fields.Text()

    @api.constrains('effort')
    def _check_effort(self):
        for rec in self:
            if rec.effort and not 1 <= rec.effort <= 10:
                raise ValidationError(self.env._("Effort is a score from 1 to 10."))


class AlfiaClearance(models.Model):
    """Clinical fitness (the vet) and event eligibility (the competition manager) are separate decisions.

    A missing record never means "cleared": the horse's status is *pending* until somebody decides."""
    _name = 'alfia.clearance'
    _description = 'Fitness or eligibility decision'
    _inherit = ['alfia.horse.record']
    _order = 'date desc, id desc'
    _rec_name = 'kind'

    kind = fields.Selection([('fitness', 'Clinical fitness (vet)'), ('eligibility', 'Event eligibility (competition manager)')], required=True)
    state = fields.Selection([('pending', 'Pending'), ('cleared', 'Cleared'), ('restricted', 'Restricted')], default='pending', required=True)
    date = fields.Date(required=True, default=fields.Date.context_today)
    decided_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    restricted_until = fields.Date()
    notes = fields.Text()

    def _check_may_decide(self, kind):
        group = 'alfia_security.group_alfia_vet_user' if kind == 'fitness' else 'alfia_security.group_alfia_training_manager'
        if not self.env.user.has_group(group):
            who = self.env._("veterinary staff") if kind == 'fitness' else self.env._("a training or competition manager")
            raise AccessError(self.env._("Only %s can decide this.", who))

    def action_decide(self, state, restricted_until=None, notes=None):
        for rec in self:
            rec._check_may_decide(rec.kind)
            rec.write({'state': state, 'decided_by_id': self.env.user.id, 'restricted_until': restricted_until, 'notes': notes or rec.notes})

    def action_clear(self):
        self.action_decide('cleared')

    def action_restrict(self):
        self.action_decide('restricted')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('state', 'pending') != 'pending':
                self._check_may_decide(vals.get('kind'))
                vals['decided_by_id'] = self.env.user.id
        return super().create(vals_list)


class AlfiaHorseClinical(models.Model):
    _inherit = 'alfia.horse'

    fitness_state = fields.Selection([('pending', 'Pending'), ('cleared', 'Cleared'), ('restricted', 'Restricted')], compute='_compute_clearances')
    eligibility_state = fields.Selection([('pending', 'Pending'), ('cleared', 'Cleared'), ('restricted', 'Restricted')], compute='_compute_clearances')
    health_record_ids = fields.One2many('alfia.health.record', 'horse_id')
    next_health_due = fields.Date(compute='_compute_next_health_due')

    def _compute_clearances(self):
        for horse in self:
            for kind in ('fitness', 'eligibility'):
                last = self.env['alfia.clearance'].search([('horse_id', '=', horse.id), ('kind', '=', kind)], order='date desc, id desc', limit=1)
                horse[f'{kind}_state'] = last.state if last else 'pending'

    @api.depends('health_record_ids.next_due')
    def _compute_next_health_due(self):
        today = fields.Date.context_today(self)
        for horse in self:
            dues = [d for d in horse.health_record_ids.mapped('next_due') if d and d >= today]
            horse.next_health_due = min(dues) if dues else False
