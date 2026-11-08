from odoo import Command, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .common import BILLING_MANAGERS, lock_after, require_any


class AlfiaBreedingContract(models.Model):
    """A stallion nomination sold to a client: fee, optional live-foal guarantee, invoice and refund."""
    _name = 'alfia.breeding.contract'
    _description = 'Breeding contract'
    _inherit = ['mail.thread']
    _order = 'id desc'

    name = fields.Char(readonly=True, copy=False, default=lambda s: s.env._('New'))
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company, required=True)
    client_id = fields.Many2one('res.partner', string='Client', required=True)
    stallion_id = fields.Many2one('alfia.horse', required=True, domain="[('sex', '=', 'male')]")
    mare_id = fields.Many2one('alfia.horse', domain="[('sex', '=', 'female')]")
    season = fields.Char(required=True)
    product_id = fields.Many2one('product.product', string='Fee product', required=True, domain="[('type', '=', 'service')]")
    fee = fields.Float(required=True)
    live_foal_guarantee = fields.Boolean(default=True)
    refund_percent = fields.Float(default=100.0)
    pregnancy_id = fields.Many2one('alfia.pregnancy', domain="[('mare_id', '=', mare_id)]")
    state = fields.Selection([('draft', 'Draft'), ('confirmed', 'Confirmed'), ('invoiced', 'Invoiced'), ('refunded', 'Refunded'), ('closed', 'Closed'), ('cancelled', 'Cancelled')],
                             default='draft', required=True, tracking=True)
    invoice_id = fields.Many2one('account.move', readonly=True, copy=False)
    credit_note_id = fields.Many2one('account.move', readonly=True, copy=False)
    outcome = fields.Selection([('open', 'Open'), ('live', 'Live foal'), ('no_live', 'No live foal')], compute='_compute_outcome', store=True)

    _fee_nonneg = models.Constraint('check(fee >= 0 and refund_percent >= 0 and refund_percent <= 100)', 'Fee and refund percentage must be valid.')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('name') or vals['name'] == self.env._('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('alfia.breeding.contract') or self.env._('New')
        return super().create(vals_list)

    @api.depends('pregnancy_id.state', 'pregnancy_id.foaling_ids.outcome', 'pregnancy_id.foaling_ids.state')
    def _compute_outcome(self):
        for rec in self:
            preg = rec.pregnancy_id
            if not preg:
                rec.outcome = 'open'
            elif preg.state in ('lost', 'negative') or any(f.outcome in ('stillborn', 'aborted') for f in preg.foaling_ids):
                rec.outcome = 'no_live'
            elif any(f.outcome == 'live' for f in preg.foaling_ids):
                rec.outcome = 'live'
            else:
                rec.outcome = 'open'

    def write(self, vals):
        # Fee and guarantee are fixed once confirmed; only the outcome link and the accounting documents move on.
        lock_after(self, vals, ('confirmed', 'invoiced', 'refunded', 'closed'), ('state', 'invoice_id', 'credit_note_id', 'pregnancy_id', 'mare_id'),
                   self.env._("A confirmed contract"))
        return super().write(vals)

    def action_confirm(self):
        require_any(self.env, BILLING_MANAGERS, self.env._("managers"))
        self.filtered(lambda r: r.state == 'draft').write({'state': 'confirmed'})

    def action_invoice(self):
        require_any(self.env, ('account.group_account_invoice', 'base.group_system'), self.env._("accounting staff"))
        for rec in self:
            if rec.state != 'confirmed' or rec.invoice_id:
                raise UserError(self.env._("Only a confirmed, not yet invoiced contract can be invoiced."))
            rec.invoice_id = self.env['account.move'].with_company(rec.company_id).create({
                'move_type': 'out_invoice', 'partner_id': rec.client_id.id, 'invoice_date': fields.Date.context_today(self),
                'invoice_line_ids': [Command.create({'product_id': rec.product_id.id, 'quantity': 1, 'price_unit': rec.fee,
                                                      'name': f"{rec.name}: {rec.stallion_id.name} ({rec.season})"})]})
            rec.state = 'invoiced'

    def action_refund(self):
        """Credit the client when the live-foal guarantee applies and there was no live foal."""
        require_any(self.env, ('account.group_account_invoice', 'base.group_system'), self.env._("accounting staff"))
        for rec in self:
            if rec.state != 'invoiced' or rec.credit_note_id:
                raise UserError(self.env._("Only an invoiced contract that was not yet refunded can be refunded."))
            if not rec.live_foal_guarantee or rec.outcome != 'no_live':
                raise UserError(self.env._("A refund is due only under a live-foal guarantee when there is no live foal."))
            credit = self.env['account.move'].with_company(rec.company_id).create({
                'move_type': 'out_refund', 'partner_id': rec.client_id.id, 'invoice_date': fields.Date.context_today(self),
                'invoice_line_ids': [Command.create({'product_id': rec.product_id.id, 'quantity': 1, 'price_unit': rec.fee * rec.refund_percent / 100.0,
                                                      'name': self.env._("Live-foal guarantee refund: %s", rec.name)})]})
            rec.write({'credit_note_id': credit.id, 'state': 'refunded'})

    def action_close(self):
        self.filtered(lambda r: r.state in ('invoiced', 'refunded')).write({'state': 'closed'})

    def action_cancel(self):
        if any(r.invoice_id for r in self):
            raise UserError(self.env._("An invoiced contract cannot be cancelled."))
        self.write({'state': 'cancelled'})
