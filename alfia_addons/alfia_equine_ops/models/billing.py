from dateutil.relativedelta import relativedelta

from odoo import Command, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .common import BILLING_MANAGERS, require_any


class AlfiaCharge(models.Model):
    """A billable service for one horse. Charges are approved, then turned into invoices exactly once."""
    _name = 'alfia.charge'
    _description = 'Charge to invoice'
    _inherit = ['mail.thread']
    _order = 'date desc, id desc'

    horse_id = fields.Many2one('alfia.horse', required=True, index=True, ondelete='restrict')
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company, required=True)
    partner_id = fields.Many2one('res.partner', string='Bill to', compute='_compute_partner', store=True, readonly=False)
    product_id = fields.Many2one('product.product', string='Service', required=True, domain="[('type', '=', 'service')]")
    name = fields.Char(string='Description')
    quantity = fields.Float(default=1.0, required=True)
    price_unit = fields.Float(string='Unit price', compute='_compute_price', store=True, readonly=False)
    amount = fields.Float(compute='_compute_amount', store=True)
    date = fields.Date(required=True, default=fields.Date.context_today)
    state = fields.Selection([('draft', 'To approve'), ('approved', 'Approved'), ('invoiced', 'Invoiced'), ('cancelled', 'Cancelled')],
                             default='draft', required=True, tracking=True, index=True)
    move_id = fields.Many2one('account.move', string='Invoice', readonly=True, copy=False)
    plan_id = fields.Many2one('alfia.charge.plan', readonly=True, copy=False, ondelete='set null')
    key = fields.Char(copy=False, index=True)

    _key_uniq = models.Constraint('unique(key)', 'This charge was already generated.')

    @api.depends('horse_id')
    def _compute_partner(self):
        for charge in self:
            horse = charge.horse_id
            charge.partner_id = charge.partner_id or horse.invoice_to_id or horse.owner_ids[:1]

    @api.depends('product_id')
    def _compute_price(self):
        for charge in self:
            charge.price_unit = charge.product_id.lst_price if charge.product_id else 0.0

    @api.depends('quantity', 'price_unit')
    def _compute_amount(self):
        for charge in self:
            charge.amount = charge.quantity * charge.price_unit

    @api.constrains('quantity', 'price_unit')
    def _check_values(self):
        for charge in self:
            if charge.quantity <= 0 or charge.price_unit < 0:
                raise ValidationError(self.env._("Quantity must be above zero and the price cannot be negative."))

    def write(self, vals):
        if any(c.state == 'invoiced' for c in self) and set(vals) - {'state', 'move_id'}:
            raise UserError(self.env._("An invoiced charge cannot be changed."))
        return super().write(vals)

    def unlink(self):
        if any(c.state == 'invoiced' for c in self):
            raise UserError(self.env._("An invoiced charge cannot be deleted."))
        return super().unlink()

    def action_approve(self):
        require_any(self.env, BILLING_MANAGERS, self.env._("managers or accounting staff"))
        self.filtered(lambda c: c.state == 'draft').write({'state': 'approved'})

    def action_cancel(self):
        if any(c.state == 'invoiced' for c in self):
            raise UserError(self.env._("An invoiced charge cannot be cancelled. Issue a credit note instead."))
        self.write({'state': 'cancelled'})

    def action_invoice(self):
        """One invoice per customer for the approved charges in `self`. Charges already invoiced are skipped, so repeating
        the action never creates a second invoice line for the same charge."""
        require_any(self.env, ('account.group_account_invoice', 'base.group_system'), self.env._("accounting staff"))
        todo = self.filtered(lambda c: c.state == 'approved' and not c.move_id)
        invoices = self.env['account.move']
        for (partner, company), charges in todo.grouped(lambda c: (c.partner_id, c.company_id)).items():
            if not partner:
                raise UserError(self.env._("Choose who to bill for %s.", charges[0].horse_id.name))
            move = self.env['account.move'].with_company(company).create({
                'move_type': 'out_invoice', 'partner_id': partner.id, 'company_id': company.id, 'invoice_date': fields.Date.context_today(self),
                'invoice_line_ids': [Command.create({
                    'product_id': c.product_id.id, 'name': f"{c.horse_id.name} – {c.name or c.product_id.display_name} ({c.date})",
                    'quantity': c.quantity, 'price_unit': c.price_unit}) for c in charges]})
            charges.write({'state': 'invoiced', 'move_id': move.id})
            invoices |= move
        if not invoices:
            return False
        return {'type': 'ir.actions.act_window', 'res_model': 'account.move', 'view_mode': 'list,form', 'domain': [('id', 'in', invoices.ids)]}


class AlfiaChargePlan(models.Model):
    _name = 'alfia.charge.plan'
    _description = 'Recurring charge'
    _order = 'name'

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company, required=True)
    target_kind = fields.Selection([('horse', 'One horse'), ('location', 'Every horse in a barn / location'), ('group', 'Every horse in a group')], required=True, default='horse')
    horse_id = fields.Many2one('alfia.horse')
    location_id = fields.Many2one('stock.location')
    group_id = fields.Many2one('alfia.horse.group')
    partner_id = fields.Many2one('res.partner', string='Bill to (otherwise the horse\'s payer)')
    product_id = fields.Many2one('product.product', string='Service', required=True, domain="[('type', '=', 'service')]")
    quantity = fields.Float(default=1.0, required=True)
    price_unit = fields.Float(string='Unit price')
    interval = fields.Selection([('weekly', 'Every week'), ('monthly', 'Every month')], required=True, default='monthly')
    next_date = fields.Date(required=True, default=fields.Date.context_today)
    end_date = fields.Date()
    auto_approve = fields.Boolean(help="Generated charges are approved at once instead of waiting for a manager.")

    @api.constrains('target_kind', 'horse_id', 'location_id', 'group_id')
    def _check_target(self):
        for plan in self:
            if not {'horse': plan.horse_id, 'location': plan.location_id, 'group': plan.group_id}[plan.target_kind]:
                raise ValidationError(self.env._("Choose what the plan applies to."))

    def _target_horses(self):
        self.ensure_one()
        Horse = self.env['alfia.horse'].sudo()
        if self.target_kind == 'horse':
            return self.horse_id
        if self.target_kind == 'location':
            return Horse.search([('location_id', 'child_of', self.location_id.id), ('status', '=', 'active')])
        return self.group_id.sudo().all_member_ids.filtered(lambda h: h.status == 'active')

    def action_generate(self, upto=None):
        """Create the charges that are due up to `upto`. Safe to repeat: each (plan, horse, date) is generated once."""
        upto = upto or fields.Date.context_today(self)
        created = 0
        Charge = self.env['alfia.charge'].sudo()
        for plan in self.filtered('active'):
            while plan.next_date <= upto and (not plan.end_date or plan.next_date <= plan.end_date):
                for horse in plan._target_horses():
                    key = f"plan:{plan.id}:{horse.id}:{plan.next_date}"
                    if Charge.search_count([('key', '=', key)]):
                        continue
                    Charge.create({
                        'horse_id': horse.id, 'product_id': plan.product_id.id, 'quantity': plan.quantity, 'name': plan.name,
                        'price_unit': plan.price_unit or plan.product_id.lst_price, 'date': plan.next_date, 'plan_id': plan.id, 'key': key,
                        'company_id': plan.company_id.id, 'partner_id': plan.partner_id.id or horse.invoice_to_id.id or horse.owner_ids[:1].id,
                        'state': 'approved' if plan.auto_approve else 'draft'})
                    created += 1
                plan.next_date = plan.next_date + (relativedelta(weeks=1) if plan.interval == 'weekly' else relativedelta(months=1))
        return created

    @api.model
    def _cron_generate(self):
        self.search([('active', '=', True)]).action_generate()
