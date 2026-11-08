from odoo import Command, api, fields, models
from odoo.exceptions import AccessError

GUARDED = ('group_ids', 'alfia_profile_ids')
SYSTEM_MANAGED = ('alfia_managed_group_ids', 'alfia_location_ids', 'alfia_warehouse_ids', 'alfia_journal_ids')


class ResUsers(models.Model):
    _inherit = 'res.users'

    alfia_profile_ids = fields.Many2many('alfia.access.profile', 'alfia_profile_user_rel', 'user_id', 'profile_id',
                                         string='Access profiles', copy=False)
    alfia_managed_group_ids = fields.Many2many('res.groups', 'alfia_user_managed_group_rel', 'user_id', 'group_id',
                                               string='Groups granted by profiles', copy=False, readonly=True)
    # Effective scope: the union of the user's profiles. Stored, so access checks stay cheap.
    alfia_location_ids = fields.Many2many('stock.location', 'alfia_user_location_rel', 'user_id', 'location_id',
                                          compute='_compute_alfia_scope', store=True, copy=False, string='Allowed stock locations')
    alfia_warehouse_ids = fields.Many2many('stock.warehouse', 'alfia_user_warehouse_rel', 'user_id', 'warehouse_id',
                                           compute='_compute_alfia_scope', store=True, copy=False, string='Allowed warehouses')
    alfia_journal_ids = fields.Many2many('account.journal', 'alfia_user_journal_rel', 'user_id', 'journal_id',
                                         compute='_compute_alfia_scope', store=True, copy=False, string='Allowed journals')
    # Whether the access rules limit this user to the scope above (internal users without the bypass group).
    alfia_scope_locations = fields.Boolean(compute='_compute_alfia_scope_flags')
    alfia_scope_warehouses = fields.Boolean(compute='_compute_alfia_scope_flags')
    alfia_scope_journals = fields.Boolean(compute='_compute_alfia_scope_flags')
    alfia_is_internal = fields.Boolean(compute='_compute_alfia_scope_flags')
    alfia_is_auditor = fields.Boolean(compute='_compute_alfia_scope_flags')

    @api.depends('alfia_profile_ids.location_ids', 'alfia_profile_ids.warehouse_ids.view_location_id', 'alfia_profile_ids.journal_ids', 'alfia_profile_ids.active')
    def _compute_alfia_scope(self):
        for user in self:
            profiles = user.alfia_profile_ids.filtered('active')
            # A warehouse-scoped role (e.g. a buyer) works with the whole warehouse; barn-level roles list their locations.
            user.alfia_location_ids = profiles.location_ids | profiles.warehouse_ids.view_location_id
            user.alfia_warehouse_ids = profiles.warehouse_ids
            user.alfia_journal_ids = profiles.journal_ids

    @api.depends('all_group_ids')
    def _compute_alfia_scope_flags(self):
        bypass = {
            'locations': self.env.ref('alfia_security.group_alfia_all_locations', raise_if_not_found=False),
            'warehouses': self.env.ref('alfia_security.group_alfia_all_warehouses', raise_if_not_found=False),
            'journals': self.env.ref('alfia_security.group_alfia_all_journals', raise_if_not_found=False),
        }
        auditor = self.env.ref('alfia_security.group_alfia_security_auditor', raise_if_not_found=False)
        for user in self:
            internal = user._is_internal()
            groups = user.all_group_ids
            user.alfia_is_internal = internal
            user.alfia_is_auditor = bool(auditor and auditor in groups)
            for key, group in bypass.items():
                user[f'alfia_scope_{key}'] = internal and not (group and group in groups)

    def _alfia_check_location(self, location, what=None):
        """Raise unless this user may work in `location` (scope bypass, or the location is inside their allowed sites).

        For code that moves stock with sudo on the user's behalf, where the record rules would not be applied."""
        self.ensure_one()
        if not location or not self.alfia_scope_locations:
            return True
        allowed = self.env['stock.location'].sudo().search_count(
            [('id', '=', location.id), ('id', 'child_of', self.alfia_location_ids.ids)]) if self.alfia_location_ids else 0
        if not allowed:
            raise AccessError(self.env._("%(place)s is outside the sites you may work in%(what)s.", place=location.display_name,
                                         what=(f" ({what})" if what else '')))
        return True

    @api.model
    def _has_field_access(self, field, operation):
        # Odoo lets only its "Access Rights" group write user fields. Security officers manage accounts without that group;
        # _alfia_guard still stops them from touching groups, administrators, other officers and their own access.
        if operation == 'write' and not self.env.su and self.env.user.has_group('alfia_security.group_alfia_security_officer'):
            return models.Model._has_field_access(self, field, operation)
        return super()._has_field_access(field, operation)

    # ------------------------------------------------------------------ guard against escalation
    @api.model
    def _alfia_group_ids_after(self, current, commands):
        """Group ids after applying many2many commands, or None if a command is not understood."""
        ids = set(current)
        if not isinstance(commands, (list, tuple)):
            return None
        for cmd in commands:
            if not isinstance(cmd, (list, tuple)) or not cmd:
                return None
            code = cmd[0]
            if code == Command.LINK:
                ids.add(cmd[1])
            elif code == Command.UNLINK:
                ids.discard(cmd[1])
            elif code == Command.CLEAR:
                ids = set()
            elif code == Command.SET:
                ids = set(cmd[2])
            else:
                return None
        return ids

    def _alfia_guard(self, vals, creating=False):
        env = self.env
        if env.su or env.context.get('alfia_sync'):
            return
        actor = env.user
        if actor.has_group('base.group_system'):
            return
        if not creating:
            for user in self:
                if user != actor and (user.has_group('base.group_system') or user.has_group('alfia_security.group_alfia_security_officer')):
                    raise AccessError(env._("Only a system administrator can change the account of %s.", user.name))
        touched = [f for f in GUARDED + SYSTEM_MANAGED if f in vals]
        if not touched:
            return
        officer = actor.has_group('alfia_security.group_alfia_security_officer')
        if any(f in vals for f in SYSTEM_MANAGED):
            raise AccessError(env._("These fields are calculated from access profiles and cannot be edited directly."))
        if 'group_ids' in vals:
            if creating:
                allowed = set(self._default_groups().ids)
                after = self._alfia_group_ids_after(allowed, vals['group_ids'])
                ok = after is not None and after <= allowed
            else:
                ok = all(self._alfia_group_ids_after(user.group_ids.ids, vals['group_ids']) == set(user.group_ids.ids) for user in self)
            if not ok:
                raise AccessError(env._("Groups cannot be edited directly. Assign an access profile instead."))
        if 'alfia_profile_ids' in vals:
            if not officer:
                raise AccessError(env._("Only security officers can assign access profiles."))
            for user in self:
                if user == actor:
                    raise AccessError(env._("You cannot change your own access profiles."))
                if user.has_group('base.group_system') or user.has_group('alfia_security.group_alfia_security_officer'):
                    raise AccessError(env._("Only a system administrator can change the access of %s.", user.name))

    # ------------------------------------------------------------------ profile -> groups
    def _alfia_sync_profile_groups(self):
        """Make each user hold exactly (own direct groups) + (groups of the active profiles)."""
        for user in self:
            wanted = user.alfia_profile_ids.filtered('active').group_ids
            managed_before = user.alfia_managed_group_ids
            current = user.group_ids
            target = (current - managed_before) | wanted
            if target == current and wanted == managed_before:
                continue
            added = target - current
            removed = current - target
            user.with_context(alfia_sync=True).sudo().write({
                'group_ids': [Command.set(target.ids)],
                'alfia_managed_group_ids': [Command.set(wanted.ids)],
            })
            self.env['alfia.security.audit']._alfia_log(
                'user.groups', self._name, user.login,
                "profiles: %s\ngranted: %s\nrevoked: %s" % (
                    ', '.join(user.alfia_profile_ids.mapped('name')) or '-',
                    ', '.join(added.mapped('display_name')) or '-',
                    ', '.join(removed.mapped('display_name')) or '-'))

    # ------------------------------------------------------------------ ORM hooks
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._alfia_guard(vals, creating=True)
        users = super().create(vals_list)
        for user in users:
            self.env['alfia.security.audit']._alfia_log('user.create', self._name, user.login)
        users.filtered('alfia_profile_ids')._alfia_sync_profile_groups()
        return users

    def write(self, vals):
        self._alfia_guard(vals)
        profiles_before = {u.id: u.alfia_profile_ids for u in self} if 'alfia_profile_ids' in vals else {}
        security_flags = {u.id: (u.active, u.login) for u in self} if ('active' in vals or 'login' in vals) else {}
        res = super().write(vals)
        if 'alfia_profile_ids' in vals and not self.env.context.get('alfia_sync'):
            for user in self:
                self.env['alfia.security.audit']._alfia_log(
                    'user.profiles', self._name, user.login,
                    "before: %s\nafter: %s" % (', '.join(profiles_before[user.id].mapped('name')) or '-',
                                              ', '.join(user.alfia_profile_ids.mapped('name')) or '-'))
            self._alfia_sync_profile_groups()
        for user in self:
            old = security_flags.get(user.id)
            if old and (old[0], old[1]) != (user.active, user.login):
                self.env['alfia.security.audit']._alfia_log(
                    'user.account', self._name, user.login, "active %s -> %s; login %s -> %s" % (old[0], user.active, old[1], user.login))
        return res
