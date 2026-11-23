from odoo import Command, api, fields, models
from odoo.exceptions import UserError

from odoo.addons.alfia_security.flow import is_trusted, workflow

G = 'alfia_security.group_alfia_'


class AlfiaMedicineIssue(models.Model):
    """Medicine taken from stock for one horse. Issued medicine is held in the treatment team's custody (real stock in a
    custody location) until it is administered (structured doses linked to the issue) or returned, so the store, the
    team and what the horse received always add up: issue 10, administer 6, return 2 leaves 2 held by the team."""
    _name = 'alfia.medicine.issue'
    _description = 'Medicine issue'
    _inherit = ['alfia.horse.record', 'mail.thread', 'alfia.guarded.mixin']
    _order = 'date desc, id desc'
    _alfia_guarded = ('state', 'returned_qty', 'wasted_qty', 'move_ids', 'custody_location_id')

    date = fields.Date(required=True, default=fields.Date.context_today)
    product_id = fields.Many2one('product.product', string='Medicine', required=True, domain="[('is_storable', '=', True)]")
    quantity = fields.Float(string='Issued', required=True, default=1.0)
    returned_qty = fields.Float(string='Returned to store', readonly=True, copy=False)
    wasted_qty = fields.Float(string='Returned as waste', readonly=True, copy=False)
    administered_qty = fields.Float(string='Administered', compute='_compute_quantities', store=True)
    held_qty = fields.Float(string='Held by the team', compute='_compute_quantities', store=True)
    location_id = fields.Many2one('stock.location', string='Take from', required=True, domain="[('usage', '=', 'internal')]")
    custody_location_id = fields.Many2one('stock.location', string='Held in', readonly=True, copy=False)
    treatment_id = fields.Many2one('alfia.treatment', domain="[('horse_id', '=', horse_id)]")
    dose_ids = fields.One2many('alfia.treatment.dose', 'issue_id', string='Doses given from this issue')
    state = fields.Selection([('draft', 'Draft'), ('issued', 'Issued'), ('returned', 'Returned (partly or fully)')], default='draft', required=True, tracking=True)
    move_ids = fields.Many2many('stock.move', readonly=True, copy=False)

    _qty_positive = models.Constraint('check(quantity > 0)', 'The quantity must be above zero.')

    @api.depends('quantity', 'returned_qty', 'wasted_qty', 'dose_ids.quantity', 'dose_ids.missed', 'state')
    def _compute_quantities(self):
        for rec in self:
            given = sum(d.quantity for d in rec.dose_ids if not d.missed)
            rec.administered_qty = given
            rec.held_qty = (rec.quantity - given - rec.returned_qty - rec.wasted_qty) if rec.state != 'draft' else 0.0

    @api.model
    def _alfia_custody_location(self, company):
        """The treatment team's custody location of the company (created once, inside the main warehouse)."""
        Location = self.env['stock.location'].sudo()
        name = 'عهدة الفريق البيطري · Treatment team custody'
        loc = Location.search([('name', '=', name), ('usage', '=', 'internal'), ('company_id', '=', company.id)], limit=1)
        if not loc:
            wh = self.env['stock.warehouse'].sudo().search([('company_id', '=', company.id)], limit=1)
            loc = Location.create({'name': name, 'usage': 'internal', 'company_id': company.id,
                                   'location_id': wh.view_location_id.id if wh else False})
        return loc

    def _consumed_location(self):
        return self.product_id.with_company(self.company_id).property_stock_inventory

    def _move(self, qty, src, dst, origin=None):
        self.ensure_one()
        move = self.env['stock.move'].sudo().create({
            'product_id': self.product_id.id, 'product_uom_qty': qty, 'quantity': qty, 'uom_id': self.product_id.uom_id.id,
            'location_id': src.id, 'location_dest_id': dst.id, 'picked': True, 'company_id': self.company_id.id,
            'origin': origin or f"{self.horse_id.name} / medicine issue {self.id}"})
        move._action_confirm()
        move._action_done()
        with workflow():
            self.move_ids = [Command.link(move.id)]
        return move

    def write(self, vals):
        # Once issued, what was taken and from where is fixed: a return puts back exactly that, never more or elsewhere.
        if not is_trusted(self.env) and {'product_id', 'quantity', 'location_id', 'horse_id'} & set(vals) and any(r.state != 'draft' for r in self):
            raise UserError(self.env._("An issued medicine record cannot be changed. Record a new issue instead."))
        return super().write(vals)

    def action_issue(self):
        if not (self.env.su or self.env.user.has_group(G + 'medicine_custodian')):
            raise UserError(self.env._("Only the medicine custodian can issue medicine."))
        for rec in self.filtered(lambda r: r.state == 'draft'):
            self.env.user._alfia_check_location(rec.location_id, self.env._("medicine store"))
            available = rec.product_id.with_context(location=rec.location_id.id).qty_available
            if available < rec.quantity:
                raise UserError(self.env._("Only %(have)s of %(product)s is available there.", have=available, product=rec.product_id.display_name))
            custody = self._alfia_custody_location(rec.company_id)
            rec._move(rec.quantity, rec.location_id, custody)
            with workflow():
                rec.write({'state': 'issued', 'custody_location_id': custody.id})

    def _alfia_held_source(self):
        # Issues made before team custody existed went straight to consumption; their returns come back from there.
        return self.custody_location_id or self._consumed_location()

    def action_return(self, qty=None, reason=None, disposition='stock'):
        """Take back medicine the team still holds: to the store (usable) or as waste. Never more than is held."""
        if not (self.env.su or self.env.user.has_group(G + 'medicine_custodian')):
            raise UserError(self.env._("Only the medicine custodian can take medicine back."))
        for rec in self.filtered(lambda r: r.state in ('issued', 'returned')):
            qty_back = rec.held_qty if qty is None else qty
            if qty_back <= 0 or qty_back > rec.held_qty + 1e-9:
                raise UserError(self.env._("The team holds %(held)s of %(issued)s issued (%(given)s administered, %(back)s already returned); "
                                           "you can return at most %(held)s.", held=rec.held_qty, issued=rec.quantity,
                                           given=rec.administered_qty, back=rec.returned_qty + rec.wasted_qty))
            self.env.user._alfia_check_location(rec.location_id, self.env._("medicine store"))
            label = self.env._("Return (%s)", reason) if reason else None
            if disposition == 'waste':
                if rec.custody_location_id:   # older issues were already consumed when issued
                    rec._move(qty_back, rec.custody_location_id, rec._consumed_location(), origin=label)
                vals = {'wasted_qty': rec.wasted_qty + qty_back}
            else:
                rec._move(qty_back, rec._alfia_held_source(), rec.location_id, origin=label)
                vals = {'returned_qty': rec.returned_qty + qty_back}
            with workflow():
                rec.write(dict(vals, state='returned'))
            rec.message_post(body=self.env._("%(qty)s returned %(how)s by %(user)s. Reason: %(reason)s", qty=qty_back,
                                             how=self.env._("as waste") if disposition == 'waste' else self.env._("to the store"),
                                             user=self.env.user.name, reason=reason or '-'))

    def action_open_return(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': 'alfia.medicine.return', 'view_mode': 'form', 'target': 'new',
                'context': {'default_issue_id': self.id, 'default_quantity': self.held_qty}}


class AlfiaTreatmentDose(models.Model):
    """A dose given from issued medicine is recorded as a quantity against the issue it came from, and leaves the team's
    custody as consumed stock: the store is never charged twice."""
    _inherit = 'alfia.treatment.dose'

    issue_id = fields.Many2one('alfia.medicine.issue', string='From medicine issue', domain="[('treatment_id', '=', treatment_id), ('state', 'in', ('issued', 'returned'))]")
    quantity = fields.Float(string='Quantity given', help="In the medicine's unit, when the dose comes from an issue.")

    @api.model_create_multi
    def create(self, vals_list):
        doses = super().create(vals_list)
        for dose in doses.filtered(lambda d: d.issue_id and not d.missed):
            issue = dose.issue_id.sudo()
            if dose.quantity <= 0:
                raise UserError(self.env._("Enter the quantity given from %s.", issue.product_id.display_name))
            held_before = issue.held_qty + dose.quantity   # held_qty already counts this dose
            if dose.quantity > held_before + 1e-9:
                raise UserError(self.env._("The team holds only %(held)s of %(product)s from this issue.", held=held_before, product=issue.product_id.display_name))
            if issue.custody_location_id:
                issue._move(dose.quantity, issue.custody_location_id, issue._consumed_location(),
                            origin=self.env._("%(horse)s / dose %(dose)s", horse=issue.horse_id.name, dose=dose.id))
        return doses

    def write(self, vals):
        if not self.env.su and {'issue_id', 'quantity', 'missed'} & set(vals) and any(d.issue_id for d in self):
            raise UserError(self.env._("A dose given from issued medicine cannot be changed; record a correction instead."))
        return super().write(vals)


class AlfiaMedicineReturn(models.TransientModel):
    _name = 'alfia.medicine.return'
    _description = 'Return issued medicine'

    issue_id = fields.Many2one('alfia.medicine.issue', required=True)
    held_qty = fields.Float(related='issue_id.held_qty', string='Held by the team')
    quantity = fields.Float(required=True)
    disposition = fields.Selection([('stock', 'Back to the store (usable)'), ('waste', 'Waste / not usable')], required=True, default='stock')
    reason = fields.Char(required=True)

    def action_confirm(self):
        self.ensure_one()
        self.issue_id.action_return(self.quantity, self.reason, self.disposition)
        return {'type': 'ir.actions.act_window_close'}
