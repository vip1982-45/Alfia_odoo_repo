from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase
from odoo.tools.binary import BinaryBytes


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

    def test_inspector_scoped_by_profile_can_open_and_decide(self):
        """Round 5 P3: an inspector whose profile lists a warehouse (not all locations) opens and accepts the inspection."""
        profile = self.env.ref('alfia_controls.profile_quality_inspector').copy({'warehouse_ids': [Command.set(self.wh.ids)]})
        scoped = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'scoped inspector', 'login': 'scopedinsp@ctl.local', 'group_ids': [Command.link(self.env.ref('base.group_user').id)]})
        scoped.alfia_profile_ids = [Command.set(profile.ids)]
        scoped._alfia_sync_profile_groups()
        self.assertTrue(scoped.alfia_scope_locations, "the case under test: site-scoped, not all locations")
        picking = self._receive(6, steps='two_steps')
        check = self.env['alfia.quality.check'].with_user(scoped).search([('picking_id', '=', picking.id)])
        self.assertEqual(len(check), 1)
        # What the form loads: the receipt, supplier, product, batches and where the goods are.
        check.read(['picking_id', 'partner_id', 'product_id', 'lot_names', 'location_id', 'move_id', 'uom_id'])
        check.picking_id.read(['display_name'])
        check.action_accept()
        self.assertEqual(check.state, 'passed')
        nowhere = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'inspector without site', 'login': 'nositeinsp@ctl.local',
            'group_ids': [Command.link(self.env.ref('base.group_user').id)]})
        nowhere.alfia_profile_ids = [Command.set(profile.copy({'warehouse_ids': [Command.clear()]}).ids)]
        nowhere._alfia_sync_profile_groups()
        self.assertFalse(nowhere.alfia_location_ids)
        self.assertFalse(self.env['alfia.quality.check'].with_user(nowhere).search([('picking_id', '=', picking.id)]),
                         "without a site the list is empty instead of failing")

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

    def test_delegation_never_covers_own_orders_even_when_exempt(self):
        """Round 5 P6: the delegate was in the segregation-of-duties exemption group and approved an order they created."""
        exempt = self.env.ref('alfia_security.group_alfia_sod_exempt')
        self.manager.group_ids = [Command.link(exempt.id)]
        self.env['alfia.approval.delegation'].with_user(self.manager2).create({
            'delegate_id': self.manager.id, 'reason': 'Leave', 'date_to': fields.Date.today() + timedelta(days=2)})
        own = self.env['purchase.order'].with_user(self.manager).create({
            'partner_id': self.vendor.id, 'order_line': [Command.create({'product_id': self.feed.id, 'product_qty': 100, 'price_unit': 30})]})
        own.with_user(self.manager).button_confirm()
        self.assertEqual(own.state, 'to approve', "confirming waits for approval instead of using the delegation")
        with self.assertRaises(UserError):
            own.with_user(self.manager).button_approve()
        small = self.env['purchase.order'].with_user(self.manager).create({
            'partner_id': self.vendor.id, 'order_line': [Command.create({'product_id': self.feed.id, 'product_qty': 10, 'price_unit': 30})]})
        small.with_user(self.manager).button_confirm()
        self.assertEqual(small.state, 'to approve', "the former exemption group no longer approves its own orders at all")
        with self.assertRaises(UserError):
            small.with_user(self.manager).button_approve()
        others = self._po(100, 30)
        others.with_user(self.buyer).button_confirm()
        others.with_user(self.manager).button_approve()
        self.assertEqual(others.state, 'purchase', "the delegation still covers other people's orders")

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


@tagged('post_install', '-at_install', 'alfia_controls')
class TestJournalAndPeriodClose(ControlsCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        env.company.alfia_sod_enabled = True
        env.company.alfia_sod_threshold = 0

        def accountant(login, manager=False):
            groups = ['account.group_account_manager' if manager else 'account.group_account_user',
                      'alfia_security.group_alfia_all_journals']
            return env['res.users'].with_context(no_reset_password=True).create({
                'name': login, 'login': f'{login}@ctl.local', 'group_ids': [Command.link(env.ref(g).id) for g in groups]})
        cls.acc1, cls.acc2 = accountant('ctlacc1'), accountant('ctlacc2')
        cls.fm1, cls.fm2 = accountant('ctlfm1', True), accountant('ctlfm2', True)
        cls.misc = env['account.journal'].search([('type', '=', 'general'), ('company_id', '=', env.company.id),
                                                  ('id', '!=', env.company.currency_exchange_journal_id.id)], limit=1)
        accounts = env['account.account'].search([('company_ids', 'in', env.company.id), ('account_type', '=', 'expense')], limit=2)
        cls.a1, cls.a2 = accounts[0], accounts[-1]

    def _entry(self, user, date=None):
        return self.env['account.move'].with_user(user).create({
            'move_type': 'entry', 'journal_id': self.misc.id, 'date': date or fields.Date.today(), 'ref': 'Accrual',
            'line_ids': [Command.create({'account_id': self.a1.id, 'debit': 250, 'name': 'Accrual'}),
                         Command.create({'account_id': self.a2.id, 'credit': 250, 'name': 'Accrual'})]})

    def test_manual_entry_is_posted_by_a_second_person(self):
        move = self._entry(self.acc1)
        self.assertTrue(move.alfia_manual)
        with self.assertRaises(UserError):
            move.with_user(self.acc1).action_post()
        move.with_user(self.acc2).action_post()
        self.assertEqual(move.state, 'posted')

    def test_manual_entry_cannot_post_itself_later(self):
        move = self._entry(self.acc1)
        with self.assertRaises(UserError):
            move.with_user(self.acc1).auto_post = 'at_date'

    def test_lock_dates_only_through_an_approved_close(self):
        with self.assertRaises(UserError):
            self.env.company.with_user(self.fm1).fiscalyear_lock_date = fields.Date.today() - timedelta(days=400)
        close = self.env['alfia.period.close'].with_user(self.fm1).create({'date': fields.Date.today() - timedelta(days=400)})
        close.action_submit()
        with self.assertRaises(UserError):
            close.with_user(self.fm1).action_approve()                    # prepared it
        with self.assertRaises(UserError):
            close.with_user(self.fm1).write({'state': 'done'})
        with self.assertRaises(AccessError):
            close.with_user(self.acc1).action_approve()                   # not a finance manager
        close.with_user(self.fm2).action_approve()
        self.assertEqual(self.env.company.fiscalyear_lock_date, close.date)
        late = self._entry(self.acc1, close.date)
        late.with_user(self.acc2).action_post()
        self.assertGreater(late.date, close.date, "nothing is posted inside a closed period")
        with self.assertRaises(UserError):
            self.env['account.lock_exception'].with_user(self.fm1).create({'company_id': self.env.company.id,
                                                                            'fiscalyear_lock_date': False})

    def test_reopen_needs_a_reason_and_a_second_person(self):
        close = self.env['alfia.period.close'].with_user(self.fm1).create({'date': fields.Date.today() - timedelta(days=400)})
        close.action_submit()
        close.with_user(self.fm2).action_approve()
        reopen = self.env['alfia.period.close'].with_user(self.fm2).create({'kind': 'reopen', 'date': close.date - timedelta(days=31)})
        with self.assertRaises(UserError):
            reopen.action_submit()                                        # no reason
        reopen.reason = 'Supplier credit note for the closed month'
        reopen.action_submit()
        reopen.with_user(self.fm1).action_approve()
        self.assertEqual(self.env.company.fiscalyear_lock_date, reopen.date)
        with self.assertRaises(UserError):
            close.with_user(self.fm1).unlink()

    def test_close_waits_for_draft_entries(self):
        self._entry(self.acc1, fields.Date.today() - timedelta(days=500))
        close = self.env['alfia.period.close'].with_user(self.fm1).create({'date': fields.Date.today() - timedelta(days=400)})
        close.action_submit()
        self.assertTrue(close.draft_count)
        with self.assertRaises(UserError):
            close.with_user(self.fm2).action_approve()


@tagged('post_install', '-at_install', 'alfia_controls')
class TestBankStatements(ControlsCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.bank = env['account.journal'].search([('type', '=', 'bank'), ('company_id', '=', env.company.id)], limit=1)
        cls.accountant = env['res.users'].with_context(no_reset_password=True).create({
            'name': 'ctlbank', 'login': 'ctlbank@ctl.local',
            'group_ids': [Command.link(env.ref(g).id) for g in ('account.group_account_user', 'alfia_security.group_alfia_all_journals')]})
        cls.customer = env['res.partner'].create({'name': 'Stud client'})
        cls.charges = env['account.account'].search([('company_ids', 'in', env.company.id), ('account_type', '=', 'expense')], limit=1)

    def _invoice(self, amount, move_type='out_invoice', partner=None):
        move = self.env['account.move'].create({
            'move_type': move_type, 'partner_id': (partner or self.customer).id, 'invoice_date': fields.Date.today(),
            'invoice_line_ids': [Command.create({'name': 'Boarding', 'quantity': 1, 'price_unit': amount, 'tax_ids': False})]})
        move.action_post()
        return move

    def _import(self, csv_text, user=None):
        wiz = self.env['alfia.bank.import'].with_user(user or self.accountant).create({
            'journal_id': self.bank.id, 'file': BinaryBytes(csv_text.encode(), 'statement.csv'), 'filename': 'statement.csv'})
        return wiz, wiz.action_import()

    def test_import_skips_lines_already_imported(self):
        csv_text = ("Date;Description;Debit;Credit;Reference\n"
                    "2026-11-02;Fee;10.00;;A1\n2026-11-02;Fee;10.00;;A1\n03/11/2026;Deposit;;1,250.50;B7\n")
        wiz, action = self._import(csv_text)
        lines = self.env['account.bank.statement.line'].search(action['domain'])
        self.assertEqual(sorted(lines.mapped('amount')), [-10.0, -10.0, 1250.5], "two identical fees are both real")
        with self.assertRaises(UserError):                                  # everything already there
            self._import(csv_text)
        wiz, action = self._import(csv_text + "2026-11-04;Transfer;;99;C9\n")
        self.assertEqual(self.env['account.bank.statement.line'].search_count(action['domain']), 1)
        self.assertIn('3 already imported', wiz.result)

    def test_bad_files_are_refused_with_a_reason(self):
        with self.assertRaises(UserError):
            self._import("When,What\n2026-11-01,x\n")
        with self.assertRaises(UserError):
            self._import("Date,Amount\n31/31/2026,5\n")

    def test_auto_match_settles_the_invoice_it_names(self):
        inv = self._invoice(1150)
        other = self._invoice(1150)                                         # same amount: only the reference decides
        _w, action = self._import(f"Date,Description,Amount,Partner\n2026-11-05,Payment {inv.name},1150,Stud client\n")
        line = self.env['account.bank.statement.line'].search(action['domain'])
        self.assertEqual(line.partner_id, self.customer)
        line.with_user(self.accountant).action_alfia_auto_match()
        self.assertTrue(line.is_reconciled)
        self.assertEqual(inv.payment_state, 'paid')
        self.assertEqual(other.payment_state, 'not_paid')

    def test_ambiguous_lines_wait_for_a_person(self):
        self._invoice(700)
        self._invoice(700)
        _w, action = self._import("Date,Description,Amount,Partner\n2026-11-05,Transfer,700,Stud client\n")
        line = self.env['account.bank.statement.line'].search(action['domain'])
        self.assertEqual(line.alfia_suggestion_count, 2)
        line.with_user(self.accountant).action_alfia_auto_match()
        self.assertFalse(line.is_reconciled)

    def test_manual_match_with_bank_charges_and_undo(self):
        bill = self._invoice(500, 'in_invoice', self.vendor)
        _w, action = self._import("Date,Description,Amount\n2026-11-06,Supplier transfer,-505\n")
        line = self.env['account.bank.statement.line'].search(action['domain'])
        match = self.env['alfia.bank.match'].with_user(self.accountant).create({'statement_line_id': line.id, 'partner_filter_id': self.vendor.id})
        payable = bill.line_ids.filtered(lambda l: l.account_id.account_type == 'liability_payable')
        self.assertIn(payable, match.candidate_ids)
        match.line_ids = payable
        self.assertEqual(match.difference, -5)
        with self.assertRaises(UserError):
            match.action_confirm()                                          # no account for the difference
        match.writeoff_account_id = self.charges
        match.action_confirm()
        self.assertTrue(line.is_reconciled)
        self.assertEqual(bill.payment_state, 'paid')
        self.assertEqual(sum(line.move_id.line_ids.filtered(lambda l: l.account_id == self.charges).mapped('balance')), 5)
        line.with_user(self.accountant).action_undo_reconciliation()
        self.assertFalse(line.is_reconciled)
        self.assertEqual(bill.payment_state, 'not_paid')


@tagged('post_install', '-at_install', 'alfia_controls')
class TestAssetAccounting(ControlsCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        env.company.alfia_sod_enabled = True
        env.company.alfia_sod_threshold = 0

        def user(login, group):
            return env['res.users'].with_context(no_reset_password=True).create({
                'name': login, 'login': f'{login}@ctl.local',
                'group_ids': [Command.link(env.ref(g).id) for g in (group, 'alfia_security.group_alfia_all_journals',
                                                                     'alfia_security.group_alfia_all_locations')]})
        cls.acc = user('assetacc', 'account.group_account_user')
        cls.fm = user('assetfm', 'account.group_account_manager')
        Account = env['account.account']

        def account(code, kind):
            return Account.create({'name': f'Asset test {code}', 'code': code, 'account_type': kind})
        cls.fixed, cls.accum = account('190001', 'asset_fixed'), account('190002', 'asset_fixed')
        cls.expense, cls.gainloss = account('690001', 'expense_depreciation'), account('790001', 'income_other')
        cls.receivable = env['account.account'].search([('account_type', '=', 'asset_receivable'), ('company_ids', 'in', env.company.id)], limit=1)
        cls.journal = env['account.journal'].search([('type', '=', 'general'), ('company_id', '=', env.company.id)], limit=1)

    def _asset(self, cost=12000, salvage=0, months=12, start='2025-01-15'):
        asset = self.env['alfia.asset'].create({'name': 'Horse trailer', 'kind': 'vehicle'})
        asset.with_user(self.acc).write({
            'cost': cost, 'salvage_value': salvage, 'useful_life_months': months, 'depreciation_start': start,
            'asset_account_id': self.fixed.id, 'accumulated_account_id': self.accum.id, 'expense_account_id': self.expense.id,
            'asset_journal_id': self.journal.id})
        asset.with_user(self.acc).action_capitalise()
        return asset

    def test_schedule_posts_monthly_and_ends_at_residual_value(self):
        asset = self._asset(cost=10000, salvage=1000, months=7)
        lines = asset.depreciation_line_ids
        self.assertEqual(len(lines), 7)
        self.assertAlmostEqual(sum(lines.mapped('amount')), 9000, places=2, msg="the last month takes the rounding difference")
        self.assertEqual(str(lines[0].date), '2025-01-31')
        with self.assertRaises(UserError):
            asset.with_user(self.acc).cost = 9000                       # fixed once capitalised
        asset.with_user(self.acc).action_post_depreciation(up_to=fields.Date.to_date('2025-03-31'))
        posted = lines.filtered(lambda l: l.state == 'posted')
        self.assertEqual(len(posted), 3, "an accountant posts depreciation; it is not a manual entry needing a second person")
        self.assertTrue(all(m.state == 'posted' and m.alfia_asset_id == asset for m in posted.move_id))
        self.assertAlmostEqual(asset.book_value, 10000 - sum(posted.mapped('amount')), places=2)
        asset.with_user(self.acc).action_post_depreciation(up_to=fields.Date.to_date('2026-12-31'))
        self.assertEqual(asset.accounting_state, 'done')
        self.assertAlmostEqual(asset.book_value, 1000, places=2)

    def test_sale_mid_month_books_part_month_and_gain(self):
        asset = self._asset(cost=12000, months=12, start='2025-01-01')    # 1,000 a month
        wizard = self.env['alfia.asset.disposal'].with_user(self.fm).create({
            'asset_id': asset.id, 'date': '2025-04-15', 'reason': 'Sold to TEST Client', 'proceeds': 9000,
            'proceeds_account_id': self.receivable.id, 'gain_loss_account_id': self.gainloss.id})
        wizard.action_confirm()
        posted = asset.depreciation_line_ids.filtered(lambda l: l.state == 'posted')
        self.assertAlmostEqual(sum(posted.mapped('amount')), 3500, places=2, msg="Jan-Mar plus 15/30 of April")
        self.assertFalse(asset.depreciation_line_ids.filtered(lambda l: l.state == 'planned'))
        self.assertEqual((asset.accounting_state, asset.state), ('disposed', 'disposed'), "both statuses say disposed")
        move = asset.disposal_move_id
        self.assertEqual(move.state, 'posted')
        gain = move.line_ids.filtered(lambda l: l.account_id == self.gainloss)
        self.assertAlmostEqual(gain.credit, 500, places=2, msg="proceeds 9,000 less book value 8,500")
        self.assertAlmostEqual(sum(move.line_ids.filtered(lambda l: l.account_id == self.fixed).mapped('credit')), 12000, places=2)

    def test_write_off_is_a_loss_and_only_managers_dispose(self):
        asset = self._asset(cost=6000, months=6, start='2025-01-01')
        with self.assertRaises(AccessError):
            asset.with_user(self.acc).action_open_disposal()
        self.env['alfia.asset.disposal'].with_user(self.fm).create({
            'asset_id': asset.id, 'date': '2025-02-28', 'reason': 'Damaged beyond repair',
            'gain_loss_account_id': self.gainloss.id}).action_confirm()
        loss = asset.disposal_move_id.line_ids.filtered(lambda l: l.account_id == self.gainloss)
        self.assertAlmostEqual(loss.debit, 4000, places=2, msg="two months depreciated, 4,000 book value written off")

    def test_finance_manager_opens_the_asset_and_retire_needs_disposal(self):
        """Round 7 Z2: Odoo 20's finance-manager group is not an accountant group; finance could not open the asset."""
        self.assertFalse(self.fm.has_group('account.group_account_user'), "the case under test")
        asset = self._asset()
        asset.with_user(self.fm).read(['name', 'cost', 'book_value', 'accounting_state', 'depreciation_line_ids'])
        self.assertTrue(asset.with_user(self.fm).search([('id', '=', asset.id)]))
        action = asset.with_user(self.fm).action_open_disposal()
        self.assertEqual(action['res_model'], 'alfia.asset.disposal')
        with self.assertRaises(UserError):
            asset.with_user(self.fm).action_retire()                    # capitalised: only through a disposal

    def test_stable_staff_do_not_see_accounting(self):
        stable = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'stableasset', 'login': 'stableasset@ctl.local',
            'group_ids': [Command.link(self.env.ref('alfia_security.group_alfia_stable_user').id),
                          Command.link(self.env.ref('alfia_security.group_alfia_all_locations').id)]})
        asset = self._asset()
        with self.assertRaises(AccessError):
            asset.with_user(stable).read(['cost'])


@tagged('post_install', '-at_install', 'alfia_controls')
class TestBoardingExit(ControlsCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.owner = env['res.partner'].create({'name': 'Boarding owner'})
        cls.service = env['product.product'].create({'name': 'Monthly boarding', 'type': 'service', 'lst_price': 3000})
        cls.barn = env['stock.location'].search([('usage', '=', 'internal'), ('company_id', '=', env.company.id)], limit=1)

    def _boarded_horse(self, start='2026-01-01'):
        horse = self.env['alfia.horse'].create({'name': 'Boarder', 'sex': 'female', 'initial_location_id': self.barn.id,
                                                'invoice_to_id': self.owner.id})
        plan = self.env['alfia.charge.plan'].create({'name': 'Boarding', 'target_kind': 'horse', 'horse_id': horse.id,
                                                     'product_id': self.service.id, 'price_unit': 3000, 'interval': 'monthly',
                                                     'next_date': start, 'auto_approve': True})
        plan.sudo().write({'state': 'approved'})
        return horse, plan

    def _charges(self, horse):
        return self.env['alfia.charge'].search([('horse_id', '=', horse.id)], order='date')

    def test_departure_prorates_the_open_month_and_stops_later_ones(self):
        horse, plan = self._boarded_horse()
        plan.action_generate(upto=fields.Date.to_date('2026-03-01'))      # Jan, Feb, Mar charges (billed in advance)
        self.assertEqual(len(self._charges(horse)), 3)
        move = self.env['alfia.horse.movement'].create({'horse_id': horse.id, 'kind': 'departure', 'date': '2026-03-10 09:00:00',
                                                        'reason': 'Returned to owner'})
        move.action_done()
        jan, feb, mar = self._charges(horse)
        self.assertEqual((jan.amount, feb.amount), (3000, 3000))
        self.assertAlmostEqual(mar.amount, 967.74, places=2, msg="10 of 31 days in March")
        self.assertEqual(str(horse.alfia_billed_until), '2026-03-10')
        self.assertEqual(str(plan.end_date), '2026-03-10', "the horse's own plan shows it has stopped")
        self.assertEqual(plan.state, 'approved')
        plan.action_generate(upto=fields.Date.to_date('2026-06-01'))
        self.assertEqual(len(self._charges(horse)), 3, "no charges after the horse left")
        self.assertIn('2026-03-10', horse.message_ids[0].body)

    def test_invoiced_months_after_leaving_are_credited(self):
        horse, plan = self._boarded_horse()
        plan.action_generate(upto=fields.Date.to_date('2026-02-01'))     # January and February, invoiced together
        charges = self._charges(horse)
        charges.action_invoice()
        credits = horse._alfia_end_recurring_charges(fields.Date.to_date('2026-01-20'), 'Returned to owner')
        self.assertEqual(len(credits), 1, "one draft credit note against the invoice")
        self.assertEqual((credits.move_type, credits.state, credits.reversed_entry_id), ('out_refund', 'draft', charges.move_id))
        self.assertAlmostEqual(credits.amount_untaxed, 1064.52 + 3000, places=2,
                               msg="11 unused January days (3,000 x 11/31) and all of February")

    def test_status_change_ends_charges_and_return_resumes(self):
        horse, plan = self._boarded_horse(start=str(fields.Date.today().replace(day=1)))
        plan.action_generate()
        horse.write({'status': 'dead'})
        self.assertEqual(horse.alfia_billed_until, fields.Date.today())
        horse2, plan2 = self._boarded_horse(start='2026-01-01')
        self.env['alfia.horse.movement'].create({'horse_id': horse2.id, 'kind': 'departure', 'date': '2026-01-05 10:00:00'}).action_done()
        self.env['alfia.horse.movement'].create({'horse_id': horse2.id, 'kind': 'arrival', 'date': '2026-03-15 10:00:00',
                                                 'location_to_id': self.barn.id}).action_done()
        self.assertFalse(horse2.alfia_billed_until)


@tagged('post_install', '-at_install', 'alfia_controls')
class TestStockAndBillControls(ControlsCase):
    """Owner decisions of 25 Nov 2026: no issue beyond stock, counts need a reason (and a second person above 1,000 QAR),
    supplier bills within 2% of the order price, expired batches are not issued; and partial acceptance at inspection."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        env.company.write({'alfia_sod_enabled': True, 'alfia_sod_threshold': 0, 'alfia_bill_price_tolerance': 2.0,
                           'alfia_count_second_person_value': 1000.0, 'po_double_validation': 'one_step'})
        cls.approver1 = env['res.users'].with_context(no_reset_password=True).create({
            'name': 'adj1', 'login': 'adj1@ctl.local',
            'group_ids': [Command.link(env.ref(g).id) for g in ('stock.group_stock_manager', 'alfia_security.group_alfia_stock_adjust_approver',
                                                                 'alfia_security.group_alfia_all_locations', 'alfia_security.group_alfia_all_warehouses')]})
        cls.approver2 = cls.approver1.copy({'login': 'adj2@ctl.local', 'name': 'adj2'})
        cls.approver2.group_ids = cls.approver1.group_ids
        cls.stocked = env['product.product'].create({'name': 'Controls stocked', 'is_storable': True, 'type': 'consu', 'standard_price': 100})
        cls.stock = cls.wh.lot_stock_id
        env['stock.quant']._update_available_quantity(cls.stocked, cls.stock, 5)

    def _out_move(self, qty, product=None, lot=None):
        product = product or self.stocked
        move = self.env['stock.move'].create({'product_id': product.id, 'product_uom_qty': qty, 'uom_id': product.uom_id.id,
                                              'location_id': self.stock.id, 'location_dest_id': self.env.ref('stock.stock_location_customers').id})
        move._action_confirm()
        move._action_assign()
        return move

    def test_no_issue_beyond_stock(self):
        move = self._out_move(8)
        move.move_line_ids.unlink()
        with self.assertRaises(UserError):
            # Refused when the line asks for more than is there, or at the latest when the transfer is done.
            self.env['stock.move.line'].create({'move_id': move.id, 'product_id': self.stocked.id, 'quantity': 8, 'picked': True,
                                                'location_id': self.stock.id, 'location_dest_id': move.location_dest_id.id})
            move._action_done()
        plain = self.env['product.product'].create({'name': 'Plain stocked', 'is_storable': True, 'type': 'consu'})
        self.env['stock.quant']._update_available_quantity(plain, self.stock, 2)
        short = self._out_move(3, plain)
        with self.assertRaises(UserError):
            short.move_line_ids.write({'quantity': 3})
            short.picked = True
            short._action_done()
        ok = self._out_move(5)
        ok.picked = True
        ok._action_done()
        self.assertEqual(ok.state, 'done')

    def test_count_needs_a_reason_and_a_second_person_above_the_threshold(self):
        quant = self.env['stock.quant'].search([('product_id', '=', self.stocked.id), ('location_id', '=', self.stock.id)])
        quant.with_user(self.approver1).inventory_quantity = 25            # +20 x 100 = 2,000 QAR
        with self.assertRaises(UserError):
            quant.with_user(self.approver1).action_apply_inventory()      # no reason
        with self.assertRaises(UserError):
            quant.with_user(self.approver1).with_context(inventory_name='Physical Inventory').action_apply_inventory()
        with self.assertRaises(UserError):
            quant.with_user(self.approver1).with_context(inventory_name='Found a pallet behind the hay').action_apply_inventory()
        quant.with_user(self.approver2).with_context(inventory_name='Found a pallet behind the hay').action_apply_inventory()
        self.assertEqual(quant.quantity, 25)
        quant.with_user(self.approver1).inventory_quantity = 26            # +1 x 100 = 100 QAR, under the threshold
        quant.with_user(self.approver1).with_context(inventory_name='Recount').action_apply_inventory()
        self.assertEqual(quant.quantity, 26)

    def test_bill_price_above_tolerance_needs_a_purchase_manager(self):
        po = self.env['purchase.order'].create({'partner_id': self.vendor.id, 'order_line': [
            Command.create({'product_id': self.tack.id, 'product_qty': 20, 'price_unit': 5.0})]})
        po.button_confirm()
        po.order_line.qty_received = 20
        bill = self.env['account.move'].create({
            'move_type': 'in_invoice', 'partner_id': self.vendor.id, 'invoice_date': fields.Date.today(),
            'invoice_line_ids': [Command.create({'purchase_line_id': po.order_line.id, 'product_id': self.tack.id, 'quantity': 20,
                                                 'price_unit': 6.0, 'name': 'Tack', 'tax_ids': False})]})
        self.assertIn('+20.0%', bill.alfia_price_variance)
        poster = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'billposter', 'login': 'billposter@ctl.local',
            'group_ids': [Command.link(self.env.ref(g).id) for g in ('account.group_account_manager', 'alfia_security.group_alfia_all_journals',
                                                                     'alfia_security.group_alfia_all_warehouses', 'alfia_security.group_alfia_all_locations',
                                                                     'stock.group_stock_user')]})
        with self.assertRaises(UserError):
            bill.with_user(poster).action_post()                         # Round 7 N1: 20% above, no approval
        with self.assertRaises(AccessError):
            self.env['alfia.bill.price.approval'].with_user(self.buyer).create({'move_id': bill.id, 'reason': 'x'}).action_confirm()
        self.env['alfia.bill.price.approval'].with_user(self.manager).create(
            {'move_id': bill.id, 'reason': 'Supplier price list changed on 1 Nov'}).action_confirm()
        bill.with_user(poster).action_post()
        self.assertEqual(bill.state, 'posted')
        within = self.env['account.move'].create({
            'move_type': 'in_invoice', 'partner_id': self.vendor.id, 'invoice_date': fields.Date.today(),
            'invoice_line_ids': [Command.create({'purchase_line_id': po.order_line.id, 'product_id': self.tack.id, 'quantity': 1,
                                                 'price_unit': 5.1, 'name': 'Tack', 'tax_ids': False})]})
        within.with_user(poster).action_post()                             # 2% exactly
        self.assertEqual(within.state, 'posted')

    def test_expired_batch_cannot_be_issued(self):
        product = self.env['product.product'].create({'name': 'Controls expiring vaccine', 'is_storable': True, 'type': 'consu',
                                                      'tracking': 'lot', 'use_expiration_date': True})
        expired = self.env['stock.lot'].create({'name': 'EXP-OLD', 'product_id': product.id,
                                                'expiration_date': fields.Datetime.now() - timedelta(days=2)})
        self.env['stock.quant']._update_available_quantity(product, self.stock, 3, lot_id=expired)
        move = self._out_move(1, product)
        move.move_line_ids.unlink()
        self.env['stock.move.line'].create({'move_id': move.id, 'product_id': product.id, 'lot_id': expired.id, 'quantity': 1,
                                            'picked': True, 'location_id': self.stock.id, 'location_dest_id': move.location_dest_id.id})
        with self.assertRaises(UserError):
            move._action_done()
        # Not towards the customer within the warehouse either (findings 26 Nov), but it may still go to quarantine.
        wh = self.env['stock.warehouse'].search([('lot_stock_id', 'parent_of', self.stock.id)], limit=1) or \
            self.env['stock.warehouse'].search([('company_id', '=', self.env.company.id)], limit=1)
        quarantine = self.env['stock.location'].create({'name': 'Expired quarantine', 'usage': 'internal', 'location_id': wh.view_location_id.id})
        for dest, refused in ((wh.wh_output_stock_loc_id, True), (quarantine, False)):
            internal = self.env['stock.move'].create({'product_id': product.id, 'product_uom_qty': 1, 'uom_id': product.uom_id.id,
                                                      'location_id': self.stock.id, 'location_dest_id': dest.id})
            internal._action_confirm()
            internal.move_line_ids.unlink()
            self.env['stock.move.line'].create({'move_id': internal.id, 'product_id': product.id, 'lot_id': expired.id, 'quantity': 1,
                                                'picked': True, 'location_id': self.stock.id, 'location_dest_id': dest.id})
            if refused:
                with self.assertRaises(UserError):
                    internal._action_done()
            else:
                internal._action_done()
                self.assertEqual(internal.state, 'done')


@tagged('post_install', '-at_install', 'alfia_controls')
class TestPartialAcceptance(TestReceiptQuality):

    def test_partial_accept_two_step(self):
        picking = self._receive(10, steps='two_steps')
        check = self.env['alfia.quality.check'].search([('picking_id', '=', picking.id)])
        check.with_user(self.inspector2).rejected_qty = 3
        with self.assertRaises(UserError):
            check.with_user(self.inspector2).action_accept()             # findings required for a partial rejection
        check.with_user(self.inspector2).findings = 'Three vials cracked'
        check.with_user(self.inspector2).action_accept()
        self.assertEqual(check.state, 'passed')
        rejected = self.env['stock.location'].search([('name', 'ilike', 'Rejected goods'), ('location_id', '=', self.wh.view_location_id.id)])
        self.assertEqual(self._qty(rejected), 3)
        put_away = picking.move_ids.move_dest_ids
        self.assertEqual(put_away.product_uom_qty, 7)
        put_away.picking_id.action_assign()
        put_away.picked = True
        put_away.picking_id.button_validate()
        self.assertEqual(self._qty(self.wh.lot_stock_id), 7)

    def test_partial_accept_one_step(self):
        picking = self._receive(10)
        check = self.env['alfia.quality.check'].search([('picking_id', '=', picking.id)])
        check.with_user(self.inspector2).write({'rejected_qty': 4, 'findings': 'Wrong strength on 4'})
        check.with_user(self.inspector2).action_accept()
        self.assertEqual(self._qty(self.wh.lot_stock_id), 6)


@tagged('post_install', '-at_install', 'alfia_controls')
class TestCustomerReturns(ControlsCase):
    """Findings 26 Nov: a sales clerk starts a customer return and the store validates it; the credit note covers what came
    back, not the whole invoice."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        mk = lambda login, groups: env['res.users'].with_context(no_reset_password=True).create({  # noqa: E731
            'name': login, 'login': f'{login}@ctl.local',
            'group_ids': [Command.link(env.ref(x).id) for x in ['base.group_user', 'alfia_security.group_alfia_all_locations',
                                                                 'alfia_security.group_alfia_all_warehouses',
                                                                 'alfia_security.group_alfia_all_journals'] + groups]})
        cls.clerk = mk('retclerk', ['sales_team.group_sale_salesman', 'account.group_account_invoice'])
        cls.store = mk('retstore', ['stock.group_stock_user'])
        cls.acc = mk('retacc', ['account.group_account_user'])
        cls.customer = env['res.partner'].create({'name': 'Return customer'})
        cls.oats = env['product.product'].create({'name': 'Return oats', 'is_storable': True, 'type': 'consu', 'list_price': 2.0,
                                                  'invoice_policy': 'delivery', 'taxes_id': False})
        env['stock.quant']._update_available_quantity(cls.oats, cls.wh.lot_stock_id, 10)

    def _deliver(self, qty):
        order = self.env['sale.order'].create({'partner_id': self.customer.id, 'order_line': [
            Command.create({'product_id': self.oats.id, 'product_uom_qty': qty, 'tax_ids': False})]})
        order.action_confirm()
        for _ in range(4):
            todo = order.picking_ids.filtered(lambda p: p.state not in ('done', 'cancel'))
            if not todo:
                break
            for picking in todo:
                picking.action_assign()
                picking.move_ids.picked = True
                picking.with_user(self.store).button_validate()
        return order, order.picking_ids.filtered(lambda p: p.picking_type_code == 'outgoing')

    def test_clerk_starts_the_return_and_the_store_validates_it(self):
        order, delivery = self._deliver(2)
        action = delivery.with_user(self.clerk).action_return()                  # needs the scrap reasons, read-only
        ret = self.env['stock.picking'].browse(action['res_id'])
        ret.move_ids.product_uom_qty = 1
        ret.move_ids.picked = True
        with self.assertRaises(AccessError):
            ret.with_user(self.clerk).button_validate()                         # only the store moves goods into stock
        ret.with_user(self.store).button_validate()
        self.assertEqual(ret.state, 'done')
        self.assertEqual(order.order_line.qty_delivered, 1)

    def test_credit_note_covers_what_came_back(self):
        order, delivery = self._deliver(2)
        invoice = order._create_invoices()
        invoice.action_post()
        self.assertEqual(invoice.amount_total, 4.0)
        action = delivery.with_user(self.store).action_return()
        ret = self.env['stock.picking'].browse(action['res_id'])
        ret.move_ids.product_uom_qty = 1
        ret.move_ids.picked = True
        ret.with_user(self.store).button_validate()
        with self.assertRaises(AccessError):
            ret.with_user(self.store).action_alfia_return_credit_note()        # accounting prepares credit notes
        credit = self.env['account.move'].browse(ret.with_user(self.acc).action_alfia_return_credit_note()['res_id'])
        self.assertEqual((credit.move_type, credit.state, credit.amount_total), ('out_refund', 'draft', 2.0))
        self.assertEqual(credit.invoice_line_ids.quantity, 1)
        self.assertIn(ret.name, credit.ref)
        self.assertEqual(ret.alfia_return_credit_ids, credit)
        with self.assertRaises(UserError):
            ret.with_user(self.acc).action_alfia_return_credit_note()          # not twice
