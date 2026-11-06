from odoo.exceptions import AccessError

G = 'alfia_security.group_alfia_'
BILLING_MANAGERS = (G + 'breeding_manager', G + 'training_manager', G + 'vet_manager', 'account.group_account_invoice')


def require_any(env, xmlids, what):
    """The current user must belong to at least one of the groups. Superuser calls (system automation) pass."""
    if env.su or any(env.user.has_group(x) for x in xmlids):
        return
    raise AccessError(env._("Only %s can do this.", what))
