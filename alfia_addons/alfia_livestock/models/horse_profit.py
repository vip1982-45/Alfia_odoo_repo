"""Profit and loss per horse.

Everything a horse costs and earns, on one page: what accounting has allocated to the horse's cost centre (medicine
and supplies issued from stock, supplier bills, boarding and service invoices, breeding contracts and semen, the sale
of the horse), plus what the stable records but accounting does not see yet (vet, dental and farrier visits, transport
on movements, feed issued from the store, or estimated from the feed plan in months with no issue) and the price paid for the horse.

A cost recorded by the stable that a supplier also bills is counted once: the stable notes the supplier's invoice
number on the record, and once a posted supplier bill carries that number as its reference, the record drops out and
the bill line, allocated to the horse, counts instead. Revaluations of the horse
(livestock valuations) are left out: the purchase price and the sale income already give the result of owning it.
"""
from odoo import api, fields, models, tools
from odoo.exceptions import UserError

FINANCE_GROUPS = 'account.group_account_user,account.group_account_manager'
PROFIT_READERS = FINANCE_GROUPS + ',alfia_security.group_alfia_security_auditor,alfia_security.group_alfia_breeding_manager'

CATEGORIES = [
    ('purchase', 'Purchase of the horse'),
    ('feed', 'Feed'),
    ('stock', 'Medicine and supplies from stock'),
    ('vet', 'Vet and health'),
    ('dental', 'Dental'),
    ('farrier', 'Farrier'),
    ('transport', 'Transport and movements'),
    ('bills', 'Supplier bills and other costs'),
    ('services', 'Boarding and services'),
    ('breeding', 'Breeding contracts and semen'),
    ('sale', 'Sale of the horse'),
]
# Care records whose cost the profit page reads: model -> (category, label column).
CARE_RECORDS = {
    'alfia.health.record': ('vet', 'title'),
    'alfia.vet.visit': ('vet', 'reason'),
    'alfia.odontology': ('dental', None),
    'alfia.farrier.visit': ('farrier', None),
}


class AlfiaCareCostBill(models.AbstractModel):
    """A stable record with a cost that a supplier may also bill."""
    _name = 'alfia.care.cost.bill'
    _description = 'Stable cost that a supplier bill may cover'

    alfia_bill_ref = fields.Char(
        string='Supplier invoice no.', copy=False,
        help="The supplier's invoice number for this cost, if they bill it. Once finance posts that supplier bill (with this "
             "number as its reference), the horse's profit counts the bill line instead of the cost recorded here, so the "
             "cost is not counted twice. Allocate the bill line to the horse.")


class AlfiaHealthRecord(models.Model):
    _name = 'alfia.health.record'
    _inherit = ['alfia.health.record', 'alfia.care.cost.bill']


class AlfiaVetVisit(models.Model):
    _name = 'alfia.vet.visit'
    _inherit = ['alfia.vet.visit', 'alfia.care.cost.bill']


class AlfiaOdontology(models.Model):
    _name = 'alfia.odontology'
    _inherit = ['alfia.odontology', 'alfia.care.cost.bill']


class AlfiaFarrierVisit(models.Model):
    _name = 'alfia.farrier.visit'
    _inherit = ['alfia.farrier.visit', 'alfia.care.cost.bill']


class AlfiaHorseMovement(models.Model):
    _name = 'alfia.horse.movement'
    _inherit = ['alfia.horse.movement', 'alfia.care.cost.bill']

    alfia_cost = fields.Monetary(string='Transport cost', currency_field='alfia_currency_id',
                                 help="What this movement cost (transport, export papers, escort). Counted in the horse's profit.")
    alfia_currency_id = fields.Many2one(related='company_id.currency_id', string='Currency')

    @api.constrains('alfia_cost')
    def _check_alfia_cost(self):
        for move in self:
            if move.alfia_cost < 0:
                raise UserError(self.env._("A transport cost cannot be negative."))


class AlfiaHorseProfit(models.Model):
    _name = 'alfia.horse.profit'
    _description = 'Horse profit and loss line'
    _auto = False
    _order = 'date desc, id desc'

    horse_id = fields.Many2one('alfia.horse', readonly=True, index=True)
    company_id = fields.Many2one('res.company', readonly=True)
    currency_id = fields.Many2one(related='company_id.currency_id')
    date = fields.Date(readonly=True)
    category = fields.Selection(CATEGORIES, readonly=True)
    source = fields.Selection([('accounting', 'Accounting'), ('stable', 'Stable records'), ('plan', 'Feed plan'),
                               ('register', 'Livestock register')], readonly=True)
    name = fields.Char(string='Description', readonly=True)
    amount = fields.Monetary(string='Result', readonly=True, currency_field='currency_id',
                             help="Income is positive, costs are negative.")
    cost = fields.Monetary(readonly=True, currency_field='currency_id')
    income = fields.Monetary(readonly=True, currency_field='currency_id')
    res_model = fields.Char(readonly=True)
    res_id = fields.Integer(readonly=True)

    @api.model
    def _alfia_semen_category_ids(self):
        semen = self.env.ref('alfia_equine.category_semen', raise_if_not_found=False)
        return self.env['product.category'].sudo().search([('id', 'child_of', semen.id)]).ids if semen else []

    def _alfia_query(self):
        plan_column = self.env['alfia.horse']._alfia_horse_plan()._column_name()
        semen_ids = tuple(self._alfia_semen_category_ids()) or (0,)
        care_parts = []
        for model, (category, label) in CARE_RECORDS.items():
            table = self.env[model]._table
            description = self.env[model]._description
            label_sql = f"r.{label}" if label else 'NULL'
            care_parts.append(f"""
                SELECT r.horse_id, h.company_id, r.date, '{category}' AS category, 'stable' AS source,
                       COALESCE({label_sql}, '{description}') AS name, -r.cost AS amount, '{model}' AS res_model, r.id AS res_id
                  FROM {table} r JOIN alfia_horse h ON h.id = r.horse_id
                 WHERE r.cost > 0 AND (r.alfia_bill_ref IS NULL OR NOT EXISTS (
                         SELECT 1 FROM account_move b WHERE b.ref = r.alfia_bill_ref AND b.state = 'posted'
                            AND b.move_type IN ('in_invoice', 'in_receipt') AND b.company_id = h.company_id))""")
        return f"""
            WITH lines AS (
                -- What accounting allocated to the horse's cost centre.
                SELECT h.id AS horse_id, al.company_id, al.date,
                       CASE WHEN EXISTS (SELECT 1 FROM alfia_horse_sale s WHERE s.move_id = aml.move_id) THEN 'sale'
                            WHEN EXISTS (SELECT 1 FROM alfia_breeding_contract c
                                          WHERE c.invoice_id = aml.move_id OR c.credit_note_id = aml.move_id) THEN 'breeding'
                            WHEN pt.categ_id IN %(semen)s THEN 'breeding'
                            WHEN EXISTS (SELECT 1 FROM stock_move sm WHERE sm.account_move_id = aml.move_id) THEN 'stock'
                            WHEN al.amount > 0 THEN 'services'
                            ELSE 'bills' END AS category,
                       'accounting' AS source, COALESCE(am.name, '') || ' ' || COALESCE(al.name, '') AS name, al.amount,
                       'account.move' AS res_model, aml.move_id AS res_id
                  FROM account_analytic_line al
                  JOIN alfia_horse h ON h.analytic_account_id = al.{plan_column}
                  JOIN account_account aa ON aa.id = al.general_account_id
                  LEFT JOIN account_move_line aml ON aml.id = al.move_line_id
                  LEFT JOIN account_move am ON am.id = aml.move_id
                  LEFT JOIN product_product pp ON pp.id = aml.product_id
                  LEFT JOIN product_template pt ON pt.id = pp.product_tmpl_id
                 WHERE aa.account_type NOT LIKE 'asset%%' AND aa.account_type NOT LIKE 'liability%%'
                   AND aa.account_type NOT LIKE 'equity%%' AND aa.account_type <> 'off_balance'
                   AND am.alfia_livestock_valuation_id IS NULL
                UNION ALL
                -- The price paid for the horse (the bill itself goes to the livestock asset account, left out above).
                SELECT h.id, h.company_id, COALESCE(h.alfia_acquisition_date, h.create_date::date), 'purchase', 'register',
                       'Purchase price', -h.alfia_acquisition_cost, 'alfia.horse', h.id
                  FROM alfia_horse h
                 WHERE h.alfia_acquisition_kind = 'purchase' AND h.alfia_acquisition_cost > 0
                UNION ALL
                -- Transport and other movement costs recorded by the stable.
                SELECT m.horse_id, h.company_id, m.date::date, 'transport', 'stable',
                       COALESCE(m.reason, m.kind), -m.alfia_cost, 'alfia.horse.movement', m.id
                  FROM alfia_horse_movement m JOIN alfia_horse h ON h.id = m.horse_id
                 WHERE m.state = 'done' AND m.alfia_cost > 0 AND (m.alfia_bill_ref IS NULL OR NOT EXISTS (
                         SELECT 1 FROM account_move b WHERE b.ref = m.alfia_bill_ref AND b.state = 'posted'
                            AND b.move_type IN ('in_invoice', 'in_receipt') AND b.company_id = h.company_id))
                UNION ALL
                {' UNION ALL '.join(care_parts)}
                UNION ALL
                -- Feed from the feed plan: daily quantity x product cost, month by month while the plan runs (and the horse
                -- is ours: not after its recurring charges end).
                SELECT f.horse_id, f.company_id, f.month_start, 'feed', 'plan', f.product_name,
                       -ROUND((f.days * f.daily_qty * f.unit_cost)::numeric, 2), 'alfia.diet', f.diet_id
                  FROM (
                    SELECT d.horse_id, h.company_id, d.id AS diet_id, l.daily_qty,
                           COALESCE((pp.standard_price ->> h.company_id::text)::numeric, 0) AS unit_cost,
                           COALESCE(pt.name ->> 'en_US', '') AS product_name,
                           GREATEST(m.month::date, d.date_from) AS month_start,
                           (LEAST((m.month + interval '1 month - 1 day')::date,
                                  COALESCE(d.date_to, CURRENT_DATE), CURRENT_DATE,
                                  COALESCE(h.alfia_billed_until, CURRENT_DATE))
                            - GREATEST(m.month::date, d.date_from) + 1) AS days
                      FROM alfia_diet d
                      JOIN alfia_horse h ON h.id = d.horse_id
                      JOIN alfia_diet_line l ON l.diet_id = d.id
                      JOIN product_product pp ON pp.id = l.product_id
                      JOIN product_template pt ON pt.id = pp.product_tmpl_id
                      CROSS JOIN LATERAL generate_series(date_trunc('month', d.date_from),
                                                         LEAST(COALESCE(d.date_to, CURRENT_DATE), CURRENT_DATE),
                                                         interval '1 month') AS m(month)
                  ) f
                 WHERE f.days > 0 AND f.unit_cost > 0
                   -- A month in which the store issued feed to the horse counts what was issued, not the plan.
                   AND NOT EXISTS (SELECT 1 FROM alfia_feed_issue_line fl
                                    WHERE fl.horse_id = f.horse_id AND fl.state = 'issued'
                                      AND date_trunc('month', fl.date) = date_trunc('month', f.month_start))
                UNION ALL
                -- Feed the store issued to the horse, at the product's cost when it was issued.
                SELECT fl.horse_id, fi.company_id, fi.date, 'feed', 'stable', fi.name, -fl.value, 'alfia.feed.issue', fi.id
                  FROM alfia_feed_issue_line fl JOIN alfia_feed_issue fi ON fi.id = fl.issue_id
                 WHERE fi.state = 'issued' AND fl.value > 0
            )
            SELECT row_number() OVER (ORDER BY date, res_model, res_id, category) AS id,
                   horse_id, company_id, date, category, source, name, amount,
                   GREATEST(-amount, 0) AS cost, GREATEST(amount, 0) AS income, res_model, res_id
              FROM lines
             WHERE amount <> 0
        """, {'semen': semen_ids}

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        query, params = self._alfia_query()
        self.env.cr.execute(f"CREATE OR REPLACE VIEW {self._table} AS ({query})", params)

    def action_open_source(self):
        self.ensure_one()
        if not self.res_model or not self.res_id:
            return False
        return {'type': 'ir.actions.act_window', 'res_model': self.res_model, 'res_id': self.res_id, 'view_mode': 'form',
                'target': 'current'}


class AlfiaHorse(models.Model):
    _inherit = 'alfia.horse'

    alfia_profit_cost = fields.Monetary(string='Total costs', compute='_compute_alfia_profit',
                                        currency_field='alfia_company_currency_id', groups=PROFIT_READERS)
    alfia_profit_income = fields.Monetary(string='Total income', compute='_compute_alfia_profit',
                                          currency_field='alfia_company_currency_id', groups=PROFIT_READERS)
    alfia_profit_net = fields.Monetary(string='Net result', compute='_compute_alfia_profit',
                                       currency_field='alfia_company_currency_id', groups=PROFIT_READERS)
    alfia_profit_days = fields.Integer(string='Days with us', compute='_compute_alfia_profit', groups=PROFIT_READERS)
    alfia_profit_cost_per_day = fields.Monetary(string='Running cost per day', compute='_compute_alfia_profit',
                                                currency_field='alfia_company_currency_id', groups=PROFIT_READERS,
                                                help="Costs other than the purchase price, divided by the days the horse has been with us.")

    def _compute_alfia_profit(self):
        self.env.flush_all()        # the profit lines are read straight from the tables
        Profit = self.env['alfia.horse.profit'].sudo()
        rows = Profit._read_group([('horse_id', 'in', self.ids)], ['horse_id', 'category'], ['cost:sum', 'income:sum'])
        totals = {}
        for horse, category, cost, income in rows:
            entry = totals.setdefault(horse.id, {'cost': 0.0, 'income': 0.0, 'running': 0.0})
            entry['cost'] += cost
            entry['income'] += income
            if category != 'purchase':
                entry['running'] += cost
        today = fields.Date.context_today(self)
        for horse in self:
            entry = totals.get(horse.id, {'cost': 0.0, 'income': 0.0, 'running': 0.0})
            start = horse.sudo().alfia_acquisition_date or horse.birthdate or (horse.create_date and horse.create_date.date()) or today
            end = min(horse.sudo().alfia_billed_until or today, today)
            days = max((end - start).days + 1, 1)
            horse.alfia_profit_cost = entry['cost']
            horse.alfia_profit_income = entry['income']
            horse.alfia_profit_net = entry['income'] - entry['cost']
            horse.alfia_profit_days = days
            horse.alfia_profit_cost_per_day = entry['running'] / days

    def action_alfia_horse_profit(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': self.env._("Profit and loss: %s", self.name),
                'res_model': 'alfia.horse.profit', 'view_mode': 'list,pivot,graph',
                'views': [(self.env.ref('alfia_livestock.alfia_horse_profit_view_list').id, 'list'), (False, 'pivot'), (False, 'graph')],
                'domain': [('horse_id', '=', self.id)],
                'context': {'search_default_group_category': 1, 'pivot_row_groupby': ['category'], 'pivot_measures': ['cost', 'income', 'amount']}}
