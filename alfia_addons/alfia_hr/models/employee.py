from dateutil.relativedelta import relativedelta

from odoo import api, fields, models

DOCUMENT_FIELDS = ('alfia_qid_expiry', 'alfia_passport_expiry', 'alfia_health_card_expiry')
NOTICE_DAYS = 30


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    alfia_qid_expiry = fields.Date('Qatar ID expiry', groups='hr.group_hr_user', tracking=True)
    alfia_passport_expiry = fields.Date('Passport expiry', groups='hr.group_hr_user', tracking=True)
    alfia_health_card_expiry = fields.Date('Health card expiry', groups='hr.group_hr_user', tracking=True)
    alfia_access_revoked = fields.Boolean('System access removed', readonly=True, copy=False, groups='hr.group_hr_user')

    # ------------------------------------------------------------------ onboarding
    @api.model_create_multi
    def create(self, vals_list):
        employees = super().create(vals_list)
        plan = self.env.ref('alfia_hr.plan_onboarding', raise_if_not_found=False)
        if plan and plan.active and not self.env.context.get('alfia_no_onboarding'):
            employees._alfia_launch_plan(plan)
        return employees

    def _alfia_launch_plan(self, plan):
        """Start an activity plan (onboarding or offboarding) on these employees; the person who starts it is asked for
        the steps assigned 'at launch' (normally HR)."""
        if not self:
            return
        self.env['mail.activity.schedule'].sudo().create({
            'res_model': 'hr.employee', 'res_ids': repr(self.ids), 'plan_id': plan.id,
            'plan_on_demand_user_id': self.env.user.id if not self.env.su else self.env.ref('base.user_admin').id,
        }).action_schedule_plan()

    # ------------------------------------------------------------------ offboarding
    def action_archive(self):
        leaving = self.filtered('active')
        res = super().action_archive()
        leaving._alfia_revoke_access()
        plan = self.env.ref('alfia_hr.plan_offboarding', raise_if_not_found=False)
        if plan and plan.active:
            leaving.with_context(active_test=False)._alfia_launch_plan(plan)
        return res

    def _alfia_revoke_access(self, reason=None):
        """A person who leaves loses every access at once: their access profiles are removed and their account is
        switched off. Recorded in the security audit trail."""
        Audit = self.env['alfia.security.audit']
        for employee in self.sudo().with_context(active_test=False):
            user = employee.user_id
            if user and (user.active or user.alfia_profile_ids):
                user.with_context(alfia_sync=True).write({'alfia_profile_ids': [(5, 0, 0)]})
                user._alfia_sync_profile_groups()
                if user.active and user != self.env.user:
                    user.with_context(alfia_sync=True).write({'active': False})
                Audit._alfia_log('user.offboarded', 'res.users', user.login,
                                 reason or "Employee %s left: access profiles removed and account switched off." % employee.name)
            employee.alfia_access_revoked = True

    # ------------------------------------------------------------------ document expiry
    @api.model
    def _cron_alfia_document_expiry(self):
        """A to-do for HR before a Qatar ID, passport or health card expires (once per document and date)."""
        today = fields.Date.context_today(self)
        limit = today + relativedelta(days=NOTICE_DAYS)
        todo = self.env.ref('mail.mail_activity_data_todo')
        labels = {'alfia_qid_expiry': self.env._("Qatar ID"), 'alfia_passport_expiry': self.env._("Passport"),
                  'alfia_health_card_expiry': self.env._("Health card")}
        for field in DOCUMENT_FIELDS:
            label = labels[field]
            for employee in self.sudo().search([(field, '!=', False), (field, '<=', limit), ('active', '=', True)]):
                date = employee[field]
                summary = self.env._("%(doc)s of %(name)s expires on %(date)s", doc=label, name=employee.name, date=date)
                if employee.activity_ids.filtered(lambda a: a.summary == summary):
                    continue
                responsible = employee.hr_responsible_id if 'hr_responsible_id' in employee._fields else False
                employee.activity_schedule(activity_type_id=todo.id, summary=summary, date_deadline=min(date, limit),
                                           user_id=(responsible or employee.parent_id.user_id or self.env.ref('base.user_admin')).id)
