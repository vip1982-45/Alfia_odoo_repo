from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

FORBIDDEN_GROUPS = (
    'base.group_system',
    'base.group_erp_manager',
    'base.group_portal',
    'base.group_public',
    'alfia_security.group_alfia_security_officer',
)


class AlfiaAccessProfile(models.Model):
    """A reusable role-and-scope bundle: which functions a person performs, and where.

    Profiles are *designed* by system administrators and *assigned* by security officers.
    """
    _name = 'alfia.access.profile'
    _description = 'Access profile'
    _order = 'name'

    name = fields.Char(required=True, translate=True)
    active = fields.Boolean(default=True)
    description = fields.Text()
    group_ids = fields.Many2many('res.groups', 'alfia_profile_group_rel', 'profile_id', 'group_id', string='Functions (groups)')
    location_ids = fields.Many2many('stock.location', 'alfia_profile_location_rel', 'profile_id', 'location_id', string='Stock locations',
                                    help="The user may only see and move stock in these locations and everything below them.")
    warehouse_ids = fields.Many2many('stock.warehouse', 'alfia_profile_warehouse_rel', 'profile_id', 'warehouse_id', string='Warehouses',
                                     help="Purchase and sales orders of these warehouses.")
    journal_ids = fields.Many2many('account.journal', 'alfia_profile_journal_rel', 'profile_id', 'journal_id', string='Journals',
                                   help="Accounting entries, payments and statements of these journals.")
    user_ids = fields.Many2many('res.users', 'alfia_profile_user_rel', 'profile_id', 'user_id', string='Users')
    user_count = fields.Integer(compute='_compute_user_count')

    _name_uniq = models.Constraint('unique(name)', 'A profile with this name already exists.')

    @api.depends('user_ids')
    def _compute_user_count(self):
        for profile in self:
            profile.user_count = len(profile.user_ids)

    @api.constrains('group_ids')
    def _check_group_ids(self):
        forbidden = self.env['res.groups']
        for xmlid in FORBIDDEN_GROUPS:
            forbidden |= self.env.ref(xmlid, raise_if_not_found=False) or self.env['res.groups']
        for profile in self:
            bad = profile.group_ids & forbidden
            if bad:
                raise ValidationError(self.env._(
                    "A profile cannot grant: %s. These rights are given directly by a system administrator.",
                    ', '.join(bad.mapped('display_name'))))

    def _alfia_forbid_direct_user_edit(self, vals):
        # Assigning a profile must go through res.users, where the escalation guard, the group sync and the audit live.
        if 'user_ids' in vals and not self.env.su and not self.env.context.get('alfia_sync'):
            raise AccessError(self.env._("Assign profiles from the user's own form."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._alfia_forbid_direct_user_edit(vals)
        profiles = super().create(vals_list)
        for profile in profiles:
            self.env['alfia.security.audit']._alfia_log('profile.create', self._name, profile.name, self._alfia_describe(profile))
        return profiles

    def write(self, vals):
        self._alfia_forbid_direct_user_edit(vals)
        before = {p.id: self._alfia_describe(p) for p in self}
        res = super().write(vals)
        scope_changed = any(f in vals for f in ('group_ids', 'location_ids', 'warehouse_ids', 'journal_ids'))
        for profile in self:
            after = self._alfia_describe(profile)
            if before[profile.id] != after or 'active' in vals or 'name' in vals:
                self.env['alfia.security.audit']._alfia_log('profile.write', self._name, profile.name, f"before: {before[profile.id]}\nafter: {after}")
        if scope_changed or 'active' in vals:
            # Everybody holding the profile receives the new rights (or loses the old ones) at once.
            self.user_ids._alfia_sync_profile_groups()
        return res

    def unlink(self):
        if any(p.user_ids for p in self):
            raise UserError(self.env._("This profile is still assigned to users. Remove it from them (or archive it) first."))
        for profile in self:
            self.env['alfia.security.audit']._alfia_log('profile.unlink', self._name, profile.name)
        return super().unlink()

    @staticmethod
    def _alfia_describe(profile):
        return "groups=[%s] locations=[%s] warehouses=[%s] journals=[%s]" % (
            ', '.join(sorted(profile.group_ids.mapped('display_name'))),
            ', '.join(sorted(profile.location_ids.mapped('complete_name'))),
            ', '.join(sorted(profile.warehouse_ids.mapped('name'))),
            ', '.join(sorted(profile.journal_ids.mapped('name'))))
