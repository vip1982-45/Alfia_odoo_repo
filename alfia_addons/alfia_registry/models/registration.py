from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.alfia_security.flow import workflow

MANAGERS = ('alfia_security.group_alfia_breeding_manager',)
KINDS = [('registration', 'Studbook registration'), ('dna', 'DNA sample / parentage'), ('passport', 'Passport'),
         ('breeding_certificate', 'Breeding certificate'), ('other', 'Other certificate')]


class AlfiaHorseRegistration(models.Model):
    """A request to the registering authority (studbook, WAHO-related process, laboratory) for a horse: registration,
    DNA sample, passport or breeding certificate. Raised by staff, approved by a breeding manager who did not raise it,
    sent, then completed with the certificate or result. Due dates are followed with activities and an overdue flag."""
    _name = 'alfia.horse.registration'
    _description = 'Horse registration and certificate request'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'alfia.guarded.mixin']
    _order = 'due_date, id desc'
    _alfia_guarded = ('state', 'requested_by_id', 'approved_by_id', 'approved_date', 'sent_date', 'completed_date')
    _alfia_guarded_create = {'requested_by_id': '*'}

    name = fields.Char(readonly=True, copy=False, default='New')
    horse_id = fields.Many2one('alfia.horse', required=True, index=True, tracking=True)
    kind = fields.Selection(KINDS, required=True, default='registration', tracking=True)
    authority_id = fields.Many2one('res.partner', string='Authority / laboratory',
                                   help="For example the Qatar Arabian horse studbook, a WAHO-related registry or a DNA laboratory.")
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company, required=True)
    request_date = fields.Date(default=fields.Date.context_today, required=True)
    due_date = fields.Date(required=True, tracking=True, help="When the authority's answer or the certificate is due.")
    sample_date = fields.Date(string='Sample taken on', help="DNA sample: when it was taken.")
    sample_taken_by_id = fields.Many2one('res.users', string='Sample taken by')
    authority_reference = fields.Char(string='Authority reference')
    certificate_number = fields.Char(tracking=True)
    issue_date = fields.Date()
    expiry_date = fields.Date()
    dna_result = fields.Selection([('verified', 'Parentage verified'), ('not_verified', 'Parentage not verified'),
                                   ('profile_only', 'Profile recorded (no parentage test)')], string='DNA result', tracking=True)
    notes = fields.Text()
    state = fields.Selection([('draft', 'Draft'), ('submitted', 'Waiting for approval'), ('approved', 'Approved, to send'),
                              ('sent', 'Sent to the authority'), ('done', 'Completed'), ('rejected', 'Rejected'),
                              ('cancelled', 'Cancelled')], default='draft', required=True, tracking=True)
    requested_by_id = fields.Many2one('res.users', default=lambda self: self.env.user, readonly=True)
    approved_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    approved_date = fields.Datetime(readonly=True, copy=False)
    sent_date = fields.Date(readonly=True, copy=False)
    completed_date = fields.Date(readonly=True, copy=False)
    is_overdue = fields.Boolean(compute='_compute_is_overdue', search='_search_is_overdue')

    @api.depends('due_date', 'state')
    def _compute_is_overdue(self):
        today = fields.Date.context_today(self)
        for rec in self:
            rec.is_overdue = bool(rec.due_date and rec.due_date < today and rec.state in ('draft', 'submitted', 'approved', 'sent'))

    def _search_is_overdue(self, operator, value):
        domain = [('due_date', '<', fields.Date.context_today(self)), ('state', 'in', ('draft', 'submitted', 'approved', 'sent'))]
        values = value if isinstance(value, (list, tuple, set)) else [value]
        wanted = any(bool(v) for v in values)
        if operator in ('!=', 'not in'):
            wanted = not wanted
        return domain if wanted else ['!', '&'] + domain

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('alfia.horse.registration') or 'New'
        return super().create(vals_list)

    def write(self, vals):
        locked = {'horse_id', 'kind', 'authority_id', 'request_date'}
        if locked & set(vals) and not self.env.su and any(r.state not in ('draft', 'rejected') for r in self):
            raise UserError(self.env._("What a request is for cannot change once it is submitted. Cancel it and raise a new one."))
        return super().write(vals)

    def _check_manager(self):
        if not (self.env.su or any(self.env.user.has_group(g) for g in MANAGERS)):
            raise AccessError(self.env._("Only breeding managers approve registration and certificate requests."))

    def _set(self, state, **extra):
        with workflow():
            self.write(dict(extra, state=state))

    def action_submit(self):
        for rec in self:
            if rec.state not in ('draft', 'rejected'):
                raise UserError(self.env._("%s has already been submitted.", rec.name))
            if rec.kind == 'dna' and not rec.sample_date:
                raise UserError(self.env._("Record when the DNA sample was taken before submitting %s.", rec.name))
            rec._set('submitted', requested_by_id=self.env.user.id)

    def action_approve(self):
        self._check_manager()
        for rec in self:
            if rec.state != 'submitted':
                raise UserError(self.env._("Only a submitted request can be approved."))
            if rec.requested_by_id == self.env.user and self.env['alfia.sod.mixin']._alfia_sod_applies(None, rec.company_id):
                raise UserError(self.env._("You raised %s, so another breeding manager must approve it.", rec.name))
            rec._set('approved', approved_by_id=self.env.user.id, approved_date=fields.Datetime.now())
            rec.activity_schedule('mail.mail_activity_data_todo', date_deadline=rec.due_date, user_id=rec.requested_by_id.id,
                                  summary=self.env._("Follow up %(kind)s for %(horse)s", kind=dict(KINDS)[rec.kind], horse=rec.horse_id.name))

    def action_reject(self):
        self._check_manager()
        for rec in self.filtered(lambda r: r.state == 'submitted'):
            rec._set('rejected')
            rec.message_post(body=self.env._("Rejected by %s.", self.env.user.name))

    def action_sent(self):
        for rec in self:
            if rec.state != 'approved':
                raise UserError(self.env._("Only an approved request is sent to the authority."))
            rec._set('sent', sent_date=fields.Date.context_today(self))

    def action_done(self):
        for rec in self:
            if rec.state != 'sent':
                raise UserError(self.env._("Only a request sent to the authority can be completed."))
            if rec.kind == 'dna' and not rec.dna_result:
                raise UserError(self.env._("Record the DNA result before completing %s.", rec.name))
            if rec.kind != 'dna' and not rec.certificate_number:
                raise UserError(self.env._("Record the certificate or registration number before completing %s.", rec.name))
            rec._set('done', completed_date=fields.Date.context_today(self))
            rec.activity_ids.action_done()

    def action_cancel(self):
        for rec in self:
            if rec.state == 'done':
                raise UserError(self.env._("A completed request is kept as the record."))
            rec._set('cancelled')
            rec.activity_ids.unlink()


class AlfiaHorse(models.Model):
    _inherit = 'alfia.horse'

    alfia_registration_ids = fields.One2many('alfia.horse.registration', 'horse_id', string='Registrations and certificates')
    alfia_dna_status = fields.Selection([('none', 'No DNA test'), ('pending', 'Pending'), ('verified', 'Parentage verified'),
                                         ('not_verified', 'Parentage NOT verified'), ('profile_only', 'Profile only')],
                                        string='DNA', compute='_compute_alfia_dna_status')
    alfia_registration_number = fields.Char(string='Studbook number', compute='_compute_alfia_dna_status')

    @api.depends('alfia_registration_ids.state', 'alfia_registration_ids.dna_result', 'alfia_registration_ids.certificate_number')
    def _compute_alfia_dna_status(self):
        for horse in self:
            regs = horse.sudo().alfia_registration_ids
            dna = regs.filtered(lambda r: r.kind == 'dna' and r.state != 'cancelled').sorted('id', reverse=True)[:1]
            horse.alfia_dna_status = (dna.dna_result if dna.state == 'done' else 'pending') if dna else 'none'
            studbook = regs.filtered(lambda r: r.kind == 'registration' and r.state == 'done').sorted('completed_date', reverse=True)[:1]
            horse.alfia_registration_number = studbook.certificate_number or False
