from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


class ControlsCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.company = env.company
        cls.wh = env['stock.warehouse'].search([('company_id', '=', cls.company.id)], limit=1)
        g = lambda x: env.ref(x).id  # noqa: E731

        def user(login, groups):
            return env['res.users'].with_context(no_reset_password=True).create({
                'name': login, 'login': f'{login}@ctl.local', 'email': f'{login}@ctl.local',
                'group_ids': [Command.link(g(x)) for x in ['base.group_user', 'alfia_security.group_alfia_all_locations',
                                                           'alfia_security.group_alfia_all_warehouses', 'alfia_security.group_alfia_all_journals'] + groups]})
        cls.requester = user('requester', [])
        cls.buyer = user('ctlbuyer', ['purchase.group_purchase_user', 'stock.group_stock_user'])
        cls.manager = user('ctlmanager', ['purchase.group_purchase_manager', 'stock.group_stock_user'])
        cls.manager2 = user('ctlmanager2', ['purchase.group_purchase_manager', 'stock.group_stock_user'])
        cls.vendor = env['res.partner'].create({'name': 'Controls vendor'})
        cls.feed = env['product.product'].create({'name': 'Controls feed', 'type': 'consu', 'is_storable': True,
                                                  'seller_ids': [Command.create({'partner_id': cls.vendor.id, 'price': 3})]})
        cls.other_vendor = env['res.partner'].create({'name': 'Other vendor'})
        cls.tack = env['product.product'].create({'name': 'Controls tack', 'type': 'consu'})


@tagged('post_install', '-at_install', 'alfia_controls')
class TestPurchaseRequest(ControlsCase):

    def _request(self, user=None):
        user = user or self.requester
        return self.env['alfia.purchase.request'].with_user(user).create({
            'reason': 'Winter feed for Barn A', 'line_ids': [Command.create({'product_id': self.feed.id, 'quantity': 20, 'estimated_price': 3}),
                                                             Command.create({'product_id': self.tack.id, 'quantity': 2, 'vendor_id': self.other_vendor.id})]})

    def test_request_is_approved_by_someone_else_and_ordered_with_traceability(self):
        req = self._request()
        self.assertTrue(req.name.startswith('PR/'))
        req.action_submit()
        with self.assertRaises(UserError):
            req.with_user(self.requester).write({'reason': 'changed after submitting'})
        with self.assertRaises(AccessError):
            req.with_user(self.buyer).action_approve()                     # not a purchase manager
        req.with_user(self.manager).action_approve()
        self.assertEqual((req.state, req.approved_by_id), ('approved', self.manager))
        req.with_user(self.buyer).action_create_rfq()
        self.assertEqual(req.state, 'ordered')
        self.assertEqual(req.purchase_order_count, 2, "one request for quotation per vendor; the requester sees the count")
        orders = req.with_user(self.buyer).purchase_order_ids
        self.assertEqual(set(orders.partner_id.ids), {self.vendor.id, self.other_vendor.id})
        self.assertEqual(orders.mapped('origin'), [req.name, req.name])
        feed_line = orders.order_line.filtered(lambda l: l.product_id == self.feed)
        self.assertEqual((feed_line.product_qty, feed_line.alfia_request_id), (20, req))

    def test_requester_who_is_a_manager_cannot_approve_own_request(self):
        req = self._request(self.manager)
        req.action_submit()
        with self.assertRaises(UserError):
            req.with_user(self.manager).action_approve()
        req.with_user(self.manager2).action_approve()

    def test_rejection_needs_a_reason_and_the_request_can_be_resubmitted(self):
        req = self._request()
        req.action_submit()
        with self.assertRaises(UserError):
            req.with_user(self.manager).action_reject()
        req.with_user(self.manager).rejection_reason = 'Too much; order 10'
        req.with_user(self.manager).action_reject()
        self.assertEqual(req.state, 'rejected')
        req.line_ids.filtered(lambda l: l.product_id == self.feed).with_user(self.requester).quantity = 10
        req.with_user(self.requester).action_submit()
        self.assertEqual(req.state, 'submitted')
        with self.assertRaises(UserError):
            req.with_user(self.requester).write({'state': 'approved'})      # status only through the buttons

    def test_requester_sees_only_own_requests(self):
        mine = self._request()
        other = self._request(self.buyer)
        seen = self.env['alfia.purchase.request'].with_user(self.requester).search([])
        self.assertIn(mine, seen)
        self.assertNotIn(other, seen)


@tagged('post_install', '-at_install', 'alfia_controls')
class TestReceiptQuality(ControlsCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        insp = env.ref('alfia_controls.group_quality_inspector')
        cls.inspector = cls.manager.copy({'login': 'inspector@ctl.local', 'name': 'inspector'})
        cls.inspector.group_ids = [Command.link(insp.id), Command.link(env.ref('stock.group_stock_user').id)]
        cls.inspector2 = cls.manager.copy({'login': 'inspector2@ctl.local', 'name': 'inspector2'})
        cls.inspector2.group_ids = [Command.link(insp.id), Command.link(env.ref('stock.group_stock_user').id)]
        cls.categ = env['product.category'].create({'name': 'Inspected medicines', 'alfia_requires_inspection': True})
        cls.sub = env['product.category'].create({'name': 'Vaccines', 'parent_id': cls.categ.id})
        cls.vaccine = env['product.product'].create({'name': 'Controls vaccine', 'type': 'consu', 'is_storable': True,
                                                     'categ_id': cls.sub.id})

    def _receive(self, qty=10, user=None, steps='one_step'):
        user = user or self.inspector
        self.wh.reception_steps = steps
        picking = self.env['stock.picking'].create({
            'picking_type_id': self.wh.in_type_id.id, 'partner_id': self.vendor.id,
            'location_id': self.env.ref('stock.stock_location_suppliers').id,
            'location_dest_id': self.wh.in_type_id.default_location_dest_id.id,
            'move_ids': [Command.create({'product_id': self.vaccine.id, 'product_uom_qty': qty,
                                         'location_id': self.env.ref('stock.stock_location_suppliers').id,
                                         'location_dest_id': self.wh.in_type_id.default_location_dest_id.id})]})
        picking.action_confirm()
        picking.move_ids.quantity = qty
        picking.move_ids.picked = True
        picking.with_user(user).button_validate()
        return picking

    def _qty(self, location):
        return sum(self.env['stock.quant'].search([('product_id', '=', self.vaccine.id), ('location_id', '=', location.id)]).mapped('quantity'))

    def test_inherited_flag(self):
        self.assertTrue(self.sub.alfia_effective_inspection)
        self.categ.alfia_requires_inspection = False
        self.assertFalse(self.sub.alfia_effective_inspection)

    def test_goods_wait_aside_until_a_different_inspector_accepts(self):
        picking = self._receive()
        check = self.env['alfia.quality.check'].search([('picking_id', '=', picking.id)])
        self.assertEqual((check.state, check.quantity, check.received_by_id), ('pending', 10, self.inspector))
        self.assertEqual(self._qty(self.wh.lot_stock_id), 0, "nothing usable until inspected")
        self.assertEqual(self._qty(check.location_id), 10)
        with self.assertRaises(AccessError):
            check.with_user(self.buyer).action_accept()                 # not an inspector
        with self.assertRaises(UserError):
            check.with_user(self.inspector).action_accept()             # received them
        with self.assertRaises(UserError):
            check.with_user(self.inspector2).write({'state': 'passed'})  # only through the buttons
        check.with_user(self.inspector2).action_accept()
        self.assertEqual((check.state, check.inspector_id), ('passed', self.inspector2))
        self.assertEqual(self._qty(self.wh.lot_stock_id), 10)
        with self.assertRaises(UserError):
            check.with_user(self.inspector2).action_accept()            # decided once

    def test_rejection_needs_findings_and_moves_goods_to_rejected(self):
        picking = self._receive(4)
        check = self.env['alfia.quality.check'].search([('picking_id', '=', picking.id)])
        with self.assertRaises(UserError):
            check.with_user(self.inspector2).action_reject()
        check.with_user(self.inspector2).findings = 'Cold chain broken: 14 °C on arrival'
        check.with_user(self.inspector2).action_reject()
        self.assertEqual(check.state, 'rejected')
        self.assertIn('Rejected', check.location_id.name)
        self.assertEqual(self._qty(check.location_id), 4)
        self.assertEqual(self._qty(self.wh.lot_stock_id), 0)

    def test_two_step_put_away_is_blocked_until_accepted(self):
        picking = self._receive(3, steps='two_steps')
        check = self.env['alfia.quality.check'].search([('picking_id', '=', picking.id)])
        put_away = picking.move_ids.move_dest_ids.picking_id
        self.assertTrue(put_away)
        put_away.action_assign()
        put_away.move_ids.picked = True
        with self.assertRaises(UserError):
            put_away.button_validate()
        check.with_user(self.inspector2).action_accept()
        put_away.button_validate()
        self.assertEqual(self._qty(self.wh.lot_stock_id), 3)

    def test_two_step_rejection_cancels_the_put_away(self):
        picking = self._receive(5, steps='two_steps')
        check = self.env['alfia.quality.check'].search([('picking_id', '=', picking.id)])
        put_away = picking.move_ids.move_dest_ids
        check.with_user(self.inspector2).findings = 'Wrong strength delivered'
        check.with_user(self.inspector2).action_reject()
        self.assertEqual(put_away.state, 'cancel')
        self.assertEqual(self._qty(check.location_id), 5)

    def test_other_goods_are_not_held(self):
        self.vaccine.categ_id = self.env.ref('product.product_category_goods', raise_if_not_found=False) or self.env['product.category'].create({'name': 'Plain'})
        picking = self._receive(2)
        self.assertFalse(self.env['alfia.quality.check'].search([('picking_id', '=', picking.id)]))
        self.assertEqual(self._qty(self.wh.lot_stock_id), 2)


@tagged('post_install', '-at_install', 'alfia_controls')
class TestApprovalLimits(ControlsCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.env.company.po_double_validation = 'one_step'
        cls.finance = env['res.users'].with_context(no_reset_password=True).create({
            'name': 'ctlfinance', 'login': 'ctlfinance@ctl.local',
            'group_ids': [Command.link(env.ref('account.group_account_manager').id)]})
        Limit = env['alfia.approval.limit'].with_user(cls.finance)
        Limit.create({'document_type': 'purchase', 'user_id': cls.manager.id, 'amount': 1000})
        Limit.create({'document_type': 'purchase', 'user_id': cls.manager2.id, 'amount': 50000})
        cls.usd = env.ref('base.USD')
        cls.usd.active = True

    def _po(self, qty, price, currency=None):
        return self.env['purchase.order'].with_user(self.buyer).create({
            'partner_id': self.vendor.id, 'currency_id': (currency or self.env.company.currency_id).id,
            'order_line': [Command.create({'product_id': self.feed.id, 'product_qty': qty, 'price_unit': price})]})

    def test_order_goes_to_someone_whose_limit_covers_it(self):
        po = self._po(100, 30)                                            # 3,000 in company currency
        po.with_user(self.buyer).button_confirm()
        self.assertEqual(po.state, 'to approve', "the buyer has no limit")
        with self.assertRaises(UserError):
            po.with_user(self.manager).button_approve()                   # limit 1,000
        po.with_user(self.manager2).button_approve()
        self.assertEqual(po.state, 'purchase')

    def test_a_foreign_currency_cannot_slip_under_the_limit(self):
        company_currency = self.env.company.currency_id
        if company_currency == self.usd:
            self.skipTest('needs a company currency other than USD')
        self.env['res.currency.rate'].create({'currency_id': self.usd.id, 'name': fields.Date.today(),
                                              'company_rate': 1 / 3.64, 'company_id': self.env.company.id})
        po = self._po(1, 900, self.usd)                                   # 900 USD ≈ 3,276 in company currency
        po.with_user(self.buyer).button_confirm()
        with self.assertRaises(UserError):
            po.with_user(self.manager).button_approve()

    def test_delegation_lends_the_limit_for_its_dates_only(self):
        po = self._po(100, 30)
        po.with_user(self.buyer).button_confirm()
        Delegation = self.env['alfia.approval.delegation']
        with self.assertRaises(UserError):
            Delegation.with_user(self.manager).create({'delegator_id': self.manager2.id, 'delegate_id': self.manager.id,
                                                       'date_to': fields.Date.today(), 'reason': 'not mine to give'})
        with self.assertRaises(UserError):
            Delegation.with_user(self.manager2).create({'delegate_id': self.manager.id, 'reason': 'backdated',
                                                        'date_from': fields.Date.today() - timedelta(days=3),
                                                        'date_to': fields.Date.today()})
        Delegation.with_user(self.manager2).create({'delegate_id': self.manager.id, 'reason': 'Leave next month',
                                                             'date_from': fields.Date.today() + timedelta(days=30),
                                                             'date_to': fields.Date.today() + timedelta(days=40)})
        with self.assertRaises(UserError):
            po.with_user(self.manager).button_approve()                   # not yet active
        today = Delegation.with_user(self.manager2).create({'delegate_id': self.manager.id, 'reason': 'Annual leave',
                                                            'date_to': fields.Date.today() + timedelta(days=5)})
        with self.assertRaises(UserError):
            today.with_user(self.manager2).write({'date_to': fields.Date.today() + timedelta(days=20)})  # cannot extend
        po.with_user(self.manager).button_approve()
        self.assertEqual(po.state, 'purchase')
        self.assertTrue(any('on behalf of' in (m.body or '') for m in po.message_ids))

    def test_delegation_never_covers_own_orders(self):
        self.env['alfia.approval.delegation'].with_user(self.manager2).create({
            'delegate_id': self.manager.id, 'reason': 'Leave', 'date_to': fields.Date.today() + timedelta(days=2)})
        po = self.env['purchase.order'].with_user(self.manager).create({
            'partner_id': self.vendor.id, 'order_line': [Command.create({'product_id': self.feed.id, 'product_qty': 100, 'price_unit': 30})]})
        po.with_user(self.manager).button_confirm()
        self.assertEqual(po.state, 'to approve')
        with self.assertRaises(UserError):
            po.with_user(self.manager).button_approve()

    def test_nobody_sets_their_own_limit(self):
        with self.assertRaises(UserError):
            self.env['alfia.approval.limit'].with_user(self.finance).create(
                {'document_type': 'bill', 'user_id': self.finance.id, 'unlimited': True})
        with self.assertRaises(UserError):
            self.env['alfia.approval.limit'].with_user(self.finance).create(
                {'document_type': 'bill', 'group_id': self.env.ref('account.group_account_manager').id, 'unlimited': True})

    def test_purchase_request_limit(self):
        self.env['alfia.approval.limit'].with_user(self.finance).create(
            {'document_type': 'purchase_request', 'group_id': self.env.ref('purchase.group_purchase_manager').id, 'amount': 30})
        req = self.env['alfia.purchase.request'].with_user(self.requester).create({
            'reason': 'Feed', 'line_ids': [Command.create({'product_id': self.feed.id, 'quantity': 20, 'estimated_price': 3})]})
        req.action_submit()
        with self.assertRaises(UserError):
            req.with_user(self.manager).action_approve()                  # 60 > 30

    def test_unlimited_kinds_stay_unlimited(self):
        self.assertTrue(self.env['alfia.approval.limit']._allows(self.buyer, 'payment', 10 ** 9, self.env.company))

    def test_vendor_bill_limit(self):
        clerk = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'ctlap', 'login': 'ctlap@ctl.local',
            'group_ids': [Command.link(self.env.ref('account.group_account_invoice').id),
                          Command.link(self.env.ref('alfia_security.group_alfia_all_journals').id)]})
        self.env['alfia.approval.limit'].with_user(self.finance).create(
            {'document_type': 'bill', 'user_id': clerk.id, 'amount': 100})
        bill = self.env['account.move'].create({
            'move_type': 'in_invoice', 'partner_id': self.vendor.id, 'invoice_date': fields.Date.today(),
            'invoice_line_ids': [Command.create({'name': 'Feed', 'quantity': 1, 'price_unit': 500, 'tax_ids': False})]})
        with self.assertRaises(UserError):
            bill.with_user(clerk).action_post()
        bill.line_ids.filtered(lambda l: l.display_type == 'product').price_unit = 80
        bill.with_user(clerk).action_post()
        self.assertEqual(bill.state, 'posted')
