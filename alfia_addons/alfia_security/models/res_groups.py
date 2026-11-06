from odoo import models


class ResGroups(models.Model):
    _inherit = 'res.groups'

    def write(self, vals):
        watched = {'implied_ids', 'privilege_id', 'name'}
        before = {g.id: ', '.join(sorted(g.implied_ids.mapped('display_name'))) for g in self} if watched & set(vals) else {}
        res = super().write(vals)
        for group in self:
            if group.id in before:
                self.env['alfia.security.audit']._alfia_log(
                    'group.write', self._name, group.display_name,
                    "implied before: %s\nimplied after: %s" % (before[group.id] or '-', ', '.join(sorted(group.implied_ids.mapped('display_name'))) or '-'))
        return res
