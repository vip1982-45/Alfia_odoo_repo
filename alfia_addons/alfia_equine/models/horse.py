from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

MAX_GENERATIONS = 12
KINSHIP_GENERATIONS = 30


class AlfiaHorse(models.Model):
    _name = 'alfia.horse'
    _description = 'Horse'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'name'

    name = fields.Char(required=True, tracking=True)
    alias = fields.Char()
    active = fields.Boolean(default=True, tracking=True)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company, required=True)
    image_1920 = fields.Image()
    reg_id = fields.Char(string='Registration / studbook ID', copy=False, tracking=True)
    number = fields.Char(copy=False)
    microchip = fields.Char(copy=False, tracking=True)
    passport = fields.Char(string='Passport no.', copy=False)
    ueln = fields.Char(string='UELN', copy=False)
    fei = fields.Char(string='FEI no.', copy=False)
    sex = fields.Selection([('female', 'Female'), ('male', 'Male'), ('gelding', 'Gelding')], required=True, tracking=True)
    birthdate = fields.Date(tracking=True)
    age_text = fields.Char(compute='_compute_age_text')
    breed = fields.Char()
    color = fields.Char()
    category = fields.Char()
    association = fields.Char()
    markings = fields.Text()
    comments = fields.Text()
    insurance_policy = fields.Char()
    insurance_contact = fields.Char()

    sire_id = fields.Many2one('alfia.horse', string='Sire', domain="[('sex', '=', 'male')]", index=True, tracking=True)
    dam_id = fields.Many2one('alfia.horse', string='Genetic dam', domain="[('sex', '=', 'female')]", index=True, tracking=True)
    carrier_mare_id = fields.Many2one('alfia.horse', string='Carrier (recipient) mare', domain="[('sex', '=', 'female')]",
                                      help="The mare that carried the foal when it was born from a transferred embryo.")
    sire_name = fields.Char(string='Sire (name, if not in the system)')
    dam_name = fields.Char(string='Dam (name, if not in the system)')
    breeder_id = fields.Many2one('res.partner', string='Breeder')
    rider_id = fields.Many2one('res.partner', string='Rider')
    vet_id = fields.Many2one('res.partner', string='Vet')
    invoice_to_id = fields.Many2one('res.partner', string='Invoice to')

    status = fields.Selection([('active', 'Active'), ('reference', 'Reference only'), ('sold', 'Sold'), ('gifted', 'Gifted'),
                               ('dead', 'Dead'), ('retired', 'Retired')], default='active', required=True, tracking=True)
    status_log_ids = fields.One2many('alfia.horse.status.log', 'horse_id', string='Status history')
    ownership_ids = fields.One2many('alfia.horse.ownership', 'horse_id', string='Ownership history')
    owner_ids = fields.Many2many('res.partner', compute='_compute_owner_ids', string='Current owners')
    movement_ids = fields.One2many('alfia.horse.movement', 'horse_id', string='Movements')
    location_id = fields.Many2one('stock.location', compute='_compute_location', store=True, index=True, string='Current location')
    on_site = fields.Boolean(compute='_compute_location', store=True)
    initial_location_id = fields.Many2one('stock.location', string='Arrival location', store=False,
                                          help="Where the horse is placed when the record is created (records its arrival).")

    inbreeding = fields.Float(compute='_compute_inbreeding', string='Inbreeding coefficient (%)', digits=(6, 2))
    offspring_count = fields.Integer(compute='_compute_counts')
    sibling_count = fields.Integer(compute='_compute_counts')
    pedigree_html = fields.Html(compute='_compute_pedigree_html', sanitize=False)

    _microchip_uniq = models.Constraint(
        "unique(microchip, company_id)", "Another horse already has this microchip number. Resolve the duplicate (Merge) instead of creating a second record.")

    # ------------------------------------------------------------------ computes
    @api.depends('birthdate')
    def _compute_age_text(self):
        today = fields.Date.context_today(self)
        for horse in self:
            if not horse.birthdate or horse.birthdate > today:
                horse.age_text = False
                continue
            delta = relativedelta(today, horse.birthdate)
            horse.age_text = f"{delta.years}Y {delta.months}M {delta.days}D"

    @api.depends('ownership_ids.date_from', 'ownership_ids.date_to', 'ownership_ids.partner_id')
    def _compute_owner_ids(self):
        today = fields.Date.context_today(self)
        for horse in self:
            current = horse.ownership_ids.filtered(lambda o: o.date_from <= today and (not o.date_to or o.date_to >= today))
            horse.owner_ids = current.partner_id

    @api.depends('movement_ids.state', 'movement_ids.date', 'movement_ids.kind', 'movement_ids.location_to_id')
    def _compute_location(self):
        for horse in self:
            done = horse.movement_ids.filtered(lambda m: m.state == 'done').sorted(key=lambda m: (m.date, m.id))
            last = done[-1:] if done else done
            horse.on_site = bool(last) and last.kind != 'departure'
            horse.location_id = last.location_to_id if horse.on_site else False

    def _compute_counts(self):
        for horse in self:
            horse.offspring_count = self.search_count(['|', ('sire_id', '=', horse.id), ('dam_id', '=', horse.id)])
            horse.sibling_count = len(self._siblings(horse))

    @api.model
    def _siblings(self, horse):
        domain = []
        if horse.sire_id:
            domain.append(('sire_id', '=', horse.sire_id.id))
        if horse.dam_id:
            domain.append(('dam_id', '=', horse.dam_id.id))
        if not domain:
            return self.browse()
        return self.search(['|'] * (len(domain) - 1) + domain + [('id', '!=', horse.id)])

    # ------------------------------------------------------------------ pedigree
    def _compute_inbreeding(self):
        for horse in self:
            horse.inbreeding = round(self._kinship(horse.sire_id, horse.dam_id) * 100, 2)

    def _depth(self, horse, memo, stack=()):
        if not horse:
            return 0
        if horse.id in memo:
            return memo[horse.id]
        if horse.id in stack or len(stack) > KINSHIP_GENERATIONS:
            return 0
        depth = 1 + max(self._depth(horse.sire_id, memo, stack + (horse.id,)), self._depth(horse.dam_id, memo, stack + (horse.id,)))
        memo[horse.id] = depth
        return depth

    def _kinship(self, a, b, _ctx=None, _depth=0):
        """Wright's kinship coefficient between two horses (the inbreeding of their foal is kinship(sire, dam)).

        Ancestors beyond KINSHIP_GENERATIONS are treated as unrelated founders, which bounds both time and recursion."""
        ctx = _ctx if _ctx is not None else {'k': {}, 'd': {}}
        if not a or not b or _depth > KINSHIP_GENERATIONS:
            return 0.0
        key = (min(a.id, b.id), max(a.id, b.id))
        if key in ctx['k']:
            return ctx['k'][key]
        if a == b:
            value = 0.5 * (1 + self._kinship(a.sire_id, a.dam_id, ctx, _depth + 1))
        else:
            first, second = (a, b) if self._depth(a, ctx['d']) >= self._depth(b, ctx['d']) else (b, a)
            value = 0.5 * (self._kinship(first.sire_id, second, ctx, _depth + 1) + self._kinship(first.dam_id, second, ctx, _depth + 1))
        ctx['k'][key] = value
        return value

    def _pedigree_tree(self, generations=3, _level=1):
        self.ensure_one()
        node = {'id': self.id, 'name': self.name, 'reg_id': self.reg_id, 'sex': self.sex}
        if _level < generations:
            node['sire'] = self.sire_id._pedigree_tree(generations, _level + 1) if self.sire_id else (
                {'name': self.sire_name, 'external': True} if self.sire_name else None)
            node['dam'] = self.dam_id._pedigree_tree(generations, _level + 1) if self.dam_id else (
                {'name': self.dam_name, 'external': True} if self.dam_name else None)
        return node

    @api.depends('sire_id', 'dam_id', 'sire_name', 'dam_name', 'name')
    def _compute_pedigree_html(self):
        from markupsafe import Markup, escape
        def label(node):
            return escape(node['name']) if node else Markup('&mdash;')
        for horse in self:
            tree = horse._pedigree_tree(3)
            sire, dam = tree.get('sire') or {}, tree.get('dam') or {}
            ss, sd = sire.get('sire'), sire.get('dam')
            ds, dd = dam.get('sire'), dam.get('dam')
            horse.pedigree_html = Markup(
                '<table class="table table-sm"><tr><td rowspan="4"><b>%s</b></td><td rowspan="2">%s</td><td>%s</td></tr>'
                '<tr><td>%s</td></tr><tr><td rowspan="2">%s</td><td>%s</td></tr><tr><td>%s</td></tr></table>') % (
                escape(horse.name), label(sire), label(ss), label(sd), label(dam), label(ds), label(dd))

    # ------------------------------------------------------------------ constraints
    @api.constrains('sire_id', 'dam_id', 'carrier_mare_id')
    def _check_parents(self):
        for horse in self:
            if horse.sire_id and horse.sire_id.sex != 'male':
                raise ValidationError(self.env._("The sire of %s must be a stallion.", horse.name))
            if horse.dam_id and horse.dam_id.sex != 'female':
                raise ValidationError(self.env._("The dam of %s must be a mare.", horse.name))
            if horse.carrier_mare_id and horse.carrier_mare_id.sex != 'female':
                raise ValidationError(self.env._("The carrier mare of %s must be a mare.", horse.name))
            frontier, seen = [horse.sire_id, horse.dam_id], set()
            for _generation in range(MAX_GENERATIONS * 3):
                nxt = []
                for ancestor in frontier:
                    if not ancestor or ancestor.id in seen:
                        continue
                    if ancestor == horse:
                        raise ValidationError(self.env._("%s cannot be its own ancestor.", horse.name))
                    seen.add(ancestor.id)
                    nxt += [ancestor.sire_id, ancestor.dam_id]
                frontier = nxt
                if not frontier:
                    break

    @api.constrains('birthdate')
    def _check_birthdate(self):
        today = fields.Date.context_today(self)
        for horse in self:
            if horse.birthdate and horse.birthdate > today:
                raise ValidationError(self.env._("The birthdate of %s is in the future.", horse.name))

    # ------------------------------------------------------------------ ORM
    @api.model_create_multi
    def create(self, vals_list):
        initial = [vals.pop('initial_location_id', False) for vals in vals_list]
        horses = super().create(vals_list)
        for horse, location in zip(horses, initial):
            self.env['alfia.horse.status.log'].sudo().create({'horse_id': horse.id, 'status': horse.status, 'reason': self.env._("Record created"), 'user_id': self.env.user.id})
            if location:
                self.env['alfia.horse.movement'].create({
                    'horse_id': horse.id, 'kind': 'arrival', 'location_to_id': location, 'reason': self.env._("Arrival")}).action_done()
        return horses

    def write(self, vals):
        before = {h.id: h.status for h in self} if 'status' in vals else {}
        res = super().write(vals)
        for horse in self:
            if 'status' in vals and before.get(horse.id) != horse.status:
                self.env['alfia.horse.status.log'].sudo().create({'horse_id': horse.id, 'status': horse.status, 'user_id': self.env.user.id})
        return res

    RECORD_MODELS = {
        'alfia.task': 'horse_id', 'alfia.health.record': 'horse_id', 'alfia.vet.visit': 'horse_id', 'alfia.treatment': 'horse_id',
        'alfia.lab.test': 'horse_id', 'alfia.diag.image': 'horse_id', 'alfia.odontology': 'horse_id', 'alfia.farrier.visit': 'horse_id',
        'alfia.measurement': 'horse_id', 'alfia.temperature': 'horse_id', 'alfia.horse.note': 'horse_id', 'alfia.clearance': 'horse_id',
        'alfia.training.session': 'horse_id', 'alfia.competition': 'horse_id', 'alfia.scoring': 'horse_id',
        'alfia.pregnancy': 'mare_id', 'alfia.breeding.service': 'mare_id', 'alfia.teasing': 'mare_id', 'alfia.ovulation': 'mare_id',
        'alfia.reproductive.exam': 'mare_id', 'alfia.semen.collection': 'stallion_id',
    }

    def action_open_records(self):
        """Open the records of one kind for this horse (a whitelisted model name comes from the button's context)."""
        self.ensure_one()
        model = self.env.context.get('records_model')
        field = self.RECORD_MODELS.get(model)
        if not field:
            raise UserError(self.env._("Unknown record type."))
        return {'type': 'ir.actions.act_window', 'name': self.env[model]._description, 'res_model': model, 'view_mode': 'list,form',
                'domain': [(field, '=', self.id)], 'context': {f'default_{field}': self.id}}

    def action_open_merge(self):
        return {'type': 'ir.actions.act_window', 'res_model': 'alfia.horse.merge', 'view_mode': 'form', 'target': 'new',
                'context': {'default_duplicate_id': self.id}}


class AlfiaHorseStatusLog(models.Model):
    _name = 'alfia.horse.status.log'
    _description = 'Horse status history'
    _order = 'date desc, id desc'

    horse_id = fields.Many2one('alfia.horse', required=True, ondelete='cascade', index=True)
    status = fields.Selection(related=False, selection=[('active', 'Active'), ('reference', 'Reference only'), ('sold', 'Sold'), ('gifted', 'Gifted'),
                                                       ('dead', 'Dead'), ('retired', 'Retired')], required=True)
    date = fields.Datetime(default=fields.Datetime.now, required=True)
    user_id = fields.Many2one('res.users', default=lambda self: self.env.user, required=True)
    reason = fields.Char()

    def write(self, vals):
        if not self.env.context.get('alfia_merge'):
            raise UserError(self.env._("Status history cannot be edited."))
        return super().write(vals)

    def unlink(self):
        if not self.env.su:
            raise UserError(self.env._("Status history cannot be deleted."))
        return super().unlink()


class AlfiaHorseOwnership(models.Model):
    _name = 'alfia.horse.ownership'
    _description = 'Horse ownership (effective-dated)'
    _order = 'date_from desc, id desc'

    horse_id = fields.Many2one('alfia.horse', required=True, ondelete='cascade', index=True)
    partner_id = fields.Many2one('res.partner', string='Owner', required=True)
    share = fields.Float(string='Share (%)', default=100.0, required=True)
    date_from = fields.Date(string='Owner from', required=True, default=fields.Date.context_today)
    date_to = fields.Date(string='Owner until')
    reason = fields.Char(help="Purchase, gift, inheritance, correction…")

    _share_range = models.Constraint('check(share > 0 and share <= 100)', 'A share must be between 0 and 100%.')

    @api.constrains('date_from', 'date_to', 'share', 'horse_id', 'partner_id')
    def _check_periods(self):
        for line in self:
            if line.date_to and line.date_to < line.date_from:
                raise ValidationError(self.env._("The ownership of %s ends before it starts.", line.horse_id.name))
            others = self.search([('horse_id', '=', line.horse_id.id), ('id', '!=', line.id)])
            overlapping = others.filtered(lambda o: o.date_from <= (line.date_to or fields.Date.from_string('9999-12-31'))
                                          and (not o.date_to or o.date_to >= line.date_from))
            if any(o.partner_id == line.partner_id for o in overlapping):
                raise ValidationError(self.env._("%(owner)s already owns %(horse)s in that period.", owner=line.partner_id.name, horse=line.horse_id.name))
            if line.share + sum(overlapping.mapped('share')) > 100.0001:
                raise ValidationError(self.env._("Ownership shares of %s add up to more than 100%% in that period.", line.horse_id.name))

    def action_transfer_all(self, new_partner, date=None, reason=None):
        """Close the current owners the day before `date` and make `new_partner` the sole owner from `date`."""
        date = date or fields.Date.context_today(self)
        horses = self.horse_id
        for horse in horses:
            current = horse.ownership_ids.filtered(lambda o: not o.date_to or o.date_to >= date)
            for line in current:
                if line.date_from >= date:
                    line.unlink()
                else:
                    line.date_to = date - relativedelta(days=1)
            self.create({'horse_id': horse.id, 'partner_id': new_partner.id, 'date_from': date, 'reason': reason})
        return True


class AlfiaHorseMovement(models.Model):
    _name = 'alfia.horse.movement'
    _description = 'Horse movement'
    _order = 'date desc, id desc'

    horse_id = fields.Many2one('alfia.horse', required=True, ondelete='cascade', index=True)
    kind = fields.Selection([('arrival', 'Arrival'), ('internal', 'Internal move'), ('departure', 'Departure')], required=True, default='internal')
    state = fields.Selection([('planned', 'Planned'), ('done', 'Done'), ('cancelled', 'Cancelled')], default='planned', required=True, index=True)
    date = fields.Datetime(string='Date', default=fields.Datetime.now, required=True)
    location_from_id = fields.Many2one('stock.location', string='From')
    location_to_id = fields.Many2one('stock.location', string='To')
    partner_id = fields.Many2one('res.partner', string='Farm / counterpart')
    responsible_id = fields.Many2one('res.users', string='Responsible', default=lambda self: self.env.user)
    reason = fields.Char()
    company_id = fields.Many2one('res.company', related='horse_id.company_id', store=True)

    @api.model_create_multi
    def create(self, vals_list):
        moves = super().create(vals_list)
        if not self.env.su:
            for move in moves:
                # The access rule lets people record moves *into* their sites; moving a horse that is on the farm also needs
                # its current place to be one of theirs, so nobody can pull a horse into their scope from elsewhere.
                if move.horse_id.location_id:
                    self.env.user._alfia_check_location(move.horse_id.location_id, self.env._("current place of %s", move.horse_id.name))
                if move.location_to_id:
                    self.env.user._alfia_check_location(move.location_to_id, self.env._("destination"))
        return moves

    @api.constrains('kind', 'location_to_id')
    def _check_destination(self):
        for move in self:
            if move.kind in ('arrival', 'internal') and not move.location_to_id:
                raise ValidationError(self.env._("A %s needs a destination location.", dict(self._fields['kind'].selection)[move.kind].lower()))

    def action_done(self):
        for move in self:
            if move.state != 'planned':
                raise UserError(self.env._("Only a planned movement can be completed."))
            if move.kind == 'internal' and not move.horse_id.on_site:
                raise UserError(self.env._("%s is not on site, so it cannot be moved internally.", move.horse_id.name))
            move.write({'state': 'done', 'location_from_id': move.location_from_id.id or move.horse_id.location_id.id})
        return True

    def action_cancel(self):
        for move in self:
            if move.state == 'done':
                raise UserError(self.env._("A completed movement cannot be cancelled. Record a new movement to correct it."))
        self.state = 'cancelled'

    def write(self, vals):
        allowed = {'reason', 'state', 'location_from_id'} | ({'horse_id'} if self.env.context.get('alfia_merge') else set())
        if any(m.state == 'done' for m in self) and set(vals) - allowed:
            raise UserError(self.env._("A completed movement cannot be changed. Record a new movement to correct it."))
        return super().write(vals)
