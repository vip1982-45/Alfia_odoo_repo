from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .test_livestock import LivestockCase


class HorseProfitCase(LivestockCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.horse = env['alfia.horse'].create({'name': 'Profit colt', 'sex': 'male'})
        cls.income = env['account.account'].create({'name': 'Boarding income (test)', 'code': '410991', 'account_type': 'income'})
        cls.client = env['res.partner'].create({'name': 'Profit client'})

    def _lines(self, horse=None):
        self.env.flush_all()
        Profit = self.env['alfia.horse.profit']
        Profit.invalidate_model()
        return Profit.search([('horse_id', '=', (horse or self.horse).id)])

    def _by_category(self, horse=None):
        totals = {}
        for line in self._lines(horse):
            totals[line.category] = round(totals.get(line.category, 0.0) + line.amount, 2)
        return totals


@tagged('post_install', '-at_install', 'alfia_livestock')
class TestHorseProfit(HorseProfitCase):

    def test_bills_and_invoices_named_for_the_horse_count_in_its_result(self):
        bill = self._bill(400)
        bill.invoice_line_ids.alfia_horse_id = self.horse
        account = self.horse.analytic_account_id
        self.assertEqual(bill.invoice_line_ids.analytic_distribution, {str(account.id): 100})
        bill.action_post()
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': self.client.id, 'invoice_date': fields.Date.today(),
            'invoice_line_ids': [Command.create({'name': 'Boarding', 'quantity': 1, 'price_unit': 1500, 'tax_ids': False,
                                                 'account_id': self.income.id, 'alfia_horse_id': self.horse.id})]})
        invoice.action_post()
        self.assertEqual(self._by_category(), {'bills': -400, 'services': 1500})
        horse = self.horse.sudo()
        horse.invalidate_recordset()
        self.assertEqual((horse.alfia_profit_cost, horse.alfia_profit_income, horse.alfia_profit_net), (400, 1500, 1100))

    def test_clearing_the_horse_takes_the_line_off_its_result(self):
        bill = self._bill(250)
        line = bill.invoice_line_ids
        line.alfia_horse_id = self.horse
        line.alfia_horse_id = False
        self.assertFalse(line.analytic_distribution)
        other = self.env['alfia.horse'].create({'name': 'Other colt', 'sex': 'male'})
        line.alfia_horse_id = other
        self.assertEqual(line.analytic_distribution, {str(other.analytic_account_id.id): 100})

    def test_purchase_price_counts_and_revaluations_do_not(self):
        self.env['alfia.livestock.classify'].with_user(self.acc).create({
            'horse_id': self.horse.id, 'livestock_class': 'breeding', 'acquisition_kind': 'purchase',
            'acquisition_date': fields.Date.today() - timedelta(days=9), 'acquisition_cost': 50000,
            'note': 'Bought at auction'}).action_confirm()
        val = self.env['alfia.livestock.valuation'].with_user(self.fm1).create({
            'horse_id': self.horse.id, 'value': 60000, 'method': 'cost', 'effective_date': fields.Date.today(),
            'counterpart_account_id': self.gain.id, 'notes': 'Valuer report'})
        self.env['ir.attachment'].create({'name': 'report.pdf', 'raw': b'%PDF-1.4', 'res_model': val._name, 'res_id': val.id})
        val.with_user(self.fm1).action_submit()
        val.with_user(self.fm2).action_approve()
        self.assertTrue(val.move_id)
        self.assertEqual(self._by_category(), {'purchase': -50000})
        horse = self.horse.sudo()
        horse.invalidate_recordset()
        self.assertEqual(horse.alfia_profit_days, 10)
        self.assertEqual(horse.alfia_profit_cost_per_day, 0)      # the purchase price is not a running cost

    def test_stable_costs_count_once_and_a_supplier_bill_replaces_them(self):
        visit = self.env['alfia.vet.visit'].create({'horse_id': self.horse.id, 'reason': 'Colic check', 'cost': 300,
                                                    'alfia_bill_ref': 'VET-778'})
        self.env['alfia.farrier.visit'].create({'horse_id': self.horse.id, 'cost': 120})
        self.env['alfia.odontology'].create({'horse_id': self.horse.id, 'cost': 90})
        self.env['alfia.health.record'].create({'horse_id': self.horse.id, 'title': 'Flu vaccine', 'cost': 60})
        barn = self.env['stock.warehouse'].search([('company_id', '=', self.company.id)], limit=1).lot_stock_id
        move = self.env['alfia.horse.movement'].create({'horse_id': self.horse.id, 'kind': 'internal', 'state': 'done',
                                                        'location_to_id': barn.id, 'reason': 'To the show', 'alfia_cost': 700})
        self.env['alfia.horse.movement'].create({'horse_id': self.horse.id, 'kind': 'internal', 'location_to_id': barn.id,
                                                 'reason': 'Planned', 'alfia_cost': 999})
        self.assertEqual(self._by_category(), {'vet': -360, 'farrier': -120, 'dental': -90, 'transport': -700})
        # The vet bills the visit: once that bill is posted, its line (for the horse) counts and the recorded cost drops out.
        bill = self._bill(320, account=self.vet_exp)
        bill.write({'ref': 'VET-778'})
        bill.invoice_line_ids.alfia_horse_id = self.horse
        self.assertEqual(self._by_category()['vet'], -360)             # draft bill: the recorded cost still counts
        bill.action_post()
        totals = self._by_category()
        self.assertEqual((totals['vet'], totals['bills']), (-60, -320))
        self.assertEqual(visit.alfia_bill_ref, 'VET-778')
        self.assertTrue(move.alfia_cost)

    def test_feed_plan_costs_by_day(self):
        feed = self.env['product.product'].create({'name': 'Profit oats', 'type': 'consu', 'standard_price': 2.0})
        start = fields.Date.today() - timedelta(days=9)
        self.env['alfia.diet'].create({'horse_id': self.horse.id, 'name': 'Winter', 'date_from': start,
                                       'line_ids': [Command.create({'product_id': feed.id, 'qty_per_meal': 2.5, 'meals_per_day': 2})]})
        self.assertEqual(self._by_category(), {'feed': -100.0})       # 10 days x 5 kg x 2.00, split by month if needed
        horse = self.horse.sudo()
        horse.invalidate_recordset()
        self.assertGreater(horse.alfia_profit_cost_per_day, 0)

    def test_semen_sold_is_credited_to_the_stallion_whose_batch_left(self):
        warehouse = self.env['stock.warehouse'].search([('company_id', '=', self.company.id)], limit=1)
        vet = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Profit vet', 'login': 'profitvet@liv.local',
            'group_ids': [Command.link(self.env.ref(g).id) for g in ('base.group_user', 'alfia_security.group_alfia_vet_user',
                                                                       'alfia_security.group_alfia_all_locations')]})
        collection = self.env['alfia.semen.collection'].create({'stallion_id': self.horse.id, 'doses': 4, 'concentration': 100,
                                                                'motility': 60, 'tank_id': warehouse.lot_stock_id.id})
        collection.with_user(vet).action_release()
        semen = collection.product_id
        order = self.env['sale.order'].create({'partner_id': self.client.id, 'order_line': [Command.create({
            'product_id': semen.id, 'product_uom_qty': 1, 'price_unit': 800})]})
        order.action_confirm()
        line = order.order_line
        self.assertFalse(line.alfia_horse_id)
        move = order.picking_ids.move_ids
        move.move_line_ids.unlink()
        self.env['stock.move.line'].create({'move_id': move.id, 'picking_id': move.picking_id.id, 'product_id': semen.id,
                                            'lot_id': collection.lot_id.id, 'quantity': 1, 'location_id': move.location_id.id,
                                            'location_dest_id': move.location_dest_id.id, 'picked': True})
        move._action_done()
        self.assertEqual(line.alfia_horse_id, self.horse)
        self.assertEqual(line.analytic_distribution, {str(self.horse.analytic_account_id.id): 100})
        invoice = order._create_invoices()
        self.assertEqual(invoice.invoice_line_ids.alfia_horse_id, self.horse)
        invoice.action_post()
        self.assertEqual(self._by_category(), {'breeding': 800})

    def test_who_can_see_the_result(self):
        with self.assertRaises(AccessError):
            self.env['alfia.horse.profit'].with_user(self.buyer).search([])
        self.env['alfia.horse.profit'].with_user(self.acc).search([])
        stud = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Profit stud manager', 'login': 'profitstud@liv.local',
            'group_ids': [Command.link(self.env.ref(g).id) for g in ('base.group_user', 'alfia_security.group_alfia_breeding_manager')]})
        self.env['alfia.horse.profit'].with_user(stud).search([])
        self.assertEqual(self.horse.with_user(stud).alfia_profit_net, 0)


@tagged('post_install', '-at_install', 'alfia_livestock')
class TestFeedIssue(HorseProfitCase):

    def test_feed_issued_from_the_store_is_charged_to_each_horse(self):
        wh = self.env['stock.warehouse'].search([('company_id', '=', self.company.id)], limit=1)
        oats = self.env['product.product'].create({'name': 'Issued oats', 'is_storable': True, 'standard_price': 3.0})
        self.env['stock.quant']._update_available_quantity(oats, wh.lot_stock_id, 100)
        other = self.env['alfia.horse'].create({'name': 'Feed filly', 'sex': 'female'})
        start = fields.Date.today().replace(day=1)
        for horse, qty in ((self.horse, 4), (other, 6)):
            self.env['alfia.diet'].create({'horse_id': horse.id, 'name': 'Plan', 'date_from': start,
                                           'line_ids': [Command.create({'product_id': oats.id, 'qty_per_meal': qty, 'meals_per_day': 1})]})
        self.assertIn('feed', self._by_category())                     # the plan estimate, before anything is issued
        groom = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Feed groom', 'login': 'feedgroom@liv.local',
            'group_ids': [Command.link(self.env.ref(g).id) for g in ('base.group_user', 'alfia_security.group_alfia_stable_user',
                                                                       'alfia_security.group_alfia_all_locations')]})
        store = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Feed store', 'login': 'feedstore@liv.local',
            'group_ids': [Command.link(self.env.ref(g).id) for g in ('base.group_user', 'stock.group_stock_user',
                                                                       'alfia_security.group_alfia_all_locations',
                                                                       'alfia_security.group_alfia_all_warehouses')]})
        issue = self.env['alfia.feed.issue'].with_user(groom).create({'product_id': oats.id, 'location_id': wh.lot_stock_id.id})
        issue.with_user(groom).action_fill_from_plans()
        self.assertEqual({l.horse_id: l.quantity for l in issue.line_ids}, {self.horse: 4, other: 6})
        with self.assertRaises(UserError):
            issue.with_user(groom).action_issue()                       # the store issues, not the stable
        issue.with_user(store).action_issue()
        self.assertEqual((issue.state, issue.total_qty, issue.total_value), ('issued', 10, 30))
        self.assertEqual(oats.with_context(location=wh.lot_stock_id.id).qty_available, 90)
        # This month counts what was issued (4 x 3.00), not the plan.
        self.assertEqual(self._by_category(), {'feed': -12.0})
        self.assertEqual(self._by_category(other), {'feed': -18.0})
        with self.assertRaises(UserError):
            issue.line_ids[:1].with_user(store).quantity = 1

    def test_feed_beyond_stock_is_refused(self):
        wh = self.env['stock.warehouse'].search([('company_id', '=', self.company.id)], limit=1)
        hay = self.env['product.product'].create({'name': 'Scarce hay', 'is_storable': True, 'standard_price': 1.0})
        self.env['stock.quant']._update_available_quantity(hay, wh.lot_stock_id, 2)
        issue = self.env['alfia.feed.issue'].create({'product_id': hay.id, 'location_id': wh.lot_stock_id.id,
                                                     'line_ids': [Command.create({'horse_id': self.horse.id, 'quantity': 5})]})
        with self.assertRaises(UserError):
            issue.action_issue()


@tagged('post_install', '-at_install', 'alfia_livestock')
class TestStudExtras(HorseProfitCase):

    def _manager(self, login, group):
        return self.env['res.users'].with_context(no_reset_password=True).create({
            'name': login, 'login': f'{login}@liv.local',
            'group_ids': [Command.link(self.env.ref(g).id) for g in ('base.group_user', group, 'alfia_security.group_alfia_all_locations')]})

    def test_horse_sale_shows_payment_terms_paid_and_balance(self):
        maker = self._manager('salemaker', 'alfia_security.group_alfia_training_manager')
        checker = self._manager('salechecker', 'alfia_security.group_alfia_breeding_manager')
        term = self.env['account.payment.term'].create({'name': '30% now, rest in 60 days', 'line_ids': [
            Command.create({'value': 'percent', 'value_amount': 30, 'nb_days': 0}),
            Command.create({'value': 'percent', 'value_amount': 70, 'nb_days': 60})]})
        sale = self.env['alfia.horse.sale'].with_user(maker).create({
            'horse_id': self.horse.id, 'buyer_id': self.client.id, 'price': 10000, 'payment_term_id': term.id})
        sale.with_user(checker).action_approve()
        sale.with_user(checker).action_complete()
        invoice = sale.move_id.sudo()
        self.assertEqual(invoice.invoice_payment_term_id, term)
        invoice.action_post()
        sale.invalidate_recordset()
        total = invoice.amount_total                                    # the price plus any tax on the sale
        self.assertEqual((sale.alfia_invoice_total, sale.alfia_amount_paid, sale.alfia_amount_due, sale.alfia_payment_state),
                         (total, 0, total, 'not_paid'))
        self.assertEqual(sale.alfia_due_date, invoice.invoice_date_due)
        self.assertFalse(sale.alfia_overdue)                           # the first instalment is due today, not before
        self.env['account.payment.register'].with_context(active_model='account.move', active_ids=invoice.ids).create(
            {'amount': 3000, 'payment_difference_handling': 'open'})._create_payments()
        sale.invalidate_recordset()
        self.assertEqual((sale.alfia_amount_paid, sale.alfia_amount_due), (3000, total - 3000))
        self.assertIn(sale.alfia_payment_state, ('partial', 'in_payment'))
        # The stud manager reads it without access to invoices.
        self.assertEqual(sale.with_user(checker).alfia_amount_due, total - 3000)

    def test_stud_dashboard_counts_for_the_stud_manager(self):
        today = fields.Date.today()
        self.env['alfia.health.record'].create([
            {'horse_id': self.horse.id, 'title': 'Overdue vaccine', 'date': today - timedelta(days=400), 'next_due': today - timedelta(days=3)},
            {'horse_id': self.horse.id, 'title': 'Due today', 'date': today - timedelta(days=30), 'next_due': today},
            {'horse_id': self.horse.id, 'title': 'Due soon', 'date': today, 'next_due': today + timedelta(days=10)}])
        self.env['alfia.vet.visit'].create({'horse_id': self.horse.id, 'reason': 'Dashboard check', 'cost': 75})
        stud = self._manager('dashstud', 'alfia_security.group_alfia_breeding_manager')
        action = self.env['alfia.stud.dashboard'].with_user(stud).action_open_dashboard()
        dash = self.env['alfia.stud.dashboard'].with_user(stud).browse(action['res_id'])
        self.assertGreaterEqual(dash.health_overdue, 1)
        self.assertGreaterEqual(dash.health_today, 1)
        self.assertGreaterEqual(dash.health_30, 2)
        self.assertGreaterEqual(dash.horses_active, 1)
        self.assertTrue(dash.can_see_profit)
        self.assertIn('Profit colt', str(dash.best_html) + str(dash.worst_html))
        overdue = self.env['alfia.health.record'].with_user(stud).search(dash.action_health_overdue()['domain'])
        self.assertIn('Overdue vaccine', overdue.mapped('title'))
        self.assertNotIn('Due soon', overdue.mapped('title'))
        vet = self._manager('dashvet', 'alfia_security.group_alfia_vet_manager')
        vet_dash = self.env['alfia.stud.dashboard'].with_user(vet).create({})
        self.assertFalse(vet_dash.can_see_profit)                      # results are for the stud manager and finance

    def test_days_with_us_start_at_the_first_arrival(self):
        barn = self.env['stock.warehouse'].search([('company_id', '=', self.company.id)], limit=1).lot_stock_id
        horse = self.env['alfia.horse'].create({'name': 'Arrival mare', 'sex': 'female',
                                                'birthdate': fields.Date.today() - timedelta(days=2000)})
        self.env['alfia.horse.movement'].create({'horse_id': horse.id, 'kind': 'arrival', 'state': 'done', 'location_to_id': barn.id,
                                                 'date': fields.Datetime.now() - timedelta(days=4)})
        self.assertEqual(horse.sudo().alfia_profit_days, 5)

    def test_store_cannot_save_a_feed_issue_from_another_site(self):
        wh = self.env['stock.warehouse'].search([('company_id', '=', self.company.id)], limit=1)
        elsewhere = self.env['stock.location'].create({'name': 'Other site store', 'usage': 'internal', 'location_id': wh.view_location_id.id})
        profile_site = self.env['stock.location'].create({'name': 'My site store', 'usage': 'internal', 'location_id': wh.view_location_id.id})
        store = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Scoped store', 'login': 'scopedstore@liv.local',
            'group_ids': [Command.link(self.env.ref(g).id) for g in ('base.group_user', 'stock.group_stock_user')]})
        if not store.alfia_scope_locations:
            self.skipTest("Location scope is not enforced for this user in this database")
        self.assertNotIn(elsewhere, store.alfia_location_ids)
        feed = self.env['product.product'].create({'name': 'Scoped oats', 'is_storable': True})
        with self.assertRaises(AccessError):
            self.env['alfia.feed.issue'].with_user(store).create({'product_id': feed.id, 'location_id': elsewhere.id})
        self.assertTrue(profile_site)
