import csv
import hashlib
import io
from datetime import datetime

from odoo import Command, api, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.alfia_security.flow import workflow

CUSTODIANS = ('alfia_security.group_alfia_semen_custodian', 'alfia_security.group_alfia_breeding_manager')
MOVES = {'received': 'Received from us', 'used': 'Used (insemination)', 'shipped': 'Shipped to a third party',
         'balance': 'Balance held'}
ALIASES = {'received': ('received', 'receipt', 'in', 'استلام'), 'used': ('used', 'usage', 'insemination', 'استخدام'),
           'shipped': ('shipped', 'dispatch', 'out', 'شحن'), 'balance': ('balance', 'stock', 'closing', 'رصيد')}
HEADERS = {'date': ('date', 'التاريخ'), 'batch': ('batch', 'lot', 'straw batch', 'الدفعة'),
           'movement': ('movement', 'type', 'transaction', 'الحركة'), 'quantity': ('quantity', 'qty', 'straws', 'الكمية'),
           'reference': ('reference', 'ref', 'mare', 'المرجع')}
MAX_ROWS = 5000


def _date(text):
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%d.%m.%Y'):
        try:
            return datetime.strptime((text or '').strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unrecognised date {text!r}")


class StockLocation(models.Model):
    _inherit = 'stock.location'

    alfia_external_centre_id = fields.Many2one('res.partner', string='External semen centre',
                                               help="Set when this location is our semen held at an external centre. Its "
                                                    "statements are imported under Breeding › External semen statements.")


class AlfiaSemenStatement(models.Model):
    """A statement from an external semen centre, imported from the agreed spreadsheet: what it received from us, used,
    shipped on, and holds. Each line is checked against our records (batch exists, quantity valid, shipment matches,
    balance matches) and logged as OK, an exception or a duplicate. Posting by a second person moves the used and
    shipped straws out of the centre's location; the source file stays attached."""
    _name = 'alfia.semen.statement'
    _description = 'External semen centre statement'
    _inherit = ['mail.thread', 'alfia.guarded.mixin']
    _order = 'statement_date desc, id desc'
    _alfia_guarded = ('state', 'imported_by_id', 'posted_by_id', 'posted_date', 'file_hash')
    _alfia_guarded_create = {'imported_by_id': '*', 'file_hash': '*'}

    name = fields.Char(required=True)
    location_id = fields.Many2one('stock.location', string='Centre location', required=True,
                                  domain="[('alfia_external_centre_id', '!=', False)]")
    centre_id = fields.Many2one(related='location_id.alfia_external_centre_id', string='Centre', store=True)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company, required=True)
    statement_date = fields.Date(required=True, default=fields.Date.context_today)
    filename = fields.Char()
    file_hash = fields.Char(readonly=True, index=True, copy=False)
    line_ids = fields.One2many('alfia.semen.statement.line', 'statement_id', readonly=True)
    state = fields.Selection([('checked', 'Checked'), ('posted', 'Posted'), ('cancelled', 'Cancelled')], default='checked',
                             required=True, tracking=True)
    imported_by_id = fields.Many2one('res.users', default=lambda self: self.env.user, readonly=True)
    posted_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    posted_date = fields.Datetime(readonly=True, copy=False)
    centre_stock = fields.Text(string='Our stock at the centre now', compute='_compute_centre_stock',
                               help="Straws we hold at this centre, by batch, as recorded now (after any posting).")
    exception_count = fields.Integer(compute='_compute_counts')
    ok_count = fields.Integer(compute='_compute_counts')

    @api.depends('state', 'location_id')
    def _compute_centre_stock(self):
        for st in self:
            quants = self.env['stock.quant'].sudo().search([('location_id', '=', st.location_id.id), ('quantity', '!=', 0)])
            by_lot = {}
            for quant in quants:
                key = f"{quant.lot_id.name or '—'} ({quant.product_id.display_name})"
                by_lot[key] = by_lot.get(key, 0.0) + quant.quantity
            st.centre_stock = '\n'.join(f"{k}: {v:g}" for k, v in sorted(by_lot.items())) or self.env._("Nothing held")

    @api.depends('line_ids.status')
    def _compute_counts(self):
        for st in self:
            st.exception_count = len(st.line_ids.filtered(lambda l: l.status == 'exception'))
            st.ok_count = len(st.line_ids.filtered(lambda l: l.status in ('ok', 'accepted')))

    @api.model
    def _check_custodian(self):
        if not (self.env.su or any(self.env.user.has_group(g) for g in CUSTODIANS)):
            raise AccessError(self.env._("Only semen custodians and breeding managers handle external centre statements."))

    # ------------------------------------------------------------------ import and checks
    @api.model
    def _parse(self, raw):
        try:
            text = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            text = raw.decode('cp1256', errors='replace')
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=',;\t')
        except csv.Error:
            dialect = csv.excel
        rows = [r for r in csv.reader(io.StringIO(text), dialect) if any((c or '').strip() for c in r)]
        if len(rows) < 2:
            raise UserError(self.env._("The statement has no lines."))
        if len(rows) > MAX_ROWS + 1:
            raise UserError(self.env._("The statement has more than %s lines.", MAX_ROWS))
        header = [(h or '').strip().lower() for h in rows[0]]
        cols = {key: next((i for i, h in enumerate(header) if h in names), None) for key, names in HEADERS.items()}
        missing = [k for k in ('date', 'batch', 'movement', 'quantity') if cols[k] is None]
        if missing:
            raise UserError(self.env._("The statement needs the columns Date, Batch, Movement and Quantity (and optionally "
                                       "Reference). Missing: %s", ', '.join(missing)))
        parsed = []
        for number, row in enumerate(rows[1:], start=2):
            def cell(key):
                i = cols[key]
                return row[i].strip() if i is not None and i < len(row) else ''
            parsed.append({'row': number, 'date_text': cell('date'), 'batch': cell('batch'), 'movement_text': cell('movement'),
                           'quantity_text': cell('quantity'), 'reference': cell('reference')})
        return parsed

    @api.model
    def import_statement(self, location, raw, filename=None, statement_date=None):
        self._check_custodian()
        if not location.alfia_external_centre_id:
            raise UserError(self.env._("%s is not an external centre location.", location.display_name))
        digest = hashlib.sha256(raw).hexdigest()
        previous = self.sudo().search([('file_hash', '=', digest), ('state', '!=', 'cancelled')], limit=1)
        if previous:
            raise UserError(self.env._("This file was already imported as %s.", previous.name))
        rows = self._parse(raw)
        with workflow():
            statement = self.create({'name': filename or self.env._('Statement %s', fields.Date.today()), 'location_id': location.id,
                                     'statement_date': statement_date or fields.Date.context_today(self), 'filename': filename,
                                     'file_hash': digest, 'imported_by_id': self.env.user.id})
        self.env['ir.attachment'].sudo().create({'name': filename or 'statement.csv', 'raw': raw, 'res_model': self._name,
                                                 'res_id': statement.id, 'mimetype': 'text/csv'})
        Line = self.env['alfia.semen.statement.line'].sudo()
        seen = {}
        for row in rows:
            vals = statement._check_row(row, seen)
            Line.create(dict(vals, statement_id=statement.id))
        statement.message_post(body=self.env._("Imported by %(user)s: %(ok)s line(s) OK, %(exc)s exception(s).", user=self.env.user.name,
                                               ok=statement.ok_count, exc=statement.exception_count))
        return statement

    def _check_row(self, row, seen):
        self.ensure_one()
        vals = {'row': row['row'], 'batch': row['batch'], 'reference': row['reference'], 'raw_movement': row['movement_text'],
                'raw_quantity': row['quantity_text'], 'raw_date': row['date_text']}
        problems = []
        try:
            vals['date'] = _date(row['date_text'])
        except ValueError as e:
            problems.append(str(e))
        movement = next((key for key, names in ALIASES.items() if row['movement_text'].strip().lower() in names), None)
        vals['movement'] = movement
        if not movement:
            problems.append(self.env._("unknown movement %r", row['movement_text']))
        try:
            quantity = float(row['quantity_text'].replace(',', ''))
            if quantity < 0 or (quantity == 0 and movement != 'balance'):
                raise ValueError
            vals['quantity'] = quantity
        except ValueError:
            problems.append(self.env._("quantity %r is not a positive number", row['quantity_text']))
            quantity = 0.0
        lots = self.env['stock.lot'].sudo().search([('name', '=', row['batch']), ('company_id', 'in', (False, self.company_id.id))])
        if len(lots) != 1:
            problems.append(self.env._("batch %r not found", row['batch']) if not lots else self.env._("batch %r is ambiguous", row['batch']))
        else:
            vals['lot_id'] = lots.id
        base = '|'.join([str(self.location_id.id), row['date_text'], row['batch'], row['movement_text'].lower(), row['quantity_text'],
                         row['reference']])
        seen[base] = seen.get(base, 0) + 1
        key = hashlib.sha256(f"{base}|{seen[base]}".encode()).hexdigest()
        vals['key'] = key
        if not problems:
            if self.env['alfia.semen.statement.line'].sudo().search_count([('key', '=', key), ('statement_id.state', '=', 'posted')]):
                vals.update(status='duplicate', message=self.env._("already posted from an earlier statement"))
                return vals
            problems += self._reconcile(lots, movement, quantity, vals['date'])
        vals.update(status='exception' if problems else 'ok', message='; '.join(problems) if problems else False)
        return vals

    def _held(self, lot):
        quants = self.env['stock.quant'].sudo().search([('location_id', '=', self.location_id.id), ('lot_id', '=', lot.id)])
        return sum(quants.mapped('quantity'))

    def _reconcile(self, lot, movement, quantity, day):
        """What the statement says against what we recorded."""
        problems = []
        if movement == 'received':
            shipped = sum(self.env['stock.move.line'].sudo().search([
                ('state', '=', 'done'), ('lot_id', '=', lot.id), ('location_dest_id', '=', self.location_id.id)]).mapped('quantity'))
            confirmed = sum(self.env['alfia.semen.statement.line'].sudo().search([
                ('statement_id.state', '=', 'posted'), ('statement_id.location_id', '=', self.location_id.id),
                ('lot_id', '=', lot.id), ('movement', '=', 'received')]).mapped('quantity'))
            if confirmed + quantity > shipped + 1e-6:
                problems.append(self.env._("centre reports receiving %(qty)s but we shipped %(open)s not yet confirmed",
                                           qty=quantity, open=max(shipped - confirmed, 0)))
        elif movement in ('used', 'shipped'):
            earlier = sum(self.line_ids.filtered(lambda l: l.lot_id == lot and l.movement in ('used', 'shipped')
                                                 and l.status == 'ok').mapped('quantity'))
            held = self._held(lot) - earlier                      # earlier lines of this statement already take straws
            if quantity > held + 1e-6:
                problems.append(self.env._("centre reports %(qty)s %(what)s but we hold %(held)s there",
                                           qty=quantity, what=MOVES[movement].lower(), held=held))
        elif movement == 'balance':
            held = self._held(lot)
            if abs(held - quantity) > 1e-6:
                problems.append(self.env._("balance %(qty)s at the centre differs from our %(held)s", qty=quantity, held=held))
        return problems

    # ------------------------------------------------------------------ posting
    def action_post(self):
        self._check_custodian()
        for st in self:
            if st.state != 'checked':
                raise UserError(self.env._("%s is already posted or cancelled.", st.name))
            if st.imported_by_id == self.env.user and self.env['alfia.sod.mixin']._alfia_sod_applies(None, st.company_id):
                raise UserError(self.env._("You imported %s, so another person must review and post it.", st.name))
            used_loc = self.env.ref('alfia_registry.location_semen_used_external')
            customers = self.env.ref('stock.stock_location_customers')
            for line in st.line_ids.filtered(lambda l: l.status in ('ok', 'accepted') and l.movement in ('used', 'shipped')):
                dest = used_loc if line.movement == 'used' else customers
                move = self.env['stock.move'].sudo().create({
                    'product_id': line.lot_id.product_id.id, 'product_uom_qty': line.quantity, 'uom_id': line.lot_id.product_id.uom_id.id,
                    'location_id': st.location_id.id, 'location_dest_id': dest.id, 'company_id': st.company_id.id,
                    'origin': f"{st.name} / {line.reference or line.batch}"})
                move._action_confirm()
                move.write({'move_line_ids': [Command.create({'product_id': line.lot_id.product_id.id, 'lot_id': line.lot_id.id,
                                                              'quantity': line.quantity, 'location_id': st.location_id.id,
                                                              'location_dest_id': dest.id, 'picked': True})]})
                move.picked = True
                move._action_done()
                line.sudo().move_id = move
            with workflow():
                st.write({'state': 'posted', 'posted_by_id': self.env.user.id, 'posted_date': fields.Datetime.now()})
            st.message_post(body=self.env._("Posted by %(user)s. Lines still in exception were not posted: %(exc)s.",
                                            user=self.env.user.name, exc=st.exception_count))

    def action_cancel(self):
        self._check_custodian()
        for st in self:
            if st.state == 'posted':
                raise UserError(self.env._("A posted statement is kept; correct it with the next statement."))
            with workflow():
                st.write({'state': 'cancelled'})


class AlfiaSemenStatementLine(models.Model):
    _name = 'alfia.semen.statement.line'
    _description = 'External semen statement line'
    _order = 'statement_id, row'

    statement_id = fields.Many2one('alfia.semen.statement', required=True, ondelete='cascade', index=True)
    row = fields.Integer(string='File row')
    date = fields.Date()
    raw_date = fields.Char(string='Date in file')
    batch = fields.Char()
    lot_id = fields.Many2one('stock.lot', string='Our batch')
    movement = fields.Selection(list(MOVES.items()))
    raw_movement = fields.Char(string='Movement in file')
    quantity = fields.Float()
    raw_quantity = fields.Char(string='Quantity in file')
    reference = fields.Char()
    key = fields.Char(index=True)
    status = fields.Selection([('ok', 'OK'), ('exception', 'Exception'), ('accepted', 'Exception accepted'), ('duplicate', 'Duplicate')],
                              required=True, default='ok')
    message = fields.Char(string='Check result')
    resolution = fields.Char(help="Why an exception was accepted.")
    move_id = fields.Many2one('stock.move', readonly=True)

    def action_accept_exception(self):
        """A custodian accepts an exception with a reason (for example a shipment recorded late); it then posts like an OK line."""
        self.env['alfia.semen.statement']._check_custodian()
        for line in self:
            if line.status != 'exception' or line.statement_id.state != 'checked':
                raise UserError(self.env._("Only an exception on a statement not yet posted can be accepted."))
            if not line.resolution:
                raise UserError(self.env._("Write why the exception is accepted."))
            if not (line.lot_id and line.movement and line.quantity is not None and line.date):
                raise UserError(self.env._("This line cannot be posted: %s", line.message))
            line.sudo().write({'status': 'accepted'})
            line.statement_id.message_post(body=self.env._("Row %(row)s accepted by %(user)s: %(why)s (was: %(msg)s)",
                                                           row=line.row, user=self.env.user.name, why=line.resolution, msg=line.message))


class AlfiaSemenImport(models.TransientModel):
    _name = 'alfia.semen.import'
    _description = 'Import an external centre statement'

    location_id = fields.Many2one('stock.location', string='Centre location', required=True,
                                  domain="[('alfia_external_centre_id', '!=', False)]")
    statement_date = fields.Date(required=True, default=fields.Date.context_today)
    file = fields.Binary(required=True)
    filename = fields.Char()

    def action_import(self):
        self.ensure_one()
        statement = self.env['alfia.semen.statement'].import_statement(self.location_id, self.file.content if self.file else b'',
                                                                       self.filename, self.statement_date)
        return {'type': 'ir.actions.act_window', 'res_model': 'alfia.semen.statement', 'res_id': statement.id, 'view_mode': 'form'}
