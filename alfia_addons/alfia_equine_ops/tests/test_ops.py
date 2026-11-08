import base64

from dateutil.relativedelta import relativedelta

from odoo import Command, fields
from odoo.addons.alfia_equine.tests.test_equine import EquineCase
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged
from odoo.tools import mute_logger
from psycopg2 import IntegrityError

TODAY = fields.Date.context_today


class OpsCase(EquineCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.accountant = cls._user('acct', [])
        cls.accountant.write({'group_ids': [Command.link(env.ref(x).id) for x in ('account.group_account_invoice', 'alfia_security.group_alfia_all_journals', 'alfia_security.group_alfia_all_warehouses')]})
        cls.custodian = cls._user('medcust', ['medicine_custodian', 'vet_user'])
        cls.client = env['res.partner'].create({'name': 'Client One'})
        cls.buyer = env['res.partner'].create({'name': 'Buyer Two'})
        cls.service = env['product.product'].create({'name': 'Board', 'type': 'service', 'list_price': 100.0})
        (cls.mare | cls.mare2 | cls.far_mare | cls.stallion).invoice_to_id = cls.client

    def _today(self):
        return fields.Date.context_today(self.env['alfia.charge'])


@tagged('post_install', '-at_install', 'alfia_equine_ops')
class TestOps(OpsCase):

    # --- groups
    def test_dynamic_group(self):
        group = self.env['alfia.horse.group'].create({'name': 'Mares in A', 'sex': 'female', 'location_id': self.barn_a.id, 'member_ids': [Command.link(self.stallion.id)]})
        self.assertEqual(group.all_member_ids, self.mare | self.mare2 | self.stallion)
        self.assertNotIn(self.far_mare, group.all_member_ids)

    # --- billing
    def test_plan_is_idempotent_and_invoices_once(self):
        today = self._today()
        plan = self.env['alfia.charge.plan'].create({'name': 'Barn A fee', 'target_kind': 'location', 'location_id': self.barn_a.id,
                                                      'product_id': self.service.id, 'next_date': today, 'auto_approve': True})
        self.assertEqual(plan.action_generate(), 2)
        self.assertEqual(plan.next_date, today + relativedelta(months=1))
        plan.next_date = today
        self.assertEqual(plan.action_generate(), 0, "the same (plan, horse, date) is never generated twice")
        charges = self.env['alfia.charge'].search([('plan_id', '=', plan.id)])
        self.assertEqual(len(charges), 2)
        charges.with_user(self.accountant).action_invoice()
        self.assertEqual(set(charges.mapped('state')), {'invoiced'})
        count = self.env['account.move'].search_count([('invoice_line_ids.name', 'ilike', 'Cleopatra')])
        charges.with_user(self.accountant).action_invoice()
        self.assertEqual(self.env['account.move'].search_count([('invoice_line_ids.name', 'ilike', 'Cleopatra')]), count)
        with self.assertRaises(UserError):
            charges[:1].write({'quantity': 5})

    def test_charge_permissions(self):
        charge = self.env['alfia.charge'].create({'horse_id': self.mare.id, 'product_id': self.service.id})
        self.assertEqual(charge.partner_id, self.client)
        with self.assertRaises(AccessError):
            charge.with_user(self.stable).read(['amount'])
        with self.assertRaises(AccessError):
            charge.with_user(self.breeder).action_approve()
        charge.with_user(self.breed_mgr).action_approve()
        with self.assertRaises(AccessError):
            charge.with_user(self.breed_mgr).action_invoice()
        with self.assertRaises(ValidationError):
            charge.quantity = 0

    def test_charge_site_scope(self):
        scoped = self._user('scopedbm', ['breeding_manager'], scoped=True)
        scoped.alfia_location_ids = [Command.set([self.barn_b.id])]
        a = self.env['alfia.charge'].create({'horse_id': self.mare.id, 'product_id': self.service.id})
        b = self.env['alfia.charge'].create({'horse_id': self.far_mare.id, 'product_id': self.service.id, 'partner_id': self.client.id})
        seen = self.env['alfia.charge'].with_user(scoped).search([('id', 'in', (a | b).ids)])
        self.assertEqual(seen, b)

    # --- sale
    def test_sale_flow(self):
        self.env['alfia.horse.ownership'].create({'horse_id': self.mare2.id, 'partner_id': self.client.id, 'date_from': '2020-01-01'})
        sale = self.env['alfia.horse.sale'].with_user(self.breed_mgr).create({'horse_id': self.mare2.id, 'buyer_id': self.buyer.id, 'price': 5000})
        with self.assertRaises(UserError):
            sale.action_complete()
        with self.assertRaises(UserError):
            sale.action_approve()  # preparer cannot approve own sale
        sale.with_user(self.train_mgr).action_approve()
        sale.with_user(self.train_mgr).action_complete()
        horse = self.mare2
        self.assertEqual(horse.status, 'sold')
        self.assertFalse(horse.on_site)
        self.assertEqual(horse.owner_ids, self.buyer)
        self.assertEqual(sale.move_id.amount_untaxed, 5000)
        with self.assertRaises(UserError):
            sale.action_cancel()
        gift = self.env['alfia.horse.sale'].create({'horse_id': self.far_mare.id, 'kind': 'gift', 'buyer_id': self.buyer.id})
        with self.assertRaises(ValidationError):
            self.env['alfia.horse.sale'].create({'horse_id': self.far_mare.id, 'kind': 'sale', 'buyer_id': self.buyer.id, 'price': 0})
        gift.action_approve()
        gift.action_complete()
        self.assertEqual(self.far_mare.status, 'gifted')
        self.assertFalse(gift.move_id)

    # --- boarding
    def test_boarding_creates_plan(self):
        stay = self.env['alfia.boarding'].create({'horse_id': self.mare.id, 'client_id': self.client.id, 'product_id': self.service.id, 'rate': 250.0})
        stay.action_start()
        self.assertEqual(stay.plan_id.price_unit, 250.0)
        stay.plan_id.action_generate()
        charge = self.env['alfia.charge'].search([('plan_id', '=', stay.plan_id.id)])
        self.assertEqual((charge.partner_id, charge.price_unit), (self.client, 250.0))
        stay.action_end()
        self.assertEqual(stay.state, 'ended')
        self.assertEqual(stay.plan_id.end_date, stay.end_date)

    # --- feed
    def test_diet_requirements(self):
        feed = self.env['product.product'].create({'name': 'Oats', 'type': 'consu'})
        diet = self.env['alfia.diet'].create({'horse_id': self.mare.id, 'name': 'Winter', 'line_ids': [Command.create({'product_id': feed.id, 'qty_per_meal': 2.5, 'meals_per_day': 3})]})
        self.assertEqual(diet.line_ids.daily_qty, 7.5)
        self.assertIn(diet, self.env['alfia.diet'].search([('current', '=', True)]))
        self.assertNotIn(diet, self.env['alfia.diet'].search([('current', '=', False)]))
        with self.assertRaises(IntegrityError), mute_logger('odoo.sql_db'):
            diet.line_ids.qty_per_meal = 0
            self.env.flush_all()

    # --- quarantine
    def test_quarantine(self):
        today = self._today()
        q = self.env['alfia.quarantine'].create({'horse_id': self.mare.id, 'planned_end': today + relativedelta(days=14), 'reason': 'New arrival'})
        self.assertTrue(self.env['alfia.task'].search([('key', 'like', f'quarantine:{q.id}')]))
        with self.assertRaises(ValidationError):
            self.env['alfia.quarantine'].create({'horse_id': self.mare.id, 'planned_end': today + relativedelta(days=3), 'reason': 'again'})
        with self.assertRaises(UserError):
            q.with_user(self.stable).action_release()
        q.with_user(self.vet).action_release()
        self.assertEqual(q.state, 'released')
        self.assertFalse(self.env['alfia.task'].search([('key', 'like', f'quarantine:{q.id}'), ('state', '=', 'todo')]))

    # --- medicine
    def test_medicine_issue_and_return(self):
        med = self.env['product.product'].create({'name': 'Penicillin', 'type': 'consu', 'is_storable': True})
        self.env['stock.quant']._update_available_quantity(med, self.barn_a, 10.0)
        issue = self.env['alfia.medicine.issue'].create({'horse_id': self.mare.id, 'product_id': med.id, 'quantity': 4, 'location_id': self.barn_a.id})
        with self.assertRaises(UserError):
            issue.with_user(self.vet).action_issue()  # a vet who is not the custodian
        issue.with_user(self.custodian).action_issue()
        self.assertEqual(med.with_context(location=self.barn_a.id).qty_available, 6.0)
        with self.assertRaises(UserError):
            issue.with_user(self.custodian).action_return(5)
        issue.with_user(self.custodian).action_return(1)
        self.assertEqual(med.with_context(location=self.barn_a.id).qty_available, 7.0)
        big = self.env['alfia.medicine.issue'].create({'horse_id': self.mare.id, 'product_id': med.id, 'quantity': 50, 'location_id': self.barn_a.id})
        with self.assertRaises(UserError):
            big.with_user(self.custodian).action_issue()

    # --- contracts
    def test_contract_invoice_and_refund(self):
        pregnancy = self._pregnancy()
        contract = self.env['alfia.breeding.contract'].with_user(self.breed_mgr).create({
            'client_id': self.client.id, 'stallion_id': self.stallion.id, 'mare_id': self.mare.id, 'season': '2026', 'product_id': self.service.id, 'fee': 3000, 'pregnancy_id': pregnancy.id})
        self.assertTrue(contract.name.startswith('BC/'))
        contract.action_confirm()
        with self.assertRaises(AccessError):
            contract.with_user(self.breed_mgr).action_invoice()
        contract.with_user(self.accountant).action_invoice()
        self.assertEqual(contract.invoice_id.amount_untaxed, 3000)
        with self.assertRaises(UserError):
            contract.with_user(self.accountant).action_refund()  # pregnancy still open
        pregnancy.state = 'lost'
        self.assertEqual(contract.outcome, 'no_live')
        contract.with_user(self.accountant).action_refund()
        self.assertEqual(contract.credit_note_id.move_type, 'out_refund')
        self.assertEqual(contract.credit_note_id.amount_untaxed, 3000)
        with self.assertRaises(UserError):
            contract.with_user(self.accountant).action_refund()
        with self.assertRaises(UserError):
            contract.action_cancel()

    # --- events
    def test_entry_rules(self):
        today = self._today()
        rule = self.env['alfia.restriction.rule'].with_user(self.train_mgr).create({'name': 'Mares 4-12', 'sex': 'female', 'min_age_years': 4})
        event = self.env['alfia.event'].with_user(self.train_mgr).create({'name': 'Cup', 'date_start': today + relativedelta(days=10), 'rule_id': rule.id})
        self.mare.birthdate = today - relativedelta(years=8)
        entry = self.env['alfia.event.entry'].with_user(self.trainer).create({'event_id': event.id, 'horse_id': self.mare.id})
        entry.with_user(self.trainer).action_submit()
        self.assertIn('fitness', entry.issues)
        with self.assertRaises(UserError):
            entry.with_user(self.train_mgr).action_approve()
        with self.assertRaises(AccessError):
            entry.with_user(self.trainer).action_approve()
        self.env['alfia.clearance'].create({'horse_id': self.mare.id, 'kind': 'fitness'}).with_user(self.vet).action_clear()
        entry.with_user(self.vet).action_vet_confirm()
        entry.with_user(self.train_mgr).action_approve()
        self.assertEqual(entry.state, 'approved')
        young = self.env['alfia.horse'].create({'name': 'Yearling', 'sex': 'female', 'birthdate': today - relativedelta(years=1), 'initial_location_id': self.barn_a.id})
        self.assertTrue(any('young' in p.lower() for p in rule._problems(young, today)))
        with self.assertRaises(IntegrityError), mute_logger('odoo.sql_db'):
            self.env['alfia.event.entry'].create({'event_id': event.id, 'horse_id': self.mare.id})

    def test_entry_blocked_by_withdrawal(self):
        today = self._today()
        rule = self.env['alfia.restriction.rule'].create({'name': 'Open', 'require_fitness': False})
        self.env['alfia.treatment'].create({'horse_id': self.mare.id, 'drug': 'Bute', 'start_date': today, 'withdrawal_days': 5})
        self.assertTrue(any('withdrawal' in p.lower() for p in rule._problems(self.mare, today)))

    # --- assets / report / import
    def test_asset_and_report(self):
        self.env['alfia.asset'].create({'name': 'Horse trailer', 'kind': 'vehicle', 'reference': 'Q-123'})
        pregnancy = self._pregnancy()
        service = self.env['alfia.breeding.service'].create({'mare_id': self.mare.id, 'stallion_id': self.stallion.id, 'method': 'natural', 'date': OVULATION_DATE})
        service.action_done()
        self.env.flush_all()
        row = self.env['alfia.report.breeding'].search([('stallion_id', '=', self.stallion.id)])
        self.assertEqual(row.services, 1)
        with self.assertRaises(AccessError):
            self.env['alfia.report.breeding'].with_user(self.stable).search([])

    def test_import(self):
        csv_text = "name,sex,birthdate,breed\nSahara,F,2019-04-01,Arabian\nBadName,x,,\nSahara,F,,\n,F,,\nDesert,stallion,not-a-date,\n"
        wiz = self.env['alfia.horse.import'].create({'file': base64.b64encode(csv_text.encode()).decode(), 'filename': 'h.csv'})
        wiz.action_import()
        self.assertIn('Created 1, skipped 1, failed 3', wiz.result)
        self.assertTrue(self.env['alfia.horse'].search([('name', '=', 'Sahara'), ('breed', '=', 'Arabian')]))
        bad = self.env['alfia.horse.import'].create({'file': base64.b64encode(b"foo,bar\n1,2\n").decode()})
        with self.assertRaises(UserError):
            bad.action_import()


OVULATION_DATE = fields.Date.from_string('2026-03-02')


@tagged('post_install', '-at_install', 'alfia_equine_ops')
class TestOpsHardening(OpsCase):

    def test_quarantine_release_leaves_other_quarantines_alone(self):
        today = self._today()
        Q = self.env['alfia.quarantine']
        horses = self.env['alfia.horse'].create([{'name': f'Q{n}', 'sex': 'female', 'initial_location_id': self.barn_a.id} for n in range(12)])
        records = Q.create([{'horse_id': h.id, 'planned_end': today + relativedelta(days=7), 'reason': 'arrival'} for h in horses])
        first = records[0]
        others = records.filtered(lambda q: str(q.id).startswith(str(first.id)) and q != first) or records[1:]
        first.with_user(self.vet).action_release()
        for q in others:
            self.assertTrue(self.env['alfia.task'].search_count([('key', '=', f'quarantine:{q.id}'), ('state', '=', 'todo')]))

    def test_approved_charge_amount_is_locked_and_creator_cannot_approve(self):
        charge = self.env['alfia.charge'].with_user(self.breed_mgr).create({'horse_id': self.mare.id, 'product_id': self.service.id})
        with self.assertRaises(UserError):
            charge.with_user(self.breed_mgr).action_approve()
        self.assertEqual(charge.price_unit, 100.0)
        charge.with_user(self.train_mgr).action_approve()
        with self.assertRaises(UserError):
            charge.with_user(self.train_mgr).write({'price_unit': 1.0})
        charge.with_user(self.accountant).action_invoice()
        self.assertEqual(self.env['alfia.charge'].browse(charge.id).move_id.amount_untaxed, 100.0)

    def test_approved_sale_and_confirmed_contract_are_locked(self):
        sale = self.env['alfia.horse.sale'].with_user(self.breed_mgr).create({'horse_id': self.mare2.id, 'buyer_id': self.buyer.id, 'price': 5000})
        sale.with_user(self.train_mgr).action_approve()
        with self.assertRaises(UserError):
            sale.with_user(self.breed_mgr).write({'price': 1})
        with self.assertRaises(AccessError):
            sale.with_user(self.breeder).action_complete()      # staff, not a manager
        contract = self.env['alfia.breeding.contract'].create({'client_id': self.client.id, 'stallion_id': self.stallion.id, 'season': '2026',
                                                               'product_id': self.service.id, 'fee': 3000, 'live_foal_guarantee': False})
        contract.action_confirm()
        with self.assertRaises(UserError):
            contract.with_user(self.breed_mgr).write({'live_foal_guarantee': True})
        with self.assertRaises(UserError):
            contract.with_user(self.breed_mgr).write({'fee': 1})

    def test_plan_scope_and_catch_up_cap(self):
        scoped = self._user('scopedplan', ['breeding_manager'], scoped=True)
        profile = self.env['alfia.access.profile'].create({'name': 'Ops barn B', 'location_ids': [Command.set([self.barn_b.id])]})
        scoped.with_context(alfia_sync=True).sudo().write({'alfia_profile_ids': [Command.set([profile.id])]})
        with self.assertRaises(AccessError):
            self.env['alfia.charge.plan'].with_user(scoped).create({'name': 'A fees', 'target_kind': 'location', 'location_id': self.barn_a.id, 'product_id': self.service.id})
        old = self._today() - relativedelta(years=10)
        plan = self.env['alfia.charge.plan'].create({'name': 'Old', 'target_kind': 'horse', 'horse_id': self.mare.id, 'product_id': self.service.id,
                                                     'next_date': old, 'interval': 'weekly'})
        from odoo.addons.alfia_equine_ops.models.billing import MAX_PERIODS_PER_RUN
        self.assertEqual(plan.action_generate(), MAX_PERIODS_PER_RUN, "a plan far behind catches up in bounded steps")
        self.assertEqual(plan.next_date, old + relativedelta(weeks=MAX_PERIODS_PER_RUN))

    def test_medicine_store_outside_scope(self):
        profile = self.env['alfia.access.profile'].create({'name': 'Ops med B', 'location_ids': [Command.set([self.barn_b.id])],
            'group_ids': [Command.set([self.env.ref('alfia_security.group_alfia_medicine_custodian').id, self.env.ref('alfia_security.group_alfia_vet_user').id])]})
        cust_b = self.env['res.users'].with_context(no_reset_password=True).create({'name': 'custb', 'login': 'custb@eq.local', 'alfia_profile_ids': [Command.set([profile.id])]})
        med = self.env['product.product'].create({'name': 'Bute', 'type': 'consu', 'is_storable': True})
        self.env['stock.quant']._update_available_quantity(med, self.barn_a, 10.0)
        issue = self.env['alfia.medicine.issue'].create({'horse_id': self.far_mare.id, 'product_id': med.id, 'quantity': 1, 'location_id': self.barn_a.id})
        with self.assertRaises(AccessError):
            issue.with_user(cust_b).action_issue()

    def test_report_counts_each_service_once(self):
        pregnancy = self._pregnancy()
        service = self.env['alfia.breeding.service'].create({'mare_id': self.mare.id, 'stallion_id': self.stallion.id, 'method': 'natural', 'date': OVULATION_DATE})
        service.action_done()
        pregnancy.write({'service_id': service.id, 'state': 'pregnant'})
        second = self._pregnancy(self.mare2)
        second.write({'service_id': service.id, 'state': 'pregnant'})
        self.env.flush_all()
        row = self.env['alfia.report.breeding'].search([('stallion_id', '=', self.stallion.id)])
        self.assertEqual((row.services, row.pregnancies), (1, 2))

    def test_import_rejects_oversized_files(self):
        from odoo.addons.alfia_equine_ops.models.wizard import MAX_BYTES
        big = base64.b64encode(b"name,sex\n" + b"x,F\n" * (MAX_BYTES // 4 + 1)).decode()
        with self.assertRaises(UserError):
            self.env['alfia.horse.import'].create({'file': big}).action_import()


@tagged('post_install', '-at_install', 'alfia_equine_ops')
class TestOpsDisplayNames(OpsCase):

    def test_operations_records_read_as_text(self):
        charge = self.env['alfia.charge'].create({'horse_id': self.mare.id, 'product_id': self.service.id, 'name': 'Board'})
        self.assertTrue(charge.display_name.startswith('Cleopatra – Board'))
        q = self.env['alfia.quarantine'].create({'horse_id': self.mare.id, 'planned_end': self._today() + relativedelta(days=3), 'reason': 'Arrival'})
        self.assertTrue(q.display_name.startswith('Cleopatra – Arrival'))

    def test_any_manager_who_sees_the_horse_completes_an_approved_sale(self):
        profile = self.env['alfia.access.profile'].create({'name': 'Ops resident vet A', 'location_ids': [Command.set([self.barn_a.id])],
            'group_ids': [Command.set([self.env.ref('alfia_security.group_alfia_vet_manager').id])]})
        rvet = self.env['res.users'].with_context(no_reset_password=True).create({'name': 'rv', 'login': 'rv@eq.local', 'alfia_profile_ids': [Command.set([profile.id])]})
        sale = self.env['alfia.horse.sale'].with_user(self.breed_mgr).create({'horse_id': self.mare2.id, 'buyer_id': self.buyer.id, 'price': 900})
        sale.with_user(rvet).action_approve()
        sale.with_user(rvet).action_complete()
        self.assertEqual((self.mare2.status, self.mare2.owner_ids, self.mare2.on_site), ('sold', self.buyer, False))
        far = self.env['alfia.horse.sale'].with_user(self.breed_mgr).create({'horse_id': self.far_mare.id, 'buyer_id': self.buyer.id, 'price': 900})
        far.with_user(self.train_mgr).action_approve()
        with self.assertRaises(AccessError):
            far.with_user(rvet).action_complete()       # a horse outside their sites
