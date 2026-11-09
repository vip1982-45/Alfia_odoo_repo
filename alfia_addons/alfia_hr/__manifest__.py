{
    'name': 'Alfia HR',
    'version': '20.0.1.0.0',
    'category': 'Human Resources',
    'summary': 'Employees, Qatar time off, onboarding and offboarding, reimbursements, attendance, recruitment and skills for the stud',
    'description': 'HR for the stud farm on Odoo Community HR apps, with Qatar leave rules, access removal on departure, '
                   'document-expiry reminders, segregation of duties and HR access by department. See README.md.',
    'author': 'Alfia',
    'license': 'LGPL-3',
    'depends': [
        'alfia_finance',
        'hr', 'hr_holidays', 'hr_expense', 'hr_attendance', 'hr_recruitment', 'hr_recruitment_survey', 'hr_skills',
        'hr_work_entry', 'hr_presence', 'hr_fleet', 'hr_maintenance', 'hr_timesheet', 'hr_gamification',
    ],
    'data': [
        'security/alfia_hr_groups.xml',
        'security/ir.access.csv',
        'data/hr_data.xml',
        'data/hr_profiles.xml',
        'views/hr_views.xml',
    ],
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'application': False,
}
