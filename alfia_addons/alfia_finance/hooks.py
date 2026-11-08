def post_init_hook(env):
    env['res.company'].search([])._alfia_finance_setup()
    # Horses that exist before this module get their analytic account too.
    env['alfia.horse'].with_context(active_test=False).search([('analytic_account_id', '=', False)])._alfia_ensure_analytic_account()
