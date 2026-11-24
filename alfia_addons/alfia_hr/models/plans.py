from odoo import models


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
