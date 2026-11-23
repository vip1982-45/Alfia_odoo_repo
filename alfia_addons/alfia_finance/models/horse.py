from odoo import api, fields, models
from odoo.exceptions import UserError


class AlfiaHorse(models.Model):
    _inherit = 'alfia.horse'

    analytic_account_id = fields.Many2one('account.analytic.account', string='Cost centre', copy=False, readonly=True,
                                          help="Costs and income allocated to this horse (invoices, bills, stock issues).")

    @api.model
    def _alfia_horse_plan(self):
        plan = self.env['account.analytic.plan'].sudo().search([('name', '=', 'Horses')], limit=1)
        return plan or self.env['account.analytic.plan'].sudo().create({'name': 'Horses', 'default_applicability': 'optional'})

    @api.model
    def action_alfia_horse_costs(self):
        """Costs and income per horse: analytic lines of the Horses plan, grouped by horse and by general account."""
        column = self._alfia_horse_plan()._column_name()
        return {'type': 'ir.actions.act_window', 'name': self.env._("Costs and income per horse"), 'res_model': 'account.analytic.line',
                'view_mode': 'pivot,list,graph', 'views': [(False, 'pivot'), (False, 'list'), (False, 'graph')],
                'domain': [(column, '!=', False)],
                'context': {'pivot_row_groupby': [column], 'pivot_column_groupby': ['general_account_id'], 'pivot_measures': ['amount']}}

    def _alfia_ensure_analytic_account(self):
        plan = self._alfia_horse_plan()
        Account = self.env['account.analytic.account'].sudo()
        for horse in self.filtered(lambda h: not h.analytic_account_id):
            horse.sudo().analytic_account_id = Account.create({
                'name': horse.name, 'code': horse.reg_id or False, 'plan_id': plan.id, 'company_id': horse.company_id.id})

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and any(v.get('analytic_account_id') for v in vals_list):
            raise UserError(self.env._("A horse's cost centre is set by the system."))
        horses = super().create(vals_list)
        horses._alfia_ensure_analytic_account()
        return horses

    def write(self, vals):
        # Each horse has its own cost centre, made by the system; pointing a horse at another account (which would then be
        # renamed and charged with the horse's costs) is not allowed.
        if 'analytic_account_id' in vals and not self.env.su:
            raise UserError(self.env._("A horse's cost centre is set by the system."))
        res = super().write(vals)
        if 'name' in vals:
            for horse in self.filtered('analytic_account_id'):
                horse.analytic_account_id.sudo().name = horse.name
        return res


class AlfiaCharge(models.Model):
    _inherit = 'alfia.charge'

    def _alfia_invoice_line_vals(self):
        vals = super()._alfia_invoice_line_vals()
        account = self.horse_id.analytic_account_id
        if account:
            vals['analytic_distribution'] = {str(account.id): 100}
        return vals


class AlfiaBreedingContract(models.Model):
    _inherit = 'alfia.breeding.contract'

    def _alfia_invoice_line_vals(self, price=None, name=None):
        vals = super()._alfia_invoice_line_vals(price, name)
        # A nomination fee is the stallion's income (and its refund the stallion's cost); without a stallion of ours,
        # it goes to the mare named on the contract.
        horse = self.stallion_id if self.stallion_id.analytic_account_id else self.mare_id
        if horse.analytic_account_id:
            vals['analytic_distribution'] = {str(horse.analytic_account_id.id): 100}
        return vals


class AlfiaMedicineIssue(models.Model):
    _inherit = 'alfia.medicine.issue'

    def _move(self, qty, src, dst, origin=None):
        move = super()._move(qty, src, dst, origin=origin)
        # The cost of medicine given to a horse is allocated to that horse.
        account = self.horse_id.analytic_account_id
        if account:
            lines = move.sudo().account_move_id.line_ids.filtered(lambda l: l.account_id.account_type in ('expense', 'expense_direct_cost', 'expense_other'))
            lines.write({'analytic_distribution': {str(account.id): 100}})
        return move
