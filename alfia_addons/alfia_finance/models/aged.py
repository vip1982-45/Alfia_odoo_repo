from odoo import fields, models, tools


class AlfiaAgedBalance(models.Model):
    """Open customer and vendor balances by how long they are overdue, as of today. Read-only analysis view."""
    _name = 'alfia.aged.balance'
    _description = 'Aged receivables and payables'
    _auto = False
    _order = 'date_maturity'

    partner_id = fields.Many2one('res.partner', string='Partner', readonly=True)
    kind = fields.Selection([('receivable', 'Receivable'), ('payable', 'Payable')], readonly=True)
    move_id = fields.Many2one('account.move', string='Document', readonly=True)
    journal_id = fields.Many2one('account.journal', readonly=True)
    company_id = fields.Many2one('res.company', readonly=True)
    currency_id = fields.Many2one('res.currency', readonly=True)
    date = fields.Date(readonly=True)
    date_maturity = fields.Date(string='Due date', readonly=True)
    days_overdue = fields.Integer(readonly=True)
    bucket = fields.Selection([('0_current', 'Not yet due'), ('1_30', '1–30 days'), ('2_60', '31–60 days'), ('3_90', '61–90 days'),
                               ('4_older', 'Over 90 days')], string='Overdue', readonly=True)
    amount_residual = fields.Monetary(string='Open amount', currency_field='currency_id', readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(f"""
            CREATE VIEW {self._table} AS (
                SELECT l.id, l.partner_id, l.move_id, l.journal_id, l.company_id, l.company_currency_id AS currency_id, l.date,
                       COALESCE(l.date_maturity, l.date) AS date_maturity,
                       GREATEST(CURRENT_DATE - COALESCE(l.date_maturity, l.date), 0) AS days_overdue,
                       CASE WHEN a.account_type = 'asset_receivable' THEN 'receivable' ELSE 'payable' END AS kind,
                       CASE WHEN CURRENT_DATE <= COALESCE(l.date_maturity, l.date) THEN '0_current'
                            WHEN CURRENT_DATE - COALESCE(l.date_maturity, l.date) <= 30 THEN '1_30'
                            WHEN CURRENT_DATE - COALESCE(l.date_maturity, l.date) <= 60 THEN '2_60'
                            WHEN CURRENT_DATE - COALESCE(l.date_maturity, l.date) <= 90 THEN '3_90'
                            ELSE '4_older' END AS bucket,
                       CASE WHEN a.account_type = 'asset_receivable' THEN l.amount_residual ELSE -l.amount_residual END AS amount_residual
                  FROM account_move_line l
                  JOIN account_account a ON a.id = l.account_id
                 WHERE l.parent_state = 'posted'
                   AND a.account_type IN ('asset_receivable', 'liability_payable')
                   AND NOT l.reconciled
                   AND l.amount_residual <> 0
            )""")
