"""Readable names for records that have no name field of their own (otherwise a link shows 'alfia.x,17')."""
from odoo import api, fields, models


def _date(value):
    return fields.Date.to_string(value.date() if hasattr(value, 'date') and callable(value.date) else value) if value else ''


def _join(*parts):
    return ' – '.join(p for p in parts if p)


class _Named(models.AbstractModel):
    _name = 'alfia.display.mixin'
    _description = 'Readable record names'

    def _alfia_label_parts(self):
        return []

    @api.depends_context('lang')
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = _join(*rec._alfia_label_parts()) or f"#{rec.id or ''}"


def _fresh_compute():
    # A new function object per model: @api.depends stores its fields on the function itself.
    @api.depends_context('lang')
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = _join(*rec._alfia_label_parts()) or f"#{rec.id or ''}"
    return _compute_display_name


def _named(model, parts, depends, module):
    """Give `model` a display name built from `parts(rec)`. `module` is the caller's __name__, so the class belongs to its addon."""
    cls = type(f"Named_{model.replace('.', '_')}", (models.Model,), {
        '__module__': module,
        '_inherit': [model, 'alfia.display.mixin'],
        '_name': model,
        '_alfia_label_parts': lambda self: parts(self),
        '_compute_display_name': api.depends(*depends)(_fresh_compute()),
    })
    return cls


SPEC = {
    'alfia.breeding.service': (lambda r: [r.mare_id.name and f"{r.mare_id.name} × {r.stallion_id.name or r.stallion_name or '?'}", _date(r.date)],
                               ('mare_id.name', 'stallion_id.name', 'stallion_name', 'date')),
    'alfia.ovulation': (lambda r: [r.mare_id.name, _date(r.date)], ('mare_id.name', 'date')),
    'alfia.reproductive.exam': (lambda r: [r.mare_id.name, _date(r.date)], ('mare_id.name', 'date')),
    'alfia.pregnancy.check': (lambda r: [r.mare_id.name, _date(r.date), dict(r._fields['result']._description_selection(r.env)).get(r.result)],
                              ('mare_id.name', 'date', 'result')),
    'alfia.foaling': (lambda r: [r.mare_id.name, r.foal_name, _date(r.date)], ('mare_id.name', 'foal_name', 'date')),
    'alfia.teasing': (lambda r: [r.mare_id.name, _date(r.date), r.symbol_id.code], ('mare_id.name', 'date', 'symbol_id.code')),
    'alfia.horse.movement': (lambda r: [r.horse_id.name, dict(r._fields['kind']._description_selection(r.env)).get(r.kind), _date(r.date)],
                             ('horse_id.name', 'kind', 'date')),
    'alfia.horse.ownership': (lambda r: [r.horse_id.name, r.partner_id.name, _date(r.date_from)], ('horse_id.name', 'partner_id.name', 'date_from')),
    'alfia.horse.status.log': (lambda r: [r.horse_id.name, dict(r._fields['status']._description_selection(r.env)).get(r.status), _date(r.date)],
                               ('horse_id.name', 'status', 'date')),
    'alfia.treatment.dose': (lambda r: [r.treatment_id.drug, r.horse_id.name, _date(r.given_at)], ('treatment_id.drug', 'horse_id.name', 'given_at')),
}
for _model, (_parts, _deps) in SPEC.items():
    _named(_model, _parts, _deps, __name__)
