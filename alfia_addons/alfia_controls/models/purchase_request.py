from odoo import Command, api, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.alfia_security.flow import workflow

PURCHASE_USER = 'purchase.group_purchase_user'
PURCHASE_MANAGER = 'purchase.group_purchase_manager'


class AlfiaPurchaseRequest(models.Model):
    """An internal request to buy (a barn, the clinic, the office): submitted by whoever needs it, approved by a purchase
    manager who is not the requester, then turned into requests for quotation, each line keeping its link to the order."""
    _name = 'alfia.purchase.request'
    _description = 'Purchase request'
    _inherit = ['mail.thread', 'alfia.guarded.mixin', 'alfia.sod.mixin']
    _order = 'id desc'
    _alfia_guarded = ('state', 'approved_by_id', 'approved_date', 'requested_by_id')

    name = fields.Char(readonly=True, copy=False, default='New')
    requested_by_id = fields.Many2one('res.users', string='Requested by', default=lambda self: self.env.user, required=True, readonly=True)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company, required=True)
    location_id = fields.Many2one('stock.location', string='Needed at', domain="[('usage', '=', 'internal')]")
    date_needed = fields.Date(string='Needed by')
    reason = fields.Text(required=True)
    line_ids = fields.One2many('alfia.purchase.request.line', 'request_id', copy=True)
    state = fields.Selection([('draft', 'Draft'), ('submitted', 'Waiting for approval'), ('approved', 'Approved'),
                              ('rejected', 'Rejected'), ('ordered', 'Ordered'), ('cancelled', 'Cancelled')],
                             default='draft', required=True, tracking=True, copy=False)
    approved_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    approved_date = fields.Datetime(readonly=True, copy=False)
    rejection_reason = fields.Text(copy=False, tracking=True)
    estimated_total = fields.Float(compute='_compute_totals', string='Estimated total')
    purchase_order_ids = fields.Many2many('purchase.order', compute='_compute_totals', string='Purchase orders')
    purchase_order_count = fields.Integer(compute='_compute_totals')

    @api.depends('line_ids.estimated_price', 'line_ids.quantity', 'line_ids.purchase_line_ids')
    def _compute_totals(self):
        for rec in self:
            rec.estimated_total = sum(line.quantity * line.estimated_price for line in rec.line_ids)
            # Shown to the requester too (who may not open purchase orders): read with system rights.
            rec.purchase_order_ids = rec.sudo().line_ids.purchase_line_ids.order_id.ids
            rec.purchase_order_count = len(rec.purchase_order_ids)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('alfia.purchase.request') or 'New'
        return super().create(vals_list)

    def write(self, vals):
        # Once submitted, what was asked for is what gets approved: lines and reason change only in draft.
        if {'line_ids', 'reason', 'company_id'} & set(vals) and any(r.state not in ('draft', 'rejected') for r in self) and not self.env.su:
            raise UserError(self.env._("A submitted request cannot be changed. Ask the approver to reject it, then change and resubmit it."))
        return super().write(vals)

    def _set_state(self, state, **extra):
        with workflow():
            self.write(dict(extra, state=state))

    def action_submit(self):
        for rec in self:
            if rec.state not in ('draft', 'rejected'):
                raise UserError(self.env._("Only a draft or rejected request can be submitted."))
            if not rec.line_ids:
                raise UserError(self.env._("Add what you need before submitting."))
            if rec.requested_by_id != self.env.user and not self.env.su:
                raise AccessError(self.env._("Only the requester submits the request."))
            rec._set_state('submitted', rejection_reason=False)
            rec.message_post(body=self.env._("Submitted for approval by %s.", self.env.user.name))

    def _check_approver(self):
        if not (self.env.su or self.env.user.has_group(PURCHASE_MANAGER)):
            raise AccessError(self.env._("Only a purchase manager can approve or reject purchase requests."))
        for rec in self:
            if rec.requested_by_id == self.env.user and rec._alfia_sod_applies(None, rec.company_id):
                raise UserError(self.env._("You requested %s, so another purchase manager must decide on it.", rec.name))

    def action_approve(self):
        self._check_approver()
        for rec in self:
            if rec.state != 'submitted':
                raise UserError(self.env._("Only a submitted request can be approved."))
            rec._set_state('approved', approved_by_id=self.env.user.id, approved_date=fields.Datetime.now())
            rec.message_post(body=self.env._("Approved by %s.", self.env.user.name))

    def action_reject(self):
        self._check_approver()
        for rec in self:
            if rec.state != 'submitted':
                raise UserError(self.env._("Only a submitted request can be rejected."))
            if not rec.rejection_reason:
                raise UserError(self.env._("Write the reason for rejecting %s first.", rec.name))
            rec._set_state('rejected')
            rec.message_post(body=self.env._("Rejected by %(user)s: %(reason)s", user=self.env.user.name, reason=rec.rejection_reason))

    def action_cancel(self):
        for rec in self:
            if rec.state == 'ordered':
                raise UserError(self.env._("%s is already ordered; cancel the purchase order instead.", rec.name))
            if rec.requested_by_id != self.env.user and not (self.env.su or self.env.user.has_group(PURCHASE_MANAGER)):
                raise AccessError(self.env._("Only the requester or a purchase manager can cancel a request."))
            rec._set_state('cancelled')

    def action_create_rfq(self):
        """One request for quotation per vendor; each order line remembers the request line it comes from."""
        if not (self.env.su or self.env.user.has_group(PURCHASE_USER)):
            raise AccessError(self.env._("Only purchasing staff turn requests into orders."))
        orders = self.env['purchase.order']
        for rec in self:
            if rec.state != 'approved':
                raise UserError(self.env._("Only an approved request can be ordered."))
            missing = rec.line_ids.filtered(lambda l: not l._vendor())
            if missing:
                raise UserError(self.env._("Choose a vendor for: %s", ', '.join(missing.product_id.mapped('display_name'))))
            for vendor, lines in rec.line_ids.grouped(lambda l: l._vendor()).items():
                order = self.env['purchase.order'].create({
                    'partner_id': vendor.id, 'company_id': rec.company_id.id, 'origin': rec.name,
                    'order_line': [Command.create(line._purchase_line_vals()) for line in lines]})
                orders |= order
            rec._set_state('ordered')
            rec.message_post(body=self.env._("Ordered: %s", ', '.join(rec.purchase_order_ids.mapped('name'))))
        return orders._get_action_view_purchase_orders() if hasattr(orders, '_get_action_view_purchase_orders') else {
            'type': 'ir.actions.act_window', 'res_model': 'purchase.order', 'view_mode': 'list,form', 'domain': [('id', 'in', orders.ids)]}

    def action_view_orders(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': 'purchase.order', 'view_mode': 'list,form',
                'domain': [('id', 'in', self.purchase_order_ids.ids)], 'name': self.env._("Purchase orders")}


class AlfiaPurchaseRequestLine(models.Model):
    _name = 'alfia.purchase.request.line'
    _description = 'Purchase request line'

    request_id = fields.Many2one('alfia.purchase.request', required=True, ondelete='cascade', index=True)
    state = fields.Selection(related='request_id.state')
    product_id = fields.Many2one('product.product', required=True, domain="[('purchase_ok', '=', True)]")
    description = fields.Char()
    quantity = fields.Float(required=True, default=1.0)
    uom_id = fields.Many2one('uom.uom', string='Unit', compute='_compute_uom', store=True, readonly=False)
    vendor_id = fields.Many2one('res.partner', string='Preferred vendor')
    estimated_price = fields.Float(string='Estimated unit price')
    purchase_line_ids = fields.One2many('purchase.order.line', 'alfia_request_line_id', string='Purchase order lines', readonly=True)
    purchase_order_id = fields.Many2one('purchase.order', compute='_compute_purchase_order', string='Purchase order')

    @api.depends('purchase_line_ids')
    def _compute_purchase_order(self):
        for line in self:
            line.purchase_order_id = line.sudo().purchase_line_ids[:1].order_id.id

    _qty_positive = models.Constraint('check(quantity > 0)', 'The quantity must be above zero.')

    def _check_editable(self):
        if not self.env.su and any(line.request_id.state not in ('draft', 'rejected') for line in self):
            raise UserError(self.env._("A submitted request cannot be changed. Ask the approver to reject it, then change and resubmit it."))

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines._check_editable()
        return lines

    def write(self, vals):
        self._check_editable()
        return super().write(vals)

    def unlink(self):
        self._check_editable()
        return super().unlink()

    @api.depends('product_id')
    def _compute_uom(self):
        for line in self:
            line.uom_id = line.uom_id or line.product_id.uom_id

    def _vendor(self):
        self.ensure_one()
        return self.vendor_id or self.product_id.seller_ids[:1].partner_id

    def _purchase_line_vals(self):
        self.ensure_one()
        vals = {'product_id': self.product_id.id, 'product_qty': self.quantity, 'alfia_request_line_id': self.id}
        if self.uom_id:
            vals['uom_id'] = self.uom_id.id
        if self.description:
            vals['name'] = self.description
        if self.estimated_price:
            vals['price_unit'] = self.estimated_price
        return vals


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    alfia_request_line_id = fields.Many2one('alfia.purchase.request.line', string='Purchase request line', readonly=True, copy=False, index=True)
    alfia_request_id = fields.Many2one(related='alfia_request_line_id.request_id', string='Purchase request', store=True)
