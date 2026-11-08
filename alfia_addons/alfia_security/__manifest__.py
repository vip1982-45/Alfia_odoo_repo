{
    'name': 'Alfia Security',
    'version': '20.0.1.1.0',
    'category': 'Administration',
    'summary': 'Segregated accounts by site, function and sensitive stock, with segregation of duties and audit',
    'description': """
Role-and-scope security for a stud farm
=======================================
* Access profiles bundle functional groups with the sites (stock locations), warehouses and journals a user may work in.
* Scope is enforced by the server on every read and write, not by the menus.
* Product categories (semen, embryos, medicine...) can be restricted to one group, with optional dual approval before dispatch.
* Segregation of duties for purchase approval, vendor bills, outbound payments and inventory adjustments.
* Profile design (system administrators) and profile assignment (security officers) are separate duties; neither can escalate itself.
* Immutable audit trail of every security-relevant change.
* Enables two-factor authentication, a password policy and session time-out.
""",
    'author': 'Alfia',
    'license': 'LGPL-3',
    'depends': ['base', 'mail', 'product', 'stock', 'purchase', 'sale_management', 'account',
                'auth_totp', 'auth_password_policy', 'auth_timeout'],
    'data': [
        'security/alfia_security_groups.xml',
        'security/ir.access.csv',
        'data/alfia_security_data.xml',
        'views/alfia_access_profile_views.xml',
        'views/res_users_views.xml',
        'views/product_category_views.xml',
        'views/stock_picking_views.xml',
        'views/alfia_security_audit_views.xml',
        'views/res_company_views.xml',
        'views/menus.xml',
        'views/site_setup_views.xml',
    ],
    'installable': True,
    'application': False,
}
