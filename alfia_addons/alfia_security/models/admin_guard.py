"""Access control itself is changed by system administrators only.

Odoo's own "Access Rights" group (base.group_erp_manager) may otherwise edit group membership and implications, access rules
and external identifiers, any of which is a path to full control. Module installation and sudo calls are unaffected."""
from odoo import api, models
from odoo.exceptions import AccessError


def _require_system(env, what):
    if env.su or env.user.has_group('base.group_system'):
        return
    raise AccessError(env._("Only a system administrator can change %s.", what))


class ResGroupsGuard(models.Model):
    _inherit = 'res.groups'

    @api.model_create_multi
    def create(self, vals_list):
        _require_system(self.env, self.env._("groups"))
        return super().create(vals_list)

    def write(self, vals):
        _require_system(self.env, self.env._("groups"))
        return super().write(vals)

    def unlink(self):
        _require_system(self.env, self.env._("groups"))
        return super().unlink()


class IrAccessGuard(models.Model):
    _inherit = 'ir.access'

    @api.model_create_multi
    def create(self, vals_list):
        _require_system(self.env, self.env._("access rules"))
        return super().create(vals_list)

    def write(self, vals):
        _require_system(self.env, self.env._("access rules"))
        return super().write(vals)

    def unlink(self):
        _require_system(self.env, self.env._("access rules"))
        return super().unlink()


class IrModelDataGuard(models.Model):
    _inherit = 'ir.model.data'

    @api.model_create_multi
    def create(self, vals_list):
        _require_system(self.env, self.env._("external identifiers"))
        return super().create(vals_list)

    def write(self, vals):
        _require_system(self.env, self.env._("external identifiers"))
        return super().write(vals)

    def unlink(self):
        _require_system(self.env, self.env._("external identifiers"))
        return super().unlink()
