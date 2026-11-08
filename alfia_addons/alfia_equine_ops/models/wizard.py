import csv
import io

from odoo import fields, models

MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 20000
from odoo.exceptions import UserError


class AlfiaHorseImport(models.TransientModel):
    """Create horses from a CSV file: name, sex, birthdate (YYYY-MM-DD), breed, color. One bad row never blocks the others."""
    _name = 'alfia.horse.import'
    _description = 'Import horses from CSV'

    file = fields.Binary(required=True)
    filename = fields.Char()
    skip_existing = fields.Boolean(default=True, help="Rows whose name already exists are skipped instead of duplicated.")
    result = fields.Text(readonly=True)

    SEX = {'f': 'female', 'female': 'female', 'mare': 'female', 'm': 'male', 'male': 'male', 'stallion': 'male', 'colt': 'male', 'g': 'gelding', 'gelding': 'gelding'}

    def action_import(self):
        self.ensure_one()
        raw = bytes(self.file or b'')
        if len(raw) > MAX_BYTES:
            raise UserError(self.env._("The file is larger than %s MB. Split it into smaller files.", MAX_BYTES // (1024 * 1024)))
        try:
            text = raw.decode('utf-8-sig')
        except Exception:
            raise UserError(self.env._("The file is not a UTF-8 text file."))
        reader = csv.DictReader(io.StringIO(text))
        cols = {(c or '').strip().lower() for c in (reader.fieldnames or [])}
        if not {'name', 'sex'} <= cols:
            raise UserError(self.env._("The first row must contain at least the columns: name, sex."))
        Horse = self.env['alfia.horse']
        created, skipped, errors = 0, 0, []
        for n, raw in enumerate(reader, start=2):
            if n - 1 > MAX_ROWS:
                errors.append(self.env._("Stopped after %s rows; import the rest in another file.", MAX_ROWS))
                break
            row = {(k or '').strip().lower(): (v or '').strip() for k, v in raw.items()}
            try:
                if not row.get('name'):
                    raise ValueError(self.env._("name is empty"))
                if self.skip_existing and Horse.search_count([('name', '=ilike', row['name'])]):
                    skipped += 1
                    continue
                sex = self.SEX.get(row.get('sex', '').lower())
                if not sex:
                    raise ValueError(self.env._("unknown sex '%s'", row.get('sex', '')))
                vals = {'name': row['name'], 'sex': sex, 'breed': row.get('breed') or False}
                if row.get('birthdate'):
                    vals['birthdate'] = fields.Date.from_string(row['birthdate'])
                with self.env.cr.savepoint():
                    Horse.create(vals)
                created += 1
            except Exception as exc:  # report and carry on with the next row
                errors.append(self.env._("Row %(n)s: %(err)s", n=n, err=str(exc).splitlines()[0] if str(exc) else type(exc).__name__))
        self.result = self.env._("Created %(c)s, skipped %(s)s, failed %(f)s.", c=created, s=skipped, f=len(errors)) + ('\n' + '\n'.join(errors) if errors else '')
        return {'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': self.id, 'view_mode': 'form', 'target': 'new'}
