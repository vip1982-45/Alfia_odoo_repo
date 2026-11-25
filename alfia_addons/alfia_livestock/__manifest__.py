{
    'name': 'Alfia Livestock Accounting and Budgets',
    'version': '20.0.1.0.0',
    'category': 'Accounting',
    'summary': 'Versioned budgets with commitments, actuals and control rules; livestock register, classification and '
               'approved valuations posted to the ledger; profit and loss per horse',
    'description': 'RFP REQ-BUD-01..03 and REQ-LIV-01..03. See README.md.',
    'author': 'Alfia',
    'license': 'LGPL-3',
    'depends': ['alfia_controls', 'hr', 'sale_stock', 'purchase_stock'],
    'data': [
        'security/ir.access.csv',
        'data/livestock_data.xml',
        'views/budget_views.xml',
        'views/livestock_views.xml',
        'views/horse_profit_views.xml',
        'views/feed_views.xml',
        'views/stud_extras_views.xml',
        'views/menus.xml',
    ],
    'installable': True,
}
