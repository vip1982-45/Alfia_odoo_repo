"""Accounting profiles match bills to purchase orders of every warehouse: give existing installations the warehouse
bypass that new installations get from the profile data (which is not rewritten on upgrade)."""
from odoo import SUPERUSER_ID, Command, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    bypass = env.ref('alfia_security.group_alfia_all_warehouses', raise_if_not_found=False)
    if not bypass:
        return
    for xmlid in ('alfia_security.profile_ap_clerk', 'alfia_security.profile_ar_clerk', 'alfia_security.profile_accountant'):
        profile = env.ref(xmlid, raise_if_not_found=False)
        if profile and bypass not in profile.group_ids:
            profile.write({'group_ids': [Command.link(bypass.id)]})
