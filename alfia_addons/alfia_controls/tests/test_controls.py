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
