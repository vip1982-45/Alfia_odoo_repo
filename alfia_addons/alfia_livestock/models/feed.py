"""Feed issued from the store to named horses. The stable asks (or the store fills it from the feed plans), the store
issues it: the feed leaves stock as consumed and each horse is charged its share at the product's cost, so the horse's
profit shows the feed it actually ate. For a month in which a horse has feed issues, its feed-plan estimate is left out."""
from odoo import Command, api, fields, models
from odoo.exceptions import UserError

from odoo.addons.alfia_security.flow import workflow

STORE = 'stock.group_stock_user'


class AlfiaFeedIssue(models.Model):
    _name = 'alfia.feed.issue'
    _description = 'Feed issue to horses'
    _inherit = ['mail.thread', 'alfia.guarded.mixin']
    _order = 'date desc, id desc'
    _alfia_guarded = ('state', 'move_id', 'issued_by_id')

    name = fields.Char(readonly=True, copy=False, default=lambda self: self.env._('New'))
    date = fields.Date(required=True, default=fields.Date.context_today, tracking=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    product_id = fields.Many2one('product.product', string='Feed', required=True, domain="[('is_storable', '=', True)]", tracking=True)
    uom_id = fields.Many2one(related='product_id.uom_id', string='Unit')
    location_id = fields.Many2one('stock.location', string='Take from', required=True, domain="[('usage', '=', 'internal')]")
    line_ids = fields.One2many('alfia.feed.issue.line', 'issue_id', string='Horses', copy=True)
    total_qty = fields.Float(string='Total', compute='_compute_totals', store=True)
    total_value = fields.Monetary(string='Value', compute='_compute_totals', store=True, currency_field='currency_id')
    currency_id = fields.Many2one(related='company_id.currency_id')
    state = fields.Selection([('draft', 'Requested'), ('issued', 'Issued'), ('cancelled', 'Cancelled')], default='draft',
                             required=True, tracking=True)
    issued_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    move_id = fields.Many2one('stock.move', string='Stock move', readonly=True, copy=False)
    notes = fields.Text()

    @api.depends('line_ids.quantity', 'line_ids.value')
    def _compute_totals(self):
        for issue in self:
            issue.total_qty = sum(issue.line_ids.mapped('quantity'))
            issue.total_value = sum(issue.line_ids.mapped('value'))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            vals['name'] = self.env['ir.sequence'].sudo().next_by_code('alfia.feed.issue') or self.env._('Feed issue')
        issues = super().create(vals_list)
        issues._alfia_check_store()
        return issues

    def write(self, vals):
        if {'product_id', 'location_id', 'date', 'company_id'} & set(vals) and any(i.state != 'draft' for i in self):
            raise UserError(self.env._("An issued feed record cannot be changed."))
        res = super().write(vals)
        if 'location_id' in vals:
            self._alfia_check_store()
        return res

    def _alfia_check_store(self):
        # Someone from the store (who will issue it) can only choose a store in their own sites: say so when saving, not
        # at issue. Stable staff ask from any store (even with inventory rights); the store that issues is checked then.
        user = self.env.user
        if self.env.su or not user.has_group(STORE) or user.has_group('alfia_security.group_alfia_stable_user'):
            return
        for issue in self:
            self.env.user._alfia_check_location(issue.location_id, self.env._("feed store"))

    def action_fill_from_plans(self):
        """One line per horse whose current feed plan includes this feed, with its daily quantity."""
        for issue in self.filtered(lambda i: i.state == 'draft'):
            if not issue.product_id:
                raise UserError(self.env._("Choose the feed first."))
            plans = self.env['alfia.diet.line'].search([('product_id', '=', issue.product_id.id), ('diet_id.date_from', '<=', issue.date),
                                                        '|', ('diet_id.date_to', '=', False), ('diet_id.date_to', '>=', issue.date)])
            present = issue.line_ids.horse_id
            needs = {}
            for plan in plans.filtered(lambda p: p.horse_id not in present and p.horse_id.status == 'active'):
                needs[plan.horse_id] = needs.get(plan.horse_id, 0.0) + plan.daily_qty
            if not needs:
                raise UserError(self.env._("No horse's current feed plan includes %s.", issue.product_id.display_name))
            issue.line_ids = [Command.create({'horse_id': horse.id, 'quantity': qty}) for horse, qty in needs.items()]

    def action_issue(self):
        if not (self.env.su or self.env.user.has_group(STORE)):
            raise UserError(self.env._("Only the store (inventory users) issues feed."))
        consumed = self.env['stock.location'].sudo().search([('usage', '=', 'inventory'), ('company_id', 'in', (False, self.env.company.id))], limit=1)
        for issue in self.filtered(lambda i: i.state == 'draft'):
            if not issue.line_ids or any(l.quantity <= 0 for l in issue.line_ids):
                raise UserError(self.env._("Add each horse with a quantity above zero."))
            self.env.user._alfia_check_location(issue.location_id, self.env._("feed store"))
            target = issue.product_id.with_company(issue.company_id).property_stock_inventory or consumed
            cost = issue.product_id.with_company(issue.company_id).standard_price
            move = self.env['stock.move'].sudo().create({
                'product_id': issue.product_id.id, 'product_uom_qty': issue.total_qty, 'quantity': issue.total_qty,
                'uom_id': issue.product_id.uom_id.id, 'location_id': issue.location_id.id, 'location_dest_id': target.id,
                'picked': True, 'company_id': issue.company_id.id, 'origin': issue.name})
            move._action_confirm()
            move._action_done()
            for line in issue.line_ids:
                line.value = issue.currency_id.round(line.quantity * cost)
            with workflow():
                issue.write({'state': 'issued', 'move_id': move.id, 'issued_by_id': self.env.user.id})
            issue.message_post(body=self.env._("Issued by %(user)s: %(qty)s %(uom)s of %(feed)s to %(n)s horse(s).", user=self.env.user.name,
                                               qty=issue.total_qty, uom=issue.uom_id.name, feed=issue.product_id.display_name,
                                               n=len(issue.line_ids)))

    def action_cancel(self):
        for issue in self:
            if issue.state != 'draft':
                raise UserError(self.env._("Only a requested feed issue can be cancelled."))
            with workflow():
                issue.state = 'cancelled'


class AlfiaFeedIssueLine(models.Model):
    _name = 'alfia.feed.issue.line'
    _description = 'Feed issued to one horse'

    issue_id = fields.Many2one('alfia.feed.issue', required=True, ondelete='cascade', index=True)
    horse_id = fields.Many2one('alfia.horse', required=True, index=True)
    quantity = fields.Float(required=True)
    value = fields.Monetary(readonly=True, currency_field='currency_id')
    currency_id = fields.Many2one(related='issue_id.currency_id')
    date = fields.Date(related='issue_id.date', store=True)
    state = fields.Selection(related='issue_id.state', store=True)

    _one_line_per_horse = models.Constraint('unique(issue_id, horse_id)', 'Each horse appears once on a feed issue.')

    def write(self, vals):
        if any(l.issue_id.state != 'draft' for l in self) and set(vals) - {'value'}:
            raise UserError(self.env._("An issued feed record cannot be changed."))
        return super().write(vals)

    def unlink(self):
        if any(l.issue_id.state != 'draft' for l in self):
            raise UserError(self.env._("An issued feed record cannot be changed."))
        return super().unlink()
