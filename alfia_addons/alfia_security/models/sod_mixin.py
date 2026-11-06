from odoo import models


class AlfiaSodMixin(models.AbstractModel):
    _name = 'alfia.sod.mixin'
    _description = 'Segregation of duties helper'

    def _alfia_sod_applies(self, amount_company_currency=None, company=None):
        """Whether the current user must be a different person from the record's creator.

        Not enforced for the superuser / sudo calls (system automation) or for members of the
        explicit emergency-exemption group. The company switch and threshold are set by system
        administrators only.
        """
        env = self.env
        if env.su:
            return False
        if env.user.has_group('alfia_security.group_alfia_sod_exempt'):
            return False
        company = company or env.company
        if not company.alfia_sod_enabled:
            return False
        if amount_company_currency is None:  # no amount (e.g. a stock transfer): the threshold does not apply
            return True
        return amount_company_currency >= (company.alfia_sod_threshold or 0.0)
