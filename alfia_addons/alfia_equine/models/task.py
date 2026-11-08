from odoo import api, fields, models
from odoo.exceptions import UserError

TASK_KINDS = [
    ('pregnancy_check', 'Pregnancy check'), ('pregnancy_vaccine', 'Pregnancy vaccination'), ('foaling_prep', 'Foaling preparation'),
    ('heat_alert', 'Heat in pregnant mare'), ('teasing', 'Teasing'), ('health_due', 'Health care due'), ('vet_follow_up', 'Vet follow-up'),
    ('dental', 'Dental check'), ('farrier', 'Farrier'), ('exam', 'Examination'), ('other', 'Other')]


class AlfiaTask(models.Model):
    _name = 'alfia.task'
    _description = 'Work item'
    _inherit = ['mail.thread']
    _order = 'due_date, urgent desc, id'

    name = fields.Char(required=True)
    horse_id = fields.Many2one('alfia.horse', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one('res.company', related='horse_id.company_id', store=True)
    kind = fields.Selection(TASK_KINDS, required=True, default='other', index=True)
    due_date = fields.Date(required=True, index=True, tracking=True)
    state = fields.Selection([('todo', 'To do'), ('done', 'Done'), ('deferred', 'Deferred'), ('cancelled', 'Cancelled')],
                             default='todo', required=True, index=True, tracking=True)
    overdue = fields.Boolean(compute='_compute_overdue', search='_search_overdue')
    urgent = fields.Boolean(help="Shown first on the workboard.")
    assigned_user_id = fields.Many2one('res.users', string='Assigned to')
    assigned_group_id = fields.Many2one('res.groups', string='Assigned function')
    key = fields.Char(copy=False, index=True, help="Stable identity so the same rule never creates the same task twice.")
    rule_id = fields.Many2one('alfia.breeding.rule', string='Rule set used', ondelete='set null')
    notes = fields.Text()
    done_by_id = fields.Many2one('res.users', readonly=True)
    done_date = fields.Datetime(readonly=True)

    _key_uniq = models.Constraint('unique(key)', 'This work item already exists.')

    @api.depends('due_date', 'state')
    def _compute_overdue(self):
        today = fields.Date.context_today(self)
        for task in self:
            task.overdue = task.state in ('todo', 'deferred') and bool(task.due_date) and task.due_date < today

    def _search_overdue(self, operator, value):
        today = fields.Date.context_today(self)
        if operator in ('=', '!='):
            wanted = bool(value) == (operator == '=')
        elif operator in ('in', 'not in'):
            wanted = (True in value) == (operator == 'in')
        else:
            raise UserError(self.env._("Unsupported search on 'overdue'."))
        domain = [('state', 'in', ('todo', 'deferred')), ('due_date', '<', today)]
        return domain if wanted else ['!'] + domain

    # ------------------------------------------------------------------ idempotent generation
    @api.model
    def _alfia_upsert(self, key, horse, due_date, name, kind, urgent=False, rule=None, group=None):
        """Create the work item for `key`, or move it if its date changed. Finished or cancelled items are never touched."""
        task = self.with_context(active_test=False).sudo().search([('key', '=', key)], limit=1)
        if task:
            if task.state in ('todo', 'deferred') and task.due_date != due_date:
                task.write({'due_date': due_date, 'name': name})
                return task, 'moved'
            return task, 'kept'
        task = self.sudo().create({'name': name, 'horse_id': horse.id, 'due_date': due_date, 'kind': kind, 'key': key, 'urgent': urgent,
                                   'rule_id': rule.id if rule else False, 'assigned_group_id': group.id if group else False})
        return task, 'created'

    @api.model
    def _alfia_key_domain(self, key_prefix):
        """Keys equal to `key_prefix`, or below it (`prefix:...`). 'quarantine:1' must not match 'quarantine:12'."""
        base = key_prefix.rstrip(':')
        escaped = base.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        return ['|', ('key', '=', base), ('key', '=like', escaped + ':%')]

    @api.model
    def _alfia_cancel_pending(self, key_prefix, reason):
        pending = self.sudo().search(self._alfia_key_domain(key_prefix) + [('state', 'in', ('todo', 'deferred'))])
        for task in pending:
            task.message_post(body=reason)
        pending.write({'state': 'cancelled'})
        return pending

    # ------------------------------------------------------------------ actions
    def action_done(self):
        for task in self:
            if task.state in ('done', 'cancelled'):
                raise UserError(self.env._("This work item is already %s.", task.state))
        self.write({'state': 'done', 'done_by_id': self.env.user.id, 'done_date': fields.Datetime.now()})

    def action_cancel(self):
        if any(t.state == 'done' for t in self):
            raise UserError(self.env._("Completed work is never cancelled."))
        self.state = 'cancelled'

    def action_defer(self):
        self.state = 'deferred'

    def action_reopen(self):
        if any(t.state == 'done' for t in self):
            raise UserError(self.env._("Completed work cannot be reopened."))
        self.state = 'todo'

    def unlink(self):
        if any(t.state == 'done' for t in self) and not self.env.su:
            raise UserError(self.env._("Completed work is kept as a record and cannot be deleted."))
        return super().unlink()
