"""Development tool: writes views/generated_views.xml, views/menus.xml and security/ir.access.csv from one specification.
Run from the module folder:  python3 tools/gen_views.py
The generated files are committed; the module does not run this at install time."""
import csv
from xml.sax.saxutils import escape

G = 'alfia_security.group_alfia_'
V, VM, B, BM, T, TM, S, AUD = (G + x for x in ('vet_user', 'vet_manager', 'breeding_user', 'breeding_manager', 'training_user', 'training_manager', 'stable_user', 'security_auditor'))
CUST = G + 'semen_custodian'
HORSE_PATH = {'horse_id': 'horse_id.location_id', 'mare_id': 'mare_id.location_id', 'stallion_id': 'stallion_id.location_id', 'donor_id': 'donor_id.location_id'}

# model: title, menu section, list, form, long (full-width fields), search, scope field, chatter, header buttons, access
# access = (read groups, write groups (create+update), delete groups)
SPEC = [
    dict(model='alfia.task', title='Work items', menu='work', list=['due_date', 'horse_id', 'name', 'kind', 'urgent', 'assigned_group_id', 'state'],
         form=['name', 'horse_id', 'kind', 'due_date', 'urgent', 'assigned_user_id', 'assigned_group_id', 'state'], long=['notes'], search=['name', 'horse_id'],
         scope='horse_id', chatter=True, status='state',
         buttons=[('action_done', 'Mark done', "state in ('done','cancelled')", 'btn-primary'), ('action_defer', 'Defer', "state != 'todo'", ''), ('action_cancel', 'Cancel', "state in ('done','cancelled')", '')],
         access=([V, B, T, S, AUD], [V, B, T, S], [BM]), context="{'search_default_todo': 1}",
         filters=[('todo', 'To do', "[('state', '=', 'todo')]"), ('overdue', 'Overdue', "[('overdue', '=', True)]"), ('urgent', 'Urgent', "[('urgent', '=', True)]")]),
    dict(model='alfia.health.record', title='Health records', menu='health', list=['date', 'horse_id', 'type', 'title', 'vet_id', 'next_due', 'cost'],
         form=['horse_id', 'date', 'type', 'title', 'vet_id', 'cost', 'next_due'], long=['description'], search=['title', 'horse_id'], scope='horse_id', access=([V, B, AUD], [V], [VM])),
    dict(model='alfia.vet.visit', title='Vet visits', menu='health', list=['date', 'horse_id', 'vet_id', 'reason', 'follow_up', 'cost'],
         form=['horse_id', 'date', 'vet_id', 'reason', 'follow_up', 'cost'], long=['diagnosis', 'treatment_given'], search=['reason', 'horse_id'], scope='horse_id', access=([V, B, AUD], [V], [VM])),
    dict(model='alfia.treatment', title='Treatments', menu='health', list=['start_date', 'horse_id', 'drug', 'dose', 'route', 'frequency', 'withdrawal_until', 'given_count'],
         form=['horse_id', 'drug', 'dose', 'route', 'frequency', 'vet_id', 'start_date', 'end_date', 'withdrawal_days', 'withdrawal_until'], long=['notes'],
         extra_form='<field name="dose_ids"><list editable="bottom"><field name="given_at"/><field name="dose_given"/><field name="missed"/><field name="notes"/></list></field>',
         search=['drug', 'horse_id'], scope='horse_id', access=([V, B, S, AUD], [V], [VM])),
    dict(model='alfia.treatment.dose', title='Doses given', menu=None, list=['given_at', 'horse_id', 'treatment_id', 'dose_given', 'missed', 'given_by_id'],
         form=['treatment_id', 'given_at', 'dose_given', 'missed', 'notes'], search=['treatment_id'], scope='horse_id', access=([V, B, S, AUD], [V, S], [VM])),
    dict(model='alfia.lab.test', title='Laboratory tests', menu='health', list=['date', 'horse_id', 'test', 'lab', 'result', 'state'],
         form=['horse_id', 'date', 'test', 'lab', 'vet_id', 'result', 'reference_range', 'state', 'reviewed_by_id'], long=['notes'], search=['test', 'horse_id'], scope='horse_id', status='state',
         buttons=[('action_review', 'Vet review', "state == 'reviewed'", 'btn-primary')], access=([V, B, AUD], [V], [VM])),
    dict(model='alfia.diag.image', title='Diagnostic images', menu='health', list=['date', 'horse_id', 'modality', 'body_part', 'vet_id'],
         form=['horse_id', 'date', 'modality', 'body_part', 'vet_id', 'attachment_ids'], long=['findings'], search=['body_part', 'horse_id'], scope='horse_id', access=([V, B, AUD], [V], [VM])),
    dict(model='alfia.odontology', title='Dental exams', menu='health', list=['date', 'horse_id', 'vet_id', 'floated', 'next_date'],
         form=['horse_id', 'date', 'vet_id', 'floated', 'extractions', 'next_date', 'cost'], long=['findings'], search=['horse_id'], scope='horse_id', access=([V, B, AUD], [V], [VM])),
    dict(model='alfia.farrier.visit', title='Farrier', menu='care', list=['date', 'horse_id', 'farrier_id', 'lf_shoe', 'rf_shoe', 'lh_shoe', 'rh_shoe', 'next_date'],
         form=['horse_id', 'date', 'farrier_id', 'lf_shoe', 'rf_shoe', 'lh_shoe', 'rh_shoe', 'materials', 'cost', 'next_date'], long=['findings'], search=['horse_id'], scope='horse_id', access=([V, B, T, S, AUD], [V, S], [VM])),
    dict(model='alfia.measurement', title='Weight and height', menu='care', list=['date', 'horse_id', 'weight', 'height', 'girth'],
         form=['horse_id', 'date', 'weight', 'height', 'girth', 'notes'], search=['horse_id'], scope='horse_id', access=([V, B, T, S, AUD], [V, S, T], [VM, BM])),
    dict(model='alfia.temperature', title='Temperature', menu='care', list=['taken_at', 'horse_id', 'temperature', 'pulse', 'respiration', 'notes'],
         form=['horse_id', 'taken_at', 'temperature', 'pulse', 'respiration', 'notes'], search=['horse_id'], scope='horse_id', access=([V, B, T, S, AUD], [V, S, T], [VM, BM])),
    dict(model='alfia.horse.note', title='Notes', menu='care', list=['date', 'horse_id', 'content', 'recorded_by_id'], form=['horse_id', 'date'], long=['content'], search=['content', 'horse_id'], scope='horse_id',
         access=([V, B, T, S, AUD], [V, S, T, B], [VM, BM])),
    dict(model='alfia.clearance', title='Fitness and eligibility', menu='training', list=['date', 'horse_id', 'kind', 'state', 'restricted_until', 'decided_by_id'],
         form=['horse_id', 'kind', 'date', 'state', 'restricted_until', 'decided_by_id'], long=['notes'], search=['horse_id'], scope='horse_id', status='state',
         buttons=[('action_clear', 'Clear', "state == 'cleared'", 'btn-primary'), ('action_restrict', 'Restrict', "state == 'restricted'", '')], access=([V, B, T, S, AUD], [V, TM], [])),
    dict(model='alfia.training.session', title='Training sessions', menu='training', list=['date', 'horse_id', 'program', 'gait', 'distance_km', 'duration_min', 'effort', 'rest_day'],
         form=['horse_id', 'date', 'program', 'trainer_id', 'gait', 'distance_km', 'duration_min', 'effort', 'horse_weight', 'rest_day', 'entry_mode'], long=['notes'], search=['program', 'horse_id'],
         scope='horse_id', access=([V, T, AUD], [T], [TM])),
    dict(model='alfia.competition', title='Competitions', menu='training', list=['date', 'horse_id', 'event', 'discipline', 'result', 'score', 'prize_money'],
         form=['horse_id', 'date', 'event', 'location', 'discipline', 'class_name', 'rider_id', 'result', 'score', 'prize_money'], long=['notes'], search=['event', 'horse_id'],
         scope='horse_id', access=([V, B, T, AUD], [T], [TM])),
    dict(model='alfia.scoring', title='Assessments', menu='training', list=['date', 'horse_id', 'assessor_id', 'score', 'movement', 'conformation', 'temperament'],
         form=['horse_id', 'date', 'assessor_id', 'score', 'movement', 'conformation', 'temperament'], long=['notes'], search=['horse_id'], scope='horse_id', access=([V, B, T, AUD], [T], [TM])),
    dict(model='alfia.teasing', title='Teasing chart', menu='breeding', list=['date', 'mare_id', 'symbol_id', 'is_heat', 'observer_id', 'notes'], form=['mare_id', 'date', 'symbol_id', 'notes'],
         search=['mare_id'], scope='mare_id', access=([V, B, S, AUD], [S, B, V], [BM]),
         filters=[('heat', 'Heat observed', "[('is_heat', '=', True)]")]),
    dict(model='alfia.breeding.service', title='Services', menu='breeding', list=['date', 'mare_id', 'stallion_id', 'stallion_name', 'method', 'season', 'state'],
         form=['mare_id', 'stallion_id', 'stallion_name', 'date', 'method', 'semen_lot_id', 'state'], long=['notes'], search=['mare_id', 'stallion_id'], scope='mare_id', chatter=True, status='state',
         buttons=[('action_done', 'Mark done', "state != 'planned'", 'btn-primary'), ('action_cancel', 'Cancel', "state != 'planned'", '')], access=([V, B, S, AUD], [B, V], [BM])),
    dict(model='alfia.ovulation', title='Ovulations', menu='breeding', list=['date', 'mare_id', 'service_id', 'state', 'pregnancy_id'], form=['mare_id', 'service_id', 'date', 'state', 'confirmed_by_id', 'pregnancy_id'],
         long=['notes'], search=['mare_id'], scope='mare_id', chatter=True, status='state', buttons=[('action_confirm', 'Vet confirms ovulation', "state == 'confirmed'", 'btn-primary')], access=([V, B, AUD], [V, B], [BM])),
    dict(model='alfia.reproductive.exam', title='Reproductive exams', menu='breeding', list=['date', 'mare_id', 'left_follicle_mm', 'right_follicle_mm', 'edema', 'cervix', 'next_action'],
         form=['mare_id', 'date', 'left_follicle_mm', 'right_follicle_mm', 'edema', 'cervix', 'service_id', 'next_action'], long=['findings'], search=['mare_id'], scope='mare_id', access=([V, B, AUD], [V], [VM])),
    dict(model='alfia.pregnancy', title='Pregnancies', menu='breeding', list=['ovulation_date', 'mare_id', 'genetic_dam_id', 'sire_id', 'expected_foaling', 'state', 'day_count'],
         form=['mare_id', 'genetic_dam_id', 'sire_id', 'service_id', 'ovulation_date', 'rule_id', 'expected_foaling', 'state'], search=['mare_id'], scope='mare_id', chatter=True, status='state',
         extra_form='<field name="check_ids"><list><field name="date"/><field name="days_after_ovulation"/><field name="result"/><field name="next_check_date"/><field name="vet_id"/></list></field><field name="task_ids"><list><field name="due_date"/><field name="name"/><field name="state"/></list></field>',
         access=([V, B, S, AUD], [V, B], [])),
    dict(model='alfia.pregnancy.check', title='Pregnancy checks', menu='breeding', list=['date', 'mare_id', 'days_after_ovulation', 'result', 'next_check_date', 'vet_id'],
         form=['pregnancy_id', 'task_id', 'date', 'result', 'next_check_date'], long=['notes'], search=['mare_id'], scope='mare_id', access=([V, B, S, AUD], [V], [])),
    dict(model='alfia.foaling', title='Foalings', menu='breeding', list=['date', 'mare_id', 'outcome', 'foal_name', 'state', 'foal_id'],
         form=['pregnancy_id', 'date', 'outcome', 'foal_sex', 'foal_name', 'complications', 'state', 'foal_id'], long=['notes'], search=['mare_id', 'foal_name'], scope='mare_id', chatter=True, status='state',
         buttons=[('action_vet_confirm', 'Vet confirms birth', "state != 'draft'", 'btn-primary'), ('action_complete', 'Complete foal identity', "state != 'vet_confirmed'", 'btn-primary')], access=([V, B, S, AUD], [S, V, B], [BM])),
    dict(model='alfia.embryo', title='Embryos', menu='breeding', list=['name', 'donor_id', 'sire_id', 'flush_date', 'grade', 'state', 'tank_id'], form=['name', 'donor_id', 'sire_id', 'flush_date', 'stage', 'grade', 'state', 'tank_id', 'pregnancy_id'],
         long=['notes'], search=['name', 'donor_id'], scope=('donor_id', 'tank_id'), chatter=True, status='state',
         buttons=[('action_store', 'Freeze / store', "state != 'collected'", ''), ('action_discard', 'Discard', "state in ('transferred','discarded')", '')], access=([V, B, AUD], [V, B], [BM])),
    dict(model='alfia.semen.collection', title='Semen collections', menu='breeding', list=['date', 'name', 'stallion_id', 'doses', 'motility', 'concentration', 'tank_id', 'state'],
         form=['stallion_id', 'date', 'volume_ml', 'concentration', 'motility', 'morphology', 'doses', 'product_id', 'tank_id', 'state', 'lot_id', 'released_by_id'], long=['notes'], search=['name', 'stallion_id'],
         scope=('stallion_id', 'tank_id'), chatter=True, status='state',
         buttons=[('action_release', 'Release batch', "state != 'draft'", 'btn-primary'), ('action_reject', 'Reject', "state != 'draft'", '')], access=([V, B, CUST, AUD], [V, B], [])),
    dict(model='alfia.breeding.rule', title='Breeding schedule rule sets', menu='config', list=['name', 'version', 'state', 'check_offsets', 'gestation_days', 'approved_by_id'],
         form=['name', 'version', 'state', 'check_offsets', 'gestation_days', 'prep_days', 'vaccine_months', 'season_start_month', 'season_start_day', 'season_end_month', 'season_end_day', 'teasing_pregnant_until_day', 'approved_by_id', 'approved_date'],
         long=['day_zero_notes'], search=['name'], chatter=True, status='state',
         buttons=[('action_approve', 'Approve (resident vet)', "state != 'draft'", 'btn-primary'), ('action_new_version', 'New version', "state == 'draft'", '')], access=([V, B, AUD], [VM], [])),
    dict(model='alfia.teasing.symbol', title='Teasing chart symbols', menu='config', list=['sequence', 'code', 'name', 'is_heat'], form=['code', 'name', 'sequence', 'is_heat'], search=['name', 'code'], access=([V, B, S, AUD], [BM], [BM])),
    dict(model='alfia.horse.ownership', title='Ownership', menu=None, list=['horse_id', 'partner_id', 'share', 'date_from', 'date_to', 'reason'], form=['horse_id', 'partner_id', 'share', 'date_from', 'date_to', 'reason'],
         search=['horse_id', 'partner_id'], scope='horse_id', access=([V, B, T, S, AUD], [BM, TM], [BM])),
    dict(model='alfia.horse.movement', title='Movements', menu='horses', list=['date', 'horse_id', 'kind', 'location_from_id', 'location_to_id', 'partner_id', 'state'],
         form=['horse_id', 'kind', 'date', 'location_to_id', 'partner_id', 'responsible_id', 'reason', 'state'], search=['horse_id'], scope='horse_id', status='state',
         buttons=[('action_done', 'Complete', "state != 'planned'", 'btn-primary'), ('action_cancel', 'Cancel', "state != 'planned'", '')], access=([V, B, T, S, AUD], [S, BM, TM, VM], [])),
    dict(model='alfia.horse.status.log', title='Status history', menu=None, list=['date', 'horse_id', 'status', 'user_id', 'reason'], form=['horse_id', 'status', 'date'], search=['horse_id'], scope='horse_id', access=([V, B, T, S, AUD], [], [])),
]
HORSE_READ = [V, B, T, S, AUD]


def scope_domain(path):
    """`path` is one location path, or a tuple of them: the record is in scope when any of them is in the user's sites."""
    paths = path if isinstance(path, tuple) else (path,)
    terms = ["'|'"] * (len(paths) - 1) + [f"('{p}', 'child_of', user.alfia_location_ids.ids)" for p in paths]
    return f"[{', '.join(terms)}] if user.alfia_scope_locations else []"


def view_xml(s):
    m, t = s['model'], s['title']
    key = m.replace('.', '_')
    lst = ''.join(f'<field name="{f}"/>' for f in s['list'])
    half = (len(s['form']) + 1) // 2
    left = ''.join(f'<field name="{f}"/>' for f in s['form'][:half])
    right = ''.join(f'<field name="{f}"/>' for f in s['form'][half:])
    longs = ''.join(f'<field name="{f}"/>' for f in s.get('long', []))
    header = ''
    if s.get('buttons') or s.get('status'):
        btns = ''.join(f'<button name="{n}" string="{escape(l)}" type="object" class="{c}" invisible="{escape(i)}"/>' for n, l, i, c in s.get('buttons', []))
        status = f'<field name="{s["status"]}" widget="statusbar"/>' if s.get('status') else ''
        header = f'<header>{btns}{status}</header>'
    chatter = '<chatter/>' if s.get('chatter') else ''
    extra = s.get('extra_form', '')
    search_fields = ''.join(f'<field name="{f}"/>' for f in s.get('search', []))
    ctx = f'\n        <field name="context">{escape(s["context"])}</field>' if s.get('context') else ''
    filters = ''.join(f'<filter name="{n}" string="{escape(l)}" domain="{escape(d)}"/>' for n, l, d in s.get('filters', []))
    return f'''
    <record id="{key}_view_list" model="ir.ui.view">
        <field name="name">{m}.list</field>
        <field name="model">{m}</field>
        <field name="arch" type="xml"><list>{lst}</list></field>
    </record>
    <record id="{key}_view_form" model="ir.ui.view">
        <field name="name">{m}.form</field>
        <field name="model">{m}</field>
        <field name="arch" type="xml">
            <form>{header}<sheet><group><group>{left}</group><group>{right}</group></group>{('<group>' + longs + '</group>') if longs else ''}{extra}</sheet>{chatter}</form>
        </field>
    </record>
    <record id="{key}_view_search" model="ir.ui.view">
        <field name="name">{m}.search</field>
        <field name="model">{m}</field>
        <field name="arch" type="xml"><search>{search_fields}{filters}</search></field>
    </record>
    <record id="{key}_action" model="ir.actions.act_window">
        <field name="name">{escape(t)}</field>
        <field name="res_model">{m}</field>
        <field name="view_mode">list,form</field>{ctx}
    </record>'''


def main():
    out = ['<odoo>']
    for s in SPEC:
        out.append(view_xml(s))
    out.append('</odoo>')
    open('views/generated_views.xml', 'w').write('\n'.join(out) + '\n')

    sections = {'horses': ('Horses', 10), 'work': ('Daily work', 20), 'care': ('Daily care', 30), 'health': ('Health and veterinary', 40),
                'breeding': ('Breeding', 50), 'training': ('Training and competition', 60), 'config': ('Configuration', 90)}
    menu = ['<odoo>', '    <menuitem id="menu_alfia_equine_root" name="Stud" sequence="1" web_icon="base,static/description/icon.png" groups="alfia_security.group_alfia_breeding_user,alfia_security.group_alfia_training_user,alfia_security.group_alfia_vet_user,alfia_security.group_alfia_stable_user,alfia_security.group_alfia_security_auditor,base.group_system"/>',
            '    <menuitem id="menu_alfia_horses" name="Horses" parent="menu_alfia_equine_root" action="alfia_horse_action" sequence="5"/>']
    for sid, (name, seq) in sections.items():
        if sid == 'horses':
            continue
        menu.append(f'    <menuitem id="menu_alfia_{sid}" name="{name}" parent="menu_alfia_equine_root" sequence="{seq}"/>')
    n = 0
    for s in SPEC:
        if s.get('menu'):
            n += 10
            parent = 'menu_alfia_equine_root' if s['menu'] == 'horses' else f"menu_alfia_{s['menu']}"
            menu.append(f'    <menuitem id="menu_{s["model"].replace(".", "_")}" name="{escape(s["title"])}" parent="{parent}" action="{s["model"].replace(".", "_")}_action" sequence="{n}"/>')
    menu.append('</odoo>')
    open('views/menus.xml', 'w').write('\n'.join(menu) + '\n')

    rows = [['id', 'name', 'model_id', 'group_id/id', 'operation', 'domain']]
    def add(rid, name, model, group, op, domain=''):
        rows.append([rid, name, model, group, op, domain])
    models = [(s['model'], s['access'], s.get('scope')) for s in SPEC]
    horse_access = (HORSE_READ, [BM, TM, VM], [BM])
    models.append(('alfia.horse', horse_access, 'LOCATION'))
    for model, (read, write, delete), scope in models:
        key = model.replace('.', '_')
        for g in dict.fromkeys(read + write + delete):
            ops = ''.join(letter for letter, on in (('c', g in write), ('r', True), ('u', g in write), ('d', g in delete)) if on)
            add(f'acc_{key}_{g.split(".")[-1]}', f'{model}: {g.split(".")[-1]}', model, g, ops)
        add(f'acc_{key}_system', f'{model}: system administrators', model, 'base.group_system', 'crud')
        if scope:
            if scope == 'LOCATION':
                path = 'location_id'
            elif isinstance(scope, tuple):
                # A horse path, or a storage location field (tank_id) used as it is.
                path = tuple(HORSE_PATH.get(f, f if f.endswith('tank_id') else f + '.location_id') for f in scope)
            else:
                path = HORSE_PATH.get(scope, scope + '.location_id')
            if scope == 'LOCATION':
                # Horses not on the farm (reference ancestors, sold or departed) carry no site: everyone who reads horses may
                # read them, so pedigrees always open; changing a horse still needs it to be in one of the user's sites.
                add(f'scope_{key}', f'Alfia site scope: {model} (changes)', model, '', 'cud', scope_domain(path))
                add(f'scope_{key}_read', f'Alfia site scope: {model} (reading)', model, '', 'r',
                    "['|', ('location_id', '=', False), ('location_id', 'child_of', user.alfia_location_ids.ids)] if user.alfia_scope_locations else []")
            else:
                add(f'scope_{key}', f'Alfia site scope: {model}', model, '', 'crud', scope_domain(path))
    for g, gk in ((BM, 'bm'), (VM, 'vm'), (TM, 'tm')):
        add(f'acc_merge_{gk}', 'Merge horses', 'alfia.horse.merge', g, 'crud')
    open('security/ir.access.csv', 'w', newline='').writelines(csv_lines(rows))


def csv_lines(rows):
    import io
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    return [buf.getvalue()]


if __name__ == '__main__':
    main()
