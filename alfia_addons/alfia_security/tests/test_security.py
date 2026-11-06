from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install', 'alfia_security')
class AlfiaSecurityCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.wh = cls.env['stock.warehouse'].search([('company_id', '=', cls.company.id)], limit=1)
        Location = cls.env['stock.location']
        parent = cls.wh.view_location_id
        cls.barn_a = Location.create({'name': 'Barn A', 'location_id': parent.id, 'usage': 'internal'})
        cls.barn_b = Location.create({'name': 'Barn B', 'location_id': parent.id, 'usage': 'internal'})
        cls.tanks = Location.create({'name': 'Semen tanks', 'location_id': parent.id, 'usage': 'internal'})
        cls.tank_1 = Location.create({'name': 'Tank 1', 'location_id': cls.tanks.id, 'usage': 'internal'})

        Categ = cls.env['product.category']
        cls.categ_feed = Categ.create({'name': 'Feed test'})
        cls.g_semen = cls.env.ref('alfia_security.group_alfia_semen_custodian')
        cls.g_adm_appr = cls.env.ref('alfia_security.group_alfia_dispatch_admin_approver')
        cls.g_vet_appr = cls.env.ref('alfia_security.group_alfia_dispatch_vet_approver')
        cls.categ_semen = Categ.create({
            'name': 'Semen test', 'alfia_access_group_id': cls.g_semen.id,
            'alfia_dual_approval': True, 'alfia_approver_group_ids': [Command.set([cls.g_adm_appr.id, cls.g_vet_appr.id])]})
        cls.categ_semen_frozen = Categ.create({'name': 'Frozen semen test', 'parent_id': cls.categ_semen.id})

        Product = cls.env['product.product']
        vals = {'type': 'consu', 'is_storable': True}
        cls.hay = Product.create({**vals, 'name': 'Hay test', 'categ_id': cls.categ_feed.id})
        cls.semen = Product.create({**vals, 'name': 'Semen straw test', 'categ_id': cls.categ_semen_frozen.id})
        Quant = cls.env['stock.quant']
        Quant._update_available_quantity(cls.hay, cls.barn_a, 10)
        Quant._update_available_quantity(cls.hay, cls.barn_b, 20)
        Quant._update_available_quantity(cls.semen, cls.tank_1, 5)

        g = cls.env.ref
        cls.stock_user = g('stock.group_stock_user')
        Profile = cls.env['alfia.access.profile']
        cls.p_barn_a = Profile.create({'name': 'T Barn A', 'group_ids': [Command.set([cls.stock_user.id])], 'location_ids': [Command.set([cls.barn_a.id])]})
        cls.p_barn_b = Profile.create({'name': 'T Barn B', 'group_ids': [Command.set([cls.stock_user.id])], 'location_ids': [Command.set([cls.barn_b.id])]})
        cls.p_custodian = Profile.create({'name': 'T Custodian', 'group_ids': [Command.set([cls.g_semen.id])], 'location_ids': [Command.set([cls.tanks.id])]})
        cls.p_adm_appr = Profile.create({'name': 'T Admin approver', 'group_ids': [Command.set([cls.g_adm_appr.id])], 'location_ids': [Command.set([cls.tanks.id])]})
        cls.p_vet_appr = Profile.create({'name': 'T Vet approver', 'group_ids': [Command.set([cls.g_vet_appr.id])], 'location_ids': [Command.set([cls.tanks.id])]})
        cls.p_global = Profile.create({'name': 'T Global stock', 'group_ids': [Command.set([cls.stock_user.id, g('alfia_security.group_alfia_all_locations').id])]})

        cls.officer = cls._make_user('officer', groups=['alfia_security.group_alfia_security_officer'])
        cls.sysadmin = cls._make_user('sysadmin', groups=['base.group_system'])

    @classmethod
    def _make_user(cls, login, profiles=(), groups=()):
        user = cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': login, 'login': f'{login}@test.local', 'email': f'{login}@test.local',
            'group_ids': [Command.link(cls.env.ref(x).id) for x in groups] or [Command.link(cls.env.ref('base.group_user').id)],
            'alfia_profile_ids': [Command.set([p.id for p in profiles])],
        })
        return user

    def _pickings(self, user):
        return self.env['stock.picking'].with_user(user).search([('id', 'in', self.created_pickings.ids)])


@tagged('post_install', '-at_install', 'alfia_security')
class TestScope(AlfiaSecurityCase):

    def test_site_scope_limits_what_each_user_sees(self):
        a = self._make_user('a', [self.p_barn_a])
        b = self._make_user('b', [self.p_barn_b])
        g = self._make_user('g', [self.p_global])
        nobody = self._make_user('nobody', [], groups=['stock.group_stock_user'])
        Quant = self.env['stock.quant']
        def qty(user, product):
            return sum(Quant.with_user(user).search([('product_id', '=', product.id)]).mapped('quantity'))
        self.assertEqual(qty(a, self.hay), 10, "Barn A staff see only Barn A hay")
        self.assertEqual(qty(b, self.hay), 20)
        self.assertEqual(qty(g, self.hay), 30, "a global profile sees every site")
        self.assertEqual(qty(nobody, self.hay), 0, "a stock user with no scope sees nothing (default deny)")
        self.assertEqual(qty(self.sysadmin, self.hay), 30, "system administrators are not scope-limited")

    def test_out_of_scope_records_cannot_be_read_or_written(self):
        a = self._make_user('a', [self.p_barn_a])
        quant_b = self.env['stock.quant'].search([('product_id', '=', self.hay.id), ('location_id', '=', self.barn_b.id)])
        with self.assertRaises(AccessError):
            quant_b.with_user(a).read(['quantity'])
        with self.assertRaises(AccessError):
            quant_b.with_user(a).write({'inventory_quantity': 99})

    def test_moves_and_transfers_follow_the_scope(self):
        a = self._make_user('a', [self.p_barn_a])
        b = self._make_user('b', [self.p_barn_b])
        Picking = self.env['stock.picking']
        def internal(src):
            return Picking.create({
                'picking_type_id': self.wh.int_type_id.id, 'location_id': src.id, 'location_dest_id': src.id,
                'move_ids': [Command.create({'product_id': self.hay.id, 'product_uom_qty': 1, 'location_id': src.id, 'location_dest_id': src.id})],
            })
        pa, pb = internal(self.barn_a), internal(self.barn_b)
        self.assertEqual(Picking.with_user(a).search([('id', 'in', (pa | pb).ids)]), pa)
        self.assertEqual(Picking.with_user(b).search([('id', 'in', (pa | pb).ids)]), pb)
        Move = self.env['stock.move']
        self.assertEqual(Move.with_user(a).search([('picking_id', 'in', (pa | pb).ids)]).picking_id, pa)

    def test_other_sites_locations_are_visible_by_name_but_not_changeable(self):
        # Names stay readable (Odoo's own purchasing and transfer flows read them); the stock inside is what is protected.
        manager_a = self._make_user('mgra', [self.p_barn_a], groups=['stock.group_stock_manager'])
        self.assertIn(self.barn_b, self.env['stock.location'].with_user(manager_a).search([]))
        self.barn_a.with_user(manager_a).write({'name': 'Barn A (renamed)'})
        with self.assertRaises(AccessError):
            self.barn_b.with_user(manager_a).write({'name': 'Not mine'})
        self.assertFalse(self.env['stock.quant'].with_user(manager_a).search([('location_id', '=', self.barn_b.id)]))

    def test_a_warehouse_scoped_role_reaches_the_whole_warehouse(self):
        profile = self.env['alfia.access.profile'].create({
            'name': 'T Site stock', 'group_ids': [Command.set([self.stock_user.id])], 'warehouse_ids': [Command.set([self.wh.id])]})
        site = self._make_user('site', [profile])
        qty = sum(self.env['stock.quant'].with_user(site).search([('product_id', '=', self.hay.id)]).mapped('quantity'))
        self.assertEqual(qty, 30, "both barns belong to the warehouse")
        other_wh = self.env['stock.warehouse'].create({'name': 'Stud C', 'code': 'STC'})
        elsewhere = self.env['stock.location'].create({'name': 'Elsewhere', 'location_id': other_wh.view_location_id.id, 'usage': 'internal'})
        self.env['stock.quant']._update_available_quantity(self.hay, elsewhere, 3)
        self.assertEqual(self.env['stock.quant'].with_user(site).search_count([('location_id', '=', elsewhere.id)]), 0)

    def test_scope_follows_profile_changes(self):
        a = self._make_user('a', [self.p_barn_a])
        self.assertEqual(a.alfia_location_ids, self.barn_a)
        self.p_barn_a.location_ids = self.barn_a | self.barn_b
        self.assertEqual(a.alfia_location_ids, self.barn_a | self.barn_b)
        sums = sum(self.env['stock.quant'].with_user(a).search([('product_id', '=', self.hay.id)]).mapped('quantity'))
        self.assertEqual(sums, 30)
        self.p_barn_a.active = False
        self.assertFalse(a.alfia_location_ids, "an archived profile stops granting scope")


@tagged('post_install', '-at_install', 'alfia_security')
class TestControlledStock(AlfiaSecurityCase):

    def test_restricted_category_is_invisible_without_the_group_and_inherited_by_subcategories(self):
        # Same site scope for both users: only the function differs.
        site_only = self.env['alfia.access.profile'].create({'name': 'T Tanks site', 'group_ids': [Command.set([self.stock_user.id])], 'location_ids': [Command.set([self.tanks.id])]})
        plain = self._make_user('plain', [site_only])
        custodian = self._make_user('custodian', [self.p_custodian])
        Quant = self.env['stock.quant']
        self.assertEqual(Quant.with_user(plain).search_count([('product_id', '=', self.semen.id)]), 0, "no group, no semen stock")
        self.assertEqual(Quant.with_user(custodian).search_count([('product_id', '=', self.semen.id)]), 1)
        self.assertEqual(self.categ_semen_frozen.alfia_effective_group_id, self.g_semen, "sub-categories inherit the restriction")
        self.assertEqual(self.env['stock.move'].with_user(plain).search_count([('product_id', '=', self.semen.id)]), 0)

    def test_products_without_a_category_are_not_hidden(self):
        # Regression: a path-based rule made category-less products vanish for everybody.
        loose = self.env['product.product'].create({'name': 'Loose item', 'type': 'consu', 'is_storable': True, 'categ_id': False})
        self.env['stock.quant']._update_available_quantity(loose, self.barn_a, 4)
        a = self._make_user('a', [self.p_barn_a])
        self.assertEqual(self.env['stock.quant'].with_user(a).search_count([('product_id', '=', loose.id)]), 1)
        self.assertEqual(self.env['stock.quant'].with_user(self.sysadmin).search_count([('product_id', '=', loose.id)]), 1)

    def test_only_officers_and_admins_change_restrictions(self):
        plain = self._make_user('plain', [self.p_barn_a], groups=['product.group_product_manager'])
        with self.assertRaises(AccessError):
            self.categ_feed.with_user(plain).write({'alfia_access_group_id': self.g_semen.id})   # may edit categories, not restrictions
        with self.assertRaises(AccessError):
            self.categ_feed.with_user(self.officer).write({'name': 'renamed'})                   # officers edit restrictions only
        self.categ_feed.with_user(self.officer).write({'alfia_access_group_id': self.g_semen.id})
        self.assertEqual(self.categ_feed.alfia_effective_group_id, self.g_semen)
        self.assertTrue(self.env['alfia.security.audit'].search([('action', '=', 'category.write'), ('target_ref', '=', 'Feed test')]))

    def _dispatch(self):
        picking = self.env['stock.picking'].create({
            'picking_type_id': self.wh.out_type_id.id, 'location_id': self.tank_1.id,
            'location_dest_id': self.env.ref('stock.stock_location_customers').id,
            'move_ids': [Command.create({'product_id': self.semen.id, 'product_uom_qty': 2, 'location_id': self.tank_1.id,
                                         'location_dest_id': self.env.ref('stock.stock_location_customers').id})],
        })
        picking.action_confirm()
        picking.action_assign()
        picking.move_ids.quantity = 2
        return picking

    def test_dual_approval_before_dispatch(self):
        custodian = self._make_user('custodian', [self.p_custodian])
        admin_appr = self._make_user('admappr', [self.p_adm_appr])
        vet_appr = self._make_user('vetappr', [self.p_vet_appr])
        picking = self._dispatch()
        self.assertEqual(picking.alfia_required_group_ids, self.g_adm_appr | self.g_vet_appr)
        with self.assertRaises(UserError):
            picking.with_user(custodian).button_validate()
        with self.assertRaises(AccessError):
            picking.with_user(custodian).action_alfia_approve()   # a custodian is not an approver
        picking.with_user(admin_appr).action_alfia_approve()
        with self.assertRaises(UserError):
            picking.with_user(custodian).button_validate()          # one approval is not enough
        with self.assertRaises(AccessError):
            picking.with_user(admin_appr).action_alfia_approve()    # the same function cannot approve twice
        picking.with_user(vet_appr).action_alfia_approve()
        self.assertFalse(picking.alfia_missing_group_ids)
        picking.with_user(custodian).button_validate()
        self.assertEqual(picking.state, 'done')
        self.assertEqual(len(picking.alfia_approval_ids), 2, "both approvals are kept as evidence")
        self.assertEqual(set(picking.alfia_approval_ids.user_id), {admin_appr, vet_appr})

    def test_one_person_cannot_provide_both_approvals(self):
        both = self._make_user('both', [self.p_adm_appr, self.p_vet_appr])
        picking = self._dispatch()
        picking.with_user(both).action_alfia_approve()
        with self.assertRaises(UserError):
            picking.with_user(both).action_alfia_approve()
        self.assertEqual(len(picking.alfia_approval_ids), 1)

    def test_an_approver_cannot_also_dispatch(self):
        approver_custodian = self._make_user('apprcust', [self.p_custodian, self.p_adm_appr])
        vet_appr = self._make_user('vetappr', [self.p_vet_appr])
        picking = self._dispatch()
        picking.with_user(approver_custodian).action_alfia_approve()
        picking.with_user(vet_appr).action_alfia_approve()
        with self.assertRaises(UserError):
            picking.with_user(approver_custodian).button_validate()

    def test_changing_the_transfer_clears_approvals(self):
        admin_appr = self._make_user('admappr', [self.p_adm_appr])
        picking = self._dispatch()
        picking.with_user(admin_appr).action_alfia_approve()
        self.assertEqual(len(picking.alfia_approval_ids), 1)
        picking.move_ids.quantity = 1
        self.assertFalse(picking.alfia_approval_ids, "approvals do not survive a change to the transfer")
        self.assertTrue(self.env['alfia.security.audit'].search([('action', '=', 'dispatch.approvals_reset')]))

    def test_approvers_see_the_transfer_but_cannot_change_semen_stock(self):
        admin_appr = self._make_user('admappr', [self.p_adm_appr])
        picking = self._dispatch()
        self.assertEqual(self.env['stock.picking'].with_user(admin_appr).search([('id', '=', picking.id)]), picking)
        self.assertTrue(picking.with_user(admin_appr).move_ids, "the approver can see what they approve")
        with self.assertRaises(AccessError):
            picking.move_ids.with_user(admin_appr).write({'quantity': 1})
        self.assertEqual(self.env['stock.quant'].with_user(admin_appr).search_count([('product_id', '=', self.semen.id)]), 0)

    def test_inventory_adjustments_need_an_approver(self):
        counter = self._make_user('counter', [self.p_global])
        controller = self._make_user('controller', [self.p_global], groups=['alfia_security.group_alfia_stock_adjust_approver'])
        quant = self.env['stock.quant'].search([('product_id', '=', self.hay.id), ('location_id', '=', self.barn_a.id)])
        quant.inventory_quantity = 7
        with self.assertRaises(AccessError):
            quant.with_user(counter).action_apply_inventory()
        quant.with_user(controller).action_apply_inventory()
        self.assertEqual(quant.quantity, 7)


@tagged('post_install', '-at_install', 'alfia_security')
class TestSegregationOfDuties(AlfiaSecurityCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.supplier = cls.env['res.partner'].create({'name': 'Feed supplier'})
        cls.buyer = cls._make_user('buyer', groups=['purchase.group_purchase_user', 'alfia_security.group_alfia_all_warehouses', 'alfia_security.group_alfia_all_locations'])
        cls.approver = cls._make_user('approver', groups=['purchase.group_purchase_manager', 'alfia_security.group_alfia_all_warehouses', 'alfia_security.group_alfia_all_locations'])
        cls.company.po_double_validation = 'one_step'

    def _po(self, user):
        po = self.env['purchase.order'].with_user(user).create({
            'partner_id': self.supplier.id,
            'order_line': [Command.create({'product_id': self.hay.id, 'product_qty': 1, 'price_unit': 100})],
        })
        return po

    def test_buyer_cannot_approve_their_own_order(self):
        po = self._po(self.buyer)
        po.with_user(self.buyer).button_confirm()
        self.assertEqual(po.state, 'to approve', "a buyer's own order goes to someone else")
        with self.assertRaises(UserError):
            po.with_user(self.buyer).button_approve()
        po.with_user(self.approver).button_approve()
        self.assertEqual(po.state, 'purchase')

    def test_a_manager_cannot_approve_their_own_order_either(self):
        po = self._po(self.approver)
        po.with_user(self.approver).button_confirm()
        self.assertEqual(po.state, 'to approve')
        with self.assertRaises(UserError):
            po.with_user(self.approver).button_approve()

    def test_switching_the_control_off_or_using_the_exemption(self):
        self.company.alfia_sod_enabled = False
        po = self._po(self.buyer)
        po.with_user(self.buyer).button_confirm()
        self.assertEqual(po.state, 'purchase', "with the control off, one-step approval works as in standard Odoo")
        self.company.alfia_sod_enabled = True
        exempt = self._make_user('exempt', groups=['purchase.group_purchase_user', 'alfia_security.group_alfia_sod_exempt', 'alfia_security.group_alfia_all_warehouses', 'alfia_security.group_alfia_all_locations'])
        po2 = self._po(exempt)
        po2.with_user(exempt).button_confirm()
        self.assertEqual(po2.state, 'purchase')

    def test_threshold_keeps_small_orders_simple(self):
        self.company.alfia_sod_threshold = 1000
        po = self._po(self.buyer)   # 100 < 1000
        po.with_user(self.buyer).button_confirm()
        self.assertEqual(po.state, 'purchase')

    def test_changing_the_control_is_audited(self):
        self.company.alfia_sod_enabled = False
        self.assertTrue(self.env['alfia.security.audit'].search([('action', '=', 'company.sod')]))

    def test_vendor_bill_cannot_be_posted_by_its_creator(self):
        clerk = self._make_user('clerk', groups=['account.group_account_invoice', 'alfia_security.group_alfia_all_journals'])
        poster = self._make_user('poster', groups=['account.group_account_user', 'alfia_security.group_alfia_all_journals'])
        journal = self.env['account.journal'].search([('type', '=', 'purchase'), ('company_id', '=', self.company.id)], limit=1)
        bill = self.env['account.move'].with_user(clerk).create({
            'move_type': 'in_invoice', 'partner_id': self.supplier.id, 'journal_id': journal.id,
            'invoice_date': fields.Date.today(),
            'invoice_line_ids': [Command.create({'name': 'Hay', 'quantity': 1, 'price_unit': 100})],
        })
        with self.assertRaises(UserError):
            bill.with_user(clerk).action_post()
        bill.with_user(poster).action_post()
        self.assertEqual(bill.state, 'posted')
        # customer invoices and system automation are not affected
        invoice = self.env['account.move'].with_user(clerk).create({
            'move_type': 'out_invoice', 'partner_id': self.supplier.id, 'invoice_date': fields.Date.today(),
            'invoice_line_ids': [Command.create({'name': 'Service', 'quantity': 1, 'price_unit': 50})]})
        invoice.with_user(clerk).action_post()
        self.assertEqual(invoice.state, 'posted')
        bill2 = self.env['account.move'].with_user(clerk).create({
            'move_type': 'in_invoice', 'partner_id': self.supplier.id, 'journal_id': journal.id, 'invoice_date': fields.Date.today(),
            'invoice_line_ids': [Command.create({'name': 'Hay', 'quantity': 1, 'price_unit': 10})]})
        bill2.sudo().action_post()
        self.assertEqual(bill2.state, 'posted', "system automation (sudo) is not blocked")


@tagged('post_install', '-at_install', 'alfia_security')
class TestJournalScope(AlfiaSecurityCase):

    def test_journal_scope_separates_accounting_functions(self):
        Journal = self.env['account.journal']
        j1 = Journal.create({'name': 'Breeding income', 'type': 'sale', 'code': 'BRI'})
        j2 = Journal.create({'name': 'Training income', 'type': 'sale', 'code': 'TRI'})
        partner = self.env['res.partner'].create({'name': 'Client'})
        def invoice(journal):
            return self.env['account.move'].create({
                'move_type': 'out_invoice', 'journal_id': journal.id, 'partner_id': partner.id, 'invoice_date': fields.Date.today(),
                'invoice_line_ids': [Command.create({'name': 'x', 'quantity': 1, 'price_unit': 10})]})
        m1, m2 = invoice(j1), invoice(j2)
        profile = self.env['alfia.access.profile'].create({
            'name': 'T Breeding AR', 'group_ids': [Command.set([self.env.ref('account.group_account_invoice').id])], 'journal_ids': [Command.set([j1.id])]})
        clerk = self._make_user('arclerk', [profile])
        Move = self.env['account.move'].with_user(clerk)
        self.assertEqual(Move.search([('id', 'in', (m1 | m2).ids)]), m1)
        with self.assertRaises(AccessError):
            m2.with_user(clerk).read(['name'])
        controller = self._make_user('fin', groups=['account.group_account_manager', 'alfia_security.group_alfia_security_auditor'])
        self.assertEqual(self.env['account.move'].with_user(controller).search_count([('id', 'in', (m1 | m2).ids)]), 2)

    def test_warehouse_scope_separates_purchasing(self):
        wh2 = self.env['stock.warehouse'].create({'name': 'Stud B', 'code': 'STB'})
        partner = self.env['res.partner'].create({'name': 'Supplier'})
        def po(wh):
            return self.env['purchase.order'].create({
                'partner_id': partner.id, 'picking_type_id': wh.in_type_id.id,
                'order_line': [Command.create({'product_id': self.hay.id, 'product_qty': 1, 'price_unit': 5})]})
        po1, po2 = po(self.wh), po(wh2)
        profile = self.env['alfia.access.profile'].create({
            'name': 'T Buyer WH1', 'group_ids': [Command.set([self.env.ref('purchase.group_purchase_user').id])], 'warehouse_ids': [Command.set([self.wh.id])]})
        buyer = self._make_user('whbuyer', [profile])
        seen = self.env['purchase.order'].with_user(buyer).search([('id', 'in', (po1 | po2).ids)])
        self.assertEqual(seen, po1)


@tagged('post_install', '-at_install', 'alfia_security')
class TestProfilesAndEscalation(AlfiaSecurityCase):

    def test_profiles_grant_and_revoke_groups(self):
        user = self._make_user('worker')
        self.assertNotIn(self.stock_user, user.group_ids)
        self.officer_write(user, {'alfia_profile_ids': [Command.link(self.p_barn_a.id)]})
        self.assertIn(self.stock_user, user.all_group_ids)
        self.officer_write(user, {'alfia_profile_ids': [Command.unlink(self.p_barn_a.id)]})
        self.assertNotIn(self.stock_user, user.all_group_ids)

    def officer_write(self, users, vals):
        return users.with_user(self.officer).write(vals)

    def test_revoking_a_profile_keeps_groups_that_were_granted_directly(self):
        user = self._make_user('worker', groups=['base.group_user', 'purchase.group_purchase_user'])
        self.officer_write(user, {'alfia_profile_ids': [Command.set([self.p_barn_a.id])]})
        self.officer_write(user, {'alfia_profile_ids': [Command.clear()]})
        self.assertIn(self.env.ref('purchase.group_purchase_user'), user.group_ids, "only profile-granted groups are removed")
        self.assertNotIn(self.stock_user, user.group_ids)

    def test_editing_a_profile_updates_everyone_who_holds_it(self):
        u1, u2 = self._make_user('u1', [self.p_barn_a]), self._make_user('u2', [self.p_barn_a])
        extra = self.env.ref('purchase.group_purchase_user')
        self.p_barn_a.write({'group_ids': [Command.link(extra.id)]})
        self.assertIn(extra, (u1 | u2).group_ids)
        self.p_barn_a.write({'group_ids': [Command.unlink(extra.id)]})
        self.assertNotIn(extra, (u1 | u2).group_ids)

    def test_officers_cannot_edit_groups_directly_or_escalate(self):
        user = self._make_user('worker')
        with self.assertRaises(AccessError):
            self.officer_write(user, {'group_ids': [Command.link(self.env.ref('base.group_system').id)]})
        with self.assertRaises(AccessError):
            self.officer_write(user, {'group_ids': [Command.link(self.stock_user.id)]})
        with self.assertRaises(AccessError):
            self.officer_write(self.officer, {'alfia_profile_ids': [Command.link(self.p_barn_a.id)]})   # not for themselves
        with self.assertRaises(AccessError):
            self.officer_write(self.sysadmin, {'alfia_profile_ids': [Command.link(self.p_barn_a.id)]})  # not for administrators
        with self.assertRaises(AccessError):
            self.officer_write(user, {'alfia_location_ids': [Command.link(self.barn_a.id)]})            # scope only through profiles
        with self.assertRaises(AccessError):
            user.with_user(user).write({'alfia_profile_ids': [Command.link(self.p_barn_a.id)]})         # nor can users
        with self.assertRaises(AccessError):
            self.env['alfia.access.profile'].with_user(self.officer).create({'name': 'Evil', 'group_ids': [Command.set([self.stock_user.id])]})

    def test_officer_can_create_a_user_with_a_profile_but_not_with_extra_groups(self):
        Users = self.env['res.users'].with_user(self.officer).with_context(no_reset_password=True)
        user = Users.create({'name': 'New', 'login': 'new@test.local', 'alfia_profile_ids': [Command.set([self.p_barn_a.id])]})
        self.assertIn(self.stock_user, user.all_group_ids)
        with self.assertRaises(AccessError):
            Users.create({'name': 'Bad', 'login': 'bad@test.local', 'group_ids': [Command.link(self.env.ref('base.group_system').id)]})

    def test_profiles_cannot_carry_administrator_rights(self):
        for xmlid in ('base.group_system', 'base.group_erp_manager', 'alfia_security.group_alfia_security_officer'):
            with self.assertRaises(ValidationError):
                self.env['alfia.access.profile'].create({'name': 'X ' + xmlid, 'group_ids': [Command.set([self.env.ref(xmlid).id])]})

    def test_assigned_profiles_cannot_be_deleted_and_users_field_is_not_directly_writable(self):
        self._make_user('worker', [self.p_barn_a])
        with self.assertRaises(UserError):
            self.p_barn_a.unlink()
        other = self._make_user('other')
        with self.assertRaises(AccessError):
            self.p_barn_a.with_user(self.sysadmin).write({'user_ids': [Command.link(other.id)]})

    def test_everything_is_audited_and_the_trail_is_immutable(self):
        user = self._make_user('worker')
        self.officer_write(user, {'alfia_profile_ids': [Command.link(self.p_barn_a.id)]})
        Audit = self.env['alfia.security.audit']
        entry = Audit.search([('action', '=', 'user.groups'), ('target_ref', '=', user.login)], limit=1)
        self.assertTrue(entry)
        self.assertEqual(entry.actor_id, self.officer, "the audit shows who made the change")
        self.assertIn('Inventory', entry.details)
        self.assertTrue(Audit.search([('action', '=', 'user.profiles'), ('target_ref', '=', user.login)]))
        access = self.env['ir.access'].search([('name', '=', 'Alfia site scope: quants')], limit=1)
        access.domain = "[('location_id', 'child_of', user.alfia_location_ids.ids)] if user.alfia_scope_locations else []  "
        logged = Audit.search([('action', '=', 'access.write'), ('target_ref', '=', access.name)])
        self.assertTrue(logged, "changing an access rule is audited")
        self.assertIn('before:', logged.details)
        with self.assertRaises(UserError):
            entry.write({'details': 'tampered'})
        with self.assertRaises(UserError):
            entry.unlink()
        with self.assertRaises(UserError):
            entry.with_user(self.sysadmin).unlink()

    def test_audit_trail_visibility(self):
        auditor = self._make_user('auditor', [self.env['alfia.access.profile'].search([('name', '=', 'Auditor (read-only)')])])
        worker = self._make_user('worker')
        Audit = self.env['alfia.security.audit']
        Audit._alfia_log('test.event', 'res.users', 'x')
        self.assertTrue(Audit.with_user(auditor).search([('action', '=', 'test.event')]))
        with self.assertRaises(AccessError):
            Audit.with_user(worker).search([('action', '=', 'test.event')])
        with self.assertRaises(AccessError):
            Audit.with_user(auditor).create({'action': 'forged'})

    def test_hardening_defaults(self):
        self.assertGreaterEqual(self.env['ir.config_parameter'].sudo().get_int('auth_password_policy.minlength'), 12)
        self.assertEqual(self.env.ref('base.group_user').lock_timeout_inactivity, 30)
