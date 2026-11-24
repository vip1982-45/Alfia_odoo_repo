"""Nobody approves their own time off, allocation or reimbursement, as everywhere else in the system."""
from odoo import api, models
from odoo.exceptions import UserError

APPROVAL_STATES = ('validate1', 'validate')


def _sod_applies(env):
    if env.su:
        return False
    return env['alfia.sod.mixin']._alfia_sod_applies(None, env.company)


class HrLeave(models.Model):
    _inherit = 'hr.leave'

    def _check_approval_update(self, state, raise_if_not_possible=True):
        if state in APPROVAL_STATES and _sod_applies(self.env):
            own = self.filtered(lambda h: h.employee_id.user_id == self.env.user)
            if own:
                if raise_if_not_possible:
                    raise UserError(self.env._("You cannot approve your own time off. Another approver must do it."))
                return False
        return super()._check_approval_update(state, raise_if_not_possible=raise_if_not_possible)


    @api.model_create_multi
    def create(self, vals_list):
        leaves = super().create(vals_list)
        # Created already approved (e.g. by a time-off officer for themselves) is self-approval too, unless the type
        # needs no approval at all.
        if _sod_applies(self.env) and leaves.filtered(lambda h: h.state in APPROVAL_STATES and h.employee_id.user_id == self.env.user
                                                       and h.work_entry_type_id.leave_validation_type != 'no_validation'):
            raise UserError(self.env._("You cannot approve your own time off. Another approver must do it."))
        return leaves


class HrLeaveAllocation(models.Model):
    _inherit = 'hr.leave.allocation'

    def _check_approval_update(self, state, raise_if_not_possible=True):
        if state in APPROVAL_STATES and _sod_applies(self.env):
            own = self.filtered(lambda a: a.employee_id.user_id == self.env.user)
            if own:
                if raise_if_not_possible:
                    raise UserError(self.env._("You cannot approve your own time-off allocation. Another approver must do it."))
                return False
        return super()._check_approval_update(state, raise_if_not_possible=raise_if_not_possible)


    @api.model_create_multi
    def create(self, vals_list):
        allocations = super().create(vals_list)
        if _sod_applies(self.env) and allocations.filtered(lambda a: a.state in APPROVAL_STATES and a.employee_id.user_id == self.env.user
                                                           and a.work_entry_type_id.allocation_validation_type != 'no_validation'):
            raise UserError(self.env._("You cannot approve your own time-off allocation. Another approver must do it."))
        return allocations


class HrExpense(models.Model):
    _inherit = 'hr.expense'

    def _can_be_autovalidated(self):
        # Odoo approves an expense at once when its approver is the employee, or when nobody is set to approve it:
        # either way the submitter would approve their own expense, so it waits for an approver instead.
        if _sod_applies(self.env) and self.employee_id.user_id:
            return False
        return super()._can_be_autovalidated()

    def _do_approve(self):
        # Every approval path (the button, the duplicate-expense wizard) ends here.
        self._alfia_refuse_own()
        return super()._do_approve()

    def _alfia_refuse_own(self):
        if _sod_applies(self.env) and self.filtered(lambda e: e.employee_id.user_id == self.env.user):
            raise UserError(self.env._("You cannot approve your own expense. Another approver must do it."))

    def _check_can_approve(self):
        self._alfia_refuse_own()
        return super()._check_can_approve()

    # Approving a reimbursement is the control; the accounting entry behind it is the system's job. The approver (a line
    # manager) and the employee usually have no access to accounting journals, so the journal is found and the entry
    # created with system rights once the approval rights have been checked.
    def _get_default_journal(self):
        return super(HrExpense, self.sudo())._get_default_journal().with_env(self.env)

    def _create_move(self):
        return super(HrExpense, self.sudo())._create_move()
