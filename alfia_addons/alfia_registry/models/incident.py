from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.alfia_security.flow import workflow

MANAGER = 'alfia_registry.group_incident_manager'


class AlfiaIncident(models.Model):
    """An incident (horse or staff injury, damage, near miss, biosecurity) with its severity, investigation, corrective
    actions with owners and due dates, evidence, and a closure approved by a manager who did not investigate it."""
    _name = 'alfia.incident'
    _description = 'Incident'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'alfia.guarded.mixin']
    _order = 'date desc, id desc'
    _alfia_guarded = ('state', 'reported_by_id', 'closed_by_id', 'closed_date')
    _alfia_guarded_create = {'reported_by_id': '*'}

    name = fields.Char(readonly=True, copy=False, default='New')
    date = fields.Datetime(required=True, default=fields.Datetime.now, tracking=True)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company, required=True)
    kind = fields.Selection([('horse_injury', 'Horse injury or illness'), ('staff_injury', 'Staff injury'),
                             ('damage', 'Property or vehicle damage'), ('near_miss', 'Near miss'), ('biosecurity', 'Biosecurity'),
                             ('security', 'Security'), ('other', 'Other')], required=True, default='horse_injury', tracking=True)
    severity = fields.Selection([('low', 'Low'), ('medium', 'Medium'), ('high', 'High'), ('critical', 'Critical')],
                                required=True, default='low', tracking=True)
    location_id = fields.Many2one('stock.location', string='Where', domain="[('usage', '=', 'internal')]")
    horse_id = fields.Many2one('alfia.horse', string='Horse involved')
    employee_id = fields.Many2one('hr.employee', string='Person involved')
    description = fields.Text(required=True)
    immediate_action = fields.Text(string='Immediate action taken')
    reported_by_id = fields.Many2one('res.users', default=lambda self: self.env.user, readonly=True)
    investigator_id = fields.Many2one('res.users', tracking=True)
    investigation = fields.Text(string='Investigation findings')
    root_cause = fields.Text()
    action_ids = fields.One2many('alfia.incident.action', 'incident_id', string='Corrective actions')
    open_action_count = fields.Integer(compute='_compute_open_action_count')
    closure_notes = fields.Text()
    state = fields.Selection([('reported', 'Reported'), ('investigating', 'Under investigation'), ('actions', 'Actions in progress'),
                              ('to_close', 'Waiting for closure approval'), ('closed', 'Closed')],
                             default='reported', required=True, tracking=True)
    closed_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    closed_date = fields.Datetime(readonly=True, copy=False)

    @api.depends('action_ids.state')
    def _compute_open_action_count(self):
        for rec in self:
            rec.open_action_count = len(rec.action_ids.filtered(lambda a: a.state == 'open'))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('alfia.incident') or 'New'
        incidents = super().create(vals_list)
        for rec in incidents.filtered(lambda r: r.severity in ('high', 'critical')):
            managers = self.env.ref(MANAGER).sudo().all_user_ids.filtered(lambda u: not u.share)[:1]
            if managers:
                rec.activity_schedule('mail.mail_activity_data_todo', user_id=managers.id,
                                      summary=self.env._("%s incident reported: review and assign an investigator", rec.severity))
        return incidents

    def write(self, vals):
        if not self.env.su and any(r.state == 'closed' for r in self) and set(vals) - {'message_follower_ids'}:
            raise UserError(self.env._("A closed incident cannot be changed."))
        return super().write(vals)

    def _check_manager(self):
        if not (self.env.su or self.env.user.has_group(MANAGER)):
            raise AccessError(self.env._("Only incident managers do this."))

    def _set(self, state, **extra):
        with workflow():
            self.write(dict(extra, state=state))

    def action_investigate(self):
        self._check_manager()
        for rec in self:
            if rec.state != 'reported':
                raise UserError(self.env._("%s is already being handled.", rec.name))
            if not rec.investigator_id:
                raise UserError(self.env._("Choose the investigator for %s first.", rec.name))
            rec._set('investigating')
            rec.activity_schedule('mail.mail_activity_data_todo', user_id=rec.investigator_id.id,
                                  summary=self.env._("Investigate %s", rec.name))

    def action_findings_done(self):
        for rec in self:
            if rec.state != 'investigating':
                raise UserError(self.env._("%s is not under investigation.", rec.name))
            if self.env.user not in (rec.investigator_id,) and not (self.env.su or self.env.user.has_group(MANAGER)):
                raise AccessError(self.env._("Only the investigator or an incident manager records the findings."))
            if not rec.investigation or (rec.severity in ('high', 'critical') and not rec.root_cause):
                raise UserError(self.env._("Record the investigation findings (and the root cause for a high or critical incident)."))
            rec._set('actions' if rec.action_ids else 'to_close')

    def action_request_closure(self):
        for rec in self:
            if rec.state not in ('actions', 'investigating'):
                raise UserError(self.env._("%s is not ready for closure.", rec.name))
            if rec.open_action_count:
                raise UserError(self.env._("%(count)s corrective action(s) of %(name)s are still open.", count=rec.open_action_count, name=rec.name))
            if not rec.investigation:
                raise UserError(self.env._("Record the investigation findings first."))
            rec._set('to_close')

    def action_close(self):
        self._check_manager()
        for rec in self:
            if rec.state != 'to_close':
                raise UserError(self.env._("Request closure of %s first.", rec.name))
            if rec.open_action_count:
                raise UserError(self.env._("%(count)s corrective action(s) of %(name)s are still open.", count=rec.open_action_count, name=rec.name))
            if self.env.user in (rec.investigator_id, rec.reported_by_id) and not self.env.su:
                raise UserError(self.env._("You reported or investigated %s, so another incident manager approves its closure.", rec.name))
            if not rec.closure_notes:
                raise UserError(self.env._("Write the closure notes (what was done and how it was checked)."))
            rec._set('closed', closed_by_id=self.env.user.id, closed_date=fields.Datetime.now())
            rec.activity_ids.action_done()

    def action_reopen(self):
        self._check_manager()
        for rec in self.filtered(lambda r: r.state in ('to_close',)):
            rec._set('actions' if rec.action_ids else 'investigating')


class AlfiaIncidentAction(models.Model):
    _name = 'alfia.incident.action'
    _description = 'Incident corrective action'
    _order = 'due_date, id'

    incident_id = fields.Many2one('alfia.incident', required=True, ondelete='cascade', index=True)
    name = fields.Char(string='Action', required=True)
    owner_id = fields.Many2one('res.users', required=True)
    due_date = fields.Date(required=True)
    state = fields.Selection([('open', 'Open'), ('done', 'Done')], default='open', required=True)
    done_date = fields.Date(readonly=True)
    evidence = fields.Char(help="What shows the action was done (attach files to the incident).")

    @api.model_create_multi
    def create(self, vals_list):
        actions = super().create(vals_list)
        for action in actions:
            action.incident_id.sudo().activity_schedule('mail.mail_activity_data_todo', user_id=action.owner_id.id,
                                                        date_deadline=action.due_date, summary=action.name)
        return actions

    def write(self, vals):
        if not self.env.su and any(a.incident_id.state == 'closed' for a in self):
            raise UserError(self.env._("A closed incident's actions cannot be changed."))
        return super().write(vals)

    def action_done(self):
        for action in self:
            if self.env.user != action.owner_id and not (self.env.su or self.env.user.has_group(MANAGER)):
                raise AccessError(self.env._("Only the action's owner or an incident manager marks it done."))
            if not action.evidence:
                raise UserError(self.env._("Describe the evidence that the action was done."))
            action.write({'state': 'done', 'done_date': fields.Date.context_today(self)})
            action.incident_id.sudo().message_post(body=self.env._("Action done by %(user)s: %(action)s (%(evidence)s)",
                                                                   user=self.env.user.name, action=action.name, evidence=action.evidence))
