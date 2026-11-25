from odoo import api, fields, models


class MailActivityPlanTemplate(models.Model):
    _inherit = 'mail.activity.plan.template'

    def _determine_responsible(self, on_demand_responsible, on_demand_responsible_fname, applied_on_record, on_demand_role=False):
        """No onboarding or offboarding step is left with nobody: a step for the employee's manager or for the employee
        goes, when there is no such user, to the employee's HR responsible, otherwise to whoever started the plan."""
        result = super()._determine_responsible(on_demand_responsible, on_demand_responsible_fname, applied_on_record,
                                                on_demand_role=on_demand_role)
        if result.get('responsible') or self.plan_id.res_model != 'hr.employee':
            return result
        employee = applied_on_record
        fallback = (employee.hr_responsible_id if 'hr_responsible_id' in employee._fields else False) or on_demand_responsible
        if not fallback:
            fallback = self.env.user if not self.env.su else self.env.ref('base.user_admin', raise_if_not_found=False)
        if fallback:
            result['responsible'] = fallback
            result['error'] = ''
            note = self.env._("assigned to %s because there is no user for this step", fallback.name)
            result['warning'] = '; '.join(filter(None, [result.get('warning'), note]))
        return result


class HrEmployeeDeparture(models.Model):
    """Scheduling a departure starts the farm's offboarding plan at once (not only on the leaving day), so the exit steps
    have time to be done; its steps get owners like any plan."""
    _inherit = 'hr.employee.departure'

    @api.model_create_multi
    def create(self, vals_list):
        departures = super().create(vals_list)
        plan = self.env.ref('alfia_hr.plan_offboarding', raise_if_not_found=False)
        if plan and plan.active:
            employees = departures.employee_id.sudo().filtered(lambda e: not e.alfia_offboarding_started)
            employees._alfia_start_offboarding(plan)
            for departure in departures.filtered(lambda d: d.employee_id in employees):
                departure.employee_id.message_post(body=self.env._("Offboarding plan started for the departure on %s.",
                                                                   departure.departure_date))
        return departures


class HrEmployeeOffboarding(models.Model):
    _inherit = 'hr.employee'

    alfia_offboarding_started = fields.Boolean(readonly=True, copy=False, groups='hr.group_hr_user')

    def _alfia_start_offboarding(self, plan):
        todo = self.sudo().filtered(lambda e: not e.alfia_offboarding_started).with_env(self.env)
        if todo:
            todo.with_context(active_test=False)._alfia_launch_plan(plan)
            todo.sudo().write({'alfia_offboarding_started': True})
