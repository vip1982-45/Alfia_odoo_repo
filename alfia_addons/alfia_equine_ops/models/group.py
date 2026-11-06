from dateutil.relativedelta import relativedelta

from odoo import api, fields, models


class AlfiaHorseGroup(models.Model):
    """A named set of horses: fixed members plus everything that matches the criteria."""
    _name = 'alfia.horse.group'
    _description = 'Horse group'
    _order = 'name'

    name = fields.Char(required=True)
    description = fields.Char()
    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company, required=True)
    member_ids = fields.Many2many('alfia.horse', string='Fixed members')
    sex = fields.Selection([('female', 'Female'), ('male', 'Male'), ('gelding', 'Gelding')])
    breed = fields.Char(string='Breed contains')
    status = fields.Selection([('active', 'Active'), ('reference', 'Reference only'), ('retired', 'Retired')], default='active')
    min_age_years = fields.Float(string='Age from (years)')
    max_age_years = fields.Float(string='Age to (years)')
    location_id = fields.Many2one('stock.location', string='Located in (or below)')
    reproductive_state = fields.Selection([('open', 'Open (barren)'), ('bred', 'Bred'), ('pregnant', 'Pregnant')])
    all_member_ids = fields.Many2many('alfia.horse', compute='_compute_all_members', string='Members')
    member_count = fields.Integer(compute='_compute_all_members')

    def _has_criteria(self):
        self.ensure_one()
        return any((self.sex, self.breed, self.location_id, self.reproductive_state, self.min_age_years, self.max_age_years))

    def _dynamic_domain(self):
        self.ensure_one()
        domain = [('company_id', '=', self.company_id.id)]
        if self.status:
            domain.append(('status', '=', self.status))
        if self.sex:
            domain.append(('sex', '=', self.sex))
        if self.breed:
            domain.append(('breed', 'ilike', self.breed))
        if self.location_id:
            domain.append(('location_id', 'child_of', self.location_id.id))
        if self.reproductive_state:
            domain.append(('reproductive_state', '=', self.reproductive_state))
        today = fields.Date.context_today(self)
        if self.min_age_years:
            domain.append(('birthdate', '<=', today - relativedelta(days=int(self.min_age_years * 365.25))))
        if self.max_age_years:
            domain.append(('birthdate', '>=', today - relativedelta(days=int(self.max_age_years * 365.25))))
        return domain

    @api.depends('member_ids', 'sex', 'breed', 'status', 'min_age_years', 'max_age_years', 'location_id', 'reproductive_state')
    def _compute_all_members(self):
        # Searched as the current user, so a user's site scope also limits what they see in a group.
        for group in self:
            members = group.member_ids
            if group._has_criteria():
                members |= self.env['alfia.horse'].search(group._dynamic_domain())
            group.all_member_ids = members
            group.member_count = len(members)

    def action_open_members(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': self.name, 'res_model': 'alfia.horse', 'view_mode': 'list,form',
                'domain': [('id', 'in', self.all_member_ids.ids)]}
