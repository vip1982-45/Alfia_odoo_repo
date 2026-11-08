from odoo import models


class ResCompany(models.Model):
    _inherit = 'res.company'

    def _alfia_finance_setup(self):
        """Stud-ready defaults, applied once at installation (each can be changed later in the settings):
        - perpetual (Anglo-Saxon) accounting: cost of goods sold is booked with the customer invoice;
        - stock valued in real time at average cost, so every receipt, issue and adjustment posts to the general ledger;
        - receipts in two steps (input, then stock) and deliveries in two (pick, then ship), giving pick lists and reservations;
        - analytic plans for allocating costs to horses and to sites."""
        for company in self:
            # Cost of goods sold is booked when goods are invoiced (perpetual inventory), whichever chart is used.
            if not company.anglo_saxon_accounting:
                company.anglo_saxon_accounting = True
            categories = self.env['product.category'].with_company(company).search([])
            categories.write({'property_cost_method': 'average', 'property_valuation': 'real_time'})
            # Categories created later start the same way.
            Default = self.env['ir.default'].sudo()
            Default.set('product.category', 'property_cost_method', 'average', company_id=company.id)
            Default.set('product.category', 'property_valuation', 'real_time', company_id=company.id)
            for warehouse in self.env['stock.warehouse'].search([('company_id', '=', company.id)]):
                warehouse.write({'reception_steps': 'two_steps', 'delivery_steps': 'pick_ship'})
        Plan = self.env['account.analytic.plan'].sudo()
        for name in ('Horses', 'Stud sites'):
            if not Plan.search_count([('name', '=', name)]):
                Plan.create({'name': name, 'default_applicability': 'optional'})
