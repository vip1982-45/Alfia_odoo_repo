"""Regression tests for the security scan of 22 Nov 2026 (findings F2-F58). Each test tries the attack the finding
describes, as the user it names, and checks that it is refused."""
from odoo import Command, fields
from odoo.addons.alfia_equine.tests.test_equine import OVULATION
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from .test_ops import OpsCase


@tagged('post_install', '-at_install', 'alfia_equine_ops', 'alfia_security_findings')
class TestSecurityFindings(OpsCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.officer = cls._user('secofficer', ['security_officer'])
        cls.accountant2 = cls._user('acct2', [])
        cls.accountant2.write({'group_ids': [Command.link(cls.env.ref(x).id) for x in ('account.group_account_invoice', 'alfia_security.group_alfia_all_journals')]})

    def _collection(self, **extra):
        vals = {'stallion_id': self.stallion.id, 'doses': 10, 'concentration': 200, 'motility': 70, 'tank_id': self.tank.id}
        return self.env['alfia.semen.collection'].create({**vals, **extra})

    def _scoped(self, login, groups, locations):
        profile = self.env['alfia.access.profile'].create({'name': f'F {login}', 'location_ids': [Command.set([l.id for l in locations])],
            'group_ids': [Command.set([self.env.ref(f'alfia_security.group_alfia_{g}').id for g in groups])]})
        return self.env['res.users'].with_context(no_reset_password=True).create(
            {'name': login, 'login': f'{login}@f.local', 'alfia_profile_ids': [Command.set([profile.id])]})

    # --- F2: the client-sent 'alfia_sync' context no longer switches off the escalation guard
    def test_f2_sync_context_does_not_bypass_the_user_guard(self):
        system = self.env.ref('base.group_system')
        with self.assertRaises(AccessError):
            self.officer.with_user(self.officer).with_context(alfia_sync=True).write({'group_ids': [Command.link(system.id)]})
        with self.assertRaises(AccessError):
            self.env['alfia.access.profile'].with_user(self.officer).with_context(alfia_sync=True).search([], limit=1).write(
                {'user_ids': [Command.link(self.officer.id)]})
        self.assertFalse(self.officer.has_group('base.group_system'))

    # --- F3: an administrator's contact (password-reset email) is changed by administrators only
    def test_f3_officer_cannot_change_an_administrators_email(self):
        admin = self.env.ref('base.user_admin')
        with self.assertRaises(AccessError):
            admin.partner_id.with_user(self.officer).write({'email': 'attacker@example.com'})

    # --- F4 / F45: approvals are cleared by any change, whatever context the client sends
    def _approved_dispatch(self):
        col = self._collection()
        col.with_user(self.vet).action_release()
        customers = self.env.ref('stock.stock_location_customers')
        picking = self.env['stock.picking'].create({
            'picking_type_id': self.wh.out_type_id.id, 'location_id': self.tank.id, 'location_dest_id': customers.id,
            'move_ids': [Command.create({'product_id': col.product_id.id, 'product_uom_qty': 1, 'location_id': self.tank.id, 'location_dest_id': customers.id})]})
        picking.action_confirm()
        users = [self.vet, self.vet_mgr]
        for group, user in zip(picking.alfia_required_group_ids, users):
            self.env['alfia.picking.approval'].sudo().create({'picking_id': picking.id, 'group_id': group.id, 'user_id': user.id})
        self.assertTrue(picking.alfia_approval_ids)
        return picking, col

    def test_f4_keep_approvals_context_is_ignored(self):
        picking, _col = self._approved_dispatch()
        picking.move_ids.with_context(alfia_keep_approvals=True).write({'product_uom_qty': 3})
        self.assertFalse(picking.alfia_approval_ids)

    def test_f45_a_line_added_after_approval_clears_the_approvals(self):
        picking, col = self._approved_dispatch()
        self.env['stock.move'].create({'product_id': col.product_id.id, 'product_uom_qty': 1, 'picking_id': picking.id,
                                       'location_id': self.tank.id, 'location_dest_id': picking.location_dest_id.id})
        self.assertFalse(picking.alfia_approval_ids)

    # --- F35: a horse's cost centre is the system's
    def test_f35_horse_cost_centre_cannot_be_repointed(self):
        other = self.env['account.analytic.account'].create({'name': 'Someone else', 'plan_id': self.env['alfia.horse']._alfia_horse_plan().id})
        with self.assertRaises(UserError):
            self.mare.with_user(self.breed_mgr).write({'analytic_account_id': other.id})

    # --- F36: a planned movement cannot be rewritten to pull a horse from another site
    def test_f36_movement_write_keeps_the_site_check(self):
        groom = self._scoped('fgroom', ['stable_user'], [self.barn_a])
        move = self.env['alfia.horse.movement'].with_user(groom).create({'horse_id': self.mare2.id, 'kind': 'internal', 'location_to_id': self.barn_a.id})
        with self.assertRaises(AccessError):
            move.with_user(groom).write({'horse_id': self.far_mare.id})
        with self.assertRaises(UserError):
            move.with_user(groom).write({'state': 'done'})

    # --- F37: fitness is the vet's decision, eligibility the manager's, never a direct write
    def test_f37_clearance_state_is_not_written_directly(self):
        clearance = self.env['alfia.clearance'].with_user(self.train_mgr).create({'horse_id': self.mare.id, 'kind': 'fitness'})
        with self.assertRaises(UserError):
            clearance.with_user(self.train_mgr).write({'state': 'cleared'})
        with self.assertRaises(AccessError):
            self.env['alfia.clearance'].with_user(self.train_mgr).create({'horse_id': self.mare.id, 'kind': 'fitness', 'state': 'cleared'})
        clearance.with_user(self.vet).action_clear()
        self.assertEqual((clearance.state, clearance.decided_by_id), ('cleared', self.vet))

    # --- F38 / F48 / F49 / F50: vet-only and approval outcomes are set only by their buttons
    def test_f38_reproductive_outcomes_need_their_buttons(self):
        self._approve_rule()
        ov = self.env['alfia.ovulation'].with_user(self.breeder).create({'mare_id': self.mare.id, 'date': OVULATION})
        with self.assertRaises(UserError):
            ov.with_user(self.breeder).write({'state': 'confirmed'})
        embryo = self.env['alfia.embryo'].with_user(self.breeder).create({'donor_id': self.mare.id})
        with self.assertRaises(UserError):
            embryo.with_user(self.breeder).write({'state': 'discarded'})
        col = self._collection()
        with self.assertRaises(UserError):
            col.with_user(self.breeder).write({'state': 'released'})
        with self.assertRaises(UserError):
            self.env['alfia.pregnancy'].with_user(self.breeder).create({'mare_id': self.mare.id, 'ovulation_date': OVULATION,
                                                                       'rule_id': self.rule.id, 'state': 'pregnant'})

    def test_f48_only_semen_is_released_into_stock(self):
        col = self._collection(product_id=self.service.id)
        with self.assertRaises(UserError):
            col.with_user(self.vet).action_release()
        self.assertEqual(col.state, 'draft')

    def test_f49_birth_needs_the_vet(self):
        preg = self._pregnancy()
        foaling = self.env['alfia.foaling'].with_user(self.stable).create({'pregnancy_id': preg.id, 'foal_sex': 'colt', 'foal_name': 'F49'})
        with self.assertRaises(UserError):
            foaling.with_user(self.stable).write({'state': 'vet_confirmed'})
        with self.assertRaises(UserError):   # a context key sent by the client is not the workflow
            foaling.with_user(self.stable).with_context(alfia_breeding_flow=True).write({'state': 'vet_confirmed'})
        preg2 = self._pregnancy(mare=self.mare2)
        with self.assertRaises(UserError):
            preg2.with_user(self.vet_mgr).with_context(alfia_breeding_flow=True).write({'state': 'foaled'})

    def test_f50_rule_set_is_approved_only_by_the_button(self):
        draft = self.env.ref('alfia_equine.rule_default').copy({'name': 'F50', 'state': 'draft'})
        with self.assertRaises(UserError):
            draft.with_user(self.vet_mgr).write({'state': 'approved'})

    # --- F39: entries are approved only after the checks
    def test_f39_entry_is_not_approved_by_writing(self):
        event = self.env['alfia.event'].create({'name': 'F39 show', 'date_start': fields.Date.today(), 'date_end': fields.Date.today()})
        entry = self.env['alfia.event.entry'].with_user(self.trainer).create({'event_id': event.id, 'horse_id': self.mare.id})
        with self.assertRaises(UserError):
            entry.with_user(self.trainer).write({'state': 'approved'})
        with self.assertRaises(UserError):
            entry.with_user(self.vet).write({'vet_confirmed': True})

    # --- F40: an issued medicine record cannot be inflated before a return
    def test_f40_medicine_return_puts_back_only_what_was_issued(self):
        product = self.env['product.product'].create({'name': 'F40 drug', 'is_storable': True, 'type': 'consu'})
        self.env['stock.quant']._update_available_quantity(product, self.barn_a, 5)
        issue = self.env['alfia.medicine.issue'].with_user(self.custodian).create(
            {'horse_id': self.mare.id, 'product_id': product.id, 'quantity': 1, 'location_id': self.barn_a.id})
        issue.with_user(self.custodian).action_issue()
        with self.assertRaises(UserError):
            issue.with_user(self.custodian).write({'quantity': 500})
        with self.assertRaises(UserError):
            issue.with_user(self.custodian).write({'returned_qty': -100})

    # --- F41: maker and checker of a horse sale
    def test_f41_sale_cannot_be_self_approved_by_writing(self):
        sale = self.env['alfia.horse.sale'].with_user(self.breed_mgr).create({'horse_id': self.mare2.id, 'buyer_id': self.buyer.id, 'price': 1000})
        with self.assertRaises(UserError):
            sale.with_user(self.breed_mgr).write({'state': 'approved'})
        with self.assertRaises(UserError):
            self.env['alfia.horse.sale'].with_user(self.breed_mgr).create(
                {'horse_id': self.mare2.id, 'buyer_id': self.buyer.id, 'price': 1000, 'created_by_id': self.train_mgr.id})

    # --- F42 / F57 / F58: recurring charges
    def test_f42_plan_is_not_self_approved(self):
        vals = {'name': 'F42', 'target_kind': 'horse', 'horse_id': self.mare.id, 'product_id': self.service.id}
        with self.assertRaises(UserError):
            self.env['alfia.charge.plan'].with_user(self.breed_mgr).create({**vals, 'state': 'approved'})
        plan = self.env['alfia.charge.plan'].with_user(self.breed_mgr).create(vals)
        with self.assertRaises(UserError):
            plan.with_user(self.breed_mgr).write({'state': 'approved', 'approved_by_id': self.train_mgr.id})

    def test_f57_group_change_sends_its_plans_back_for_approval(self):
        group = self.env['alfia.horse.group'].create({'name': 'F57', 'member_ids': [Command.set([self.mare.id])]})
        plan = self.env['alfia.charge.plan'].create({'name': 'F57', 'target_kind': 'group', 'group_id': group.id, 'product_id': self.service.id})
        plan.action_approve()
        self.assertEqual(plan.state, 'approved')
        group.write({'member_ids': [Command.link(self.mare2.id)]})
        self.assertEqual(plan.state, 'draft')

    def test_f58_group_ages_are_bounded(self):
        with self.assertRaises(ValidationError):
            self.env['alfia.horse.group'].create({'name': 'F58', 'min_age_years': 1e9})

    # --- F43 / F51: charges
    def test_f43_charge_is_not_approved_or_reopened_by_writing(self):
        charge = self.env['alfia.charge'].with_user(self.breed_mgr).create({'horse_id': self.mare.id, 'product_id': self.service.id})
        with self.assertRaises(UserError):
            charge.with_user(self.breed_mgr).write({'state': 'approved'})
        charge.with_user(self.train_mgr).action_approve()
        charge.with_user(self.accountant).action_invoice()
        with self.assertRaises(UserError):
            charge.with_user(self.accountant).write({'state': 'approved', 'move_id': False})

    def test_f51_rewriting_a_colleagues_draft_makes_it_yours(self):
        charge = self.env['alfia.charge'].with_user(self.breed_mgr).create({'horse_id': self.mare.id, 'product_id': self.service.id})
        charge.with_user(self.train_mgr).write({'price_unit': 9999})
        with self.assertRaises(UserError):
            charge.with_user(self.train_mgr).action_approve()

    # --- F44: contracts
    def test_f44_contract_documents_and_status_are_the_systems(self):
        contract = self.env['alfia.breeding.contract'].with_user(self.breed_mgr).create({
            'client_id': self.client.id, 'stallion_id': self.stallion.id, 'season': '2026', 'product_id': self.service.id, 'fee': 10})
        with self.assertRaises(UserError):
            contract.with_user(self.breed_mgr).write({'state': 'confirmed'})
        contract.with_user(self.accountant).action_confirm()
        contract.with_user(self.accountant2).action_invoice()
        with self.assertRaises(UserError):
            contract.with_user(self.accountant).write({'invoice_id': False, 'state': 'confirmed'})

    # --- F53: controlled-stock restrictions cannot be lifted by moving products or categories
    def test_f53_moving_out_of_a_controlled_category_needs_an_officer(self):
        semen = self.env.ref('alfia_equine.product_frozen_semen')
        manager = self._user('prodmgr', [])
        manager.write({'group_ids': [Command.link(self.env.ref('product.group_product_manager').id)]})
        plain = self.env['product.category'].create({'name': 'F53 plain'})
        with self.assertRaises(AccessError):
            semen.product_tmpl_id.with_user(manager).write({'categ_id': plain.id})
        child = self.env['product.category'].create({'name': 'F53 child', 'parent_id': self.env.ref('alfia_equine.category_semen').id})
        with self.assertRaises(AccessError):
            child.with_user(manager).write({'parent_id': plain.id})      # would drop the restriction it inherits

    # --- F56: system work-item identities
    def test_f56_task_keys_are_set_by_the_system(self):
        with self.assertRaises(UserError):
            self.env['alfia.task'].with_user(self.vet).create({'name': 'squat', 'horse_id': self.mare.id, 'due_date': OVULATION,
                                                              'key': f'preg:999999:check:14'})
