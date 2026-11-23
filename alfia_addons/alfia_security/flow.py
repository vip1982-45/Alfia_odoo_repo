"""Server-side trust that a client cannot claim.

Odoo's request context comes from the client: any key in `env.context` can be sent over JSON-RPC, so a guard that
checks a context key ("skip this check while syncing") can be switched off by whoever calls the API. A Python
context variable is set only by server code running in this request, never by the client, so guards use it instead:

    with trusted('alfia_sync'):
        user.sudo().write(...)

    if is_trusted(env, 'alfia_sync'):   # also true under sudo, which a client cannot obtain either
        return
"""
import contextvars
from contextlib import contextmanager

from odoo import api, models
from odoo.exceptions import UserError

_TRUSTED = contextvars.ContextVar('alfia_trusted', default=frozenset())

WORKFLOW = 'alfia_workflow'
ANY = '*'   # in _alfia_guarded_create: any value on create, fixed afterwards


@contextmanager
def trusted(*keys):
    token = _TRUSTED.set(_TRUSTED.get() | frozenset(keys or (WORKFLOW,)))
    try:
        yield
    finally:
        _TRUSTED.reset(token)


def workflow():
    """For a workflow action that has done its own checks and now sets the fields it owns."""
    return trusted(WORKFLOW)


def is_trusted(env, key=WORKFLOW):
    return env.su or key in _TRUSTED.get()


def in_trusted(key):
    """Only whether server code opened `trusted(key)`; sudo does not count (for skips that sudo writes must not get)."""
    return key in _TRUSTED.get()


class AlfiaGuardedMixin(models.AbstractModel):
    """Fields that only the record's workflow buttons may set (status, approver, linked documents...).

    `_alfia_guarded` names them. On create they may only take their default (or a value listed in
    `_alfia_guarded_create`, or anything when that lists ANY); on write they change only inside `workflow()` or
    under sudo."""
    _name = 'alfia.guarded.mixin'
    _description = 'Workflow-owned fields'

    _alfia_guarded = ()
    _alfia_guarded_create = {}

    def _alfia_guard_refuse(self, fields):
        labels = ', '.join(self._fields[f].string for f in fields if f in self._fields)
        raise UserError(self.env._("%(what)s: %(fields)s can change only through the record's buttons.",
                                   what=self._description or self._name, fields=labels))

    @api.model_create_multi
    def create(self, vals_list):
        if self._alfia_guarded and not is_trusted(self.env):
            for vals in vals_list:
                bad = []
                for f in self._alfia_guarded:
                    if f not in vals:
                        continue
                    allowed = self._alfia_guarded_create.get(f)
                    if allowed == ANY:
                        continue
                    if allowed is None:
                        field = self._fields.get(f)
                        default = field.default(self) if field is not None and callable(field.default) else None
                        allowed = (default, False, None) if not (field is not None and field.type in ('one2many', 'many2many')) else ([], False, None)
                    if vals[f] not in allowed:
                        bad.append(f)
                if bad:
                    self._alfia_guard_refuse(bad)
        return super().create(vals_list)

    def write(self, vals):
        if self._alfia_guarded and not is_trusted(self.env):
            bad = [f for f in self._alfia_guarded if f in vals]
            if bad:
                self._alfia_guard_refuse(bad)
        return super().write(vals)
