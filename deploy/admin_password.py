# Run with:  odoo-bin shell -c <conf> -d <db> < deploy/admin_password.py     (on every start)
# Odoo creates the administrator as admin/admin. Whatever else was or wasn't installed (with or without the demo farm),
# the administrator must never keep that password: if it still works, set ALFIA_ADMIN_PASSWORD instead.
import os
import sys

from odoo.exceptions import AccessDenied

wanted = os.environ.get('ALFIA_ADMIN_PASSWORD', '')
admin = env.ref('base.user_admin').sudo()
try:
    admin.with_user(admin)._check_credentials({'type': 'password', 'password': 'admin'}, {'interactive': True})
    default_works = True
except AccessDenied:
    default_works = False
if default_works:
    if not wanted:
        print('ERROR: the administrator still has the default password and ALFIA_ADMIN_PASSWORD is not set.')
        sys.exit(1)
    admin.write({'password': wanted})
    env.cr.commit()
    print('Administrator password set from ALFIA_ADMIN_PASSWORD (the default admin/admin no longer works).')
else:
    print('Administrator password: not the default.')
