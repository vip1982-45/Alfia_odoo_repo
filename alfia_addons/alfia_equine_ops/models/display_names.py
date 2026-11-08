"""Readable names for operations records that have no name field of their own."""
from odoo.addons.alfia_equine.models.display_names import _date, _named

SPEC = {
    'alfia.charge': (lambda r: [r.horse_id.name, r.name or r.product_id.name, _date(r.date)], ('horse_id.name', 'name', 'product_id.name', 'date')),
    'alfia.horse.sale': (lambda r: [r.horse_id.name, r.buyer_id.name, _date(r.date)], ('horse_id.name', 'buyer_id.name', 'date')),
    'alfia.boarding': (lambda r: [r.horse_id.name, r.client_id.name, _date(r.start_date)], ('horse_id.name', 'client_id.name', 'start_date')),
    'alfia.diet.line': (lambda r: [r.horse_id.name, r.product_id.name], ('horse_id.name', 'product_id.name')),
    'alfia.quarantine': (lambda r: [r.horse_id.name, r.reason, _date(r.start_date)], ('horse_id.name', 'reason', 'start_date')),
    'alfia.medicine.issue': (lambda r: [r.horse_id.name, r.product_id.name, _date(r.date)], ('horse_id.name', 'product_id.name', 'date')),
    'alfia.event.entry': (lambda r: [r.event_id.name, r.horse_id.name], ('event_id.name', 'horse_id.name')),
}
for _model, (_parts, _deps) in SPEC.items():
    _named(_model, _parts, _deps, __name__)
