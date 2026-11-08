{
    'name': 'Alfia Finance and Supply Chain',
    'version': '20.0.1.0.0',
    'category': 'Accounting',
    'summary': 'Trial balance, general ledger, aged balances, horse cost allocation, perpetual stock valuation, pick-and-ship warehouse',
    'description': 'Finance reports Odoo Community lacks, cost allocation per horse, and a stud-ready warehouse and valuation setup. See README.md.',
    'author': 'Alfia',
    'license': 'LGPL-3',
    'depends': ['alfia_equine_ops', 'stock_account', 'purchase_stock', 'sale_stock', 'stock_landed_costs', 'purchase_requisition', 'analytic'],
    'data': [
        'security/ir.access.csv',
        'data/finance_data.xml',
        'views/finance_views.xml',
    ],
    'post_init_hook': 'post_init_hook',
    'installable': True,
}
