from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    alfia_sod_enabled = fields.Boolean(
        string='Enforce segregation of duties', default=True,
        help="The person who creates a purchase order, vendor bill or outbound payment cannot also approve or post it.")
    alfia_sod_threshold = fields.Monetary(
        string='Segregation threshold', default=0.0, currency_field='currency_id',
        help="Only amounts from this value up need a second person. Zero means every amount.")

    def write(self, vals):
        if any(f in vals for f in ('alfia_sod_enabled', 'alfia_sod_threshold')):
            for company in self:
                self.env['alfia.security.audit']._alfia_log(
                    'company.sod', self._name, company.name,
                    "enabled %s -> %s; threshold %s -> %s" % (
                        company.alfia_sod_enabled, vals.get('alfia_sod_enabled', company.alfia_sod_enabled),
                        company.alfia_sod_threshold, vals.get('alfia_sod_threshold', company.alfia_sod_threshold)))
        return super().write(vals)
