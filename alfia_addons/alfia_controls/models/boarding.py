from dateutil.relativedelta import relativedelta

from odoo import Command, api, fields, models

from odoo.addons.alfia_security.flow import workflow

ENDING_STATUSES = ('sold', 'gifted', 'dead')


class AlfiaHorse(models.Model):
    """When a horse leaves (departure, sale, gift, death), recurring charges stop: the period it leaves in is billed for
    the days it stayed, later periods are not billed, and anything already invoiced for after it left is credited."""
    _inherit = 'alfia.horse'

    alfia_billed_until = fields.Date(string='Recurring charges end', readonly=True, copy=False,
                                     help="Set when the horse leaves. Recurring charges are not generated after this date.")

    def write(self, vals):
        leaving = self.filtered(lambda h: h.status not in ENDING_STATUSES) if vals.get('status') in ENDING_STATUSES else self.browse()
        res = super().write(vals)
        if leaving:
            reason = dict(self._fields['status']._description_selection(self.env)).get(vals['status'], vals['status'])
            leaving._alfia_end_recurring_charges(fields.Date.context_today(self), reason)
        return res

    def _alfia_end_recurring_charges(self, end_date, reason):
        Charge = self.env['alfia.charge'].sudo()
        credits = self.env['account.move']
        for horse in self.sudo():
            if horse.alfia_billed_until and horse.alfia_billed_until <= end_date:
                continue
            with workflow():
                horse.write({'alfia_billed_until': end_date})
            notes, unused = [], []
            # A plan for this horse alone ends on the day it left, so the plan itself shows it has stopped.
            for plan in self.env['alfia.charge.plan'].sudo().search([('target_kind', '=', 'horse'), ('horse_id', '=', horse.id),
                                                                     ('active', '=', True)]):
                if not plan.end_date or plan.end_date > end_date:
                    plan.write({'end_date': end_date})
                    notes.append(self.env._("plan %(plan)s ends %(date)s", plan=plan.name, date=end_date))
            for charge in Charge.search([('horse_id', '=', horse.id), ('plan_id', '!=', False), ('state', '!=', 'cancelled')]):
                start = charge.date
                period_end = start + (relativedelta(weeks=1) if charge.plan_id.interval == 'weekly' else relativedelta(months=1))
                if period_end <= end_date + relativedelta(days=1):
                    continue                                        # a period that ended before the horse left
                days = (period_end - start).days
                used = max((end_date - start).days + 1, 0) if start <= end_date else 0
                currency = charge.company_id.currency_id
                # Prorate the price, not the quantity: invoice quantities are rounded to the unit's precision.
                unused_price = currency.round(charge.price_unit * (days - used) / days)
                if charge.state != 'invoiced':
                    if used == 0:
                        with workflow():
                            charge.write({'state': 'cancelled'})
                        notes.append(self.env._("%(date)s cancelled", date=start))
                    else:
                        with workflow():
                            charge.write({'price_unit': currency.round(charge.price_unit * used / days),
                                          'name': self.env._("%(name)s (%(used)s of %(days)s days, left %(end)s)", name=charge.name or '',
                                                             used=used, days=days, end=end_date)})
                        notes.append(self.env._("%(date)s reduced to %(used)s of %(days)s days", date=start, used=used, days=days))
                elif unused_price > 0:
                    unused.append((charge, unused_price, days - used, days))
                    notes.append(self.env._("%(date)s invoiced; credit note drafted for %(unused)s unused days",
                                            date=start, unused=days - used))
            by_invoice = {}
            for item in unused:
                by_invoice.setdefault(item[0].move_id, []).append(item)
            for invoice, items in by_invoice.items():
                credits |= horse._alfia_credit_unused(invoice, items, end_date)
            horse.message_post(body=self.env._("Recurring charges end on %(date)s (%(reason)s). %(details)s",
                                               date=end_date, reason=reason,
                                               details='; '.join(notes) if notes else self.env._("No charge needed adjusting.")))
        return credits

    def _alfia_credit_unused(self, invoice, items, end_date):
        """One draft credit note against `invoice` for the parts of its periods after the horse left; accounting reviews
        and posts it."""
        lines = []
        for charge, price, unused_days, days in items:
            line = charge._alfia_invoice_line_vals()
            line.update(price_unit=price, name=self.env._("%(name)s: %(unused)s of %(days)s days not used after %(end)s",
                                                          name=line.get('name', ''), unused=unused_days, days=days, end=end_date))
            lines.append(Command.create(line))
        first = items[0][0]
        refund = self.env['account.move'].sudo().with_company(first.company_id).create({
            'move_type': 'out_refund', 'partner_id': first.partner_id.id, 'company_id': first.company_id.id,
            'invoice_date': fields.Date.context_today(self), 'reversed_entry_id': invoice.id,
            'ref': self.env._("Unused boarding, %s left", self.name), 'invoice_line_ids': lines})
        refund.message_post(body=self.env._("Drafted when %(horse)s left on %(end)s, for charges on %(invoice)s.",
                                            horse=self.name, end=end_date, invoice=invoice.name))
        return refund


class AlfiaHorseMovement(models.Model):
    _inherit = 'alfia.horse.movement'

    def action_done(self):
        res = super().action_done()
        for move in self.filtered(lambda m: m.state == 'done'):
            day = fields.Date.to_date(fields.Datetime.context_timestamp(move, move.date))
            if move.kind == 'departure':
                move.horse_id._alfia_end_recurring_charges(day, move.reason or self.env._("departure"))
            elif move.kind == 'arrival' and move.horse_id.sudo().alfia_billed_until:
                # Back on the farm: recurring charges resume from the next period (the ones in between stay unbilled).
                # Plans that were ended because it left need a manager to restart them (a new end date, then approval).
                with workflow():
                    move.horse_id.sudo().write({'alfia_billed_until': False})
        return res


class AlfiaCharge(models.Model):
    _inherit = 'alfia.charge'

    @api.model_create_multi
    def create(self, vals_list):
        # Recurring charges are not generated for periods starting after the horse left.
        generated = [v for v in vals_list if v.get('plan_id') and v.get('key')]
        if generated:
            horses = {h.id: h.alfia_billed_until for h in self.env['alfia.horse'].sudo().browse({v['horse_id'] for v in generated})}
            vals_list = [v for v in vals_list if not (v.get('plan_id') and v.get('key') and horses.get(v['horse_id'])
                                                      and fields.Date.to_date(v['date']) > horses[v['horse_id']])]
        return super().create(vals_list)
