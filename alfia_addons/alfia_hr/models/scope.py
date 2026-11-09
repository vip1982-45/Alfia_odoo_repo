"""HR access by department: an HR officer or line manager for one stable sees only that stable's people.

Access profiles gain departments (next to their stock locations, warehouses and journals). People with an HR role who are
not in the 'All departments' group see the employees, time off, allocations, reimbursements and attendance of their
departments (and below), plus their own. Everyone always sees their own records."""
from odoo import Command, api, fields, models

HR_STAFF_GROUPS = ('hr.group_hr_user', 'hr_holidays.group_hr_holidays_user', 'hr_holidays.group_hr_holidays_responsible', 'hr_expense.group_hr_expense_team_approver',
                   'hr_attendance.group_hr_attendance_officer', 'hr_recruitment.group_hr_recruitment_interviewer')


class AlfiaAccessProfile(models.Model):
    _inherit = 'alfia.access.profile'

    department_ids = fields.Many2many('hr.department', 'alfia_profile_department_rel', 'profile_id', 'department_id',
                                      string='Departments', help="HR records of these departments (and their sub-departments).")

    def write(self, vals):
        res = super().write(vals)
        if 'department_ids' in vals:
            self.user_ids._compute_alfia_departments()
        return res


class ResUsers(models.Model):
    _inherit = 'res.users'

    alfia_department_ids = fields.Many2many('hr.department', 'alfia_user_department_rel', 'user_id', 'department_id',
                                            compute='_compute_alfia_departments', store=True, copy=False, string='Allowed departments')
    alfia_scope_departments = fields.Boolean(compute='_compute_alfia_scope_departments')

    @api.depends('alfia_profile_ids.department_ids', 'alfia_profile_ids.active')
    def _compute_alfia_departments(self):
        for user in self:
            user.alfia_department_ids = user.alfia_profile_ids.filtered('active').department_ids

    @api.depends('all_group_ids')
    def _compute_alfia_scope_departments(self):
        bypass = self.env.ref('alfia_hr.group_alfia_all_departments', raise_if_not_found=False)
        staff = self.env['res.groups']
        for xmlid in HR_STAFF_GROUPS:
            staff |= self.env.ref(xmlid, raise_if_not_found=False) or self.env['res.groups']
        for user in self:
            groups = user.all_group_ids
            user.alfia_scope_departments = bool(groups & staff) and not (bypass and bypass in groups)


class AlfiaSiteSetup(models.TransientModel):
    _inherit = 'alfia.site.setup'

    department_parent_id = fields.Many2one('hr.department', string='Department under',
                                           default=lambda self: self.env.ref('alfia_hr.dept_stud_operations', raise_if_not_found=False),
                                           help="The site's own department is created here; its profiles see the site's people.")

    def action_create(self):
        res = super().action_create()
        department = self.env['hr.department'].sudo().create({'name': self.site_name.strip(), 'parent_id': self.department_parent_id.id})
        self.created_profile_ids.sudo().write({'department_ids': [Command.link(department.id)]})
        return res
