"""Development tool: writes views/generated_views.xml, views/menus.xml and security/ir.access.csv from one specification.
Run from the module folder:  python3 tools/gen_views.py   (the generated files are committed)."""
import csv
import io
from xml.sax.saxutils import escape

G = 'alfia_security.group_alfia_'
V, VM, B, BM, T, TM, S, AUD, MED = (G + x for x in ('vet_user', 'vet_manager', 'breeding_user', 'breeding_manager', 'training_user', 'training_manager', 'stable_user', 'security_auditor', 'medicine_custodian'))
ACC = 'account.group_account_invoice'
ALL_HORSE = [V, B, T, S, AUD]

SPEC = [
    dict(model='alfia.horse.group', title='Horse groups', menu='horses', list=['name', 'description', 'member_count', 'active'],
         form=['name', 'description', 'active', 'company_id', 'sex', 'status', 'breed', 'reproductive_state', 'min_age_years', 'max_age_years', 'location_id'],
         extra_form='<field name="member_ids"/>', header_buttons=[('action_open_members', 'Show members', '', '')], search=['name'], access=(ALL_HORSE + [BM], [BM, TM, VM], [BM])),
    dict(model='alfia.charge', title='Charges', menu='billing', list=['date', 'horse_id', 'partner_id', 'product_id', 'quantity', 'price_unit', 'amount', 'state'],
         form=['horse_id', 'partner_id', 'product_id', 'name', 'date', 'quantity', 'price_unit', 'amount', 'plan_id', 'move_id', 'state'], search=['horse_id', 'partner_id'], scope='horse_id', chatter=True, status='state',
         buttons=[('action_approve', 'Approve', "state != 'draft'", 'btn-primary'), ('action_invoice', 'Create invoice', "state != 'approved'", 'btn-primary'), ('action_cancel', 'Cancel', "state in ('invoiced','cancelled')", '')],
         modes='list,form,pivot,graph', filters=[('draft', 'To approve', "[('state', '=', 'draft')]"), ('approved', 'Ready to invoice', "[('state', '=', 'approved')]")],
         access=([BM, TM, VM, ACC, AUD], [BM, TM, VM, ACC], [BM])),
    dict(model='alfia.charge.plan', title='Recurring charges', menu='billing', list=['name', 'target_kind', 'product_id', 'price_unit', 'interval', 'next_date', 'active'],
         form=['name', 'active', 'company_id', 'target_kind', 'horse_id', 'location_id', 'group_id', 'partner_id', 'product_id', 'quantity', 'price_unit', 'interval', 'next_date', 'end_date', 'auto_approve'],
         header_buttons=[('action_generate', 'Generate due charges now', '', 'btn-primary')], search=['name'], access=([BM, TM, VM, ACC, AUD], [BM, TM, ACC], [BM])),
    dict(model='alfia.horse.sale', title='Sales and gifts', menu='billing', list=['date', 'horse_id', 'kind', 'buyer_id', 'price', 'state'],
         form=['horse_id', 'kind', 'buyer_id', 'date', 'price', 'currency_id', 'created_by_id', 'approved_by_id', 'move_id', 'state'], long=['notes'], search=['horse_id', 'buyer_id'], chatter=True, status='state',
         buttons=[('action_approve', 'Approve', "state != 'draft'", 'btn-primary'), ('action_complete', 'Complete', "state != 'approved'", 'btn-primary'), ('action_cancel', 'Cancel', "state in ('done','cancelled')", '')],
         access=([BM, TM, VM, ACC, AUD], [BM, TM, VM, ACC], [])),
    dict(model='alfia.breeding.contract', title='Breeding contracts', menu='breeding', list=['name', 'client_id', 'stallion_id', 'season', 'fee', 'outcome', 'state'],
         form=['name', 'client_id', 'stallion_id', 'mare_id', 'season', 'product_id', 'fee', 'live_foal_guarantee', 'refund_percent', 'pregnancy_id', 'outcome', 'invoice_id', 'credit_note_id', 'state'],
         search=['name', 'client_id', 'stallion_id'], scope='stallion_id', chatter=True, status='state',
         buttons=[('action_confirm', 'Confirm', "state != 'draft'", 'btn-primary'), ('action_invoice', 'Create invoice', "state != 'confirmed'", 'btn-primary'), ('action_refund', 'Refund (no live foal)', "state != 'invoiced'", ''),
                  ('action_close', 'Close', "state not in ('invoiced','refunded')", ''), ('action_cancel', 'Cancel', "state in ('invoiced','refunded','closed','cancelled')", '')],
         access=([BM, ACC, AUD, V], [BM, ACC], [])),
    dict(model='alfia.boarding', title='Boarding', menu='care', list=['horse_id', 'client_id', 'product_id', 'rate', 'interval', 'start_date', 'end_date', 'state'],
         form=['horse_id', 'client_id', 'product_id', 'rate', 'interval', 'start_date', 'end_date', 'location_id', 'plan_id', 'state'], long=['notes'], search=['horse_id', 'client_id'], scope='horse_id', chatter=True, status='state',
         buttons=[('action_start', 'Start billing', "state != 'draft'", 'btn-primary'), ('action_end', 'End stay', "state != 'active'", '')], access=([BM, TM, S, ACC, AUD], [BM, TM], [])),
    dict(model='alfia.diet', title='Feed plans', menu='care', list=['horse_id', 'name', 'date_from', 'date_to', 'current'],
         form=['horse_id', 'name', 'date_from', 'date_to'], long=['notes'], extra_form='<field name="line_ids"><list editable="bottom"><field name="product_id"/><field name="qty_per_meal"/><field name="meals_per_day"/><field name="daily_qty"/></list></field>',
         search=['horse_id', 'name'], scope='horse_id', filters=[('current', 'Current', "[('current', '=', True)]")], access=(ALL_HORSE, [S, V], [VM])),
    dict(model='alfia.diet.line', title='Daily feed needs', menu='care', list=['horse_id', 'product_id', 'qty_per_meal', 'meals_per_day', 'daily_qty'],
         form=['diet_id', 'product_id', 'qty_per_meal', 'meals_per_day'], search=['horse_id', 'product_id'], scope='horse_id', modes='list,form,pivot', access=(ALL_HORSE, [S, V], [VM])),
    dict(model='alfia.quarantine', title='Quarantine', menu='health', list=['horse_id', 'start_date', 'planned_end', 'reason', 'isolation_location_id', 'state'],
         form=['horse_id', 'start_date', 'planned_end', 'end_date', 'reason', 'isolation_location_id', 'released_by_id', 'state'], search=['horse_id', 'reason'], scope='horse_id', chatter=True, status='state',
         buttons=[('action_release', 'Release', "state != 'active'", 'btn-primary')], access=(ALL_HORSE, [V], [VM])),
    dict(model='alfia.medicine.issue', title='Medicine issues', menu='health', list=['date', 'horse_id', 'product_id', 'quantity', 'returned_qty', 'location_id', 'state'],
         form=['horse_id', 'date', 'product_id', 'quantity', 'returned_qty', 'location_id', 'treatment_id', 'state'], search=['horse_id', 'product_id'], scope='horse_id', chatter=True, status='state',
         buttons=[('action_issue', 'Issue from stock', "state != 'draft'", 'btn-primary'), ('action_return', 'Return remaining', "state not in ('issued','returned')", '')], access=([V, MED, AUD], [V, MED], [])),
    dict(model='alfia.restriction.rule', title='Entry rules', menu='training', list=['name', 'sex', 'min_age_years', 'max_age_years', 'require_fitness', 'forbid_withdrawal'],
         form=['name', 'active', 'company_id', 'sex', 'min_age_years', 'max_age_years', 'require_fitness', 'forbid_withdrawal', 'forbid_pregnant', 'forbid_quarantine'], search=['name'], access=([V, B, T, AUD], [TM], [TM])),
    dict(model='alfia.event', title='Events', menu='training', list=['date_start', 'name', 'venue', 'discipline', 'entry_count'], form=['name', 'company_id', 'date_start', 'date_end', 'venue', 'discipline', 'rule_id'],
         extra_form='<field name="entry_ids"><list><field name="horse_id"/><field name="rider_id"/><field name="state"/></list></field>', search=['name'], modes='list,form,calendar', chatter=True,
         access=(ALL_HORSE, [TM], [TM])),
    dict(model='alfia.event.entry', title='Event entries', menu='training', list=['event_id', 'horse_id', 'rider_id', 'vet_confirmed', 'state'], form=['event_id', 'horse_id', 'rider_id', 'vet_confirmed', 'decided_by_id', 'state'],
         long=['issues'], search=['horse_id', 'event_id'], scope='horse_id', chatter=True, status='state',
         buttons=[('action_check', 'Check rules', "state in ('approved','rejected')", ''), ('action_submit', 'Submit', "state != 'draft'", 'btn-primary'), ('action_vet_confirm', 'Vet confirms fitness', "state != 'pending'", ''),
                  ('action_approve', 'Approve', "state != 'pending'", 'btn-primary'), ('action_reject', 'Reject', "state in ('approved','rejected')", '')], access=(ALL_HORSE, [T, V], [TM])),
    dict(model='alfia.asset', title='Fleet, trophies, library', menu='facilities', list=['name', 'kind', 'reference', 'responsible_id', 'next_service', 'state'],
         form=['name', 'kind', 'company_id', 'reference', 'responsible_id', 'horse_id', 'acquired', 'value', 'next_service', 'state', 'borrower_id', 'active'], long=['notes'], search=['name', 'reference'], chatter=True, status='state',
         access=(ALL_HORSE, [TM, BM, S], [TM, BM])),
    dict(model='alfia.report.breeding', title='Breeding performance', menu='reports', list=['stallion_id', 'season', 'services', 'pregnancies', 'live_foals', 'pregnancy_rate'], form=None, search=['stallion_id', 'season'],
         scope='stallion_id', modes='list,pivot,graph', access=([BM, AUD], [], [])),
]


def scope_domain(path):
    return f"[('{path}', 'child_of', user.alfia_location_ids.ids)] if user.alfia_scope_locations else []"


def view_xml(s):
    m, t = s['model'], s['title']
    key = m.replace('.', '_')
    lst = ''.join(f'<field name="{f}"/>' for f in s['list'])
    out = f'''
    <record id="{key}_view_list" model="ir.ui.view">
        <field name="name">{m}.list</field>
        <field name="model">{m}</field>
        <field name="arch" type="xml"><list>{lst}</list></field>
    </record>'''
    if s['form']:
        half = (len(s['form']) + 1) // 2
        left = ''.join(f'<field name="{f}"/>' for f in s['form'][:half])
        right = ''.join(f'<field name="{f}"/>' for f in s['form'][half:])
        longs = ''.join(f'<field name="{f}"/>' for f in s.get('long', []))
        btns = ''.join(f'<button name="{n}" string="{escape(l)}" type="object" class="{c}"{(" invisible=" + chr(34) + escape(i) + chr(34)) if i else ""}/>' for n, l, i, c in s.get('buttons', []) + s.get('header_buttons', []))
        status = f'<field name="{s["status"]}" widget="statusbar"/>' if s.get('status') else ''
        header = f'<header>{btns}{status}</header>' if (btns or status) else ''
        chatter = '<chatter/>' if s.get('chatter') else ''
        out += f'''
    <record id="{key}_view_form" model="ir.ui.view">
        <field name="name">{m}.form</field>
        <field name="model">{m}</field>
        <field name="arch" type="xml">
            <form>{header}<sheet><group><group>{left}</group><group>{right}</group></group>{('<group>' + longs + '</group>') if longs else ''}{s.get('extra_form', '')}</sheet>{chatter}</form>
        </field>
    </record>'''
    sf = ''.join(f'<field name="{f}"/>' for f in s.get('search', []))
    flt = ''.join(f'<filter name="{n}" string="{escape(l)}" domain="{escape(d)}"/>' for n, l, d in s.get('filters', []))
    out += f'''
    <record id="{key}_view_search" model="ir.ui.view">
        <field name="name">{m}.search</field>
        <field name="model">{m}</field>
        <field name="arch" type="xml"><search>{sf}{flt}</search></field>
    </record>
    <record id="{key}_action" model="ir.actions.act_window">
        <field name="name">{escape(t)}</field>
        <field name="res_model">{m}</field>
        <field name="view_mode">{s.get('modes', 'list,form' if s['form'] else 'list')}</field>
    </record>'''
    return out


def main():
    out = ['<odoo>'] + [view_xml(s) for s in SPEC] + ['</odoo>']
    open('views/generated_views.xml', 'w').write('\n'.join(out) + '\n')
    sections = {'billing': ('Billing', 25), 'facilities': ('Facilities', 70), 'reports': ('Reports', 80)}
    menu = ['<odoo>']
    for sid, (name, seq) in sections.items():
        menu.append(f'    <menuitem id="menu_ops_{sid}" name="{name}" parent="alfia_equine.menu_alfia_equine_root" sequence="{seq}"/>')
    parents = {'billing': 'menu_ops_billing', 'facilities': 'menu_ops_facilities', 'reports': 'menu_ops_reports', 'horses': 'alfia_equine.menu_alfia_equine_root',
               'care': 'alfia_equine.menu_alfia_care', 'health': 'alfia_equine.menu_alfia_health', 'breeding': 'alfia_equine.menu_alfia_breeding', 'training': 'alfia_equine.menu_alfia_training'}
    n = 200
    for s in SPEC:
        n += 10
        key = s['model'].replace('.', '_')
        menu.append(f'    <menuitem id="menu_{key}" name="{escape(s["title"])}" parent="{parents[s["menu"]]}" action="{key}_action" sequence="{n}"/>')
    menu.append('    <menuitem id="menu_alfia_horse_import" name="Import horses" parent="menu_ops_facilities" action="alfia_horse_import_action" sequence="900" groups="alfia_security.group_alfia_breeding_manager,alfia_security.group_alfia_training_manager,alfia_security.group_alfia_vet_manager"/>')
    menu.append('</odoo>')
    open('views/menus.xml', 'w').write('\n'.join(menu) + '\n')

    rows = [['id', 'name', 'model_id', 'group_id/id', 'operation', 'domain']]
    for s in SPEC:
        read, write, delete = s['access']
        model, key = s['model'], s['model'].replace('.', '_')
        for g in dict.fromkeys(read + write + delete):
            ops = ''.join(c for c, on in (('c', g in write), ('r', True), ('u', g in write), ('d', g in delete)) if on)
            rows.append([f'acc_{key}_{g.split(".")[-1]}', f'{model}: {g.split(".")[-1]}', model, g, ops, ''])
        rows.append([f'acc_{key}_system', f'{model}: system administrators', model, 'base.group_system', 'r' if not write else 'crud', ''])
        if s.get('scope'):
            rows.append([f'scope_{key}', f'Alfia site scope: {model}', model, '', 'crud', scope_domain(f"{s['scope']}.location_id")])
    for g in (BM, TM, VM):
        rows.append([f'acc_import_{g.split("_")[-2]}', 'Import horses', 'alfia.horse.import', g, 'crud', ''])
    for g in (ACC, MED):
        rows.append([f'acc_alfia_horse_ops_{g.split(".")[-1]}', 'Horse names for billing and medicine', 'alfia.horse', g, 'r', ''])
    rows.append(['acc_import_system', 'Import horses: system', 'alfia.horse.import', 'base.group_system', 'crud', ''])
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    open('security/ir.access.csv', 'w', newline='').write(buf.getvalue())


if __name__ == '__main__':
    main()
