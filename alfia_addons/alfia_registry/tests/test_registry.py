from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


class RegistryCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        env.company.alfia_sod_enabled = True
        env.company.alfia_sod_threshold = 0
        G = 'alfia_security.group_alfia_'

        def user(login, groups):
            return env['res.users'].with_context(no_reset_password=True).create({
                'name': login, 'login': f'{login}@reg.local',
                'group_ids': [Command.link(env.ref(g).id) for g in ['base.group_user', G + 'all_locations', G + 'all_warehouses'] + groups]})
        cls.groom = user('reggroom', [G + 'stable_user'])
        cls.bm1 = user('regbm1', [G + 'breeding_manager'])
        cls.bm2 = user('regbm2', [G + 'breeding_manager'])
        cls.cust1 = user('regcust1', [G + 'semen_custodian', 'stock.group_stock_user'])
        cls.cust2 = user('regcust2', [G + 'semen_custodian', 'stock.group_stock_user'])
        cls.staff = user('regstaff', [])
        cls.staff2 = user('regstaff2', [])
        cls.horse = env['alfia.horse'].create({'name': 'Registry colt', 'sex': 'male'})


@tagged('post_install', '-at_install', 'alfia_registry')
class TestRegistrations(RegistryCase):

    def _request(self, kind='registration', user=None):
        return self.env['alfia.horse.registration'].with_user(user or self.groom).create({
            'horse_id': self.horse.id, 'kind': kind, 'due_date': fields.Date.today() + timedelta(days=30)})

    def test_request_is_approved_by_another_manager_then_completed(self):
        req = self._request()
        self.assertTrue(req.name.startswith('REG/'))
        req.action_submit()
        with self.assertRaises(AccessError):
            req.with_user(self.groom).action_approve()
        req.with_user(self.bm1).action_approve()
        self.assertTrue(req.activity_ids, "the due date is followed with an activity")
        req.with_user(self.groom).action_sent()
        with self.assertRaises(UserError):
            req.with_user(self.groom).action_done()                 # no certificate number yet
        req.with_user(self.groom).certificate_number = 'QAT-2026-0042'
        req.with_user(self.groom).action_done()
        self.assertEqual((req.state, self.horse.alfia_registration_number), ('done', 'QAT-2026-0042'))
        with self.assertRaises(UserError):
            req.with_user(self.groom).write({'horse_id': self.env['alfia.horse'].create({'name': 'Other', 'sex': 'male'}).id})

    def test_manager_cannot_approve_own_request(self):
        req = self._request(user=self.bm1)
        req.with_user(self.bm1).action_submit()
        with self.assertRaises(UserError):
            req.with_user(self.bm1).action_approve()
        req.with_user(self.bm2).action_approve()

    def test_dna_needs_sample_and_result_and_overdue_shows(self):
        req = self._request('dna')
        with self.assertRaises(UserError):
            req.action_submit()
        req.write({'sample_date': fields.Date.today(), 'due_date': fields.Date.today() - timedelta(days=1)})
        self.assertTrue(req.is_overdue)
        self.assertIn(req, self.env['alfia.horse.registration'].search([('is_overdue', '=', True)]))
        req.action_submit()
        req.with_user(self.bm1).action_approve()
        req.action_sent()
        self.assertEqual(self.horse.alfia_dna_status, 'pending')
        with self.assertRaises(UserError):
            req.action_done()
        req.dna_result = 'verified'
        req.action_done()
        self.assertEqual(self.horse.alfia_dna_status, 'verified')
        self.assertFalse(req.is_overdue)


@tagged('post_install', '-at_install', 'alfia_registry')
class TestExternalSemen(RegistryCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        wh = env['stock.warehouse'].search([('company_id', '=', env.company.id)], limit=1)
        cls.centre_partner = env['res.partner'].create({'name': 'European semen centre'})
        cls.centre = env['stock.location'].create({'name': 'At European semen centre', 'usage': 'internal',
                                                   'location_id': wh.view_location_id.id, 'alfia_external_centre_id': cls.centre_partner.id})
        cls.product = env['product.product'].create({'name': 'Registry frozen semen', 'is_storable': True, 'type': 'consu',
                                                     'tracking': 'lot', 'categ_id': env.ref('alfia_equine.category_semen').id})
        cls.lot = env['stock.lot'].create({'name': 'REG-LOT-1', 'product_id': cls.product.id})
        # 20 straws shipped to the centre with an ordinary (completed) transfer.
        move = env['stock.move'].create({'product_id': cls.product.id, 'product_uom_qty': 20, 'uom_id': cls.product.uom_id.id,
                                         'location_id': env.ref('stock.stock_location_suppliers').id, 'location_dest_id': cls.centre.id})
        move._action_confirm()
        env['stock.move.line'].create({'move_id': move.id, 'product_id': cls.product.id, 'lot_id': cls.lot.id, 'quantity': 20,
                                       'location_id': move.location_id.id, 'location_dest_id': cls.centre.id, 'picked': True})
        move._action_done()

    def _import(self, text, user=None):
        return self.env['alfia.semen.statement'].with_user(user or self.cust1).import_statement(self.centre, text.encode(), 'centre.csv')

    def _held(self):
        return sum(self.env['stock.quant'].search([('location_id', '=', self.centre.id), ('lot_id', '=', self.lot.id)]).mapped('quantity'))

    def test_statement_checks_lines_posts_usage_and_keeps_the_file(self):
        st = self._import("Date,Batch,Movement,Quantity,Reference\n"
                          "2026-11-01,REG-LOT-1,received,20,SHIP-1\n"
                          "2026-11-05,REG-LOT-1,used,3,Mare Alpha\n"
                          "2026-11-06,REG-LOT-1,used,2,Mare Beta\n"
                          "2026-11-07,NO-SUCH,used,1,\n"
                          "2026-11-30,REG-LOT-1,balance,15,\n")
        statuses = st.line_ids.sorted('row').mapped('status')
        self.assertEqual(statuses, ['ok', 'ok', 'ok', 'exception', 'exception'],
                         "unknown batch; the balance is checked against what we hold before posting (20)")
        self.assertTrue(self.env['ir.attachment'].search([('res_model', '=', st._name), ('res_id', '=', st.id)]), "source file kept")
        with self.assertRaises(UserError):
            st.with_user(self.cust1).action_post()                   # imported it
        st.with_user(self.cust2).action_post()
        self.assertEqual(self._held(), 15)
        st.invalidate_recordset(['centre_stock'])
        self.assertIn('REG-LOT-1', st.with_user(self.cust2).centre_stock)
        self.assertIn(': 15', st.with_user(self.cust2).centre_stock, "the statement shows what we hold there after posting")
        self.assertEqual(st.state, 'posted')
        with self.assertRaises(UserError):
            self._import("Date,Batch,Movement,Quantity,Reference\n"
                         "2026-11-01,REG-LOT-1,received,20,SHIP-1\n"
                         "2026-11-05,REG-LOT-1,used,3,Mare Alpha\n"
                         "2026-11-06,REG-LOT-1,used,2,Mare Beta\n"
                         "2026-11-07,NO-SUCH,used,1,\n"
                         "2026-11-30,REG-LOT-1,balance,15,\n")              # the same file again

    def test_lines_already_posted_are_duplicates_and_overuse_is_an_exception(self):
        first = self._import("Date,Batch,Movement,Quantity,Reference\n2026-11-05,REG-LOT-1,used,3,Mare Alpha\n")
        first.with_user(self.cust2).action_post()
        second = self._import("Date;Batch;Movement;Quantity;Reference\n2026-11-05;REG-LOT-1;used;3;Mare Alpha\n"
                              "2026-11-09;REG-LOT-1;used;30;Mare Gamma\n")
        self.assertEqual(second.line_ids.sorted('row').mapped('status'), ['duplicate', 'exception'],
                         "the same line in a differently separated file is still a duplicate; 30 straws exceed the 17 held")

    def test_exact_duplicate_line_is_flagged(self):
        first = self._import("Date,Batch,Movement,Quantity,Reference\n2026-11-05,REG-LOT-1,used,3,Mare Alpha\n")
        first.with_user(self.cust2).action_post()
        second = self._import("Date,Batch,Movement,Quantity,Reference\n2026-11-05,REG-LOT-1,used,3,Mare Alpha\n"
                              "2026-11-10,REG-LOT-1,used,1,Mare Delta\n")
        self.assertEqual(second.line_ids.sorted('row').mapped('status'), ['duplicate', 'ok'])
        second.with_user(self.cust2).action_post()
        self.assertEqual(self._held(), 16, "the duplicate was not posted twice")

    def test_accepting_an_exception_needs_a_reason(self):
        st = self._import("Date,Batch,Movement,Quantity,Reference\n2026-11-01,REG-LOT-1,received,25,SHIP-2\n")
        line = st.line_ids
        self.assertEqual(line.status, 'exception')
        with self.assertRaises(UserError):
            line.with_user(self.cust1).action_accept_exception()
        line.with_user(self.cust1).resolution = 'Five straws shipped by courier, transfer recorded late'
        line.with_user(self.cust1).action_accept_exception()
        self.assertEqual(line.status, 'accepted')
        with self.assertRaises(AccessError):
            self.env['alfia.semen.statement'].with_user(self.groom).import_statement(self.centre, b"Date,Batch,Movement,Quantity\n", 'x.csv')


@tagged('post_install', '-at_install', 'alfia_registry')
class TestIncidents(RegistryCase):

    def test_incident_from_report_to_approved_closure(self):
        inc = self.env['alfia.incident'].with_user(self.staff).create({
            'kind': 'horse_injury', 'severity': 'high', 'horse_id': self.horse.id,
            'description': 'Colt cut its leg on a broken fence rail', 'immediate_action': 'Vet called, area fenced off'})
        self.assertTrue(inc.name.startswith('INC/'))
        self.assertFalse(self.env['alfia.incident'].with_user(self.staff2).search([('id', '=', inc.id)]), "not other people's")
        with self.assertRaises(AccessError):
            inc.with_user(self.staff).action_investigate()
        inc.with_user(self.bm1).investigator_id = self.bm1
        inc.with_user(self.bm1).action_investigate()
        inc.with_user(self.bm1).investigation = 'Rail rotted at the base'
        with self.assertRaises(UserError):
            inc.with_user(self.bm1).action_findings_done()          # high severity needs a root cause
        inc.with_user(self.bm1).root_cause = 'No inspection schedule for paddock fences'
        self.env['alfia.incident.action'].with_user(self.bm1).create({
            'incident_id': inc.id, 'name': 'Replace rails in paddock 3', 'owner_id': self.staff2.id,
            'due_date': fields.Date.today() + timedelta(days=7)})
        inc.with_user(self.bm1).action_findings_done()
        self.assertEqual(inc.state, 'actions')
        with self.assertRaises(UserError):
            inc.with_user(self.bm1).action_request_closure()        # action still open
        action = inc.action_ids
        with self.assertRaises(UserError):
            action.with_user(self.staff2).action_done()             # no evidence
        action.with_user(self.staff2).evidence = 'Photos of new rails attached'
        action.with_user(self.staff2).action_done()
        inc.with_user(self.bm1).action_request_closure()
        inc.with_user(self.bm1).closure_notes = 'Rails replaced; monthly fence inspection added'
        with self.assertRaises(UserError):
            inc.with_user(self.bm1).action_close()                  # investigated it
        inc.with_user(self.bm2).action_close()
        self.assertEqual((inc.state, inc.closed_by_id), ('closed', self.bm2))
        with self.assertRaises(UserError):
            inc.with_user(self.bm2).write({'severity': 'low'})
