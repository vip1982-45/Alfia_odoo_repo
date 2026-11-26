from odoo import api, fields, models
from odoo.exceptions import UserError

DEFAULT_COUNT_REASON = 'Physical Inventory'


class ResCompany(models.Model):
    _inherit = 'res.company'

    alfia_count_second_person_value = fields.Monetary(
        string='Stock count: second person from', currency_field='currency_id', default=1000.0,
        help="A count adjustment worth this much or more (quantity difference × cost) must be applied by an approver who "
             "did not record the count.")
    alfia_bill_price_tolerance = fields.Float(
        string='Bill price tolerance (%)', default=2.0,
        help="A supplier bill line may be up to this much above its purchase order price. Above it, a purchase manager "
             "approves the difference (with a reason) before the bill can be posted.")


class StockMove(models.Model):
    _inherit = 'stock.move'

    def _action_done(self, cancel_backorder=False):
        done = super()._action_done(cancel_backorder=cancel_backorder)
        done._alfia_check_no_negative_stock()
        return done

    def _alfia_check_no_negative_stock(self):
        """Nothing is issued beyond what is in stock (owner decision, 25 Nov 2026): after a transfer, no internal location
        may hold a negative quantity. Count adjustments set the quantity themselves and are not affected."""
        if self.env.context.get('inventory_mode'):
            return
        lines = self.filtered(lambda m: not m.is_inventory).move_line_ids.filtered(
            lambda l: l.state == 'done' and l.location_id.usage == 'internal')
        if not lines:
            return
        Quant = self.env['stock.quant'].sudo()
        problems = []
        for (product, location, lot) in {(l.product_id, l.location_id, l.lot_id) for l in lines}:
            if not product.is_storable:
                continue
            on_hand = sum(Quant.search([('product_id', '=', product.id), ('location_id', '=', location.id),
                                        ('lot_id', '=', lot.id or False)]).mapped('quantity'))
            if on_hand < -1e-9:
                problems.append(self.env._("%(product)s%(lot)s in %(loc)s (short by %(short)g)", product=product.display_name,
                                           lot=f" / {lot.name}" if lot else '', loc=location.complete_name, short=-on_hand))
        if problems:
            raise UserError(self.env._("Not enough in stock to issue: %s. Issue only what is there and leave the rest as a "
                                       "backorder, or receive the goods first.", '; '.join(problems)))


class StockMoveLine(models.Model):
    _inherit = 'stock.move.line'

    def _alfia_is_dispatch_step(self):
        """A move towards the customer: to a non-stock location, or within the warehouse to its packing or output area,
        or as a pick, pack or delivery operation."""
        self.ensure_one()
        dest = self.location_dest_id
        if dest.usage not in ('internal', 'inventory'):
            return True
        warehouses = self.env['stock.warehouse'].sudo().search([('company_id', '=', self.company_id.id)])
        dispatch_areas = warehouses.wh_output_stock_loc_id | warehouses.wh_pack_stock_loc_id
        dispatch_types = warehouses.pick_type_id | warehouses.pack_type_id | warehouses.out_type_id
        return bool(dest & dispatch_areas) or bool(self.move_id.picking_type_id & dispatch_types) \
            or self.move_id.picking_type_id.code == 'outgoing'

    def _action_done(self):
        # An expired batch is not issued (it may only be scrapped, counted or moved within the store, e.g. to quarantine);
        # it does not go towards a customer either, not even to the packing or output area. The expiry date comes from
        # the batch.
        for ml in self:
            lot = ml.lot_id
            expiry = 'expiration_date' in lot._fields and lot.expiration_date
            if (expiry and expiry <= fields.Datetime.now() and ml.location_id.usage == 'internal'
                    and not ml.move_id.is_scrap and not ml.move_id.is_inventory and ml._alfia_is_dispatch_step()):
                raise UserError(self.env._("%(product)s batch %(lot)s expired on %(date)s and cannot be issued. Scrap it "
                                           "instead.", product=ml.product_id.display_name, lot=lot.name,
                                           date=fields.Date.to_date(expiry)))
        return super()._action_done()


class StockQuant(models.Model):
    _inherit = 'stock.quant'

    alfia_counted_by_id = fields.Many2one('res.users', string='Counted by', readonly=True, copy=False)

    def write(self, vals):
        if 'inventory_quantity' in vals and not self.env.su:
            vals = dict(vals, alfia_counted_by_id=self.env.user.id)
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            vals_list = [dict(v, alfia_counted_by_id=self.env.user.id) if 'inventory_quantity' in v else v for v in vals_list]
        return super().create(vals_list)

    def _apply_inventory(self, date=None):
        if not self.env.su:
            reason = (self.env.context.get('inventory_name') or '').strip()
            if not reason or reason == DEFAULT_COUNT_REASON or reason == self.env._(DEFAULT_COUNT_REASON):
                raise UserError(self.env._("Type the reason for this stock adjustment (what was found and why it differs)."))
            for quant in self.filtered('inventory_quantity_set'):
                company = quant.company_id or self.env.company
                value = abs(quant.inventory_diff_quantity) * (quant.product_id.standard_price or 0.0)
                if (value >= (company.alfia_count_second_person_value or 0.0) and quant.alfia_counted_by_id == self.env.user
                        and self.env['alfia.sod.mixin']._alfia_sod_applies(None, company)):
                    raise UserError(self.env._(
                        "%(product)s: this adjustment is worth %(value)s, and you recorded the count, so another approver "
                        "must apply it.", product=quant.product_id.display_name, value=f"{value:,.2f} {company.currency_id.name}"))
        return super()._apply_inventory(date)
