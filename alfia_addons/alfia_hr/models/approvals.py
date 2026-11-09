"""Nobody approves their own time off, allocation or reimbursement. Members of the explicit emergency group
(alfia_security.group_alfia_sod_exempt) are the only exception, as everywhere else in the system."""
from odoo import models
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


class HrExpense(models.Model):
    _inherit = 'hr.expense'

    def _can_be_autovalidated(self):
        # Odoo approves an expense at once when its approver is the employee: that is self-approval.
        if _sod_applies(self.env) and self.employee_id.user_id and self.manager_id == self.employee_id.user_id:
            return False
        return super()._can_be_autovalidated()

    def _check_can_approve(self):
        if _sod_applies(self.env) and self.filtered(lambda e: e.employee_id.user_id == self.env.user):
            raise UserError(self.env._("You cannot approve your own expense. Another approver must do it."))
        return super()._check_can_approve()

    # Approving a reimbursement is the control; the accounting entry behind it is the system's job. The approver (a line
    # manager) and the employee usually have no access to accounting journals, so the journal is found and the entry
    # created with system rights once the approval rights have been checked.
    def _get_default_journal(self):
        return super(HrExpense, self.sudo())._get_default_journal().with_env(self.env)

    def _create_move(self):
        return super(HrExpense, self.sudo())._create_move()
