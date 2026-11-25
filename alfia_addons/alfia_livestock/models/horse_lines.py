"""A "Horse" column on sale, purchase and invoice lines: the line's amount goes to that horse's cost centre, so it shows
in the horse's profit and loss, without anyone having to know analytic accounting. A semen sale is credited to the
stallion whose batch was delivered."""
from odoo import api, fields, models


def _horse_plan_account_ids(env):
    plan = env['alfia.horse'].sudo()._alfia_horse_plan()
    return set(env['account.analytic.account'].sudo().with_context(active_test=False).search([('plan_id', '=', plan.id)]).ids)


def _with_horse(env, distribution, horse):
    """The analytic distribution with the horse's cost centre in place of any other horse's (or with none if no horse)."""
    horse_ids = _horse_plan_account_ids(env)
    account = horse.sudo().analytic_account_id if horse else env['account.analytic.account']
    result = {}
    for key, share in (distribution or {}).items():
        ids = [part for part in str(key).split(',') if part and int(part) not in horse_ids]
        if account:
            ids.append(str(account.id))
        if ids:
            result[','.join(ids)] = result.get(','.join(ids), 0) + share
    if account and not result:
        result = {str(account.id): 100}
    return result or False


class AlfiaHorseLineMixin(models.AbstractModel):
    _name = 'alfia.horse.line.mixin'
    _description = 'Line allocated to a horse'

    alfia_horse_id = fields.Many2one('alfia.horse', string='Horse', index='btree_not_null',
                                     help="The horse this line is for: its amount counts in the horse's profit and loss.")

    def _alfia_apply_horse(self):
        for line in self:
            wanted = _with_horse(self.env, line.analytic_distribution, line.alfia_horse_id)
            if (wanted or False) != (line.analytic_distribution or False):
                super(AlfiaHorseLineMixin, line).write({'analytic_distribution': wanted})

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines.filtered('alfia_horse_id')._alfia_apply_horse()
        return lines

    def write(self, vals):
        res = super().write(vals)
        if 'alfia_horse_id' in vals or ('analytic_distribution' in vals and any(self.mapped('alfia_horse_id'))):
            self._alfia_apply_horse()
        return res


class AccountMoveLine(models.Model):
    _name = 'account.move.line'
    _inherit = ['account.move.line', 'alfia.horse.line.mixin']


class SaleOrderLine(models.Model):
    _name = 'sale.order.line'
    _inherit = ['sale.order.line', 'alfia.horse.line.mixin']

    def _prepare_invoice_line(self, **optional_values):
        vals = super()._prepare_invoice_line(**optional_values)
        if self.alfia_horse_id:
            vals['alfia_horse_id'] = self.alfia_horse_id.id
        return vals


class PurchaseOrderLine(models.Model):
    _name = 'purchase.order.line'
    _inherit = ['purchase.order.line', 'alfia.horse.line.mixin']

    def _prepare_account_move_line(self, move=False):
        vals = super()._prepare_account_move_line(move)
        if self.alfia_horse_id:
            vals['alfia_horse_id'] = self.alfia_horse_id.id
        return vals


class StockMove(models.Model):
    _inherit = 'stock.move'

    def _action_done(self, cancel_backorder=False):
        done = super()._action_done(cancel_backorder=cancel_backorder)
        done._alfia_credit_semen_to_stallion()
        return done

    def _alfia_credit_semen_to_stallion(self):
        """Semen delivered on a sale: the sale line (and its draft invoice lines) are for the stallion whose batch left."""
        semen = self.env.ref('alfia_equine.category_semen', raise_if_not_found=False)
        if not semen:
            return
        moves = self.filtered(lambda m: m.state == 'done' and 'sale_line_id' in m._fields and m.sale_line_id
                              and not m.sale_line_id.alfia_horse_id and m.product_id.categ_id.parent_path
                              and m.product_id.categ_id.parent_path.startswith(semen.parent_path))
        Collection = self.env['alfia.semen.collection'].sudo()
        for move in moves:
            stallions = Collection.search([('lot_id', 'in', move.move_line_ids.lot_id.ids)]).stallion_id
            if len(stallions) == 1:
                line = move.sale_line_id.sudo()
                line.alfia_horse_id = stallions
                line.invoice_lines.filtered(lambda l: l.parent_state == 'draft' and not l.alfia_horse_id).write(
                    {'alfia_horse_id': stallions.id})
