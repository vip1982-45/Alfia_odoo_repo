from odoo import api, models


class IrAccess(models.Model):
    """Every change to the access rules themselves is recorded in the security audit trail."""
    _inherit = 'ir.access'

    @staticmethod
    def _alfia_describe(access):
        return "%s | model=%s group=%s op=%s domain=%s active=%s" % (
            access.name, access.model_id.model, access.group_id.display_name or '-', access.operation, access.domain or '-', access.active)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for access in records:
            self.env['alfia.security.audit']._alfia_log('access.create', self._name, access.name, self._alfia_describe(access))
        return records

    def write(self, vals):
        before = {a.id: self._alfia_describe(a) for a in self}
        res = super().write(vals)
        for access in self:
            after = self._alfia_describe(access)
            if before[access.id] != after:
                self.env['alfia.security.audit']._alfia_log('access.write', self._name, access.name, f"before: {before[access.id]}\nafter: {after}")
        return res

    def unlink(self):
        for access in self:
            self.env['alfia.security.audit']._alfia_log('access.unlink', self._name, access.name, self._alfia_describe(access))
        return super().unlink()
