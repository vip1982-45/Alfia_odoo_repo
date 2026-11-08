from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

G = 'alfia_security.group_alfia_'


class AlfiaRestrictionRule(models.Model):
    """What a horse must satisfy to be entered in an event. Checks only warn or block; they never decide fitness."""
    _name = 'alfia.restriction.rule'
    _description = 'Entry restriction rule'
    _order = 'name'

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company, required=True)
    sex = fields.Selection([('female', 'Female'), ('male', 'Male'), ('gelding', 'Gelding')], string='Only this sex')
    min_age_years = fields.Float()
    max_age_years = fields.Float()
    require_fitness = fields.Boolean(default=True, help="The horse needs a current 'cleared' fitness decision from the vet.")
    forbid_withdrawal = fields.Boolean(default=True, help="Block horses still in a medicine withdrawal period.")
    forbid_pregnant = fields.Boolean(default=True)
    forbid_quarantine = fields.Boolean(default=True)

    def _problems(self, horse, on_date):
        self.ensure_one()
        out = []
        if self.sex and horse.sex != self.sex:
            out.append(self.env._("Only %s horses may enter.", dict(self._fields['sex']._description_selection(self.env))[self.sex].lower()))
        if (self.min_age_years or self.max_age_years) and not horse.birthdate:
            out.append(self.env._("The birthdate is unknown, so the age rule cannot be checked."))
        elif horse.birthdate:
            age = (on_date - horse.birthdate).days / 365.25
            if self.min_age_years and age < self.min_age_years:
                out.append(self.env._("Too young (%(age).1f years; minimum %(min).1f).", age=age, min=self.min_age_years))
            if self.max_age_years and age > self.max_age_years:
                out.append(self.env._("Too old (%(age).1f years; maximum %(max).1f).", age=age, max=self.max_age_years))
        if horse.status != 'active':
            out.append(self.env._("The horse is not active."))
        if self.require_fitness:
            fit = self.env['alfia.clearance'].sudo().search([('horse_id', '=', horse.id), ('kind', '=', 'fitness')], order='date desc, id desc', limit=1)
            if not fit or fit.state != 'cleared':
                out.append(self.env._("No current fitness clearance from the vet."))
            elif fit.restricted_until and fit.restricted_until >= on_date:
                out.append(self.env._("Fitness restricted until %s.", fit.restricted_until))
        if self.forbid_withdrawal and self.env['alfia.treatment'].sudo().search_count([('horse_id', '=', horse.id), ('withdrawal_until', '>=', on_date)]):
            out.append(self.env._("Still in a medicine withdrawal period."))
        if self.forbid_pregnant and horse.reproductive_state == 'pregnant':
            out.append(self.env._("The mare is pregnant."))
        if self.forbid_quarantine and self.env['alfia.quarantine'].sudo().search_count([('horse_id', '=', horse.id), ('state', '=', 'active')]):
            out.append(self.env._("The horse is in quarantine."))
        return out


class AlfiaEvent(models.Model):
    _name = 'alfia.event'
    _description = 'Competition event'
    _inherit = ['mail.thread']
    _order = 'date_start desc'

    name = fields.Char(required=True)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company, required=True)
    date_start = fields.Date(required=True)
    date_end = fields.Date()
    venue = fields.Char()
    discipline = fields.Char()
    rule_id = fields.Many2one('alfia.restriction.rule', string='Entry rules')
    entry_ids = fields.One2many('alfia.event.entry', 'event_id')
    entry_count = fields.Integer(compute='_compute_entry_count')

    @api.constrains('date_start', 'date_end')
    def _check_dates(self):
        for rec in self:
            if rec.date_end and rec.date_end < rec.date_start:
                raise ValidationError(self.env._("The event ends before it starts."))

    def _compute_entry_count(self):
        for rec in self:
            rec.entry_count = len(rec.entry_ids)


class AlfiaEventEntry(models.Model):
    _name = 'alfia.event.entry'
    _description = 'Event entry'
    _inherit = ['alfia.horse.record', 'mail.thread']
    _order = 'event_id, id'

    event_id = fields.Many2one('alfia.event', required=True, ondelete='cascade', index=True)
    rider_id = fields.Many2one('res.partner', string='Rider')
    state = fields.Selection([('draft', 'Draft'), ('pending', 'Pending review'), ('approved', 'Approved'), ('rejected', 'Rejected')], default='draft', required=True, tracking=True)
    issues = fields.Text(readonly=True, copy=False, help="Problems found by the last check.")
    vet_confirmed = fields.Boolean(readonly=True, copy=False)
    vet_id = fields.Many2one('res.users', readonly=True, copy=False)
    decided_by_id = fields.Many2one('res.users', readonly=True, copy=False)

    _entry_uniq = models.Constraint('unique(event_id, horse_id)', 'This horse is already entered in the event.')

    def action_check(self):
        for rec in self:
            rule = rec.event_id.rule_id
            problems = rule._problems(rec.horse_id, rec.event_id.date_start) if rule else []
            rec.issues = '\n'.join(problems)
        return True

    def action_submit(self):
        for rec in self.filtered(lambda r: r.state == 'draft'):
            rec.action_check()
            rec.state = 'pending'
            rec.vet_confirmed = False

    def action_vet_confirm(self):
        if not (self.env.su or self.env.user.has_group(G + 'vet_user')):
            raise AccessError(self.env._("Only veterinary staff can confirm fitness for an entry."))
        for rec in self.filtered(lambda r: r.state == 'pending'):
            rec.write({'vet_confirmed': True, 'vet_id': self.env.user.id})

    def action_approve(self):
        if not (self.env.su or self.env.user.has_group(G + 'training_manager')):
            raise AccessError(self.env._("Only a competition manager can approve entries."))
        for rec in self.filtered(lambda r: r.state == 'pending'):
            rec.action_check()
            if rec.issues:
                raise UserError(self.env._("%(horse)s cannot be approved:\n%(issues)s", horse=rec.horse_id.name, issues=rec.issues))
            if rec.event_id.rule_id.require_fitness and not rec.vet_confirmed:
                raise UserError(self.env._("The vet must confirm %s first.", rec.horse_id.name))
            rec.write({'state': 'approved', 'decided_by_id': self.env.user.id})

    def action_reject(self):
        if not (self.env.su or self.env.user.has_group(G + 'training_manager')):
            raise AccessError(self.env._("Only a competition manager can reject entries."))
        self.filtered(lambda r: r.state in ('pending', 'draft')).write({'state': 'rejected', 'decided_by_id': self.env.user.id})
