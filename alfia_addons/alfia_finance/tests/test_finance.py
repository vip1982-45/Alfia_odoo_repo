"""End-to-end finance and supply-chain flows, run the way the farm works them."""
from dateutil.relativedelta import relativedelta

from odoo import Command, fields
from odoo.addons.alfia_equine.tests.test_equine import EquineCase
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged


@tagged('post_install', '-at_install', 'alfia_finance')
class FinanceCase(EquineCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.company = env.company
        cls.wh = env['stock.warehouse'].search([('company_id', '=', cls.company.id)], limit=1)
        cls.wh.write({'reception_steps': 'two_steps', 'delivery_steps': 'pick_ship'})
        cls.categ = env['product.category'].create({'name': 'Feed (test)', 'property_cost_method': 'average', 'property_valuation': 'real_time'})
        cls.oats = env['product.product'].create({'name': 'Oats (test)', 'type': 'consu', 'is_storable': True, 'categ_id': cls.categ.id,
                                                  'list_price': 120.0, 'standard_price': 0.0})
        cls.freight = env['product.product'].create({'name': 'Freight (test)', 'type': 'service', 'landed_cost_ok': True,
                                                     'split_method_landed_cost': 'equal'})
        cls.vendor = env['res.partner'].create({'name': 'Feed vendor (test)'})
        cls.customer = env['res.partner'].create({'name': 'Feed customer (test)'})
        g = lambda x: env.ref(x).id  # noqa: E731

        def user(login, groups):
            return env['res.users'].with_context(no_reset_password=True).create({
                'name': login, 'login': f'{login}@fin.local', 'email': f'{login}@fin.local',
                'group_ids': [Command.link(g(x)) for x in groups + ['base.group_user', 'alfia_security.group_alfia_all_locations',
                                                                     'alfia_security.group_alfia_all_warehouses', 'alfia_security.group_alfia_all_journals']]})
        cls.buyer = user('buyer', ['purchase.group_purchase_user', 'stock.group_stock_user'])
        cls.purchase_approver = user('papprover', ['purchase.group_purchase_manager', 'stock.group_stock_user'])
        cls.storekeeper = user('store', ['stock.group_stock_user'])
        cls.ap_clerk = user('apclerk', ['account.group_account_invoice', 'purchase.group_purchase_user'])
        cls.ap_poster = user('apposter', ['account.group_account_invoice', 'purchase.group_purchase_user'])
        cls.salesman = user('sales', ['sales_team.group_sale_salesman', 'stock.group_stock_user'])
        cls.ar_clerk = user('arclerk', ['account.group_account_invoice', 'sales_team.group_sale_salesman'])

    # ------------------------------------------------------------------ helpers
    def _validate(self, picking, user=None):
        picking = picking.with_user(user) if user else picking
        picking.action_assign()
        for move in picking.move_ids:
            move.quantity = move.product_uom_qty
        move_ids = picking.move_ids
        move_ids.picked = True
        res = picking.button_validate()
        if isinstance(res, dict) and res.get('res_model'):
            wizard = self.env[res['res_model']].with_context(res.get('context', {})).create({})
            wizard.process()
        self.assertEqual(picking.state, 'done', picking.name)

    def _pay(self, move, user=None):
        Register = self.env['account.payment.register'].with_user(user) if user else self.env['account.payment.register']
        Register.with_context(active_model='account.move', active_ids=move.ids).create({})._create_payments()
        self.assertIn(move.payment_state, ('paid', 'in_payment'), move.name)

    def _balanced(self):
        debit, credit = self.env['account.move.line']._read_group([('parent_state', '=', 'posted'), ('company_id', '=', self.company.id)],
                                                                  [], ['debit:sum', 'credit:sum'])[0]
        self.assertAlmostEqual(debit or 0.0, credit or 0.0, places=2, msg="the trial balance must balance")

    def _stock(self, location):
        return self.oats.with_context(location=location.id).qty_available

    def _receive(self, qty=10.0, price=50.0):
        po = self.env['purchase.order'].with_user(self.buyer).create({'partner_id': self.vendor.id, 'order_line': [
            Command.create({'product_id': self.oats.id, 'product_qty': qty, 'price_unit': price})]})
        po.with_user(self.buyer).button_confirm()
        if po.state != 'purchase':
            po.with_user(self.purchase_approver).button_approve()
        self.assertEqual(po.state, 'purchase')
        receipt = po.picking_ids
        self.assertEqual(receipt.location_dest_id, self.wh.wh_input_stock_loc_id, "two-step receipt: goods first arrive at Input")
        self._validate(receipt, self.storekeeper)
        putaway = receipt.move_ids.move_dest_ids.picking_id
        self.assertTrue(putaway, "the put-away transfer from Input to Stock is created automatically")
        self._validate(putaway, self.storekeeper)
        return po, receipt


@tagged('post_install', '-at_install', 'alfia_finance')
class TestProcureToPay(FinanceCase):

    def test_purchase_receipt_bill_payment_and_valuation(self):
        po, receipt = self._receive(10, 50)
        self.assertEqual(self._stock(self.wh.lot_stock_id), 10)
        self.assertAlmostEqual(self.oats.standard_price, 50.0, msg="average cost from the receipt")
        self.assertEqual(po.qty_received if hasattr(po, 'qty_received') else po.order_line.qty_received, 10)

        po.with_user(self.ap_clerk).action_create_invoice()
        bill = po.invoice_ids
        self.assertEqual(bill.invoice_line_ids.quantity, 10, "the bill is matched to what was received")
        bill.invoice_date = fields.Date.today()
        with self.assertRaises(UserError):
            bill.with_user(self.ap_clerk).action_post()      # whoever entered the bill does not post it
        bill.with_user(self.ap_poster).action_post()
        self.assertEqual(bill.state, 'posted')
        stock_account = self.oats.product_tmpl_id.get_product_accounts()['stock_valuation']
        stock_debit = sum(bill.line_ids.filtered(lambda l: l.account_id == stock_account).mapped('debit'))
        self.assertAlmostEqual(stock_debit, 500.0, places=2, msg="the bill puts the received stock's value on the stock valuation account")
        # Paying is two people too: the clerk registers the payment, a second person confirms it.
        self.env['account.payment.register'].with_user(self.ap_clerk).with_context(active_model='account.move', active_ids=bill.ids).create({})._create_payments()
        payment = self.env['account.payment'].search([('partner_id', '=', self.vendor.id)], order='id desc', limit=1)
        self.assertEqual((payment.state, payment.payment_type, payment.invoice_ids), ('draft', 'outbound', bill),
                         "the registered payment waits for a second person, linked to the bill")
        self.assertNotIn(bill.payment_state, ('paid', 'in_payment'))
        with self.assertRaises(UserError):
            payment.with_user(self.ap_clerk).action_post()
        payment.with_user(self.ap_poster).action_post()
        self.assertIn(bill.payment_state, ('paid', 'in_payment'), "confirming the payment settles the bill")
        self._balanced()

    def test_landed_cost_is_allocated_to_received_stock(self):
        _po, receipt = self._receive(10, 50)
        cost = self.env['stock.landed.cost'].create({
            'picking_ids': [Command.set(receipt.ids)],
            'cost_lines': [Command.create({'product_id': self.freight.id, 'name': 'Freight', 'price_unit': 100.0, 'split_method': 'equal'})]})
        cost.compute_landed_cost()
        cost.button_validate()
        self.assertEqual(cost.state, 'done')
        self.assertTrue(cost.account_move_id and cost.account_move_id.state == 'posted', "the allocation is posted")
        self.assertAlmostEqual(self.oats.standard_price, 60.0, places=2, msg="freight of 100 over 10 units raises the average cost to 60")
        self._balanced()

    def test_purchase_agreement_feeds_purchase_orders(self):
        agreement = self.env['purchase.requisition'].create({'vendor_id': self.vendor.id, 'requisition_type': 'blanket_order', 'line_ids': [
            Command.create({'product_id': self.oats.id, 'product_qty': 100, 'price_unit': 45.0})]})
        agreement.action_confirm()
        po = self.env['purchase.order'].create({'partner_id': self.vendor.id, 'requisition_id': agreement.id, 'order_line': [
            Command.create({'product_id': self.oats.id, 'product_qty': 20, 'price_unit': 45.0})]})
        self.assertIn(po, agreement.purchase_ids)


@tagged('post_install', '-at_install', 'alfia_finance')
class TestOrderToCash(FinanceCase):

    def test_sale_pick_list_reservation_delivery_invoice_payment(self):
        self._receive(10, 50)
        so = self.env['sale.order'].with_user(self.salesman).create({'partner_id': self.customer.id, 'order_line': [
            Command.create({'product_id': self.oats.id, 'product_uom_qty': 4, 'price_unit': 120.0})]})
        so.with_user(self.salesman).action_confirm()
        pick = so.picking_ids.filtered(lambda p: p.location_dest_id == self.wh.wh_output_stock_loc_id)
        self.assertTrue(pick, "pick-then-ship: a pick list from Stock to Output")
        pick.action_assign()
        self.assertEqual(pick.state, 'assigned', "the stock is reserved for the order")
        reserved = sum(self.env['stock.quant'].search([('product_id', '=', self.oats.id), ('location_id', '=', self.wh.lot_stock_id.id)]).mapped('reserved_quantity'))
        self.assertEqual(reserved, 4)
        self._validate(pick, self.storekeeper)
        ship = so.picking_ids - pick
        self._validate(ship, self.storekeeper)
        self.assertEqual(self._stock(self.wh.lot_stock_id), 6)

        invoice = so.with_user(self.ar_clerk)._create_invoices()
        self.assertEqual(invoice.invoice_line_ids.quantity, 4, "invoiced what was delivered")
        invoice.with_user(self.ar_clerk).action_post()
        cogs = invoice.line_ids.filtered(lambda l: l.display_type == 'cogs')
        self.assertTrue(cogs, "the invoice carries the cost of the goods delivered")
        self.assertAlmostEqual(sum(cogs.mapped('debit')), 200.0, places=2, msg="4 units at an average cost of 50")
        self._pay(invoice, self.ar_clerk)
        self.assertEqual(so.invoice_status, 'invoiced')
        self._balanced()


@tagged('post_install', '-at_install', 'alfia_finance')
class TestInventory(FinanceCase):

    def test_count_adjustment_posts_and_needs_an_approver(self):
        self._receive(10, 50)
        quant = self.env['stock.quant'].search([('product_id', '=', self.oats.id), ('location_id', '=', self.wh.lot_stock_id.id)])
        quant.with_user(self.storekeeper).inventory_quantity = 8
        with self.assertRaises(AccessError):
            quant.with_user(self.storekeeper).action_apply_inventory()
        approver = self.storekeeper.copy({'login': 'adjust@fin.local'})
        approver.write({'group_ids': [Command.link(self.env.ref('alfia_security.group_alfia_stock_adjust_approver').id)]})
        quant.with_user(approver).action_apply_inventory()
        self.assertEqual(self._stock(self.wh.lot_stock_id), 8)
        self._balanced()

    def test_internal_transfer_between_barns(self):
        self._receive(10, 50)
        move = self.env['stock.picking'].create({
            'picking_type_id': self.wh.int_type_id.id, 'location_id': self.wh.lot_stock_id.id, 'location_dest_id': self.barn_a.id,
            'move_ids': [Command.create({'product_id': self.oats.id, 'product_uom_qty': 3, 'location_id': self.wh.lot_stock_id.id, 'location_dest_id': self.barn_a.id})]})
        move.action_confirm()
        self._validate(move, self.storekeeper)
        self.assertEqual(self._stock(self.barn_a), 3)


@tagged('post_install', '-at_install', 'alfia_finance')
class TestReportsAndCostAllocation(FinanceCase):

    def test_every_horse_has_a_cost_centre_and_charges_land_on_it(self):
        self.assertTrue(self.mare.analytic_account_id)
        foal = self.env['alfia.horse'].create({'name': 'Cost centre foal', 'sex': 'female'})
        self.assertEqual(foal.analytic_account_id.plan_id.name, 'Horses')
        service = self.env['product.product'].create({'name': 'Board (test)', 'type': 'service', 'list_price': 300})
        charge = self.env['alfia.charge'].create({'horse_id': self.mare.id, 'product_id': service.id, 'partner_id': self.customer.id})
        charge.action_approve()
        charge.action_invoice()
        invoice = charge.move_id
        self.assertEqual(invoice.invoice_line_ids.analytic_distribution, {str(self.mare.analytic_account_id.id): 100})
        invoice.action_post()
        column = self.env['alfia.horse']._alfia_horse_plan()._column_name()
        lines = self.env['account.analytic.line'].search([(column, '=', self.mare.analytic_account_id.id)])
        self.assertAlmostEqual(sum(lines.mapped('amount')), 300.0, places=2, msg="the horse's income is on its cost centre")

    def test_aged_receivables(self):
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': self.customer.id, 'invoice_date': fields.Date.today() - relativedelta(days=50),
            'invoice_date_due': fields.Date.today() - relativedelta(days=45),
            'invoice_line_ids': [Command.create({'name': 'Old board', 'quantity': 1, 'price_unit': 1000.0, 'tax_ids': [Command.clear()]})]})
        invoice.action_post()
        self.env.flush_all()
        row = self.env['alfia.aged.balance'].search([('move_id', '=', invoice.id)])
        self.assertEqual((row.kind, row.bucket, row.amount_residual), ('receivable', '2_60', 1000.0))
        self._pay(invoice)
        self.env.flush_all()
        self.assertFalse(self.env['alfia.aged.balance'].search([('move_id', '=', invoice.id)]), "a paid invoice leaves the aged balance")

    def test_finance_reports_open(self):
        for xmlid in ('alfia_finance.alfia_trial_balance_action', 'alfia_finance.alfia_general_ledger_action', 'alfia_finance.alfia_profit_loss_action',
                      'alfia_finance.alfia_balance_sheet_action', 'alfia_finance.alfia_aged_receivable_action', 'alfia_finance.alfia_aged_payable_action'):
            action = self.env.ref(xmlid)
            domain = eval(action.domain or '[]', {'fields': fields})  # noqa: S307 - our own static domains
            self.env[action.res_model].with_user(self.ar_clerk).search(domain, limit=5)
        costs = self.env['alfia.horse'].with_user(self.ar_clerk).action_alfia_horse_costs()
        self.env[costs['res_model']].with_user(self.ar_clerk).search(costs['domain'], limit=5)


@tagged('post_install', '-at_install', 'alfia_finance')
class TestStarterFinanceProfiles(FinanceCase):

    def test_accounting_profiles_see_every_warehouses_orders(self):
        """Bills are matched to purchase orders: an accountant who could not see the order lines would get an empty bill."""
        po = self.env['purchase.order'].create({'partner_id': self.vendor.id, 'order_line': [
            Command.create({'product_id': self.oats.id, 'product_qty': 2, 'price_unit': 10.0})]})
        for xmlid in ('alfia_security.profile_ap_clerk', 'alfia_security.profile_accountant', 'alfia_security.profile_finance_controller'):
            profile = self.env.ref(xmlid)
            user = self.env['res.users'].with_context(no_reset_password=True).create({
                'name': xmlid, 'login': f'{xmlid}@fin.local', 'alfia_profile_ids': [Command.set(profile.ids)]})
            self.assertEqual(self.env['purchase.order.line'].with_user(user).search_count([('order_id', '=', po.id)]), 1, xmlid)
