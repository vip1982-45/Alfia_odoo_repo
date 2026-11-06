{
    'name': 'Alfia Equine Operations',
    'version': '20.0.1.0.0',
    'category': 'Services',
    'summary': 'Billing, horse sales, boarding, feed, quarantine, medicine issue, breeding contracts, event entries, facilities, reports',
    'author': 'Alfia',
    'license': 'LGPL-3',
    'depends': ['alfia_equine', 'account', 'sale_management'],
    'data': [
        'security/ir.access.csv',
        'data/alfia_ops_data.xml',
        'views/ops_views.xml',
        'views/generated_views.xml',
        'views/menus.xml',
    ],
    'installable': True,
}
