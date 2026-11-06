from odoo import fields, models, tools


class AlfiaReportBreeding(models.Model):
    """One row per stallion and season: services, pregnancies and live foals. Read-only analysis view."""
    _name = 'alfia.report.breeding'
    _description = 'Breeding performance'
    _auto = False
    _order = 'season desc, stallion_id'

    stallion_id = fields.Many2one('alfia.horse', readonly=True)
    season = fields.Char(readonly=True)
    company_id = fields.Many2one('res.company', readonly=True)
    services = fields.Integer(readonly=True)
    pregnancies = fields.Integer(readonly=True)
    live_foals = fields.Integer(readonly=True)
    pregnancy_rate = fields.Float(readonly=True, aggregator='avg', help="Pregnancies per service, in %.")

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(f"""
            CREATE VIEW {self._table} AS (
                SELECT row_number() OVER () AS id, s.stallion_id, s.season, h.company_id,
                       COUNT(s.id) AS services,
                       COUNT(p.id) FILTER (WHERE p.state IN ('pregnant', 'foaled')) AS pregnancies,
                       COUNT(f.id) FILTER (WHERE f.outcome = 'live') AS live_foals,
                       CASE WHEN COUNT(s.id) = 0 THEN 0
                            ELSE 100.0 * COUNT(p.id) FILTER (WHERE p.state IN ('pregnant', 'foaled')) / COUNT(s.id) END AS pregnancy_rate
                  FROM alfia_breeding_service s
                  JOIN alfia_horse h ON h.id = s.stallion_id
             LEFT JOIN alfia_pregnancy p ON p.service_id = s.id
             LEFT JOIN alfia_foaling f ON f.pregnancy_id = p.id
                 WHERE s.stallion_id IS NOT NULL AND s.state = 'done'
              GROUP BY s.stallion_id, s.season, h.company_id
            )""")
