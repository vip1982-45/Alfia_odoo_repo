from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .test_registry import RegistryCase

G = 'alfia_security.group_alfia_'


@tagged('post_install', '-at_install', 'alfia_registry')
class TestSemenStraws(RegistryCase):
    """Semen development spec of 26 Nov 2026: acceptance tests T3 to T24 (owner decisions D1/D2 straws per lot, D3 waste,
    D6/D13 custodian and vet manager, D11 stallion mismatch with a reason)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env

        def user(login, groups):
            return env['res.users'].with_context(no_reset_password=True).create({
                'name': login, 'login': f'{login}@straw.local',
                'group_ids': [Command.link(env.ref(g).id) for g in ['base.group_user', G + 'all_locations', G + 'all_warehouses'] + groups]})
        cls.vet1 = user('strawvet1', [G + 'vet_user'])
        cls.vet2 = user('strawvet2', [G + 'vet_user'])
        cls.vetmgr = user('strawvetmgr', [G + 'vet_manager'])
        wh = env['stock.warehouse'].search([('company_id', '=', env.company.id)], limit=1)
        cls.tank = env['stock.location'].create({'name': 'Straw tank 1', 'usage': 'internal', 'location_id': wh.lot_stock_id.id,
                                                 'alfia_location_kind': 'tank'})
        cls.canister = env['stock.location'].create({'name': 'Canister 3', 'usage': 'internal', 'location_id': cls.tank.id,
                                                     'alfia_location_kind': 'canister', 'alfia_position': 3})
        cls.cane = env['stock.location'].create({'name': 'Cane A', 'usage': 'internal', 'location_id': cls.canister.id,
                                                 'alfia_location_kind': 'cane', 'alfia_capacity_straws': 30})
        cls.mare = env['alfia.horse'].create({'name': 'Straw mare', 'sex': 'female'})
        cls.other_stallion = env['alfia.horse'].create({'name': 'Other straw stallion', 'sex': 'male'})

    def _collection(self, stallion=None):
        col = self.env['alfia.semen.collection'].create({'stallion_id': (stallion or self.horse).id, 'concentration': 200,
                                                         'motility': 70, 'doses': 0})
        col.with_user(self.vet1).action_release()
        return col

    def _lot(self, straws=40, per_dose=4, collection=None, load=True, where=None):
        lot = self.env['alfia.semen.freeze.lot'].with_user(self.vet1).create({
            'collection_id': (collection or self._collection()).id, 'straws_produced': straws, 'straws_per_dose': per_dose,
            'load_location_id': (where or self.cane).id, 'post_thaw_test_date': '2026-11-20', 'post_thaw_motility_pct': 40})
        if load:
            lot.with_user(self.vet2).action_approve_load()
        return lot

    def _service(self, lot, stallion=None, **extra):
        return self.env['alfia.breeding.service'].create({
            'mare_id': self.mare.id, 'stallion_id': (stallion or self.horse).id, 'method': 'ai_frozen',
            'semen_lot_id': lot.stock_lot_id.id, **extra})

    def test_t3_t4_freeze_lot_is_loaded_with_its_own_ratio(self):
        collection = self._collection()
        self.assertEqual((collection.state, collection.lot_id), ('released', self.env['stock.lot']))    # no dose stock
        with self.assertRaises(Exception):
            with self.env.cr.savepoint():
                self.env['alfia.semen.freeze.lot'].create({'collection_id': collection.id, 'straws_produced': 10, 'straws_per_dose': 0})
        lot = self._lot(collection=collection, load=False)
        with self.assertRaises(UserError):
            lot.with_user(self.vet1).action_approve_load()                       # prepared by vet1: another vet loads it
        lot.with_user(self.vet2).action_approve_load()
        self.assertEqual(lot.stallion_id, self.horse)
        self.assertEqual(lot.stock_lot_id.product_id, self.env.ref('alfia_registry.product_semen_straw'))
        self.assertEqual((lot.straws_frozen, lot.straws_available, lot.doses_available, lot.straws_remainder), (40, 40, 10, 0))
        self.assertTrue(lot.balance_ok)
        self.assertEqual(self.cane.alfia_straws_here, 40)
        self.assertTrue(self.cane.alfia_over_capacity)                           # T2: capacity 30, warned, nothing lost
        self.assertIn('capacity', ' '.join(lot.message_ids.mapped('body')))

    def test_t5_selling_by_the_dose_books_the_lot_straws(self):
        lot4, lot8 = self._lot(per_dose=4), self._lot(per_dose=8)
        customer = self.env['res.partner'].create({'name': 'Straw client'})
        order = self.env['sale.order'].create({'partner_id': customer.id, 'order_line': [
            Command.create({'alfia_freeze_lot_id': lot4.id, 'alfia_doses': 2}),
            Command.create({'alfia_freeze_lot_id': lot8.id, 'alfia_doses': 2})]})
        self.assertEqual(order.order_line.mapped('product_uom_qty'), [8, 16])
        order.order_line[0].alfia_doses = 3
        self.assertEqual(order.order_line[0].product_uom_qty, 12)

    def test_t9_t10_t15_service_takes_straws_once_linked_by_field(self):
        lot = self._lot()
        service = self._service(lot)
        service.action_done()
        self.assertTrue(service.stock_move_id)
        self.assertEqual((service.straws_used, service.doses_used), (4, 1.0))
        self.assertEqual((lot.straws_used, lot.straws_frozen), (4, 36))
        service._alfia_use_dose()                                                # a second attempt takes nothing
        self.assertEqual(lot.straws_used, 4)
        with self.assertRaises(UserError):
            service.action_done()                                                # only a planned service is completed
        explicit = self._service(lot, straws_used=2)
        explicit.action_done()
        self.assertEqual((explicit.straws_used, explicit.doses_used, lot.straws_used), (2, 0.5, 6))
        self.assertTrue(lot.balance_ok)

    def test_t11_t12a_planned_service_cancelled_moves_nothing(self):
        lot = self._lot()
        service = self._service(lot)
        service.action_cancel()
        self.assertFalse(service.stock_move_id)
        self.assertEqual(lot.straws_frozen, 40)

    def test_t12b_correction_only_by_the_vet_manager_with_confirmation(self):
        lot = self._lot()
        service = self._service(lot)
        service.action_done()
        with self.assertRaises(AccessError):
            self.env['alfia.service.correction'].with_user(self.vet1).create(
                {'service_id': service.id, 'reason': 'Wrong mare', 'not_pulled': True}).action_confirm()
        wizard = self.env['alfia.service.correction'].with_user(self.vetmgr).create({'service_id': service.id, 'reason': 'Wrong mare'})
        with self.assertRaises(UserError):
            wizard.action_confirm()                                              # must confirm the straws were not pulled
        wizard.not_pulled = True
        wizard.action_confirm()
        self.assertEqual((service.state, service.corrected_by_id), ('cancelled', self.vetmgr))
        self.assertTrue(service.reversal_move_id)
        self.assertEqual((lot.straws_frozen, lot.straws_used), (40, 0))
        self.assertTrue(lot.balance_ok)

    def test_t12c_t20_thawed_straws_never_return_and_are_wasted_with_a_reason(self):
        lot = self._lot()
        service = self._service(lot)
        service.with_user(self.cust1).action_thaw()
        self.assertEqual((lot.straws_thawed, lot.straws_frozen, lot.straws_available), (4, 36, 36))
        service.action_cancel()
        self.assertEqual((lot.straws_thawed, lot.straws_frozen), (4, 36))         # not back to frozen
        with self.assertRaises(AccessError):                                     # custodian or vet manager only
            self.env['alfia.semen.waste'].with_user(self.groom).create(
                {'freeze_lot_id': lot.id, 'straws': 4, 'reason': 'Thawed, not used'}).action_confirm()
        self.env['alfia.semen.waste'].with_user(self.cust1).create(
            {'freeze_lot_id': lot.id, 'straws': 4, 'reason': 'Thawed, not used'}).action_confirm()
        self.assertEqual((lot.straws_thawed, lot.straws_wasted), (0, 4))
        self.assertTrue(lot.balance_ok)

    def test_thawed_service_uses_the_thawed_straws(self):
        lot = self._lot()
        service = self._service(lot)
        service.with_user(self.vet1).action_thaw()
        service.action_done()
        self.assertEqual((lot.straws_thawed, lot.straws_used, lot.straws_frozen), (0, 4, 36))
        wizard = self.env['alfia.service.correction'].with_user(self.vetmgr).create(
            {'service_id': service.id, 'reason': 'test', 'not_pulled': True})
        with self.assertRaises(UserError):
            wizard.action_confirm()                                              # thawed straws never go back

    def test_t13_t14_stallion_mismatch_needs_a_reason(self):
        lot = self._lot()
        matching = self._service(lot)
        matching.action_done()
        self.assertEqual(matching.state, 'done')
        other = self._service(lot, stallion=self.other_stallion)
        with self.assertRaises(UserError):
            other.action_done()
        other.mismatch_reason = 'Shared lot recorded under the full brother'
        other.action_done()
        self.assertEqual(other.state, 'done')
        self.assertIn('Shared lot', ' '.join(other.message_ids.mapped('body')))
        external = self.env['alfia.breeding.service'].create({'mare_id': self.mare.id, 'stallion_name': 'Visiting stallion',
                                                              'method': 'ai_cooled'})
        with self.assertRaises(UserError):
            external.action_done()                                               # an external stallion names its centre or owner
        external.external_stallion_owner_id = self.env['res.partner'].create({'name': 'Visiting centre'})
        external.action_done()

    def test_t16_t17_tank_count_adjusts_with_reference_and_nitrogen_log(self):
        lot = self._lot()
        audit = self.env['alfia.semen.tank.audit'].with_user(self.cust1).create({'tank_id': self.tank.id})
        audit.action_load_lines()
        line = audit.line_ids.filtered(lambda l: l.lot_id == lot.stock_lot_id)
        self.assertEqual(line.expected, 40)
        line.counted = 38
        with self.assertRaises(AccessError):
            audit.with_user(self.groom).action_confirm()
        audit.with_user(self.cust1).action_confirm()
        self.assertEqual((audit.state, lot.straws_frozen, lot.straws_adjusted), ('done', 38, -2))
        self.assertTrue(lot.balance_ok)
        self.assertEqual(self.cane.alfia_last_audit_date, audit.date)
        level = self.env['alfia.semen.tank.level'].with_user(self.cust1).create({'tank_id': self.tank.id, 'level_cm': 28, 'refilled': True,
                                                                                  'litres_added': 12})
        self.assertEqual(level.user_id, self.cust1)

    def test_t18b_available_doses_per_lot_with_remainder(self):
        lot = self._lot(straws=17, per_dose=8)
        lot._alfia_move(lot.stock_lot_id, 2, self.cane, lot._alfia_place('quarantine'), 'quarantine')
        self.assertEqual((lot.straws_quarantine, lot.straws_available, lot.doses_available, lot.straws_remainder), (2, 15, 1, 7))
        self.env.flush_all()
        report = self.env['alfia.semen.stock.report'].search([('freeze_lot_id', '=', lot.id)])
        totals = {b: sum(report.filtered(lambda r: r.bucket == b).mapped('straws')) for b in set(report.mapped('bucket'))}
        self.assertEqual((totals['frozen'], totals['quarantine'], totals['source']), (15, 2, -17))

    def test_t24_ratio_locked_after_use_then_corrected_with_a_reason(self):
        lot = self._lot()
        self._service(lot).action_done()
        self.assertTrue(lot.ratio_locked)
        with self.assertRaises(UserError):
            lot.with_user(self.vet1).straws_per_dose = 8
        with self.assertRaises(AccessError):
            self.env['alfia.semen.ratio.correction'].with_user(self.vet1).create(
                {'freeze_lot_id': lot.id, 'new_straws_per_dose': 8, 'reason': 'x'}).action_confirm()
        self.env['alfia.semen.ratio.correction'].with_user(self.vetmgr).create(
            {'freeze_lot_id': lot.id, 'new_straws_per_dose': 8, 'reason': 'Centre protocol confirmed 8 straws'}).action_confirm()
        self.assertEqual(lot.straws_per_dose, 8)
        self.assertEqual(lot.ratio_version_ids.mapped('old_value'), [4])
        self.assertEqual(lot.straws_used, 4)                                     # the earlier use keeps its straws

    def test_t22_earlier_dose_batches_keep_working(self):
        col = self.env['alfia.semen.collection'].create({'stallion_id': self.horse.id, 'concentration': 200, 'motility': 70,
                                                         'doses': 5, 'tank_id': self.tank.id})
        col.with_user(self.vet1).action_release()
        self.assertTrue(col.lot_id)
        self.assertFalse(col.straw_count_known)
        service = self.env['alfia.breeding.service'].create({'mare_id': self.mare.id, 'stallion_id': self.horse.id,
                                                             'method': 'ai_frozen', 'semen_lot_id': col.lot_id.id})
        service.action_done()
        self.assertTrue(service.stock_move_id)
        self.assertFalse(service.freeze_lot_id)
