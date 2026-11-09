from dateutil.relativedelta import relativedelta

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install', 'alfia_hr')
class HrCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        ref = env.ref
        cls.today = fields.Date.context_today(env['hr.employee'])
        Dept = env['hr.department']
        cls.dept_a = Dept.create({'name': 'HR test Barn A', 'parent_id': ref('alfia_hr.dept_stud_operations').id})
        cls.dept_b = Dept.create({'name': 'HR test Barn B', 'parent_id': ref('alfia_hr.dept_stud_operations').id})
        Profile = env['alfia.access.profile']
        line_a = Profile.create({'name': 'HR test line manager A', 'group_ids': [Command.set(ref('alfia_hr.profile_line_manager').group_ids.ids)],
                                 'department_ids': [Command.set(cls.dept_a.ids)]})
        officer_b = Profile.create({'name': 'HR test officer B', 'group_ids': [Command.set(ref('alfia_hr.profile_hr_officer').group_ids.ids)],
                                    'department_ids': [Command.set(cls.dept_b.ids)]})
        Users = env['res.users'].with_context(no_reset_password=True)

        def user(login, profiles=(), groups=()):
            return Users.create({'name': login, 'login': f'{login}@hr.local', 'email': f'{login}@hr.local',
                                 'group_ids': [Command.link(ref(g).id) for g in ('base.group_user',) + tuple(groups)],
                                 'alfia_profile_ids': [Command.set([p.id for p in profiles])]})
        cls.u_groom = user('hrgroom')
        cls.u_manager = user('hrmanagera', [line_a])
        cls.u_officer_b = user('hrofficerb', [officer_b])
        cls.u_hr = user('hrmanager', [ref('alfia_hr.profile_hr_manager')])
        cls.u_hr2 = user('hrmanager2', [ref('alfia_hr.profile_hr_manager')])
        Emp = env['hr.employee'].with_context(alfia_no_onboarding=True)
        cls.e_manager = Emp.create({'name': 'Manager A', 'user_id': cls.u_manager.id, 'department_id': cls.dept_a.id})
        cls.e_groom = Emp.create({'name': 'Groom A', 'user_id': cls.u_groom.id, 'department_id': cls.dept_a.id, 'parent_id': cls.e_manager.id,
                                  'leave_manager_id': cls.u_manager.id, 'expense_manager_id': cls.u_manager.id})
        cls.e_b = Emp.create({'name': 'Groom B', 'department_id': cls.dept_b.id})
        cls.e_hr = Emp.create({'name': 'HR Manager', 'user_id': cls.u_hr.id, 'department_id': ref('alfia_hr.dept_management').id,
                               'leave_manager_id': cls.u_hr.id})
        cls.annual = ref('alfia_hr.leave_annual')

    def _allocate(self, employee, days=21):
        allocation = self.env['hr.leave.allocation'].create({
            'name': 'Annual leave', 'employee_id': employee.id, 'work_entry_type_id': self.annual.id, 'number_of_days': days,
            'date_from': self.today - relativedelta(months=1)})
        allocation.with_user(self.u_hr2).action_approve()
        return allocation

    def _next_workday(self, start):
        day = start
        while day.weekday() >= 4:   # Friday and Saturday are the weekend in Qatar; Monday-Thursday are safe on any calendar
            day += relativedelta(days=1)
        return day


@tagged('post_install', '-at_install', 'alfia_hr')
class TestTimeOff(HrCase):

    def test_qatar_leave_types(self):
        codes = set(self.env['hr.work.entry.type'].search([('code', '=like', 'QA%')]).mapped('code'))
        self.assertTrue({'QAANNUAL', 'QASICK', 'QAHAJJ', 'QAMATERN', 'QACOMP', 'QAUNPAID'} <= codes)
        self.assertEqual(self.annual.leave_validation_type, 'both', "annual leave: line manager, then HR")

    def test_annual_leave_two_approvals(self):
        self._allocate(self.e_groom)
        day = self._next_workday(self.today + relativedelta(days=14))
        leave = self.env['hr.leave'].with_user(self.u_groom).create({
            'employee_id': self.e_groom.id, 'work_entry_type_id': self.annual.id, 'request_date_from': day, 'request_date_to': day})
        self.assertEqual(leave.state, 'confirm')
        leave.with_user(self.u_manager).action_approve()
        self.assertEqual(leave.state, 'validate1', "first approval: the line manager")
        leave.with_user(self.u_hr).action_approve()
        self.assertEqual(leave.state, 'validate', "second approval: HR")

    def test_nobody_approves_their_own_time_off(self):
        self._allocate(self.e_hr)
        day = self._next_workday(self.today + relativedelta(days=20))
        leave = self.env['hr.leave'].with_user(self.u_hr).create({
            'employee_id': self.e_hr.id, 'work_entry_type_id': self.annual.id, 'request_date_from': day, 'request_date_to': day})
        with self.assertRaises(UserError):
            leave.with_user(self.u_hr).action_approve()
        allocation = self.env['hr.leave.allocation'].with_user(self.u_hr).create({
            'name': 'Extra', 'employee_id': self.e_hr.id, 'work_entry_type_id': self.annual.id, 'number_of_days': 5})
        with self.assertRaises(UserError):
            allocation.with_user(self.u_hr).action_approve()


@tagged('post_install', '-at_install', 'alfia_hr')
class TestReimbursements(HrCase):

    def _expense(self, employee, user, amount=120.0):
        return self.env['hr.expense'].with_user(user).create({
            'name': 'Fuel for horse transport', 'employee_id': employee.id, 'product_id': self.env.ref('alfia_hr.expense_fuel').id,
            'total_amount_currency': amount, 'payment_mode': 'own_account'})

    def test_expense_flow_with_two_people_at_each_step(self):
        expense = self._expense(self.e_groom, self.u_groom)
        expense.with_user(self.u_groom).action_submit()
        self.assertEqual(expense.state, 'submitted')
        with self.assertRaises(UserError):
            expense.with_user(self.u_groom).action_approve()
        expense.with_user(self.u_manager).action_approve()
        self.assertIn(expense.state, ('approved', 'posted'))
        self.assertTrue(expense.account_move_id, "approving creates the accounting entry to reimburse")

    def test_own_approver_is_not_auto_approved(self):
        self.e_hr.expense_manager_id = self.u_hr
        expense = self._expense(self.e_hr, self.u_hr)
        expense.with_user(self.u_hr).action_submit()
        self.assertEqual(expense.state, 'submitted', "Odoo would approve it at once; under segregation of duties it waits")
        with self.assertRaises(UserError):
            expense.with_user(self.u_hr).action_approve()
        expense.with_user(self.u_hr2).action_approve()
        self.assertNotEqual(expense.state, 'submitted')


@tagged('post_install', '-at_install', 'alfia_hr')
class TestOnOffboarding(HrCase):

    def test_new_employee_gets_the_onboarding_plan(self):
        new = self.env['hr.employee'].with_user(self.u_hr).create({'name': 'New groom', 'department_id': self.dept_a.id, 'parent_id': self.e_manager.id})
        summaries = new.activity_ids.mapped('summary')
        self.assertTrue(any('Qatar ID' in s for s in summaries), summaries)
        self.assertTrue(any('induction' in s for s in summaries), summaries)
        self.assertGreaterEqual(len(summaries), 5)

    def test_leaving_removes_access_at_once(self):
        self.assertTrue(self.u_manager.has_group('hr_holidays.group_hr_holidays_responsible'))
        self.e_manager.with_user(self.u_hr).action_archive()
        self.assertFalse(self.u_manager.active, "the account is switched off")
        self.assertFalse(self.u_manager.alfia_profile_ids, "the access profiles are removed")
        self.assertFalse(self.u_manager.has_group('hr_holidays.group_hr_holidays_responsible'))
        self.assertTrue(self.env['alfia.security.audit'].search_count([('action', '=', 'user.offboarded'), ('target_ref', '=', self.u_manager.login)]))
        self.assertTrue(any('Collect keys' in s for s in self.e_manager.with_context(active_test=False).activity_ids.mapped('summary')))

    def test_registered_departure_removes_access_on_its_date(self):
        self.e_groom.version_id.date_version = self.today - relativedelta(years=1)
        departure = self.env['hr.employee.departure'].create({
            'employee_id': self.e_groom.id, 'departure_reason_id': self.env['hr.departure.reason'].search([], limit=1).id,
            'dismissal_date': self.today - relativedelta(days=2)})
        self.env['hr.employee.departure']._cron_apply_departure()     # Odoo's own daily job applies due departures
        self.assertTrue(departure.apply_date)
        self.assertFalse(self.e_groom.active)
        self.assertFalse(self.u_groom.active)
        self.assertFalse(self.u_groom.alfia_profile_ids)
        self.assertTrue(self.e_groom.with_context(active_test=False).alfia_access_revoked)

    def test_document_expiry_reminder_once(self):
        self.e_b.alfia_qid_expiry = self.today + relativedelta(days=10)
        Emp = self.env['hr.employee']
        Emp._cron_alfia_document_expiry()
        Emp._cron_alfia_document_expiry()
        reminders = self.e_b.activity_ids.filtered(lambda a: 'Qatar ID' in (a.summary or ''))
        self.assertEqual(len(reminders), 1)


@tagged('post_install', '-at_install', 'alfia_hr')
class TestHrScope(HrCase):

    def test_hr_staff_see_only_their_departments(self):
        seen = self.env['hr.employee'].with_user(self.u_officer_b).search([('id', 'in', (self.e_groom | self.e_b | self.e_manager).ids)])
        self.assertEqual(seen, self.e_b)
        with self.assertRaises(AccessError):
            self.e_groom.with_user(self.u_officer_b).read(['name'])

    def test_line_manager_sees_the_team_time_off(self):
        self._allocate(self.e_groom)
        day = self._next_workday(self.today + relativedelta(days=30))
        leave = self.env['hr.leave'].with_user(self.u_groom).create({
            'employee_id': self.e_groom.id, 'work_entry_type_id': self.annual.id, 'request_date_from': day, 'request_date_to': day})
        self.assertEqual(self.env['hr.leave'].with_user(self.u_manager).search([('id', '=', leave.id)]), leave)
        self.assertFalse(self.env['hr.leave'].with_user(self.u_officer_b).search([('id', '=', leave.id)]))

    def test_new_site_gets_a_department(self):
        admin = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'sysadm hr', 'login': 'sysadmhr@hr.local', 'group_ids': [Command.link(self.env.ref('base.group_system').id)]})
        wizard = self.env['alfia.site.setup'].with_user(admin).create({
            'site_name': 'HR Barn C', 'parent_location_id': self.env['stock.warehouse'].search([], limit=1).view_location_id.id,
            'template_ids': [Command.set(self.env.ref('alfia_security.profile_stable_staff').ids)]})
        wizard.action_create()
        department = self.env['hr.department'].search([('name', '=', 'HR Barn C')])
        self.assertTrue(department)
        self.assertEqual(wizard.created_profile_ids.department_ids, department)

    def test_a_manager_without_departments_still_sees_the_team(self):
        boss = self.env['res.users'].with_context(no_reset_password=True).create({'name': 'boss', 'login': 'boss@hr.local'})
        e_boss = self.env['hr.employee'].with_context(alfia_no_onboarding=True).create({'name': 'Boss', 'user_id': boss.id})
        self.e_b.write({'parent_id': e_boss.id, 'leave_manager_id': boss.id})
        self.assertTrue(boss.alfia_scope_departments, "being an approver makes Odoo give the Time Off Responsible group")
        self._allocate(self.e_b)
        self._allocate(self.e_groom)
        day = self._next_workday(self.today + relativedelta(days=40))
        Leave = self.env['hr.leave']
        b_leave = Leave.create({'employee_id': self.e_b.id, 'work_entry_type_id': self.annual.id, 'request_date_from': day, 'request_date_to': day})
        a_leave = Leave.create({'employee_id': self.e_groom.id, 'work_entry_type_id': self.annual.id, 'request_date_from': day, 'request_date_to': day})
        seen = Leave.with_user(boss).search([('id', 'in', (b_leave | a_leave).ids)])
        self.assertEqual(seen, b_leave, "a manager sees the team's requests, not other teams'")

    def test_hr_officer_hires_into_their_department(self):
        new = self.env['hr.employee'].with_user(self.u_officer_b).create({'name': 'New groom B', 'department_id': self.dept_b.id})
        self.assertEqual(new.department_id, self.dept_b)
        self.assertTrue(new.with_user(self.u_officer_b).read(['name']))
