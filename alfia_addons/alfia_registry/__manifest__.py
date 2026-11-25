{
    'name': 'Alfia Registrations, External Semen and Incidents',
    'version': '20.0.1.0.0',
    'category': 'Services',
    'summary': 'Registration, DNA, passport and breeding-certificate requests; semen held at external centres reconciled '
               'from their statements; incidents with investigation, actions and closure approval',
    'description': 'RFP REQ-HOR-03, REQ-SEM-04 and REQ-SAF-01. See README.md.',
    'author': 'Alfia',
    'license': 'LGPL-3',
    'depends': ['alfia_controls', 'hr'],
    'data': [
        'security/registry_groups.xml',
        'security/ir.access.csv',
        'data/registry_data.xml',
        'views/registration_views.xml',
        'views/semen_views.xml',
        'views/incident_views.xml',
        'views/menus.xml',
    ],
    'installable': True,
}
