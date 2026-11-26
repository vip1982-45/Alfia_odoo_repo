"""Frozen semen managed by the straw (development spec of 26 Nov 2026, owner decisions D1/D2, D3, D6/D13, D11).

The straw is the physical unit of stock: it is what is stored, counted, pulled and thawed. A dose is derived from each
freeze lot's own straws-per-dose ratio (no fixed number anywhere in the code). A freeze lot comes from an approved
collection, is quality-checked after a thaw test, and loading it puts its straws into a tank location (tank > canister >
cane > goblet, any depth). A frozen insemination takes an explicit number of straws, linked to the service by a field,
once. Thawed straws never go back to frozen stock: unused ones are recorded as waste with a reason. Collections, lots,
reservations, deliveries and the external-centre statements that existed before are extended, not replaced; existing
dose batches keep their quantities (their straw count is unknown, never invented).
"""
from collections import defaultdict
from math import floor

from odoo import Command, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from odoo.addons.alfia_security.flow import trusted, workflow

G = 'alfia_security.group_alfia_'
CUSTODIAN, VET_USER, VET_MANAGER = G + 'semen_custodian', G + 'vet_user', G + 'vet_manager'
LOCATION_KINDS = [('tank', 'Tank'), ('canister', 'Canister'), ('cane', 'Cane'), ('goblet', 'Goblet'), ('other', 'Other')]


def _has(env, *groups):
    return env.su or any(env.user.has_group(g) for g in groups)


def _require(env, groups, who):
    if not _has(env, *groups):
        raise AccessError(env._("Only %s can do this.", who))


class ResCompany(models.Model):
    _inherit = 'res.company'

    alfia_semen_qc_gate = fields.Boolean(
        string='Semen: quality check before loading',
        help="A freeze lot is loaded into a tank only after its thaw test has passed.")
    alfia_semen_min_motility = fields.Float(
        string='Semen: lowest motility after thaw (%)',
        help="Optional. A thaw test below this cannot be passed. Empty: the vet decides.")
    alfia_semen_min_progressive = fields.Float(
        string='Semen: lowest progressive motility after thaw (%)', help="Optional, as above.")


class StockLocation(models.Model):
    """Storage below a tank: canisters, canes and goblets are ordinary sub-locations, at any depth, each optionally with
    its kind, its place in its parent (position) and how many straws it holds."""
    _inherit = 'stock.location'

    alfia_location_kind = fields.Selection(LOCATION_KINDS, string='Storage kind')
    alfia_position = fields.Integer(string='Position', help="Order inside the parent (e.g. canister 3, upper or lower place).")
    alfia_capacity_straws = fields.Integer(string='Capacity (straws)', help="Optional. A warning is shown when it is exceeded.")
    alfia_last_audit_date = fields.Date(string='Last counted', readonly=True)
    alfia_straws_here = fields.Integer(string='Straws here', compute='_compute_alfia_straws')
    alfia_over_capacity = fields.Boolean(string='Over capacity', compute='_compute_alfia_straws')

    def _compute_alfia_straws(self):
        straw = self.env.ref('alfia_registry.product_semen_straw', raise_if_not_found=False)
        counts = {}
        if straw and self.ids:
            for location, qty in self.env['stock.quant'].sudo()._read_group(
                    [('product_id', '=', straw.id), ('location_id', 'in', self.ids)], ['location_id'], ['quantity:sum']):
                counts[location.id] = int(qty)
        for loc in self:
            loc.alfia_straws_here = counts.get(loc.id, 0)
            loc.alfia_over_capacity = bool(loc.alfia_capacity_straws and loc.alfia_straws_here > loc.alfia_capacity_straws)

    @api.constrains('alfia_capacity_straws')
    def _check_alfia_capacity(self):
        for loc in self:
            if loc.alfia_capacity_straws < 0:
                raise ValidationError(self.env._("A capacity cannot be negative."))


class AlfiaSemenFreezeLot(models.Model):
    _name = 'alfia.semen.freeze.lot'
    _description = 'Semen freeze lot'
    _inherit = ['mail.thread', 'alfia.guarded.mixin', 'alfia.sod.mixin']
    _order = 'freeze_date desc, id desc'
    _alfia_guarded = ('state', 'stock_lot_id', 'approved_by_id', 'qc_state', 'qc_by_id')

    name = fields.Char(readonly=True, copy=False, default=lambda self: self.env._('New'))
    collection_id = fields.Many2one('alfia.semen.collection', string='Collection', required=True, index=True,
                                    domain="[('state', '=', 'released')]", tracking=True)
    stallion_id = fields.Many2one(related='collection_id.stallion_id', store=True, string='Stallion')
    company_id = fields.Many2one(related='collection_id.company_id', store=True)
    stock_lot_id = fields.Many2one('stock.lot', string='Stock batch', readonly=True, copy=False, index=True)
    freeze_date = fields.Date(required=True, default=fields.Date.context_today, tracking=True)
    freeze_protocol = fields.Char(string='Extender / protocol')
    straw_volume = fields.Selection([('0.25', '0.25 ml'), ('0.5', '0.5 ml'), ('other', 'Other')], string='Straw volume', default='0.5')
    straw_volume_other_ml = fields.Float(string='Straw volume (ml, other)')
    straws_produced = fields.Integer(required=True, tracking=True)
    straws_per_dose = fields.Integer(string='Straws per dose', required=True, tracking=True,
                                     help="Set per lot (no fixed number). Locked after the first reservation, use or sale; a "
                                          "correction is made with Correct straws per dose, with a reason.")
    sperm_per_straw_m = fields.Float(string='Sperm per straw (million)')
    post_thaw_motility_pct = fields.Float(string='Motility after thaw (%)')
    post_thaw_progressive_pct = fields.Float(string='Progressive motility after thaw (%)')
    post_thaw_test_date = fields.Date(string='Thaw test date')
    qc_state = fields.Selection([('pending', 'Waiting for thaw test'), ('passed', 'Passed'), ('rejected', 'Rejected')],
                                string='Quality', default='pending', required=True, tracking=True)
    qc_by_id = fields.Many2one('res.users', string='Quality decided by', readonly=True, copy=False)
    owner_partner_id = fields.Many2one('res.partner', string='Owner of the semen',
                                       help="Empty: the stud. Otherwise the stallion owner or client who owns these straws.")
    load_location_id = fields.Many2one('stock.location', string='Load into', domain="[('usage', '=', 'internal')]",
                                       help="The tank, canister, cane or goblet that receives the straws.")
    state = fields.Selection([('draft', 'Draft'), ('loaded', 'Loaded'), ('cancelled', 'Cancelled')], default='draft',
                             required=True, tracking=True)
    approved_by_id = fields.Many2one('res.users', string='Approved and loaded by', readonly=True, copy=False)
    ratio_version_ids = fields.One2many('alfia.semen.ratio.version', 'freeze_lot_id', string='Straws per dose history')
    ratio_locked = fields.Boolean(compute='_compute_ratio_locked')
    notes = fields.Text()

    # Balances, in straws (section 8 of the spec): computed from the stock moves and quants of the lot.
    straws_frozen = fields.Integer(string='Frozen in storage', compute='_compute_balances')
    straws_quarantine = fields.Integer(string='Frozen, in quarantine', compute='_compute_balances')
    straws_thawed = fields.Integer(string='Thawed, in use', compute='_compute_balances')
    straws_reserved = fields.Integer(string='Reserved', compute='_compute_balances')
    straws_available = fields.Integer(string='Available straws', compute='_compute_balances',
                                      help="Frozen, not in quarantine and not reserved.")
    doses_available = fields.Integer(string='Available doses', compute='_compute_balances',
                                     help="Available straws ÷ straws per dose, for this lot only (rounded down).")
    straws_remainder = fields.Integer(string='Straws left over', compute='_compute_balances',
                                      help="Available straws that do not make a whole dose.")
    straws_used = fields.Integer(string='Used in services', compute='_compute_balances')
    straws_wasted = fields.Integer(string='Wasted or destroyed', compute='_compute_balances')
    straws_out = fields.Integer(string='Delivered out of our ownership', compute='_compute_balances')
    straws_adjusted = fields.Integer(string='Count adjustments', compute='_compute_balances')
    balance_ok = fields.Boolean(string='Balances add up', compute='_compute_balances')
    service_count = fields.Integer(compute='_compute_outcomes', string='Services')
    pregnancy_count = fields.Integer(compute='_compute_outcomes', string='Pregnancies confirmed')
    foal_count = fields.Integer(compute='_compute_outcomes', string='Foals')

    _positive = models.Constraint('check(straws_produced > 0 and straws_per_dose > 0)',
                                  'Straws produced and straws per dose must be above zero.')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            vals['name'] = self.env['ir.sequence'].sudo().next_by_code('alfia.semen.freeze.lot') or self.env._('Freeze lot')
        return super().create(vals_list)

    def write(self, vals):
        if 'straws_per_dose' in vals and not self.env.su and any(lot.ratio_locked for lot in self) \
                and not self.env.context.get('alfia_ratio_correction'):
            raise UserError(self.env._("Straws per dose is locked once straws were reserved, used or sold. Use Correct straws per "
                                       "dose, with a reason."))
        if {'straws_produced', 'collection_id', 'load_location_id'} & set(vals) and any(lot.state != 'draft' for lot in self) \
                and not self.env.su:
            raise UserError(self.env._("A loaded freeze lot's straws and storage change only through stock moves (count, waste, "
                                       "transfer)."))
        return super().write(vals)

    # -- locations used by the straw life cycle ------------------------------------------------------------------------
    @api.model
    def _alfia_place(self, kind, company=None):
        """'thawed' and 'quarantine' (internal, owned) or 'destroyed' (out of stock); created once per company."""
        company = company or self.env.company
        if kind == 'destroyed':
            return self.env.ref('alfia_registry.stock_location_semen_destroyed')
        names = {'thawed': 'سائل مذاب قيد الاستخدام · Semen thawed, in use', 'quarantine': 'سائل تحت الحجر · Semen quarantine'}
        Location = self.env['stock.location'].sudo()
        loc = Location.search([('name', '=', names[kind]), ('company_id', '=', company.id), ('usage', '=', 'internal')], limit=1)
        if not loc:
            wh = self.env['stock.warehouse'].sudo().search([('company_id', '=', company.id)], limit=1)
            parent = self.env['stock.location'].sudo().search([('name', 'ilike', 'Semen tank room'), ('usage', '=', 'internal'),
                                                               ('company_id', '=', company.id)], limit=1) or wh.lot_stock_id
            loc = Location.create({'name': names[kind], 'usage': 'internal', 'location_id': parent.id, 'company_id': company.id})
        return loc

    def _alfia_bucket(self, location, places):
        if location == places['used']:
            return 'used'
        if location == places['destroyed']:
            return 'wasted'
        if location.usage == 'internal':
            if location == places['thawed']:
                return 'thawed'
            if location == places['quarantine']:
                return 'quarantine'
            return 'frozen'
        if location.usage == 'inventory':
            return 'source'
        return 'out'

    def _compute_ratio_locked(self):
        for lot in self:
            lot.ratio_locked = bool(lot.stock_lot_id and self.env['stock.move.line'].sudo().search_count([
                ('lot_id', '=', lot.stock_lot_id.id), ('location_id.usage', '=', 'internal'),
                '|', ('state', '=', 'done'), ('move_id.state', 'in', ('assigned', 'partially_available'))], limit=1))

    def _compute_balances(self):
        used = self.env.ref('alfia_equine.stock_location_semen_used', raise_if_not_found=False)
        for lot in self:
            totals = defaultdict(float)
            reserved = 0.0
            if lot.stock_lot_id:
                places = {'used': used, 'destroyed': self._alfia_place('destroyed'),
                          'thawed': self._alfia_place('thawed', lot.company_id), 'quarantine': self._alfia_place('quarantine', lot.company_id)}
                for ml in self.env['stock.move.line'].sudo().search([('lot_id', '=', lot.stock_lot_id.id), ('state', '=', 'done')]):
                    qty = ml.quantity_product_uom if 'quantity_product_uom' in ml._fields else ml.quantity
                    totals[self._alfia_bucket(ml.location_dest_id, places)] += qty
                    totals[self._alfia_bucket(ml.location_id, places)] -= qty
                reserved = sum(self.env['stock.quant'].sudo().search([
                    ('lot_id', '=', lot.stock_lot_id.id), ('location_id.usage', '=', 'internal'),
                    ('location_id', 'not in', (places['thawed'].id, places['quarantine'].id))]).mapped('reserved_quantity'))
            lot.straws_frozen = int(totals['frozen'])
            lot.straws_quarantine = int(totals['quarantine'])
            lot.straws_thawed = int(totals['thawed'])
            lot.straws_used = int(totals['used'])
            lot.straws_wasted = int(totals['wasted'])
            lot.straws_out = int(totals['out'])
            lot.straws_reserved = int(reserved)
            # What came from outside stock (the load itself and count adjustments): the load is straws_produced.
            brought_in = -int(totals['source'])
            lot.straws_adjusted = (brought_in - lot.straws_produced) if lot.stock_lot_id else 0
            lot.straws_available = max(lot.straws_frozen - lot.straws_reserved, 0)
            ratio = lot.straws_per_dose or 0
            lot.doses_available = floor(lot.straws_available / ratio) if ratio else 0
            lot.straws_remainder = lot.straws_available - lot.doses_available * ratio if ratio else lot.straws_available
            on_hand = lot.straws_frozen + lot.straws_quarantine + lot.straws_thawed
            outputs = lot.straws_used + lot.straws_wasted + lot.straws_out
            lot.balance_ok = (not lot.stock_lot_id) or (lot.straws_produced + lot.straws_adjusted == on_hand + outputs)

    def _compute_outcomes(self):
        for lot in self:
            services = self.env['alfia.breeding.service'].sudo().search(
                [('semen_lot_id', '=', lot.stock_lot_id.id), ('state', '=', 'done')]) if lot.stock_lot_id else self.env['alfia.breeding.service']
            pregnancies = services.pregnancy_ids
            lot.service_count = len(services)
            lot.pregnancy_count = len(pregnancies.filtered(lambda p: p.state in ('pregnant', 'foaled')))
            lot.foal_count = len(pregnancies.foal_id) if 'foal_id' in pregnancies._fields else 0

    # -- quality and loading -------------------------------------------------------------------------------------------
    def action_qc_pass(self):
        _require(self.env, (VET_USER,), self.env._("veterinary staff"))
        for lot in self:
            company = lot.company_id or self.env.company
            if not lot.post_thaw_test_date or not lot.post_thaw_motility_pct:
                raise UserError(self.env._("Record the thaw test (date and motility after thaw) before passing %s.", lot.name))
            if company.alfia_semen_min_motility and lot.post_thaw_motility_pct < company.alfia_semen_min_motility:
                raise UserError(self.env._("%(lot)s: motility after thaw %(got)s%% is below the stud's minimum of %(min)s%%.",
                                           lot=lot.name, got=lot.post_thaw_motility_pct, min=company.alfia_semen_min_motility))
            if company.alfia_semen_min_progressive and lot.post_thaw_progressive_pct < company.alfia_semen_min_progressive:
                raise UserError(self.env._("%(lot)s: progressive motility after thaw %(got)s%% is below the stud's minimum of %(min)s%%.",
                                           lot=lot.name, got=lot.post_thaw_progressive_pct, min=company.alfia_semen_min_progressive))
            with workflow():
                lot.write({'qc_state': 'passed', 'qc_by_id': self.env.user.id})

    def action_qc_reject(self):
        _require(self.env, (VET_USER,), self.env._("veterinary staff"))
        for lot in self:
            if not lot.notes:
                raise UserError(self.env._("Write what was found in the notes before rejecting %s.", lot.name))
            with workflow():
                lot.write({'qc_state': 'rejected', 'qc_by_id': self.env.user.id})

    def action_approve_load(self):
        """Approve the lot and load its straws: a stock batch is created and the straws enter the chosen storage."""
        _require(self.env, (VET_USER,), self.env._("veterinary staff"))
        straw = self.env.ref('alfia_registry.product_semen_straw')
        for lot in self:
            if lot.state != 'draft':
                raise UserError(self.env._("%s has already been loaded or cancelled.", lot.name))
            if lot.collection_id.state != 'released':
                raise UserError(self.env._("Release the collection %s first.", lot.collection_id.name))
            company = lot.company_id or self.env.company
            if lot.qc_state == 'rejected' or (company.alfia_semen_qc_gate and lot.qc_state != 'passed'):
                raise UserError(self.env._("%s must pass its thaw test before it is loaded.", lot.name))
            if not lot.load_location_id:
                raise UserError(self.env._("Choose where the straws are loaded (tank, canister, cane or goblet)."))
            if lot.create_uid == self.env.user and lot._alfia_sod_applies(None, company):
                raise UserError(self.env._("You prepared %s, so another vet approves and loads it.", lot.name))
            self.env.user._alfia_check_location(lot.load_location_id, self.env._("semen storage"))
            stock_lot = self.env['stock.lot'].sudo().create({'name': lot.name, 'product_id': straw.id, 'company_id': company.id})
            source = self.env['stock.location'].sudo().search([
                ('usage', '=', 'inventory'), ('company_id', 'in', (False, company.id)),
                ('id', 'not in', (self.env.ref('alfia_equine.stock_location_semen_used').id, self._alfia_place('destroyed').id))], limit=1)
            lot._alfia_move(stock_lot, lot.straws_produced, source, lot.load_location_id, self.env._("Load %s", lot.name))
            with workflow():
                lot.write({'state': 'loaded', 'stock_lot_id': stock_lot.id, 'approved_by_id': self.env.user.id})
            lot.message_post(body=self.env._("Approved by %(user)s: %(n)s straws loaded into %(where)s (%(doses)s doses of %(ratio)s straws).",
                                             user=self.env.user.name, n=lot.straws_produced, where=lot.load_location_id.complete_name,
                                             doses=lot.straws_produced // lot.straws_per_dose, ratio=lot.straws_per_dose))
            lot.load_location_id._alfia_warn_capacity(lot)

    def action_cancel(self):
        for lot in self:
            if lot.state != 'draft':
                raise UserError(self.env._("A loaded freeze lot is not cancelled: count, waste or transfer its straws instead."))
            with workflow():
                lot.state = 'cancelled'

    @api.model
    def _alfia_move(self, stock_lot, qty, source, dest, origin):
        """One done stock move of `qty` straws of `stock_lot` (append-only: corrections are new moves)."""
        product = stock_lot.product_id
        company = stock_lot.company_id.id or self.env.company.id
        move = self.env['stock.move'].sudo().create({
            'product_id': product.id, 'product_uom_qty': qty, 'uom_id': product.uom_id.id, 'company_id': company,
            'location_id': source.id, 'location_dest_id': dest.id, 'origin': origin, 'picked': True,
            'move_line_ids': [Command.create({'product_id': product.id, 'lot_id': stock_lot.id, 'quantity': qty,
                                              'uom_id': product.uom_id.id, 'location_id': source.id, 'location_dest_id': dest.id,
                                              'company_id': company, 'picked': True})]})
        move._action_confirm()
        move._action_done()
        return move

    def _alfia_free_quants(self, qty):
        """Frozen storage holding enough unreserved straws of this lot, fullest first."""
        self.ensure_one()
        skip = (self._alfia_place('thawed', self.company_id).id, self._alfia_place('quarantine', self.company_id).id)
        quants = self.env['stock.quant'].sudo().search([('lot_id', '=', self.stock_lot_id.id), ('location_id.usage', '=', 'internal'),
                                                        ('location_id', 'not in', skip), ('quantity', '>', 0)], order='quantity desc')
        quant = next((q for q in quants if q.quantity - q.reserved_quantity >= qty), False)
        if not quant:
            raise UserError(self.env._("%(lot)s has no storage place with %(n)s free straws (reserved straws are not used).",
                                       lot=self.name, n=qty))
        return quant

    def action_open_ratio_correction(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': 'alfia.semen.ratio.correction', 'view_mode': 'form', 'target': 'new',
                'context': {'default_freeze_lot_id': self.id, 'default_new_straws_per_dose': self.straws_per_dose}}

    def action_open_waste(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': 'alfia.semen.waste', 'view_mode': 'form', 'target': 'new',
                'context': {'default_freeze_lot_id': self.id}}


class StockLocationCapacity(models.Model):
    _inherit = 'stock.location'

    def _alfia_warn_capacity(self, record):
        for loc in self:
            loc.invalidate_recordset(['alfia_straws_here', 'alfia_over_capacity'])
            if loc.alfia_over_capacity:
                record.message_post(body=self.env._("Warning: %(where)s now holds %(n)s straws, above its capacity of %(cap)s.",
                                                    where=loc.complete_name, n=loc.alfia_straws_here, cap=loc.alfia_capacity_straws))


class StockMoveCapacity(models.Model):
    _inherit = 'stock.move'

    def _action_done(self, cancel_backorder=False):
        done = super()._action_done(cancel_backorder=cancel_backorder)
        straw = self.env.ref('alfia_registry.product_semen_straw', raise_if_not_found=False)
        if straw:
            for move in done.filtered(lambda m: m.product_id == straw and m.location_dest_id.alfia_capacity_straws and m.picking_id):
                move.location_dest_id._alfia_warn_capacity(move.picking_id)
        return done


class AlfiaSemenRatioVersion(models.Model):
    _name = 'alfia.semen.ratio.version'
    _description = 'Straws per dose correction'
    _order = 'id desc'

    freeze_lot_id = fields.Many2one('alfia.semen.freeze.lot', required=True, ondelete='cascade', index=True)
    old_value = fields.Integer(string='Was', readonly=True)
    new_value = fields.Integer(string='Now', readonly=True)
    reason = fields.Char(readonly=True)
    user_id = fields.Many2one('res.users', string='Approved by', readonly=True)
    effective_date = fields.Datetime(string='From', readonly=True)


class AlfiaSemenRatioCorrection(models.TransientModel):
    _name = 'alfia.semen.ratio.correction'
    _description = 'Correct straws per dose'

    freeze_lot_id = fields.Many2one('alfia.semen.freeze.lot', required=True, readonly=True)
    new_straws_per_dose = fields.Integer(string='Straws per dose', required=True)
    reason = fields.Char(required=True)

    def action_confirm(self):
        self.ensure_one()
        _require(self.env, (VET_MANAGER,), self.env._("the vet manager"))
        lot = self.freeze_lot_id
        if self.new_straws_per_dose <= 0 or self.new_straws_per_dose == lot.straws_per_dose:
            raise UserError(self.env._("Enter a different number of straws per dose, above zero."))
        self.env['alfia.semen.ratio.version'].sudo().create({
            'freeze_lot_id': lot.id, 'old_value': lot.straws_per_dose, 'new_value': self.new_straws_per_dose, 'reason': self.reason,
            'user_id': self.env.user.id, 'effective_date': fields.Datetime.now()})
        old = lot.straws_per_dose
        lot.sudo().with_context(alfia_ratio_correction=True).straws_per_dose = self.new_straws_per_dose
        lot.message_post(body=self.env._("Straws per dose corrected by %(user)s: %(old)s → %(new)s. Reason: %(reason)s. Earlier "
                                         "reservations and uses keep the straws recorded at the time.",
                                         user=self.env.user.name, old=old, new=self.new_straws_per_dose, reason=self.reason))
        return {'type': 'ir.actions.act_window_close'}


class AlfiaSemenWaste(models.TransientModel):
    """Straws that will not be used (thawed and not used, damaged...) leave stock for 'Destroyed' with a reason (D3)."""
    _name = 'alfia.semen.waste'
    _description = 'Record wasted straws'

    freeze_lot_id = fields.Many2one('alfia.semen.freeze.lot', required=True)
    location_id = fields.Many2one('stock.location', string='From', domain="[('usage', '=', 'internal')]",
                                  default=lambda self: self.env['alfia.semen.freeze.lot']._alfia_place('thawed'))
    straws = fields.Integer(required=True, default=1)
    reason = fields.Char(required=True)

    def action_confirm(self):
        self.ensure_one()
        _require(self.env, (CUSTODIAN, VET_MANAGER), self.env._("the semen custodian or the vet manager"))
        lot = self.freeze_lot_id
        if self.straws <= 0:
            raise UserError(self.env._("Enter how many straws, above zero."))
        here = sum(self.env['stock.quant'].sudo().search([('lot_id', '=', lot.stock_lot_id.id),
                                                          ('location_id', '=', self.location_id.id)]).mapped('quantity'))
        if here < self.straws:
            raise UserError(self.env._("%(where)s holds %(n)s straws of %(lot)s.", where=self.location_id.complete_name, n=int(here), lot=lot.name))
        lot._alfia_move(lot.stock_lot_id, self.straws, self.location_id, lot._alfia_place('destroyed'),
                        self.env._("Waste %(lot)s: %(reason)s", lot=lot.name, reason=self.reason))
        lot.message_post(body=self.env._("%(n)s straws recorded as wasted by %(user)s from %(where)s. Reason: %(reason)s",
                                         n=self.straws, user=self.env.user.name, where=self.location_id.complete_name, reason=self.reason))
        return {'type': 'ir.actions.act_window_close'}


class AlfiaSemenCollection(models.Model):
    _inherit = 'alfia.semen.collection'

    volume_ml = fields.Float(string='Volume at collection (ml)')
    concentration = fields.Float(string='Concentration at collection (M/ml)')
    motility = fields.Float(string='Motility at collection (%)')
    morphology = fields.Float(string='Normal morphology at collection (%)')
    freeze_lot_ids = fields.One2many('alfia.semen.freeze.lot', 'collection_id', string='Freeze lots')
    straw_count_known = fields.Boolean(compute='_compute_straw_count_known', string='Straws recorded',
                                       help="Collections released before straws were recorded keep their doses; their straw count is unknown.")

    def _compute_straw_count_known(self):
        for rec in self:
            rec.straw_count_known = bool(rec.freeze_lot_ids)

    def action_release(self):
        """A collection frozen into freeze lots is released without dose stock (Doses produced = 0): its straws enter stock
        when each freeze lot is approved and loaded. Collections with doses keep the earlier release."""
        straws = self.filtered(lambda r: r.state == 'draft' and not r.doses)
        for rec in straws:
            _require(self.env, (VET_USER,), self.env._("veterinary staff"))
            if not (rec.concentration > 0 and rec.motility > 0):
                raise UserError(self.env._("Enter concentration and motility before the collection can be released."))
            with workflow():
                rec.write({'state': 'released', 'released_by_id': self.env.user.id})
            rec.message_post(body=self.env._("Released by %s for freezing: its straws enter stock with each approved freeze lot.",
                                             self.env.user.name))
        others = self - straws
        return super(AlfiaSemenCollection, others).action_release() if others else True


class AlfiaBreedingService(models.Model):
    """A frozen insemination records how many straws it used, linked to the stock move that took them, once (R6); the stallion
    is checked against the batch (D11); thawing, cancelling and correcting follow section 8c of the spec (D13)."""
    _inherit = 'alfia.breeding.service'
    _alfia_guarded = ('state', 'stock_move_id', 'thaw_move_id', 'reversal_move_id', 'corrected_by_id')

    freeze_lot_id = fields.Many2one('alfia.semen.freeze.lot', string='Freeze lot', compute='_compute_freeze_lot', store=True)
    straws_used = fields.Integer(string='Straws used', help="Straws taken for this service. Default: one dose of the lot.")
    doses_used = fields.Float(string='Doses used', compute='_compute_doses_used', store=True, digits=(16, 2))
    stock_move_id = fields.Many2one('stock.move', string='Stock move (use)', readonly=True, copy=False)
    thaw_move_id = fields.Many2one('stock.move', string='Stock move (thaw)', readonly=True, copy=False)
    thaw_datetime = fields.Datetime(string='Thawed at', readonly=True, copy=False)
    mismatch_reason = fields.Char(string='Reason the stallion differs from the batch',
                                  help="Needed when the service's stallion is not the stallion of the semen batch.")
    external_stallion_owner_id = fields.Many2one('res.partner', string='External stallion: centre or owner')
    reversal_move_id = fields.Many2one('stock.move', string='Correction move', readonly=True, copy=False)
    corrected_by_id = fields.Many2one('res.users', string='Corrected by', readonly=True, copy=False)
    correction_reason = fields.Char(readonly=True, copy=False)

    @api.depends('semen_lot_id')
    def _compute_freeze_lot(self):
        FreezeLot = self.env['alfia.semen.freeze.lot'].sudo()
        for rec in self:
            rec.freeze_lot_id = FreezeLot.search([('stock_lot_id', '=', rec.semen_lot_id.id)], limit=1) if rec.semen_lot_id else False

    @api.depends('straws_used', 'freeze_lot_id.straws_per_dose')
    def _compute_doses_used(self):
        for rec in self:
            ratio = rec.freeze_lot_id.straws_per_dose
            rec.doses_used = (rec.straws_used / ratio) if ratio and rec.straws_used else 0.0

    @api.onchange('semen_lot_id')
    def _onchange_alfia_straws(self):
        for rec in self:
            if rec.freeze_lot_id and not rec.straws_used:
                rec.straws_used = rec.freeze_lot_id.straws_per_dose

    def _alfia_batch_stallion(self):
        self.ensure_one()
        if self.freeze_lot_id:
            return self.freeze_lot_id.stallion_id
        collection = self.env['alfia.semen.collection'].sudo().search([('lot_id', '=', self.semen_lot_id.id)], limit=1)
        return collection.stallion_id

    def _alfia_check_stallion(self):
        """D11: a service whose stallion differs from the batch's is completed only with a recorded reason; an external
        stallion has a name and its centre or owner."""
        for rec in self:
            if rec.stallion_name and not rec.stallion_id and not rec.external_stallion_owner_id and rec.method != 'natural':
                raise UserError(self.env._("Name the centre or owner of the external stallion %s.", rec.stallion_name))
            if not rec.semen_lot_id:
                continue
            batch = rec._alfia_batch_stallion()
            if batch and batch != rec.stallion_id and not (rec.mismatch_reason or '').strip():
                raise UserError(self.env._(
                    "The semen batch %(lot)s is from %(batch)s, but the service names %(service)s. Correct the stallion, or record "
                    "the reason they differ.", lot=rec.semen_lot_id.name, batch=batch.name,
                    service=rec.stallion_id.name or rec.stallion_name or self.env._('no stallion')))

    def action_done(self):
        self._alfia_check_stallion()
        for rec in self.filtered(lambda r: r.method == 'ai_frozen' and r.freeze_lot_id and r.state == 'planned'):
            if not rec.straws_used:
                rec.straws_used = rec.freeze_lot_id.straws_per_dose
            if rec.straws_used <= 0:
                raise UserError(self.env._("Enter the straws used for this service."))
        res = super().action_done()
        for rec in self.filtered(lambda r: r.mismatch_reason):
            rec.message_post(body=self.env._("Completed with a stallion that differs from the batch. Reason: %s", rec.mismatch_reason))
        return res

    def _alfia_use_dose(self):
        """Take the straws once: a service that already has its stock move takes nothing again. A freeze-lot batch gives the
        explicit straws (from the thawed straws if they were thawed); an earlier dose batch gives one dose, as before."""
        self.ensure_one()
        if self.stock_move_id:
            return
        used = self.env.ref('alfia_equine.stock_location_semen_used')
        origin = self.env._("Service of %(mare)s on %(date)s", mare=self.mare_id.name, date=self.date)
        lot = self.freeze_lot_id
        if lot:
            if self.thaw_move_id:
                source = lot._alfia_place('thawed', lot.company_id)
            else:
                source = lot._alfia_free_quants(self.straws_used).location_id
            move = lot._alfia_move(lot.stock_lot_id, self.straws_used, source, used, origin)
            with workflow():
                self.stock_move_id = move
            self.message_post(body=self.env._("%(n)s straws of %(lot)s taken from %(where)s for this service (%(doses)s doses).",
                                              n=self.straws_used, lot=lot.name, where=source.complete_name, doses=round(self.doses_used, 2)))
            return
        Line = self.env['stock.move.line'].sudo()
        domain = [('lot_id', '=', self.semen_lot_id.id), ('location_dest_id', '=', used.id)]
        before = Line.search(domain).ids
        super()._alfia_use_dose()
        new = Line.search(domain + [('id', 'not in', before)], limit=1).move_id
        with workflow():
            self.write({'stock_move_id': new.id})

    def action_thaw(self):
        """Pull the straws for a planned frozen service: they move to 'thawed, in use' and never return to frozen stock."""
        _require(self.env, (CUSTODIAN, VET_USER), self.env._("the semen custodian or veterinary staff"))
        for rec in self:
            lot = rec.freeze_lot_id
            if rec.state != 'planned' or rec.method != 'ai_frozen' or not lot:
                raise UserError(self.env._("Only a planned frozen service with a freeze-lot batch is thawed."))
            if rec.thaw_move_id:
                raise UserError(self.env._("The straws for this service were already thawed."))
            rec._alfia_check_stallion()
            straws = rec.straws_used or lot.straws_per_dose
            source = lot._alfia_free_quants(straws).location_id
            move = lot._alfia_move(lot.stock_lot_id, straws, source, lot._alfia_place('thawed', lot.company_id),
                                   self.env._("Thaw for %s", rec.mare_id.name))
            # The custodian thaws on the service's behalf (role checked above) without editing other service fields.
            with workflow():
                rec.sudo().write({'straws_used': straws, 'thaw_move_id': move.id, 'thaw_datetime': fields.Datetime.now()})
            rec.sudo().message_post(body=self.env._("%(n)s straws of %(lot)s thawed by %(user)s.", n=straws, lot=lot.name,
                                             user=self.env.user.name))

    def action_cancel(self):
        thawed = self.filtered(lambda r: r.thaw_move_id and not r.stock_move_id and r.state == 'planned')
        res = super().action_cancel()
        for rec in thawed:
            rec.message_post(body=self.env._("Cancelled after thawing: the %(n)s thawed straws do not return to frozen stock. Record "
                                             "them as used or wasted (with a reason) on the freeze lot.", n=rec.straws_used))
        return res

    def action_open_correction(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': 'alfia.service.correction', 'view_mode': 'form', 'target': 'new',
                'context': {'default_service_id': self.id}}

    def write(self, vals):
        if {'straws_used', 'semen_lot_id'} & set(vals) and not self.env.su and any(r.stock_move_id or r.thaw_move_id for r in self):
            raise UserError(self.env._("The straws were already taken for this service; a mistake is corrected by the vet manager."))
        return super().write(vals)


class AlfiaServiceCorrection(models.TransientModel):
    """Section 8c case 2: a service recorded as done by mistake, its straws never pulled. The vet manager, with a reason
    and the explicit confirmation, puts the straws back with a reverse move linked to the original."""
    _name = 'alfia.service.correction'
    _description = 'Correct a service completed by mistake'

    service_id = fields.Many2one('alfia.breeding.service', required=True, readonly=True)
    reason = fields.Char(required=True)
    not_pulled = fields.Boolean(string='The straws were not taken out of the tank')

    def action_confirm(self):
        self.ensure_one()
        _require(self.env, (VET_MANAGER,), self.env._("the vet manager"))
        service = self.service_id
        if not self.not_pulled:
            raise UserError(self.env._("Confirm that the straws were not taken out of the tank."))
        if service.state != 'done' or not service.stock_move_id or service.reversal_move_id:
            raise UserError(self.env._("Only a completed service whose straws were taken, and not yet corrected, can be corrected."))
        if service.thaw_move_id:
            raise UserError(self.env._("These straws were thawed: thawed straws never return to frozen stock. Record them as used "
                                       "or wasted instead."))
        original = service.stock_move_id.sudo()
        line = original.move_line_ids[:1]
        lot = service.freeze_lot_id
        reverse = self.env['alfia.semen.freeze.lot']._alfia_move(
            line.lot_id, line.quantity, original.location_dest_id, line.location_id,
            self.env._("Correction of %(move)s: %(reason)s", move=original.reference or original.origin or original.id, reason=self.reason))
        with workflow():
            service.write({'reversal_move_id': reverse.id, 'corrected_by_id': self.env.user.id, 'correction_reason': self.reason,
                           'state': 'cancelled'})
        service.message_post(body=self.env._("Corrected by %(user)s: completed by mistake, straws not pulled. %(n)s straws back to "
                                             "%(where)s. Reason: %(reason)s", user=self.env.user.name, n=line.quantity,
                                             where=line.location_id.complete_name, reason=self.reason))
        if lot:
            lot.message_post(body=self.env._("Service of %(mare)s corrected: %(n)s straws back to storage.", mare=service.mare_id.name,
                                             n=line.quantity))
        return {'type': 'ir.actions.act_window_close'}


class AlfiaSemenTankAudit(models.Model):
    """A physical count of a tank: lines per storage place and batch, expected from stock, counted by the custodian; on
    confirmation the differences become stock adjustments with the count's reference, by and when (R8)."""
    _name = 'alfia.semen.tank.audit'
    _description = 'Semen tank count'
    _inherit = ['mail.thread', 'alfia.guarded.mixin']
    _order = 'date desc, id desc'
    _alfia_guarded = ('state', 'confirmed_by_id')

    name = fields.Char(readonly=True, copy=False, default=lambda self: self.env._('New'))
    tank_id = fields.Many2one('stock.location', string='Tank', required=True, domain="[('usage', '=', 'internal')]")
    date = fields.Date(required=True, default=fields.Date.context_today)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)
    line_ids = fields.One2many('alfia.semen.tank.audit.line', 'audit_id', string='Count')
    state = fields.Selection([('draft', 'Counting'), ('done', 'Confirmed')], default='draft', required=True, tracking=True)
    confirmed_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    notes = fields.Text()

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            vals['name'] = self.env['ir.sequence'].sudo().next_by_code('alfia.semen.tank.audit') or self.env._('Tank count')
        return super().create(vals_list)

    def action_load_lines(self):
        _require(self.env, (CUSTODIAN, VET_MANAGER), self.env._("the semen custodian or the vet manager"))
        semen = self.env.ref('alfia_equine.category_semen')
        for audit in self.filtered(lambda a: a.state == 'draft'):
            quants = self.env['stock.quant'].sudo().search([
                ('location_id', 'child_of', audit.tank_id.id), ('product_id.categ_id', 'child_of', semen.id), ('quantity', '!=', 0)])
            present = {(l.location_id.id, l.lot_id.id) for l in audit.line_ids}
            audit.line_ids = [Command.create({'location_id': q.location_id.id, 'lot_id': q.lot_id.id, 'product_id': q.product_id.id,
                                              'expected': q.quantity, 'counted': q.quantity})
                              for q in quants if (q.location_id.id, q.lot_id.id) not in present]

    def action_confirm(self):
        _require(self.env, (CUSTODIAN, VET_MANAGER), self.env._("the semen custodian or the vet manager"))
        for audit in self.filtered(lambda a: a.state == 'draft'):
            if not audit.line_ids:
                raise UserError(self.env._("Load the tank's contents and enter what was counted first."))
            reason = self.env._("Tank count %(name)s by %(user)s", name=audit.name, user=self.env.user.name)
            for line in audit.line_ids.filtered(lambda l: l.difference):
                quant = self.env['stock.quant'].sudo().with_context(inventory_mode=True).search([
                    ('location_id', '=', line.location_id.id), ('lot_id', '=', line.lot_id.id), ('product_id', '=', line.product_id.id)], limit=1)
                if not quant:
                    quant = self.env['stock.quant'].sudo().with_context(inventory_mode=True).create({
                        'location_id': line.location_id.id, 'lot_id': line.lot_id.id, 'product_id': line.product_id.id})
                quant.with_context(inventory_mode=True).inventory_quantity = line.counted
                with trusted('alfia_tank_audit'):
                    quant.with_context(inventory_name=reason)._apply_inventory()
            (audit.tank_id | audit.line_ids.location_id).sudo().write({'alfia_last_audit_date': audit.date})
            with workflow():
                audit.write({'state': 'done', 'confirmed_by_id': self.env.user.id})
            diffs = audit.line_ids.filtered('difference')
            audit.message_post(body=self.env._("Confirmed by %(user)s: %(n)s place(s) adjusted (%(total)s straws or doses in total).",
                                               user=self.env.user.name, n=len(diffs), total=sum(diffs.mapped('difference'))))


class AlfiaSemenTankAuditLine(models.Model):
    _name = 'alfia.semen.tank.audit.line'
    _description = 'Semen tank count line'

    audit_id = fields.Many2one('alfia.semen.tank.audit', required=True, ondelete='cascade', index=True)
    location_id = fields.Many2one('stock.location', string='Place', required=True)
    lot_id = fields.Many2one('stock.lot', string='Batch')
    product_id = fields.Many2one('product.product', required=True)
    expected = fields.Float(readonly=True)
    counted = fields.Float()
    difference = fields.Float(compute='_compute_difference', store=True)

    @api.depends('expected', 'counted')
    def _compute_difference(self):
        for line in self:
            line.difference = line.counted - line.expected

    def write(self, vals):
        if any(l.audit_id.state != 'draft' for l in self) and not self.env.su:
            raise UserError(self.env._("A confirmed count cannot be changed."))
        return super().write(vals)


class AlfiaSemenTankLevel(models.Model):
    _name = 'alfia.semen.tank.level'
    _description = 'Tank nitrogen reading'
    _order = 'reading_datetime desc, id desc'

    tank_id = fields.Many2one('stock.location', string='Tank', required=True, domain="[('usage', '=', 'internal')]")
    reading_datetime = fields.Datetime(string='When', required=True, default=fields.Datetime.now)
    level_cm = fields.Float(string='Nitrogen level (cm)')
    refilled = fields.Boolean(string='Refilled')
    litres_added = fields.Float(string='Litres added')
    user_id = fields.Many2one('res.users', string='By', default=lambda self: self.env.user, readonly=True)
    notes = fields.Char()

    @api.constrains('level_cm', 'litres_added')
    def _check_values(self):
        for rec in self:
            if rec.level_cm < 0 or rec.litres_added < 0:
                raise ValidationError(self.env._("Levels and litres cannot be negative."))


class SaleOrderLine(models.Model):
    """Selling frozen semen by the dose: choose the freeze lot and the doses; the quantity is that lot's straws per dose
    times the doses, stored in straws when the line is saved."""
    _inherit = 'sale.order.line'

    alfia_freeze_lot_id = fields.Many2one('alfia.semen.freeze.lot', string='Freeze lot', domain="[('state', '=', 'loaded')]")
    alfia_doses = fields.Integer(string='Doses')

    @api.onchange('alfia_freeze_lot_id', 'alfia_doses')
    def _onchange_alfia_doses(self):
        for line in self:
            if line.alfia_freeze_lot_id and line.alfia_doses:
                line.product_id = self.env.ref('alfia_registry.product_semen_straw')
                line.product_uom_qty = line.alfia_doses * line.alfia_freeze_lot_id.straws_per_dose

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._alfia_doses_to_straws(vals)
        return super().create(vals_list)

    def write(self, vals):
        if {'alfia_doses', 'alfia_freeze_lot_id'} & set(vals):
            for line in self:
                line_vals = dict(vals)
                merged = self._alfia_doses_to_straws({'alfia_freeze_lot_id': line.alfia_freeze_lot_id.id,
                                                      'alfia_doses': line.alfia_doses, **vals})
                for key in ('product_id', 'product_uom_qty'):
                    if key in merged:
                        line_vals[key] = merged[key]
                super(SaleOrderLine, line).write(line_vals)
            return True
        return super().write(vals)

    def _alfia_doses_to_straws(self, vals):
        lot = self.env['alfia.semen.freeze.lot'].sudo().browse(vals.get('alfia_freeze_lot_id'))
        if lot and vals.get('alfia_doses'):
            if vals['alfia_doses'] < 0:
                raise ValidationError(self.env._("Doses cannot be negative."))
            vals['product_id'] = self.env.ref('alfia_registry.product_semen_straw').id
            vals['product_uom_qty'] = vals['alfia_doses'] * lot.straws_per_dose
        return vals


class AlfiaSemenStockReport(models.Model):
    """Straws per stallion, freeze lot, owner and state, from done stock moves: frozen in storage, in quarantine, thawed,
    used in services, wasted, delivered out, and loaded or counted in (R9). Rows always add up to zero per lot, so the
    sum of the states equals what was loaded and counted in."""
    _name = 'alfia.semen.stock.report'
    _description = 'Semen straws by stallion and lot'
    _auto = False

    freeze_lot_id = fields.Many2one('alfia.semen.freeze.lot', readonly=True)
    stallion_id = fields.Many2one('alfia.horse', readonly=True)
    owner_partner_id = fields.Many2one('res.partner', string='Owner', readonly=True)
    location_id = fields.Many2one('stock.location', string='Place', readonly=True)
    bucket = fields.Selection([('frozen', 'Frozen in storage'), ('quarantine', 'Quarantine'), ('thawed', 'Thawed, in use'),
                               ('used', 'Used in services'), ('wasted', 'Wasted'), ('out', 'Delivered out'),
                               ('source', 'Loaded or counted in')], string='State', readonly=True)
    straws = fields.Float(readonly=True)

    def init(self):
        from odoo import tools
        tools.drop_view_if_exists(self.env.cr, self._table)
        used = self.env.ref('alfia_equine.stock_location_semen_used', raise_if_not_found=False)
        destroyed = self.env.ref('alfia_registry.stock_location_semen_destroyed', raise_if_not_found=False)
        self.env.cr.execute(f"""
            CREATE OR REPLACE VIEW {self._table} AS (
                WITH legs AS (
                    SELECT f.id AS freeze_lot_id, f.stallion_id, f.owner_partner_id, ml.location_dest_id AS location_id,
                           ml.quantity AS straws, ml.id * 2 AS leg
                      FROM stock_move_line ml JOIN alfia_semen_freeze_lot f ON f.stock_lot_id = ml.lot_id
                     WHERE ml.state = 'done'
                    UNION ALL
                    SELECT f.id, f.stallion_id, f.owner_partner_id, ml.location_id, -ml.quantity, ml.id * 2 + 1
                      FROM stock_move_line ml JOIN alfia_semen_freeze_lot f ON f.stock_lot_id = ml.lot_id
                     WHERE ml.state = 'done'
                )
                SELECT legs.leg AS id, legs.freeze_lot_id, legs.stallion_id, legs.owner_partner_id, legs.location_id,
                       CASE WHEN legs.location_id = %(used)s THEN 'used'
                            WHEN legs.location_id = %(destroyed)s THEN 'wasted'
                            WHEN l.usage = 'internal' AND l.name::text ILIKE '%%Semen thawed%%' THEN 'thawed'
                            WHEN l.usage = 'internal' AND l.name::text ILIKE '%%Semen quarantine%%' THEN 'quarantine'
                            WHEN l.usage = 'internal' THEN 'frozen'
                            WHEN l.usage = 'inventory' THEN 'source'
                            ELSE 'out' END AS bucket,
                       legs.straws
                  FROM legs JOIN stock_location l ON l.id = legs.location_id
            )""", {'used': used.id if used else 0, 'destroyed': destroyed.id if destroyed else 0})
