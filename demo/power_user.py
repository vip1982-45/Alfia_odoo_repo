# Run with:  odoo-bin shell -c <conf> -d <db> < demo/power_user.py      (safe to run again)
# Creates the trial "power user": every business function in every site, store, warehouse, journal and
# department, with no system administration (no Settings, no technical menus, no user or security administration).
import os

PROFILE_GROUPS = [
    # Horses, breeding, training, veterinary, controlled stock
    'alfia_security.group_alfia_breeding_manager', 'alfia_security.group_alfia_training_manager',
    'alfia_security.group_alfia_vet_manager', 'alfia_security.group_alfia_medicine_custodian',
    'alfia_security.group_alfia_semen_custodian', 'alfia_security.group_alfia_dispatch_admin_approver',
    'alfia_security.group_alfia_dispatch_vet_approver', 'alfia_security.group_alfia_stock_adjust_approver',
    # Inventory, purchasing, sales, accounting
    'stock.group_stock_manager', 'purchase.group_purchase_manager', 'sales_team.group_sale_manager',
    'account.group_account_manager', 'product.group_product_manager',
    # HR (the HR manager group also gives every department)
    'hr.group_hr_manager', 'hr_holidays.group_hr_holidays_manager', 'hr_recruitment.group_hr_recruitment_manager',
    'hr_expense.group_hr_expense_manager', 'hr_attendance.group_hr_attendance_manager',
    # Scope: everything
    'alfia_security.group_alfia_all_locations', 'alfia_security.group_alfia_all_warehouses',
    'alfia_security.group_alfia_all_journals',
    # No segregation-of-duties exemption: since 24 Nov 2026 nobody approves a document they created, this account
    # included, so a second account approves what power.user creates.
]
FORBIDDEN = ['base.group_system', 'base.group_erp_manager', 'alfia_security.group_alfia_security_officer']

groups = env['res.groups']
for xmlid in PROFILE_GROUPS:
    g = env.ref(xmlid, raise_if_not_found=False)
    if g:
        groups |= g
    else:
        print('power user: group not found, skipped:', xmlid)

Profile = env['alfia.access.profile'].sudo()
profile = Profile.search([('name', '=', 'تجربة: كل الوظائف · Trial: all business functions')], limit=1)
vals = {'name': 'تجربة: كل الوظائف · Trial: all business functions', 'group_ids': [(6, 0, groups.ids)]}
if profile:
    profile.write(vals)
else:
    profile = Profile.create(vals)

Users = env['res.users'].sudo().with_context(no_reset_password=True)
user = Users.with_context(active_test=False).search([('login', '=', 'power.user')], limit=1)
password = os.environ.get('ALFIA_DEMO_PASSWORD') or os.environ.get('DEMO_PASSWORD')
if not user:
    user = Users.create({
        'name': 'مستخدم تجريبي شامل · Trial power user',
        'login': 'power.user',
        'lang': 'ar_001' if env['res.lang'].search([('code', '=', 'ar_001'), ('active', '=', True)]) else 'en_US',
        'group_ids': [(6, 0, [env.ref('base.group_user').id])],
    })
user.write({'active': True, 'alfia_profile_ids': [(6, 0, [profile.id])]})
if password:
    user.write({'password': password})
user._alfia_sync_profile_groups()

# An employee record, so time off, expenses and attendance work for this account too.
if 'hr.employee' in env and not env['hr.employee'].sudo().with_context(active_test=False).search([('user_id', '=', user.id)], limit=1):
    dept = env.ref('alfia_hr.dept_management', raise_if_not_found=False)
    env['hr.employee'].sudo().with_context(alfia_no_onboarding=True).create(
        {'name': user.name, 'user_id': user.id, 'department_id': dept.id if dept else False})

bad = [x for x in FORBIDDEN if user.has_group(x)]
env.cr.commit()
print('Power user ready: power.user with %d groups%s' % (len(user.group_ids), ('; WARNING has ' + ', '.join(bad)) if bad else ', no administration rights'))
