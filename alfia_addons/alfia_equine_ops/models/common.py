from odoo.exceptions import AccessError, UserError

G = 'alfia_security.group_alfia_'
BILLING_MANAGERS = (G + 'breeding_manager', G + 'training_manager', G + 'vet_manager', 'account.group_account_invoice')


def lock_after(records, vals, states, editable, what):
    """Refuse changes to anything but `editable` fields once a record has reached one of `states`."""
    if records.env.su:
        return
    locked = records.filtered(lambda r: r.state in states)
    if locked and set(vals) - set(editable):
        raise UserError(records.env._("%s can no longer be changed. Cancel it and create a new one instead.", what))


def sod_blocks(record, amount=None):
    """Whether segregation of duties stops the current user approving `record`, which they created."""
    env = record.env
    if env.su or record.create_uid != env.user:
        return False
    return env['alfia.sod.mixin']._alfia_sod_applies(amount, record.company_id)


def require_any(env, xmlids, what):
    """The current user must belong to at least one of the groups. Superuser calls (system automation) pass."""
    if env.su or any(env.user.has_group(x) for x in xmlids):
        return
    raise AccessError(env._("Only %s can do this.", what))
