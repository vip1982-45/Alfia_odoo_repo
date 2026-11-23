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


PRIVILEGED_GROUPS = ('base.group_system', 'base.group_erp_manager', 'alfia_security.group_alfia_security_officer')
PARTNER_IDENTITY = {'email', 'active', 'user_ids', 'company_id'}


class ResPartnerGuard(models.Model):
    """An administrator's or security officer's contact record carries the email their password reset goes to, so only
    a system administrator (or the person themselves) may change it."""
    _inherit = 'res.partner'

    def _alfia_privileged_partners(self):
        if self.env.su or self.env.user.has_group('base.group_system'):
            return self.browse()
        own = self.env.user.partner_id
        return self.filtered(lambda p: p != own and any(
            u.has_group(g) for u in p.sudo().with_context(active_test=False).user_ids for g in PRIVILEGED_GROUPS))

    def write(self, vals):
        if PARTNER_IDENTITY & set(vals):
            protected = self._alfia_privileged_partners()
            if protected:
                raise AccessError(self.env._("Only a system administrator can change the contact details of %s.",
                                             ', '.join(protected.mapped('name'))))
        return super().write(vals)

    def unlink(self):
        protected = self._alfia_privileged_partners()
        if protected:
            raise AccessError(self.env._("Only a system administrator can delete the contact of %s.", ', '.join(protected.mapped('name'))))
        return super().unlink()
