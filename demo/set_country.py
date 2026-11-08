"""Run in an Odoo shell on a database with only `base` installed: the company is in Qatar, so installing accounting
loads the Qatar chart of accounts and the Qatari riyal."""
company = env.company  # noqa: F821 - provided by the Odoo shell
company.country_id = env.ref('base.qa')  # noqa: F821
qar = env['res.currency'].with_context(active_test=False).search([('name', '=', 'QAR')], limit=1)  # noqa: F821
if qar:
    qar.active = True
    company.currency_id = qar
env.cr.commit()  # noqa: F821
print('Company country: Qatar')
