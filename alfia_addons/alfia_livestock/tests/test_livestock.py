from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


class LivestockCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.company = env.company
        cls.company.alfia_sod_enabled = True
        cls.company.alfia_sod_threshold = 0

        def user(login, groups):
            return env['res.users'].with_context(no_reset_password=True).create({
                'name': login, 'login': f'{login}@liv.local',
                'group_ids': [Command.link(env.ref(g).id) for g in ['base.group_user', 'alfia_security.group_alfia_all_journals',
                                                                     'alfia_security.group_alfia_all_locations',
                                                                     'alfia_security.group_alfia_all_warehouses'] + groups]})
        cls.acc = user('livacc', ['account.group_account_user'])
        cls.fm1 = user('livfm1', ['account.group_account_manager'])
        cls.fm2 = user('livfm2', ['account.group_account_manager'])
        cls.buyer = user('livbuyer', ['purchase.group_purchase_user'])
        Account = env['account.account']
        cls.feed_exp = Account.create({'name': 'Feed expense (test)', 'code': '510991', 'account_type': 'expense'})
        cls.vet_exp = Account.create({'name': 'Vet expense (test)', 'code': '510992', 'account_type': 'expense'})
        cls.livestock = Account.create({'name': 'Livestock (test)', 'code': '160991', 'account_type': 'asset_non_current'})
        cls.gain = Account.create({'name': 'Livestock gain (test)', 'code': '790991', 'account_type': 'income_other'})
        cls.loss = Account.create({'name': 'Livestock loss (test)', 'code': '690991', 'account_type': 'expense'})
        cls.clearing = Account.create({'name': 'Horse purchases (test)', 'code': '160992', 'account_type': 'asset_current'})
        cls.journal = env['account.journal'].search([('type', '=', 'general'), ('company_id', '=', cls.company.id)], limit=1)
        cls.company.write({'alfia_livestock_account_id': cls.livestock.id, 'alfia_livestock_gain_account_id': cls.gain.id,
                           'alfia_livestock_loss_account_id': cls.loss.id, 'alfia_livestock_journal_id': cls.journal.id})
        cls.vendor = env['res.partner'].create({'name': 'Budget vendor'})
        cls.feed = env['product.product'].create({'name': 'Budget feed', 'type': 'consu', 'property_account_expense_id': cls.feed_exp.id,
                                                  'purchase_method': 'purchase'})
        cls.year_start, cls.year_end = fields.Date.today().replace(month=1, day=1), fields.Date.today().replace(month=12, day=31)

    def _budget(self, planned=1000, control='block', analytic=False, account=None):
        budget = self.env['alfia.budget'].with_user(self.acc).create({
            'name': 'Feed budget', 'date_from': self.year_start, 'date_to': self.year_end, 'control': control,
            'line_ids': [Command.create({'account_id': (account or self.feed_exp).id, 'planned': planned,
                                         'analytic_account_id': analytic and analytic.id})]})
        budget.with_user(self.acc).action_submit()
        budget.with_user(self.fm1).action_approve()
        return budget

    def _po(self, amount):
        return self.env['purchase.order'].with_user(self.buyer).create({
            'partner_id': self.vendor.id, 'order_line': [Command.create({'product_id': self.feed.id, 'product_qty': 1, 'price_unit': amount})]})

    def _bill(self, amount, account=None, user=None):
        return self.env['account.move'].create({
            'move_type': 'in_invoice', 'partner_id': self.vendor.id, 'invoice_date': fields.Date.today(),
            'invoice_line_ids': [Command.create({'name': 'Feed', 'quantity': 1, 'price_unit': amount, 'tax_ids': False,
                                                 'account_id': (account or self.feed_exp).id})]})


@tagged('post_install', '-at_install', 'alfia_livestock')
class TestBudgets(LivestockCase):

    def test_preparer_cannot_approve_and_versions_replace_on_approval(self):
        budget = self.env['alfia.budget'].with_user(self.fm1).create({
            'name': 'Vet budget', 'date_from': self.year_start, 'date_to': self.year_end,
            'line_ids': [Command.create({'account_id': self.vet_exp.id, 'planned': 500})]})
        budget.with_user(self.fm1).action_submit()
        with self.assertRaises(UserError):
            budget.with_user(self.fm1).action_approve()
        with self.assertRaises(AccessError):
            budget.with_user(self.acc).action_approve()
        budget.with_user(self.fm2).action_approve()
        with self.assertRaises(UserError):
            budget.with_user(self.fm1).write({'name': 'changed'})
        action = budget.with_user(self.fm1).action_new_version()
        v2 = self.env['alfia.budget'].browse(action['res_id'])
        self.assertEqual((v2.version, v2.previous_id, v2.state, budget.state), (2, budget, 'draft', 'approved'))
        v2.line_ids.with_user(self.fm1).planned = 800
        v2.with_user(self.fm1).action_submit()
        v2.with_user(self.fm2).action_approve()
        self.assertEqual((budget.state, v2.state), ('superseded', 'approved'))

    def test_block_on_purchase_order_until_a_finance_manager_overrides(self):
        budget = self._budget(planned=1000, control='block')
        po = self._po(1500)
        with self.assertRaises(UserError):
            po.with_user(self.buyer).button_confirm()
        with self.assertRaises(AccessError):
            po.with_user(self.acc).action_alfia_budget_override()
        self.env['alfia.budget.override'].with_user(self.fm1).create(
            {'res_model': 'purchase.order', 'res_id': po.id, 'reason': 'Drought: hay prices doubled'}).action_confirm()
        po.with_user(self.buyer).button_confirm()
        self.assertIn(po.state, ('purchase', 'to approve'))
        self.assertTrue(any('override' in (m.body or '') for m in po.message_ids))
        if po.state == 'to approve':
            po.sudo().button_approve()
        budget.line_ids.invalidate_recordset()
        self.assertAlmostEqual(budget.line_ids.committed, 1500, places=2, msg="confirmed and not yet billed")

    def test_warning_rule_lets_the_order_through_with_a_note(self):
        self._budget(planned=1000, control='warning')
        po = self._po(1200)
        po.with_user(self.buyer).button_confirm()
        self.assertTrue(any('Budget warning' in (m.body or '') for m in po.message_ids))

    def test_actuals_from_bills_and_block_at_posting(self):
        budget = self._budget(planned=1000, control='block')
        bill = self._bill(400)
        bill.with_user(self.fm1).action_post()
        line = budget.line_ids
        line.invalidate_recordset()
        self.assertAlmostEqual(line.actual, 400, places=2)
        self.assertAlmostEqual(line.available, 600, places=2)
        with self.assertRaises(UserError):
            self._bill(700).with_user(self.fm1).action_post()
        action = line.action_view_actuals()
        self.assertEqual(self.env['account.move.line'].search_count(action['domain']), 1)

    def test_cost_centre_line_takes_precedence(self):
        horse = self.env['alfia.horse'].create({'name': 'Budget horse', 'sex': 'male'})
        analytic = horse.sudo().analytic_account_id or self.env['account.analytic.account'].create({
            'name': 'Budget horse', 'plan_id': self.env['account.analytic.plan'].search([], limit=1).id})
        self._budget(planned=100, control='block', analytic=analytic)
        self._budget(planned=100000, control='block')                      # the account line for everything else
        bill = self._bill(300)
        bill.invoice_line_ids.analytic_distribution = {str(analytic.id): 100}
        with self.assertRaises(UserError):
            bill.with_user(self.fm1).action_post()
        self._bill(300).with_user(self.fm1).action_post()               # no cost centre: the large account line applies


@tagged('post_install', '-at_install', 'alfia_livestock')
class TestLivestock(LivestockCase):

    def _horse(self):
        return self.env['alfia.horse'].create({'name': 'Register mare', 'sex': 'female'})

    def _valuation(self, horse, value, user=None, method='cost', counterpart=None, date=None):
        val = self.env['alfia.livestock.valuation'].with_user(user or self.acc).create({
            'horse_id': horse.id, 'value': value, 'method': method, 'effective_date': date or fields.Date.today(),
            'counterpart_account_id': counterpart and counterpart.id, 'notes': 'Independent valuer report attached'})
        self._attach(val)
        val.with_user(user or self.acc).action_submit()
        return val

    def _attach(self, val):
        self.env['ir.attachment'].create({'name': 'valuer-report.pdf', 'raw': b'%PDF-1.4 test', 'res_model': val._name, 'res_id': val.id})

    def test_classification_and_acquisition_are_logged(self):
        horse = self._horse()
        wiz = self.env['alfia.livestock.classify'].with_user(self.acc).create({
            'horse_id': horse.id, 'livestock_class': 'breeding', 'acquisition_kind': 'purchase',
            'acquisition_date': fields.Date.today(), 'acquisition_cost': 250000, 'note': 'Bought at auction'})
        wiz.action_confirm()
        horse = horse.sudo()
        self.assertEqual((horse.alfia_livestock_class, horse.alfia_acquisition_cost), ('breeding', 250000))
        self.assertIn('acquisition', horse.alfia_livestock_event_ids.mapped('kind'))
        with self.assertRaises(AccessError):
            horse.with_user(self.acc).write({'name': 'Renamed by accounting'})
        horse.write({'alfia_livestock_class': 'for_sale'})
        self.assertIn('classification', horse.alfia_livestock_event_ids.mapped('kind'))

    def test_valuations_post_adjustments_with_two_people_and_keep_history(self):
        horse = self._horse()
        first = self._valuation(horse, 250000, counterpart=self.clearing)
        with self.assertRaises(AccessError):
            first.with_user(self.acc).action_approve()
        first.with_user(self.fm1).action_approve()
        self.assertEqual(first.state, 'posted')
        self.assertEqual(sum(first.move_id.line_ids.filtered(lambda l: l.account_id == self.livestock).mapped('debit')), 250000)
        self.assertEqual(horse.sudo().alfia_carrying_value, 250000)
        down = self._valuation(horse, 200000, user=self.fm1, method='fair_value')
        with self.assertRaises(UserError):
            down.with_user(self.fm1).action_approve()                   # prepared it
        down.with_user(self.fm2).action_approve()
        loss = down.move_id.line_ids.filtered(lambda l: l.account_id == self.loss)
        self.assertEqual((loss.debit, down.previous_value), (50000, 250000))
        self.assertEqual(horse.sudo().alfia_carrying_value, 200000)
        with self.assertRaises(UserError):
            down.with_user(self.fm2).unlink()
        with self.assertRaises(UserError):
            self._valuation(horse, 1, method='cost').with_user(self.fm2).write({'value': 2})
        self.assertEqual(len(horse.sudo().alfia_livestock_event_ids.filtered(lambda e: e.kind == 'valuation')), 2)

    def test_submit_needs_basis_and_disposal_is_drafted_when_the_horse_leaves(self):
        horse = self._horse()
        val = self.env['alfia.livestock.valuation'].with_user(self.acc).create({'horse_id': horse.id, 'value': 1000})
        with self.assertRaises(UserError):
            val.action_submit()                                         # no basis
        val.notes = 'Valuer report'
        with self.assertRaises(UserError):
            val.action_submit()                                         # basis but no evidence file (Round 7 L2)
        self._attach(val)
        val.action_submit()
        self._valuation(horse, 90000, counterpart=self.clearing).with_user(self.fm1).action_approve()
        horse.write({'status': 'sold'})
        draft = self.env['alfia.livestock.valuation'].search([('horse_id', '=', horse.id), ('method', '=', 'disposal')])
        self.assertEqual((draft.state, draft.value), ('draft', 0))
        self._attach(draft)
        with self.assertRaises(UserError):
            draft.with_user(self.fm1).action_submit()                   # no counterpart chosen
        draft.with_user(self.fm1).counterpart_account_id = self.loss
        draft.with_user(self.fm1).action_submit()
        draft.with_user(self.fm2).action_approve()
        self.assertEqual(horse.sudo().alfia_carrying_value, 0)
        self.assertIn('disposal', horse.sudo().alfia_livestock_event_ids.mapped('kind'))

    def test_valuation_entries_are_not_manual_entries(self):
        horse = self._horse()
        val = self._valuation(horse, 5000, user=self.fm1, counterpart=self.clearing)
        val.with_user(self.fm2).action_approve()
        self.assertFalse(val.move_id._alfia_is_manual())
