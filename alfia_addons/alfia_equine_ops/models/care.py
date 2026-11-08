from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

from .common import BILLING_MANAGERS, require_any, sod_blocks

G = 'alfia_security.group_alfia_'


class AlfiaBoarding(models.Model):
    """A client's horse boarded at the farm. Creates the recurring charge that bills the stay."""
    _name = 'alfia.boarding'
    _description = 'Boarding agreement'
    _inherit = ['mail.thread']
    _order = 'start_date desc'

    horse_id = fields.Many2one('alfia.horse', required=True, index=True, ondelete='restrict')
    company_id = fields.Many2one('res.company', related='horse_id.company_id', store=True)
    client_id = fields.Many2one('res.partner', string='Client', required=True)
    product_id = fields.Many2one('product.product', string='Board service', required=True, domain="[('type', '=', 'service')]")
    rate = fields.Float(string='Rate per period')
    interval = fields.Selection([('weekly', 'Weekly'), ('monthly', 'Monthly')], default='monthly', required=True)
    start_date = fields.Date(required=True, default=fields.Date.context_today)
    end_date = fields.Date()
    location_id = fields.Many2one('stock.location', string='Stall / paddock')
    state = fields.Selection([('draft', 'Draft'), ('active', 'Active'), ('ended', 'Ended')], default='draft', required=True, tracking=True)
    plan_id = fields.Many2one('alfia.charge.plan', readonly=True, copy=False)
    notes = fields.Text()

    @api.constrains('start_date', 'end_date')
    def _check_dates(self):
        for rec in self:
            if rec.end_date and rec.end_date < rec.start_date:
                raise ValidationError(self.env._("The stay ends before it starts."))

    def action_start(self):
        require_any(self.env, BILLING_MANAGERS, self.env._("managers or accounting staff"))
        for rec in self.filtered(lambda r: r.state == 'draft'):
            if sod_blocks(rec, rec.rate):
                raise UserError(self.env._("You entered the boarding agreement for %s, so someone else must start its billing.", rec.horse_id.name))
            rec.plan_id = self.env['alfia.charge.plan'].sudo().create({
                'name': self.env._("Board: %s", rec.horse_id.name), 'target_kind': 'horse', 'horse_id': rec.horse_id.id, 'partner_id': rec.client_id.id,
                'product_id': rec.product_id.id, 'price_unit': rec.rate, 'interval': rec.interval, 'next_date': rec.start_date, 'end_date': rec.end_date,
                'company_id': rec.company_id.id, 'state': 'approved', 'approved_by_id': self.env.user.id})
            rec.state = 'active'

    def action_end(self):
        for rec in self.filtered(lambda r: r.state == 'active'):
            rec.end_date = rec.end_date or fields.Date.context_today(self)
            rec.plan_id.sudo().write({'end_date': rec.end_date})
            rec.state = 'ended'


class AlfiaDiet(models.Model):
    _name = 'alfia.diet'
    _description = 'Feed plan'
    _order = 'horse_id, date_from desc'

    horse_id = fields.Many2one('alfia.horse', required=True, index=True, ondelete='cascade')
    company_id = fields.Many2one('res.company', related='horse_id.company_id', store=True)
    name = fields.Char(required=True)
    date_from = fields.Date(required=True, default=fields.Date.context_today)
    date_to = fields.Date()
    line_ids = fields.One2many('alfia.diet.line', 'diet_id', string='Feed')
    notes = fields.Text()
    current = fields.Boolean(compute='_compute_current', search='_search_current')

    def _compute_current(self):
        today = fields.Date.context_today(self)
        for rec in self:
            rec.current = rec.date_from <= today and (not rec.date_to or rec.date_to >= today)

    def _search_current(self, operator, value):
        today = fields.Date.context_today(self)
        domain = [('date_from', '<=', today), '|', ('date_to', '=', False), ('date_to', '>=', today)]
        if operator not in ('=', '!='):
            raise UserError(self.env._("Unsupported search on current feed plans."))
        if (operator == '=') == bool(value):
            return domain
        return ['|', ('date_from', '>', today), ('date_to', '<', today)]

    @api.constrains('date_from', 'date_to')
    def _check_dates(self):
        for rec in self:
            if rec.date_to and rec.date_to < rec.date_from:
                raise ValidationError(self.env._("The feed plan ends before it starts."))


class AlfiaDietLine(models.Model):
    _name = 'alfia.diet.line'
    _description = 'Feed plan line'

    diet_id = fields.Many2one('alfia.diet', required=True, ondelete='cascade', index=True)
    horse_id = fields.Many2one('alfia.horse', related='diet_id.horse_id', store=True)
    company_id = fields.Many2one('res.company', related='diet_id.company_id', store=True)
    product_id = fields.Many2one('product.product', string='Feed / supplement', required=True)
    qty_per_meal = fields.Float(required=True)
    meals_per_day = fields.Integer(default=2, required=True)
    daily_qty = fields.Float(compute='_compute_daily', store=True)

    _positive = models.Constraint('check(qty_per_meal > 0 and meals_per_day > 0)', 'Quantities and meals must be above zero.')

    @api.depends('qty_per_meal', 'meals_per_day')
    def _compute_daily(self):
        for line in self:
            line.daily_qty = line.qty_per_meal * line.meals_per_day


class AlfiaQuarantine(models.Model):
    _name = 'alfia.quarantine'
    _description = 'Quarantine'
    _inherit = ['alfia.horse.record', 'mail.thread']
    _order = 'start_date desc'

    start_date = fields.Date(required=True, default=fields.Date.context_today)
    planned_end = fields.Date(required=True)
    end_date = fields.Date(readonly=True, copy=False)
    reason = fields.Char(required=True)
    isolation_location_id = fields.Many2one('stock.location', string='Isolation stall')
    state = fields.Selection([('active', 'In quarantine'), ('released', 'Released')], default='active', required=True, tracking=True)
    released_by_id = fields.Many2one('res.users', readonly=True, copy=False)

    @api.constrains('start_date', 'planned_end')
    def _check_dates(self):
        for rec in self:
            if rec.planned_end < rec.start_date:
                raise ValidationError(self.env._("The planned end is before the start."))

    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        for rec in recs:
            if self.search_count([('horse_id', '=', rec.horse_id.id), ('state', '=', 'active'), ('id', '!=', rec.id)]):
                raise ValidationError(self.env._("%s is already in quarantine.", rec.horse_id.name))
            rec._sync_task()
        return recs

    def _sync_task(self):
        for rec in self:
            self.env['alfia.task']._alfia_upsert(f"quarantine:{rec.id}", rec.horse_id, rec.planned_end, self.env._("Review quarantine release"), 'exam',
                                                 group=self.env.ref(G + 'vet_user', raise_if_not_found=False))

    def action_release(self):
        if not (self.env.su or self.env.user.has_group(G + 'vet_user')):
            raise UserError(self.env._("Only veterinary staff can release a horse from quarantine."))
        for rec in self.filtered(lambda r: r.state == 'active'):
            rec.write({'state': 'released', 'end_date': fields.Date.context_today(self), 'released_by_id': self.env.user.id})
            self.env['alfia.task']._alfia_cancel_pending(f"quarantine:{rec.id}", self.env._("Released"))
