from odoo import api, fields, models
from odoo.exceptions import UserError


class AlfiaSecurityAudit(models.Model):
    _name = 'alfia.security.audit'
    _description = 'Security audit trail'
    _order = 'id desc'

    actor_id = fields.Many2one('res.users', string='Done by', readonly=True, index=True, ondelete='set null')
    actor_login = fields.Char(string='Login at the time', readonly=True)
    action = fields.Char(required=True, readonly=True, index=True)
    target_model = fields.Char(readonly=True, index=True)
    target_ref = fields.Char(string='Target', readonly=True)
    details = fields.Text(readonly=True)

    @api.model
    def _alfia_log(self, action, target_model=None, target_ref=None, details=None):
        """Append one audit entry. Always written with sudo: the actor may not be allowed to write audit records."""
        actor = self.env.user
        return self.sudo().create({
            'actor_id': actor.id,
            'actor_login': actor.login,
            'action': action,
            'target_model': target_model,
            'target_ref': target_ref,
            'details': details,
        })

    def write(self, vals):
        raise UserError(self.env._("The security audit trail cannot be modified."))

    def unlink(self):
        raise UserError(self.env._("The security audit trail cannot be deleted."))
