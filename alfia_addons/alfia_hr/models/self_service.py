from odoo import api, models

SELF_SERVICE_GROUPS = ('hr_holidays.group_hr_holidays_employee',)


class ResUsers(models.Model):
    _inherit = 'res.users'

    def _alfia_grant_self_service(self):
        """Every internal user may request their own time off (Odoo gives this only through default user settings)."""
        groups = self.env['res.groups']
        for xmlid in SELF_SERVICE_GROUPS:
            groups |= self.env.ref(xmlid, raise_if_not_found=False) or self.env['res.groups']
        for user in self.filtered(lambda u: u._is_internal()):
            missing = groups - user.group_ids
            if missing:
                user.sudo().with_context(alfia_sync=True).write({'group_ids': [(4, g.id) for g in missing]})

    @api.model_create_multi
    def create(self, vals_list):
        users = super().create(vals_list)
        users._alfia_grant_self_service()
        return users
