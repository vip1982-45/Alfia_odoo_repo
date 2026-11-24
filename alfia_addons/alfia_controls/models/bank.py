import csv
import hashlib
import io
from datetime import datetime

from odoo import Command, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_is_zero

DATE_FORMATS = ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%d.%m.%Y', '%m/%d/%Y')
MAX_ROWS = 20000
HEADERS = {
    'date': ('date', 'value date', 'transaction date', 'booking date', 'التاريخ'),
    'label': ('label', 'description', 'narrative', 'details', 'memo', 'البيان', 'الوصف'),
    'amount': ('amount', 'المبلغ'),
    'debit': ('debit', 'withdrawal', 'withdrawals', 'مدين', 'سحب'),
    'credit': ('credit', 'deposit', 'deposits', 'دائن', 'إيداع'),
    'ref': ('reference', 'ref', 'cheque', 'cheque no', 'المرجع'),
    'partner': ('partner', 'counterparty', 'beneficiary', 'payer', 'name', 'الطرف'),
}


def _number(text):
    text = (text or '').strip().replace(',', '').replace(' ', '')
    negative = text.startswith('(') and text.endswith(')')
    text = text.strip('()')
    if not text:
        return 0.0
    value = float(text)
    return -value if negative else value


def _date(text):
    text = (text or '').strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise UserError(f"Unrecognised date: {text!r}. Use YYYY-MM-DD or DD/MM/YYYY.")


class AccountBankStatementLine(models.Model):
    _inherit = 'account.bank.statement.line'

    alfia_import_key = fields.Char(index='btree_not_null', copy=False, readonly=True,
                                   help="Fingerprint of the imported bank line, used to refuse the same line twice.")
    alfia_suggestion_count = fields.Integer(string='Possible matches', compute='_compute_alfia_suggestion_count')

    def _compute_alfia_suggestion_count(self):
        for line in self:
            line.alfia_suggestion_count = len(line._alfia_candidates()) if not line.is_reconciled else 0

    # ------------------------------------------------------------------ candidates and matching
    def _alfia_supported(self):
        self.ensure_one()
        return not self.foreign_currency_id and self.journal_id.currency_id in (self.env['res.currency'], self.company_id.currency_id)

    def _alfia_candidates(self):
        """Open items for exactly this amount: customer/vendor balances and outstanding payments."""
        self.ensure_one()
        if self.is_reconciled or not self._alfia_supported():
            return self.env['account.move.line']
        currency = self.company_id.currency_id
        lines = self.env['account.move.line'].search(self._get_default_amls_matching_domain() + [
            ('account_id.account_type', 'not in', ('asset_cash', 'liability_credit_card')),
        ], limit=200)
        lines = lines.filtered(lambda l: currency.is_zero(l.amount_residual - self.amount))
        if self.partner_id:
            same = lines.filtered(lambda l: l.partner_id.commercial_partner_id == self.partner_id.commercial_partner_id)
            lines = same or lines.filtered(lambda l: not l.partner_id)
        return lines

    def _alfia_best_match(self):
        """The one candidate the bank line clearly points to, or nothing when it is ambiguous."""
        self.ensure_one()
        candidates = self._alfia_candidates()
        if len(candidates) <= 1:
            return candidates
        text = ' '.join(filter(None, [self.payment_ref, self.ref, self.partner_name])).lower()
        by_ref = candidates.filtered(lambda l: any(
            token and len(token) >= 4 and token.lower() in text
            for token in (l.move_id.name, l.move_id.ref, l.move_id.payment_reference, l.name)))
        return by_ref if len(by_ref) == 1 else self.env['account.move.line']

    def _alfia_reconcile_with(self, lines, writeoff_account=None, writeoff_label=None):
        """Replace the bank line's suspense item by the matched items (and a write-off for any difference, such as bank
        charges), then reconcile each with its counterpart."""
        self.ensure_one()
        if self.is_reconciled:
            raise UserError(self.env._("%s is already reconciled.", self.payment_ref or self.display_name))
        if not self._alfia_supported():
            raise UserError(self.env._("Bank lines in a foreign currency are matched in the standard way, not here."))
        if not lines:
            raise UserError(self.env._("Choose what this bank line pays or receives."))
        if lines.filtered(lambda l: l.reconciled or l.company_id != self.company_id or l.parent_state != 'posted'):
            raise UserError(self.env._("Only posted, open items of the same company can be matched."))
        liquidity, suspense, other = self._seek_for_lines()
        if other or not suspense:
            raise UserError(self.env._("This bank line has already been partly matched; undo it first."))
        currency = self.company_id.currency_id
        difference = currency.round(self.amount - sum(lines.mapped('amount_residual')))
        if not currency.is_zero(difference) and not writeoff_account:
            raise UserError(self.env._("The items do not add up to the bank amount (difference %s). Choose an account for "
                                       "the difference, such as bank charges.", difference))
        new_lines = [Command.create({
            'name': line.name or line.move_id.name, 'account_id': line.account_id.id, 'partner_id': line.partner_id.id,
            'balance': -line.amount_residual, 'amount_currency': -line.amount_residual, 'currency_id': currency.id,
        }) for line in lines]
        if not currency.is_zero(difference):
            new_lines.append(Command.create({
                'name': writeoff_label or self.env._('Bank difference'), 'account_id': writeoff_account.id,
                'partner_id': self.partner_id.id, 'balance': -difference, 'amount_currency': -difference, 'currency_id': currency.id}))
        move = self.move_id
        move.button_draft()
        move.with_context(skip_readonly_check=True).write({'line_ids': [Command.unlink(suspense.id)] + new_lines})
        move.action_post()
        created = move.line_ids - liquidity
        for line in lines:
            counterpart = created.filtered(lambda c: c.account_id == line.account_id and c.partner_id == line.partner_id
                                           and currency.is_zero(c.balance + line.amount_residual) and not c.reconciled)[:1]
            (counterpart | line).reconcile()
        move.message_post(body=self.env._("Matched by %(user)s with %(items)s.", user=self.env.user.name,
                                          items=', '.join(lines.move_id.mapped('display_name'))))
        return True

    def action_alfia_auto_match(self):
        matched = skipped = 0
        for line in self.filtered(lambda l: not l.is_reconciled):
            best = line._alfia_best_match()
            if len(best) == 1:
                line._alfia_reconcile_with(best)
                matched += 1
            else:
                skipped += 1
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
            'type': 'success' if matched else 'warning', 'sticky': False,
            'message': self.env._("%(matched)s line(s) matched; %(skipped)s need a decision (no single clear match).",
                                  matched=matched, skipped=skipped),
            'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'}}}

    def action_alfia_match(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': 'alfia.bank.match', 'view_mode': 'form', 'target': 'new',
                'name': self.env._('Match bank line'), 'context': {'default_statement_line_id': self.id}}


class AlfiaBankMatch(models.TransientModel):
    _name = 'alfia.bank.match'
    _description = 'Match a bank line'

    statement_line_id = fields.Many2one('account.bank.statement.line', required=True, readonly=True)
    amount = fields.Monetary(related='statement_line_id.amount')
    currency_id = fields.Many2one(related='statement_line_id.company_id.currency_id')
    partner_filter_id = fields.Many2one('res.partner', string='Partner')
    candidate_ids = fields.Many2many('account.move.line', compute='_compute_candidate_ids')
    line_ids = fields.Many2many('account.move.line', string='Matches', domain="[('id', 'in', candidate_ids)]")
    selected_total = fields.Monetary(compute='_compute_difference')
    difference = fields.Monetary(compute='_compute_difference')
    writeoff_account_id = fields.Many2one('account.account', string='Difference to', help="For example bank charges.")
    writeoff_label = fields.Char(default='Bank charges')

    @api.depends('statement_line_id', 'partner_filter_id')
    def _compute_candidate_ids(self):
        for wiz in self:
            st = wiz.statement_line_id
            domain = st._get_default_amls_matching_domain() + [('account_id.account_type', 'not in', ('asset_cash', 'liability_credit_card'))]
            partner = wiz.partner_filter_id or st.partner_id
            if partner:
                domain.append(('partner_id', 'child_of', partner.commercial_partner_id.id))
            wiz.candidate_ids = self.env['account.move.line'].search(domain, limit=500) if st else False

    @api.depends('line_ids', 'amount')
    def _compute_difference(self):
        for wiz in self:
            wiz.selected_total = sum(wiz.line_ids.mapped('amount_residual'))
            wiz.difference = wiz.amount - wiz.selected_total

    def action_confirm(self):
        self.ensure_one()
        self.statement_line_id._alfia_reconcile_with(self.line_ids, self.writeoff_account_id, self.writeoff_label)
        return {'type': 'ir.actions.act_window_close'}


class AlfiaBankImport(models.TransientModel):
    """Import a bank statement exported from online banking as CSV. Lines already imported (same date, amount, label,
    reference and position among identical lines) are skipped, so importing an overlapping export twice is safe."""
    _name = 'alfia.bank.import'
    _description = 'Import a bank statement'

    journal_id = fields.Many2one('account.journal', required=True, domain="[('type', '=', 'bank')]",
                                 default=lambda self: self.env['account.journal'].search(
                                     [('type', '=', 'bank'), ('company_id', '=', self.env.company.id)], limit=1))
    file = fields.Binary(required=True)
    filename = fields.Char()
    result = fields.Text(readonly=True)

    @api.model
    def _columns(self, header):
        found = {}
        names = [(h or '').strip().lower() for h in header]
        for key, options in HEADERS.items():
            for index, name in enumerate(names):
                if name in options and key not in found:
                    found[key] = index
        if 'date' not in found or not ({'amount'} <= set(found) or {'debit', 'credit'} & set(found)):
            raise UserError(self.env._("The file needs a Date column and either an Amount column or Debit/Credit columns. "
                                       "Columns found: %s", ', '.join(h for h in header if h)))
        return found

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
            raise UserError(self.env._("The file has no bank lines."))
        if len(rows) > MAX_ROWS + 1:
            raise UserError(self.env._("The file has more than %s lines; split it by month.", MAX_ROWS))
        cols = self._columns(rows[0])

        def cell(row, key):
            index = cols.get(key)
            return row[index].strip() if index is not None and index < len(row) else ''

        lines = []
        for number, row in enumerate(rows[1:], start=2):
            try:
                if 'amount' in cols:
                    amount = _number(cell(row, 'amount'))
                else:
                    amount = _number(cell(row, 'credit')) - _number(cell(row, 'debit'))
                lines.append({'date': _date(cell(row, 'date')), 'amount': amount, 'label': cell(row, 'label'),
                              'ref': cell(row, 'ref'), 'partner': cell(row, 'partner')})
            except (ValueError, UserError) as e:
                raise UserError(self.env._("Line %(n)s: %(error)s", n=number, error=e)) from e
        return lines

    def _key(self, journal, line, occurrence):
        raw = '|'.join([str(journal.id), str(line['date']), f"{line['amount']:.2f}", line['label'], line['ref'], str(occurrence)])
        return hashlib.sha256(raw.encode()).hexdigest()

    def action_import(self):
        self.ensure_one()
        lines = self._parse(self.file.content if self.file else b'')
        StLine = self.env['account.bank.statement.line']
        seen, vals, duplicates = {}, [], 0
        currency = self.journal_id.currency_id or self.journal_id.company_id.currency_id
        for line in lines:
            if float_is_zero(line['amount'], precision_rounding=currency.rounding):
                continue
            base = (line['date'], round(line['amount'], 2), line['label'], line['ref'])
            seen[base] = seen.get(base, 0) + 1
            key = self._key(self.journal_id, line, seen[base])
            if StLine.sudo().search_count([('journal_id', '=', self.journal_id.id), ('alfia_import_key', '=', key)]):
                duplicates += 1
                continue
            partner = self.env['res.partner'].search([('name', '=ilike', line['partner'])], limit=1) if line['partner'] else False
            vals.append({'journal_id': self.journal_id.id, 'date': line['date'], 'amount': line['amount'],
                         'payment_ref': line['label'] or line['ref'] or '/', 'ref': line['ref'] or False,
                         'partner_name': line['partner'] or False, 'partner_id': partner.id if partner else False,
                         'alfia_import_key': key})
        statement = self.env['account.bank.statement']
        if vals:
            last = self.env['account.bank.statement'].search([('journal_id', '=', self.journal_id.id)], order='date desc, id desc', limit=1)
            start = last.balance_end_real if last else 0.0
            total = sum(v['amount'] for v in vals)
            statement = statement.create({
                'name': self.filename or self.env._('Import %s', fields.Date.today()), 'journal_id': self.journal_id.id,
                'balance_start': start, 'balance_end_real': start + total,
                'line_ids': [Command.create(v) for v in vals]})
        self.result = self.env._("%(new)s line(s) imported, %(dup)s already imported and skipped.", new=len(vals), dup=duplicates)
        if not statement:
            raise UserError(self.result)
        return {'type': 'ir.actions.act_window', 'res_model': 'account.bank.statement.line', 'view_mode': 'list,form',
                'name': self.env._('Imported bank lines'), 'domain': [('statement_id', '=', statement.id)],
                'views': [(self.env.ref('alfia_controls.alfia_bank_line_view_list').id, 'list'), (False, 'form')]}

