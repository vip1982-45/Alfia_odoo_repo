{
    'name': 'Alfia Equine',
    'version': '20.0.1.0.0',
    'category': 'Services',
    'summary': 'Horses, pedigree, health, breeding schedule, foaling, semen and work items for a stud farm',
    'description': 'Horses, pedigree, health, breeding schedule, foaling, semen and work items for a stud farm. See README.md.',
    'author': 'Alfia',
    'license': 'LGPL-3',
    'depends': ['alfia_security', 'mail', 'product', 'stock'],
    'data': [
        'security/ir.access.csv',
        'data/alfia_equine_data.xml',
        'views/horse_views.xml',
        'views/generated_views.xml',
        'views/menus.xml',
    ],
    'installable': True,
    'application': True,
}
