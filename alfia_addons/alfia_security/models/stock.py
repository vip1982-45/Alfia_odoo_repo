from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

MOVE_WATCHED = {'product_id', 'product_uom_qty', 'quantity', 'location_id', 'location_dest_id', 'lot_id'}


class AlfiaPickingApproval(models.Model):
    _name = 'alfia.picking.approval'
    _description = 'Dispatch approval'
    _order = 'id'

    picking_id = fields.Many2one('stock.picking', required=True, ondelete='cascade', index=True)
    group_id = fields.Many2one('res.groups', required=True, ondelete='restrict')
    user_id = fields.Many2one('res.users', required=True, ondelete='restrict')
    date = fields.Datetime(default=fields.Datetime.now, required=True)

    _one_per_group = models.Constraint('unique(picking_id, group_id)', 'This approval is already recorded.')
    _one_per_user = models.Constraint('unique(picking_id, user_id)', 'Each approval must come from a different person.')


class StockPicking(models.Model):
    _name = 'stock.picking'
    _inherit = ['stock.picking', 'alfia.sod.mixin']

    alfia_approval_ids = fields.One2many('alfia.picking.approval', 'picking_id', string='Dispatch approvals', copy=False)
    alfia_required_group_ids = fields.Many2many('res.groups', compute='_compute_alfia_required_groups', string='Required approvals')
    alfia_missing_group_ids = fields.Many2many('res.groups', compute='_compute_alfia_required_groups', string='Missing approvals')

    @api.depends('move_ids.product_id.categ_id', 'move_ids.location_dest_id.usage', 'picking_type_code', 'alfia_approval_ids.group_id')
    def _compute_alfia_required_groups(self):
        for picking in self:
            required = self.env['res.groups']
            # Controlled stock leaving the farm's own locations (to a customer, a supplier, a loss or production) needs the
            # approvals, whatever the operation type is called.
            leaves = picking.picking_type_code == 'outgoing' or any(
                m.location_dest_id.usage not in ('internal', 'transit', 'view') for m in picking.move_ids)
            if leaves:
                for categ in picking.move_ids.product_id.categ_id:
                    required |= categ._alfia_dual_config()
            picking.alfia_required_group_ids = required
            picking.alfia_missing_group_ids = required - picking.alfia_approval_ids.group_id

    def action_alfia_approve(self):
        """Record the current user's approval of this dispatch, for the first required function they hold."""
        user = self.env.user
        for picking in self:
            if picking.state in ('done', 'cancel'):
                raise UserError(self.env._("%s is already closed.", picking.name))
            missing = picking.alfia_missing_group_ids
            if not picking.alfia_required_group_ids:
                raise UserError(self.env._("%s does not need dispatch approval.", picking.name))
            mine = missing & user.all_group_ids
            if not mine:
                raise AccessError(self.env._("You are not an approver for the missing approvals of %s, or they are already given.", picking.name))
            if user in picking.alfia_approval_ids.user_id:
                raise UserError(self.env._("You have already approved %s. The other approval must come from a different person.", picking.name))
            if picking.create_uid == user and picking._alfia_sod_applies():
                raise UserError(self.env._("You requested this transfer, so someone else must approve it."))
            group = mine.sorted('id')[:1]
            self.env['alfia.picking.approval'].sudo().create({'picking_id': picking.id, 'group_id': group.id, 'user_id': user.id})
            picking.sudo().message_post(body=self.env._("Dispatch approved by %(user)s as %(group)s.", user=user.name, group=group.display_name), author_id=user.partner_id.id)
        return True

    def _alfia_check_dual_approval(self):
        for picking in self:
            if self.env.su or not picking.alfia_required_group_ids or picking.state in ('done', 'cancel'):
                continue
            missing = picking.alfia_missing_group_ids
            if missing:
                raise UserError(self.env._(
                    "%(picking)s contains controlled stock and cannot be dispatched yet. Missing approvals: %(missing)s.",
                    picking=picking.name, missing=', '.join(missing.mapped('display_name'))))
            if self.env.user in picking.alfia_approval_ids.user_id and picking._alfia_sod_applies():
                raise UserError(self.env._("An approver cannot also dispatch %s. Another person must validate the transfer.", picking.name))

    def button_validate(self):
        self._alfia_check_dual_approval()
        return super(StockPicking, self.with_context(alfia_keep_approvals=True)).button_validate()


class StockMove(models.Model):
    _inherit = 'stock.move'

    def _alfia_invalidate_approvals(self):
        if self.env.context.get('alfia_keep_approvals'):
            return
        pickings = self.picking_id.filtered(lambda p: p.alfia_approval_ids and p.state not in ('done', 'cancel'))
        if pickings:
            for picking in pickings:
                self.env['alfia.security.audit']._alfia_log('dispatch.approvals_reset', 'stock.picking', picking.name, "The transfer was changed after approval.")
                picking.sudo().message_post(body=self.env._("The transfer changed after it was approved. Approvals were cleared and must be given again."))
            pickings.alfia_approval_ids.sudo().unlink()

    def write(self, vals):
        if MOVE_WATCHED & set(vals):
            self._alfia_invalidate_approvals()
        return super().write(vals)

    def _action_scrap(self):
        """Writing off controlled stock (semen, embryos, medicine...) is an inventory adjustment: approvers only."""
        if not self.env.su and not self.env.user.has_group('alfia_security.group_alfia_stock_adjust_approver'):
            controlled = self.filtered(lambda m: m.product_id.categ_id.alfia_effective_group_id or m.product_id.categ_id.alfia_effective_approver_group_ids)
            if controlled:
                raise AccessError(self.env._("Only inventory-adjustment approvers can scrap controlled stock (%s).",
                                             ', '.join(controlled.product_id.mapped('display_name'))))
        return super()._action_scrap()


class StockMoveLine(models.Model):
    _inherit = 'stock.move.line'

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines.move_id._alfia_invalidate_approvals()
        return lines

    def write(self, vals):
        if MOVE_WATCHED & set(vals):
            self.move_id._alfia_invalidate_approvals()
        return super().write(vals)

    def unlink(self):
        self.move_id._alfia_invalidate_approvals()
        return super().unlink()


class StockQuant(models.Model):
    _inherit = 'stock.quant'

    def action_apply_inventory(self, date=None):
        if not self.env.su and not self.env.user.has_group('alfia_security.group_alfia_stock_adjust_approver'):
            raise AccessError(self.env._("Counting is open to stock staff, but only inventory-adjustment approvers can apply a count to the books."))
        return super().action_apply_inventory(date=date)

