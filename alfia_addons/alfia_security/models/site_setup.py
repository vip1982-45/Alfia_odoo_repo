from odoo import Command, api, fields, models
from odoo.exceptions import AccessError, UserError


class AlfiaSiteSetup(models.TransientModel):
    """Add a stable (or any site) in one step: its stock location and one access profile per chosen role, limited to it.

    Profiles are copied from role templates (any existing profile): same functions, warehouses and journals, but the
    site is this one. Security officers then give people these profiles; nothing else is needed per person."""
    _name = 'alfia.site.setup'
    _description = 'Add a site with its access profiles'

    site_name = fields.Char(string='Site name', required=True, help="For example: Barn C (yearlings).")
    parent_location_id = fields.Many2one('stock.location', string='Inside', domain="[('usage', 'in', ('view', 'internal'))]",
                                         default=lambda self: self._default_parent())
    location_id = fields.Many2one('stock.location', string='Existing location', domain="[('usage', '=', 'internal')]",
                                  help="Use a location that already exists instead of creating one.")
    template_ids = fields.Many2many('alfia.access.profile', string='Roles at this site', required=True,
                                    help="Each chosen profile is copied as '<role> – <site>' with this site as its only place of work.")
    created_profile_ids = fields.Many2many('alfia.access.profile', 'alfia_site_setup_created_rel', string='Profiles created', readonly=True)

    @api.model
    def _default_parent(self):
        warehouse = self.env['stock.warehouse'].search([('company_id', '=', self.env.company.id)], limit=1)
        return warehouse.view_location_id

    def action_create(self):
        self.ensure_one()
        if not (self.env.su or self.env.user.has_group('base.group_system')):
            raise AccessError(self.env._("Only a system administrator can add a site and its profiles."))
        name = (self.site_name or '').strip()
        if not name:
            raise UserError(self.env._("Enter the site name."))
        location = self.location_id or self.env['stock.location'].sudo().create(
            {'name': name, 'location_id': self.parent_location_id.id, 'usage': 'internal'})
        # Checked above: a system administrator. They may lack inventory or accounting rights, so the records are written with
        # system rights; the audit trail still names them.
        Profile = self.env['alfia.access.profile'].sudo()
        created = Profile
        for template in self.template_ids:
            profile_name = f"{template.name} – {name}"
            if Profile.with_context(active_test=False).search_count([('name', '=', profile_name)]):
                raise UserError(self.env._("A profile named %s already exists.", profile_name))
            profile = Profile.create({
                'name': profile_name, 'description': template.description,
                'group_ids': [Command.set(template.group_ids.ids)], 'location_ids': [Command.set([location.id])],
                'warehouse_ids': [Command.set(template.warehouse_ids.ids)], 'journal_ids': [Command.set(template.journal_ids.ids)]})
            # The Arabic name follows the template's Arabic name.
            if 'ar_001' in dict(self.env['res.lang'].get_installed()):
                profile.with_context(lang='ar_001').name = f"{template.with_context(lang='ar_001').name} – {name}"
            created |= profile
        self.created_profile_ids = created
        self.env['alfia.security.audit']._alfia_log('site.create', 'stock.location', location.complete_name,
                                                    "profiles: %s" % ', '.join(created.mapped('name')))
        return {'type': 'ir.actions.act_window', 'name': self.env._("Profiles for %s", name), 'res_model': 'alfia.access.profile',
                'view_mode': 'list,form', 'domain': [('id', 'in', created.ids)]}
