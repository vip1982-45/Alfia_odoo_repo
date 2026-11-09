def post_init_hook(env):
    env['res.users'].with_context(active_test=False).search([])._alfia_grant_self_service()
