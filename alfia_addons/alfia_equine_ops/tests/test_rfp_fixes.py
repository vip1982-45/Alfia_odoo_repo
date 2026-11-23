"""Fixes for the RFP assessment of 23 Nov 2026 (section 2, issues requiring attention)."""
from datetime import timedelta

from odoo import Command, fields
from odoo.addons.alfia_equine.tests.test_equine import OVULATION
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .test_ops import OpsCase


@tagged('post_install', '-at_install', 'alfia_equine_ops', 'alfia_rfp_fixes')
class TestRfpFixes(OpsCase):

    def _collection(self, **extra):
        vals = {'stallion_id': self.stallion.id, 'doses': 10, 'concentration': 200, 'motility': 70, 'tank_id': self.tank.id}
        return self.env['alfia.semen.collection'].create({**vals, **extra})

    # Clinical restriction stops training
    def test_restricted_horse_trains_only_on_rest_days(self):
        today = fields.Date.today()
        clearance = self.env['alfia.clearance'].create({'horse_id': self.mare.id, 'kind': 'fitness', 'date': today})
        clearance.with_user(self.vet).action_decide('restricted', restricted_until=today + timedelta(days=1) * 7)
        Session = self.env['alfia.training.session'].with_user(self.train_mgr)
        with self.assertRaises(ValidationError):
            Session.create({'horse_id': self.mare.id, 'date': today, 'duration_min': 15})
        Session.create({'horse_id': self.mare.id, 'date': today, 'rest_day': True})
        Session.create({'horse_id': self.mare.id, 'date': today + timedelta(days=1) * 8, 'duration_min': 15})   # after it ends

    # Medicine custody: issue 10, administer 6, return 2 -> store 2, held 2, no double deduction
    def test_issue_administer_return_reconciles(self):
        product = self.env['product.product'].create({'name': 'RFP drug', 'is_storable': True, 'type': 'consu'})
        self.env['stock.quant']._update_available_quantity(product, self.barn_a, 10)
        treatment = self.env['alfia.treatment'].create({'horse_id': self.mare.id, 'drug': 'RFP drug'})
        issue = self.env['alfia.medicine.issue'].with_user(self.custodian).create(
            {'horse_id': self.mare.id, 'product_id': product.id, 'quantity': 10, 'location_id': self.barn_a.id, 'treatment_id': treatment.id})
        issue.with_user(self.custodian).action_issue()
        store = lambda: product.with_context(location=self.barn_a.id).qty_available   # noqa: E731
        self.assertEqual((store(), issue.held_qty), (0, 10))
        self.env['alfia.treatment.dose'].with_user(self.vet).create({'treatment_id': treatment.id, 'issue_id': issue.id, 'quantity': 6})
        self.assertEqual((store(), issue.administered_qty, issue.held_qty), (0, 6, 4), "administering does not touch the store again")
        with self.assertRaises(UserError):
            issue.with_user(self.custodian).action_return(5, 'too much', 'stock')     # only 4 held
        wizard = self.env['alfia.medicine.return'].with_user(self.custodian).create({'issue_id': issue.id, 'quantity': 2, 'reason': 'Course ended'})
        wizard.action_confirm()
        self.assertEqual((store(), issue.returned_qty, issue.held_qty), (2, 2, 2))
        custody = issue.custody_location_id
        self.assertEqual(product.with_context(location=custody.id).qty_available, 2, "the team holds 2 in its custody location")
        with self.assertRaises(UserError):
            self.env['alfia.treatment.dose'].with_user(self.vet).create({'treatment_id': treatment.id, 'issue_id': issue.id, 'quantity': 3})

    # Quarantine stops movements
    def test_quarantined_horse_moves_only_to_isolation(self):
        q = self.env['alfia.quarantine'].create({'horse_id': self.mare2.id, 'planned_end': OVULATION, 'start_date': OVULATION,
                                                 'reason': 'Strangles contact', 'isolation_location_id': self.barn_b.id})
        Move = self.env['alfia.horse.movement'].with_user(self.breed_mgr)
        with self.assertRaises(UserError):
            Move.create({'horse_id': self.mare2.id, 'kind': 'internal', 'location_to_id': self.tank.id})
        Move.create({'horse_id': self.mare2.id, 'kind': 'internal', 'location_to_id': self.barn_b.id}).action_done()
        q.with_user(self.vet).action_release()
        Move.create({'horse_id': self.mare2.id, 'kind': 'internal', 'location_to_id': self.barn_a.id}).action_done()

    # Event entries: withdrawal checked without rules; approval waits for the vet
    def test_entry_without_rules_still_checks_withdrawal_and_needs_the_vet(self):
        today = fields.Date.today()
        self.env['alfia.treatment'].create({'horse_id': self.mare.id, 'drug': 'Bute', 'start_date': today, 'end_date': today, 'withdrawal_days': 7})
        event = self.env['alfia.event'].create({'name': 'RFP show', 'date_start': today + timedelta(days=1)})
        entry = self.env['alfia.event.entry'].with_user(self.trainer).create({'event_id': event.id, 'horse_id': self.mare.id})
        entry.with_user(self.trainer).action_submit()
        self.assertIn('withdrawal', entry.issues)
        with self.assertRaises(UserError):
            entry.with_user(self.train_mgr).action_approve()
        clean = self.env['alfia.event.entry'].with_user(self.trainer).create({'event_id': event.id, 'horse_id': self.mare2.id})
        clean.with_user(self.trainer).action_submit()
        with self.assertRaises(UserError):
            clean.with_user(self.train_mgr).action_approve()        # no rules: the vet must confirm first
        clean.with_user(self.vet).action_vet_confirm()
        clean.with_user(self.train_mgr).action_approve()
        self.assertEqual(clean.state, 'approved')

    # Dispatch: an approver never validates, even when exempt from segregation of duties; rescheduling clears approvals
    def _approved_dispatch(self):
        col = self._collection()
        col.with_user(self.vet).action_release()
        customers = self.env.ref('stock.stock_location_customers')
        picking = self.env['stock.picking'].create({
            'picking_type_id': self.wh.out_type_id.id, 'location_id': self.tank.id, 'location_dest_id': customers.id,
            'move_ids': [Command.create({'product_id': col.product_id.id, 'product_uom_qty': 1, 'location_id': self.tank.id, 'location_dest_id': customers.id})]})
        picking.action_confirm()
        exempt = self._user('exemptappr', ['sod_exempt', 'semen_custodian'])
        for group, user in zip(picking.alfia_required_group_ids, [exempt, self.vet_mgr]):
            self.env['alfia.picking.approval'].sudo().create({'picking_id': picking.id, 'group_id': group.id, 'user_id': user.id})
        return picking, exempt

    def test_exempt_approver_cannot_validate(self):
        picking, exempt = self._approved_dispatch()
        with self.assertRaises(UserError):
            picking.with_user(exempt)._alfia_check_dual_approval()

    def test_rescheduling_clears_approvals(self):
        picking, _exempt = self._approved_dispatch()
        picking.scheduled_date = fields.Datetime.add(picking.scheduled_date, days=1)
        self.assertFalse(picking.alfia_approval_ids)

    # Farm assets: out of service until a documented release by someone else
    def test_asset_leaves_maintenance_only_by_release(self):
        asset = self.env['alfia.asset'].with_user(self.train_mgr).create({'name': 'RFP lorry', 'kind': 'vehicle'})
        asset.with_user(self.train_mgr).action_maintenance()
        with self.assertRaises(UserError):
            asset.with_user(self.train_mgr).write({'state': 'in_use'})
        with self.assertRaises(UserError):
            asset.with_user(self.train_mgr).action_assign()
        with self.assertRaises(UserError):
            asset.with_user(self.breed_mgr).action_release()              # no repair notes
        asset.with_user(self.train_mgr).repair_notes = 'Brake pads replaced and road-tested'
        with self.assertRaises(UserError):
            asset.with_user(self.train_mgr).action_release()              # took it out of service
        asset.with_user(self.breed_mgr).action_release()
        self.assertEqual((asset.state, asset.released_by_id), ('available', self.breed_mgr))

    # Uploaded clinical files keep their content and are readable by whoever can read the record
    def test_uploaded_report_is_readable_by_the_vet(self):
        data = b'Synthetic laboratory report ' * 20
        att = self.env['ir.attachment'].with_user(self.vet).create({'name': 'report.txt', 'raw': data,
                                                                       'res_model': 'alfia.diag.image', 'res_id': 0})
        image = self.env['alfia.diag.image'].with_user(self.vet).create({'horse_id': self.mare.id, 'modality': 'xray',
                                                                         'attachment_ids': [Command.set(att.ids)]})
        seen = image.with_user(self.vet_mgr).attachment_ids
        self.assertEqual(seen, att)
        self.assertEqual(seen.with_user(self.vet_mgr).raw.content, data)
        self.assertEqual(seen.with_user(self.vet_mgr).file_size, len(data))
