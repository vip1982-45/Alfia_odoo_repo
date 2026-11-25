"""Additions from the 25 Nov comparison: payment on horse sales, and a stud dashboard for the stud's managers."""
from datetime import timedelta

from markupsafe import Markup, escape

from odoo import api, fields, models

PAYMENT_STATES = [('not_paid', 'Not paid'), ('in_payment', 'In payment'), ('paid', 'Paid'), ('partial', 'Partially paid'),
                  ('reversed', 'Reversed'), ('blocked', 'Blocked'), ('invoicing_legacy', 'Invoicing app legacy')]


class AlfiaHorseSale(models.Model):
    """What the buyer has paid and still owes, read from the sale's invoice; and the payment terms the invoice gets (for
    example 30% now and the rest in 60 days)."""
    _inherit = 'alfia.horse.sale'

    payment_term_id = fields.Many2one('account.payment.term', string='Payment terms',
                                      help="Applied to the sale's invoice, e.g. instalments. Empty: due on the invoice date.")
    alfia_invoice_total = fields.Monetary(string='Invoiced', compute='_compute_alfia_payment', currency_field='currency_id')
    alfia_amount_paid = fields.Monetary(string='Paid', compute='_compute_alfia_payment', currency_field='currency_id')
    alfia_amount_due = fields.Monetary(string='Balance', compute='_compute_alfia_payment', currency_field='currency_id')
    alfia_payment_state = fields.Selection(PAYMENT_STATES, string='Payment', compute='_compute_alfia_payment')
    alfia_due_date = fields.Date(string='Due', compute='_compute_alfia_payment')
    alfia_overdue = fields.Boolean(string='Overdue', compute='_compute_alfia_payment')

    def _compute_alfia_payment(self):
        today = fields.Date.context_today(self)
        for sale in self:
            move = sale.sudo().move_id
            posted = move and move.state == 'posted'
            sale.alfia_invoice_total = move.amount_total if move else 0.0
            sale.alfia_amount_due = move.amount_residual if posted else (move.amount_total if move else 0.0)
            sale.alfia_amount_paid = (move.amount_total - move.amount_residual) if posted else 0.0
            sale.alfia_payment_state = move.payment_state if posted and move.payment_state in dict(PAYMENT_STATES) else False
            sale.alfia_due_date = move.invoice_date_due if move else False
            sale.alfia_overdue = bool(posted and move.amount_residual > 0 and move.invoice_date_due and move.invoice_date_due < today)

    def action_complete(self):
        res = super().action_complete()
        for sale in self.filtered(lambda s: s.payment_term_id and s.move_id):
            move = sale.move_id.sudo()
            if move.state == 'draft':
                move.invoice_payment_term_id = sale.payment_term_id
        return res


class AlfiaStudDashboard(models.TransientModel):
    """One page for the stud's managers: what is overdue, the horses by status, and the best and worst results."""
    _name = 'alfia.stud.dashboard'
    _description = 'Stud dashboard'

    health_overdue = fields.Integer(compute='_compute_counts', string='Health care overdue')
    health_today = fields.Integer(compute='_compute_counts', string='Health care due today')
    health_30 = fields.Integer(compute='_compute_counts', string='Health care due in 30 days')
    horses_active = fields.Integer(compute='_compute_counts', string='Active horses')
    horses_sold = fields.Integer(compute='_compute_counts', string='Sold or gifted')
    horses_dead = fields.Integer(compute='_compute_counts', string='Dead')
    horses_retired = fields.Integer(compute='_compute_counts', string='Retired')
    mares_pregnant = fields.Integer(compute='_compute_counts', string='Mares in foal')
    feed_requested = fields.Integer(compute='_compute_counts', string='Feed requests waiting')
    sales_overdue = fields.Integer(compute='_compute_counts', string='Horse sales with an overdue balance')
    can_see_profit = fields.Boolean(compute='_compute_counts')
    best_html = fields.Html(compute='_compute_counts', sanitize=False, string='Best results')
    worst_html = fields.Html(compute='_compute_counts', sanitize=False, string='Lowest results')

    def _health_domain(self, kind):
        today = fields.Date.context_today(self)
        return {'overdue': [('next_due', '<', today)], 'today': [('next_due', '=', today)],
                'soon': [('next_due', '>=', today), ('next_due', '<=', today + timedelta(days=30))]}[kind] + \
            [('horse_id.status', '=', 'active')]

    def _count(self, model, domain):
        Model = self.env[model]
        return Model.search_count(domain) if Model.has_access('read') else 0

    def _compute_counts(self):
        horses = 'alfia.horse'
        profit_access = self.env['alfia.horse.profit'].has_access('read')
        best = worst = Markup('')
        if profit_access:
            rows = self.env['alfia.horse.profit']._read_group([('horse_id.status', '=', 'active')], ['horse_id'], ['amount:sum'])
            ranked = sorted(rows, key=lambda r: r[1], reverse=True)
            best, worst = self._alfia_rows_html(ranked[:5]), self._alfia_rows_html(list(reversed(ranked[-5:])) if len(ranked) > 5 else [])
        sales = self.env['alfia.horse.sale'].search([('state', '=', 'done'), ('move_id', '!=', False)]) \
            if self.env['alfia.horse.sale'].has_access('read') else self.env['alfia.horse.sale']
        for dash in self:
            dash.health_overdue = self._count('alfia.health.record', self._health_domain('overdue'))
            dash.health_today = self._count('alfia.health.record', self._health_domain('today'))
            dash.health_30 = self._count('alfia.health.record', self._health_domain('soon'))
            dash.horses_active = self._count(horses, [('status', '=', 'active')])
            dash.horses_sold = self._count(horses, [('status', 'in', ('sold', 'gifted'))])
            dash.horses_dead = self._count(horses, [('status', '=', 'dead')])
            dash.horses_retired = self._count(horses, [('status', '=', 'retired')])
            dash.mares_pregnant = self._count(horses, [('status', '=', 'active'), ('reproductive_state', '=', 'pregnant')])
            dash.feed_requested = self._count('alfia.feed.issue', [('state', '=', 'draft')])
            dash.sales_overdue = len(sales.filtered('alfia_overdue'))
            dash.can_see_profit = profit_access
            dash.best_html, dash.worst_html = best, worst

    def _alfia_rows_html(self, rows):
        if not rows:
            return Markup('<p class="text-muted">%s</p>') % self.env._("Not enough horses with results yet.")
        currency = self.env.company.currency_id
        body = Markup('').join(
            Markup('<tr><td>%s</td><td class="text-end %s">%s</td></tr>') % (
                escape(horse.display_name), 'text-success' if amount >= 0 else 'text-danger', f"{amount:,.2f} {currency.name}")
            for horse, amount in rows)
        return Markup('<table class="table table-sm mb-0"><thead><tr><th>%s</th><th class="text-end">%s</th></tr></thead>'
                      '<tbody>%s</tbody></table>') % (self.env._("Horse"), self.env._("Net result"), body)

    @api.model
    def action_open_dashboard(self):
        dash = self.create({})
        return {'type': 'ir.actions.act_window', 'name': self.env._("Stud dashboard"), 'res_model': self._name, 'res_id': dash.id,
                'view_mode': 'form', 'target': 'current', 'context': {'form_view_initial_mode': 'readonly'}}

    def _open(self, model, domain, name):
        return {'type': 'ir.actions.act_window', 'name': name, 'res_model': model, 'view_mode': 'list,form', 'domain': domain}

    def action_health_overdue(self):
        return self._open('alfia.health.record', self._health_domain('overdue'), self.env._("Health care overdue"))

    def action_health_today(self):
        return self._open('alfia.health.record', self._health_domain('today'), self.env._("Health care due today"))

    def action_health_30(self):
        return self._open('alfia.health.record', self._health_domain('soon'), self.env._("Health care due in 30 days"))

    def action_horses_active(self):
        return self._open('alfia.horse', [('status', '=', 'active')], self.env._("Active horses"))

    def action_horses_sold(self):
        return self._open('alfia.horse', [('status', 'in', ('sold', 'gifted'))], self.env._("Sold or gifted"))

    def action_horses_dead(self):
        return self._open('alfia.horse', [('status', '=', 'dead')], self.env._("Dead"))

    def action_horses_retired(self):
        return self._open('alfia.horse', [('status', '=', 'retired')], self.env._("Retired"))

    def action_mares_pregnant(self):
        return self._open('alfia.horse', [('status', '=', 'active'), ('reproductive_state', '=', 'pregnant')], self.env._("Mares in foal"))

    def action_feed_requested(self):
        return self._open('alfia.feed.issue', [('state', '=', 'draft')], self.env._("Feed requests waiting"))

    def action_sales_overdue(self):
        sales = self.env['alfia.horse.sale'].search([('state', '=', 'done'), ('move_id', '!=', False)]).filtered('alfia_overdue')
        return self._open('alfia.horse.sale', [('id', 'in', sales.ids)], self.env._("Horse sales with an overdue balance"))

    def action_profit(self):
        return self.env['ir.actions.act_window']._for_xml_id('alfia_livestock.alfia_horse_profit_register_action')
