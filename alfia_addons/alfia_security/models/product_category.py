from odoo import api, fields, models
from odoo.exceptions import AccessError

WATCHED = ('alfia_access_group_id', 'alfia_dual_approval', 'alfia_approver_group_ids')


class ProductCategory(models.Model):
    _inherit = 'product.category'

    alfia_access_group_id = fields.Many2one(
        'res.groups', string='Restrict stock to group', ondelete='set null',
        help="Only members of this group can see or move stock, lots and transfers of products in this category "
             "(and its sub-categories). Leave empty for ordinary stock.")
    alfia_effective_group_id = fields.Many2one(
        'res.groups', compute='_compute_alfia_effective_group', store=True, recursive=True, index=True,
        string='Effective restriction', help="This category's own restriction, or the nearest parent's.")
    alfia_dual_approval = fields.Boolean(
        string='Dual approval before dispatch',
        help="Outgoing transfers containing these products need an approval from every group below, each by a different person.")
    alfia_approver_group_ids = fields.Many2many(
        'res.groups', 'alfia_category_approver_group_rel', 'category_id', 'group_id', string='Approver groups')

    alfia_effective_approver_group_ids = fields.Many2many(
        'res.groups', 'alfia_category_eff_approver_rel', 'category_id', 'group_id',
        compute='_compute_alfia_effective_group', store=True, recursive=True, string='Effective approver groups',
        help="This category's own approver groups when dual approval is on, otherwise the nearest parent's.")

    @api.depends('alfia_access_group_id', 'alfia_dual_approval', 'alfia_approver_group_ids',
                 'parent_id.alfia_effective_group_id', 'parent_id.alfia_effective_approver_group_ids')
    def _compute_alfia_effective_group(self):
        for category in self:
            category.alfia_effective_group_id = category.alfia_access_group_id or category.parent_id.alfia_effective_group_id
            category.alfia_effective_approver_group_ids = (
                category.alfia_approver_group_ids if category.alfia_dual_approval else category.parent_id.alfia_effective_approver_group_ids)

    def _alfia_dual_config(self):
        """The approver groups a transfer of this category needs (empty when no dual approval applies)."""
        return self.alfia_effective_approver_group_ids

    def _alfia_check_can_edit_restrictions(self, vals):
        if not any(f in vals for f in WATCHED) or self.env.su:
            return
        user = self.env.user
        if user.has_group('base.group_system'):
            return
        if not user.has_group('alfia_security.group_alfia_security_officer'):
            raise AccessError(self.env._("Only security officers and system administrators can change stock restrictions."))

    def _alfia_officer_only_restrictions(self, vals):
        """Security officers may edit the restriction fields, nothing else on a category."""
        user = self.env.user
        if self.env.su or user.has_group('base.group_system'):
            return
        is_officer = user.has_group('alfia_security.group_alfia_security_officer')
        # Officers have update-only rights on categories (see ir.access.csv). If they are also product managers,
        # they keep what product management gives them; otherwise they are limited to the restriction fields.
        if is_officer and not user.has_group('product.group_product_manager') and set(vals) - set(WATCHED):
            raise AccessError(self.env._("Security officers can only change the stock restriction fields of a category."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._alfia_check_can_edit_restrictions(vals)
        records = super().create(vals_list)
        for category in records.filtered(lambda c: c.alfia_access_group_id or c.alfia_dual_approval):
            self.env['alfia.security.audit']._alfia_log('category.create', self._name, category.complete_name, self._alfia_describe(category))
        return records

    def write(self, vals):
        self._alfia_officer_only_restrictions(vals)
        self._alfia_check_can_edit_restrictions(vals)
        watched = any(f in vals for f in WATCHED)
        before = {c.id: self._alfia_describe(c) for c in self} if watched else {}
        res = super().write(vals)
        if watched:
            for category in self:
                if before[category.id] != self._alfia_describe(category):
                    self.env['alfia.security.audit']._alfia_log(
                        'category.write', self._name, category.complete_name, f"before: {before[category.id]}\nafter: {self._alfia_describe(category)}")
        return res

    @staticmethod
    def _alfia_describe(category):
        return "restricted to=%s dual approval=%s approvers=[%s]" % (
            category.alfia_access_group_id.display_name or '-', category.alfia_dual_approval,
            ', '.join(sorted(category.alfia_approver_group_ids.mapped('display_name'))))
