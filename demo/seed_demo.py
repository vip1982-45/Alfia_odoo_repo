"""Fill an empty Alfia database with a demonstration stud farm, for end-user evaluation.

Run once, in an Odoo shell, on a database where alfia_equine_ops is installed and nothing else was entered:

    python3 odoo-bin shell -c odoo.conf -d alfia_demo < demo/seed_demo.py

Every name, number and person here is invented. All demo accounts share one password (DEMO_PASSWORD below);
change it, or delete the accounts, before the system holds real data.
"""
import logging
import os

from dateutil.relativedelta import relativedelta

from odoo import Command, fields

_logger = logging.getLogger('alfia.demo')
DEMO_PASSWORD = os.environ.get('ALFIA_DEMO_PASSWORD') or 'AlfiaDemo-2026'
# On a server others can reach, the administrator gets a password of its own (deploy/.env).
ADMIN_PASSWORD = os.environ.get('ALFIA_ADMIN_PASSWORD') or DEMO_PASSWORD
E = env(context=dict(env.context, lang='ar_001', tz='Asia/Qatar'))  # noqa: F821 - generated work items and history are written in Arabic
ref = E.ref
TODAY = fields.Date.context_today(E['res.users'])


def d(days):
    return TODAY + relativedelta(days=days)


if E['alfia.horse'].search_count([]):
    raise SystemExit("This database already has horses; the demo is seeded only into an empty database.")

# English is the working language; Arabic is added alongside (with its translations) and can be chosen in each
# person's preferences.
E['res.lang']._activate_and_install_lang('ar_001')
E['ir.default'].set('res.partner', 'lang', 'ar_001')
E['ir.module.module'].search([('state', '=', 'installed')])._update_translations(['ar_001'])
company = E.company
company.write({'name': 'مربط ألفية · Alfia Stud Farm (Demo)', 'email': 'stud@example.com', 'phone': '+974 0000 0000', 'city': 'Doha'})
qar = E['res.currency'].with_context(active_test=False).search([('name', '=', 'QAR')], limit=1)
if qar and not E['account.move'].search_count([]):
    qar.active = True
    company.currency_id = qar

# ------------------------------------------------------------------ sites
wh = E['stock.warehouse'].search([('company_id', '=', company.id)], limit=1)
wh.write({'name': 'المربط أ · Stud A', 'code': 'STA'})
Loc = E['stock.location']
root = wh.view_location_id


def loc(name, parent=None):
    return Loc.create({'name': name, 'location_id': (parent or root).id, 'usage': 'internal'})


barn_a, barn_b = loc('إسطبل أ (الأفراس) · Barn A (mares)'), loc('إسطبل ب (الفحول) · Barn B (stallions)')
foaling, quarantine = loc('وحدة الولادة · Foaling unit'), loc('إسطبلات الحجر الصحي · Quarantine stalls')
yard = loc('ساحة التدريب · Training yard')
tank_room = loc('غرفة خزانات السائل المنوي · Semen tank room')
tank_1 = loc('الخزان 1 · Tank 1', tank_room)
med_store = loc('مخزن الأدوية · Medicine store')
feed_store = loc('مخزن الأعلاف · Feed store', wh.lot_stock_id)   # inside the warehouse's stock, so sales orders can reserve feed

# ------------------------------------------------------------------ people
P = E['res.partner']
alfia = P.create({'name': 'ألفية القابضة · Alfia Holding', 'is_company': True, 'email': 'owner@example.com'})
client_1 = P.create({'name': 'الريان للفروسية · Al Rayyan Equestrian (client)', 'is_company': True, 'email': 'rayyan@example.com'})
client_2 = P.create({'name': 'الشيخة ن. (عميلة خاصة) · Sheikha N. (private client)', 'email': 'client2@example.com'})
buyer = P.create({'name': 'وردة الصحراء للخيل العربية · Desert Rose Arabians (buyer)', 'is_company': True, 'email': 'buyer@example.com'})
vendor_feed = P.create({'name': 'الخليج لتوريد الأعلاف · Gulf Feed Supplies', 'is_company': True, 'supplier_rank': 1, 'email': 'feed@example.com'})
vendor_vet = P.create({'name': 'فيت فارم للتجارة · VetPharm Trading', 'is_company': True, 'supplier_rank': 1, 'email': 'pharma@example.com'})
dr_ext = P.create({'name': 'د. طبيب بيطري زائر (خارجي)'})
farrier = P.create({'name': 'شركة خدمات البيطرة'})
rider = P.create({'name': 'الفارس الأول'})

# Starter profiles get the sites they work in.
prof = lambda x: ref(f'alfia_security.profile_{x}')  # noqa: E731
everywhere = [barn_a, barn_b, foaling, quarantine, yard]
prof('stable_staff').location_ids = [Command.set([barn_a.id])]
for name in ('breeding_staff', 'breeding_manager', 'vet_staff', 'resident_vet'):
    prof(name).location_ids = [Command.set([l.id for l in everywhere + [tank_room, med_store]])]
for name in ('training_staff', 'training_manager'):
    prof(name).location_ids = [Command.set([barn_b.id, yard.id])]
prof('semen_custodian').location_ids = [Command.set([tank_room.id])]
prof('dispatch_admin_approver').location_ids = [Command.set([tank_room.id])]
for name in ('storekeeper', 'stock_controller', 'procurement_officer', 'procurement_approver'):
    prof(name).write({'location_ids': [Command.set([feed_store.id, med_store.id])], 'warehouse_ids': [Command.set([wh.id])]})
Profile = E['alfia.access.profile']
med_profile = Profile.create({'name': 'Medicine custodian – medicine store', 'location_ids': [Command.set([med_store.id] + [l.id for l in everywhere])],
                              'group_ids': [Command.set([ref('alfia_security.group_alfia_medicine_custodian').id, ref('alfia_security.group_alfia_vet_user').id])]})
vet_approver = Profile.create({'name': 'Dispatch approver (veterinary)', 'location_ids': [Command.set([tank_room.id])],
                               'group_ids': [Command.set([ref('alfia_security.group_alfia_dispatch_vet_approver').id])]})
groom_b = Profile.create({'name': 'Stable staff – Barn B', 'location_ids': [Command.set([barn_b.id])],
                          'group_ids': [Command.set(prof('stable_staff').group_ids.ids)]})
journals = E['account.journal'].search([('company_id', '=', company.id)])
all_sites = ref('alfia_security.group_alfia_all_locations')
for name in ('ap_clerk', 'ar_clerk', 'accountant', 'finance_controller'):
    if not prof(name).journal_ids:
        prof(name).journal_ids = [Command.set(journals.ids)]
# Billing covers every horse, so finance roles are not limited to sites (the site scope still applies to everyone else).
for name in ('ar_clerk', 'accountant', 'finance_controller'):
    prof(name).group_ids = [Command.link(all_sites.id)]

Users = E['res.users'].with_context(no_reset_password=True)
USERS = [
    # login, name, profiles, extra groups (granted directly by the administrator)
    ('officer', 'مسؤول الأمن', [], ['alfia_security.group_alfia_security_officer']),
    ('auditor', 'المدقق الداخلي', [prof('auditor')], ['alfia_security.group_alfia_security_auditor']),
    ('resident.vet', 'د. الطبيب البيطري المقيم', [prof('resident_vet')], []),
    ('vet', 'د. الطبيب البيطري', [prof('vet_staff')], []),
    ('breeding.manager', 'مدير التناسل', [prof('breeding_manager')], []),
    ('breeding', 'مساعد التناسل', [prof('breeding_staff')], []),
    ('groom.a', 'سائس – إسطبل أ', [prof('stable_staff')], []),
    ('groom.b', 'سائس – إسطبل ب', [groom_b], []),
    ('trainer', 'المدرب', [prof('training_staff')], []),
    ('training.manager', 'مدير التدريب والمنافسات', [prof('training_manager')], []),
    ('semen.custodian', 'أمين عهدة السائل المنوي', [prof('semen_custodian')], []),
    ('medicine.custodian', 'أمين مخزن الأدوية', [med_profile], []),
    ('approver.admin', 'معتمِد الصرف (إداري)', [prof('dispatch_admin_approver')], []),
    ('approver.vet', 'معتمِد الصرف (بيطري)', [vet_approver], []),
    ('storekeeper', 'أمين المخزن', [prof('storekeeper')], []),
    ('buyer', 'مسؤول المشتريات', [prof('procurement_officer')], []),
    ('purchase.approver', 'معتمِد المشتريات', [prof('procurement_approver')], []),
    ('accountant', 'المحاسب', [prof('accountant')], []),
    ('finance', 'المراقب المالي', [prof('finance_controller')], []),
]
users = {}
for login, name, profiles, groups in USERS:
    users[login] = Users.create({
        'name': name, 'login': login, 'email': f'{login}@example.com', 'password': DEMO_PASSWORD, 'lang': 'ar_001', 'tz': 'Asia/Qatar',
        'group_ids': [Command.link(ref('base.group_user').id)] + [Command.link(ref(g).id) for g in groups],
        'alfia_profile_ids': [Command.set([p.id for p in profiles])]})
admin = ref('base.user_admin')
admin.write({'password': ADMIN_PASSWORD, 'name': 'مدير النظام', 'lang': 'ar_001', 'tz': 'Asia/Qatar'})
admin.group_ids = [Command.link(ref('alfia_security.group_alfia_semen_custodian').id)]   # sees controlled stock in the demo

# ------------------------------------------------------------------ horses and pedigrees
Horse = E['alfia.horse']


def horse(name, sex, born=None, loc_=None, **kw):
    vals = {'name': name, 'sex': sex, 'birthdate': born, 'breed': kw.pop('breed', 'عربي أصيل'), **kw}
    if loc_:
        vals['initial_location_id'] = loc_.id
    return Horse.create(vals)


# Reference ancestors (not on the farm).
gs1 = horse('Desert Storm (ref.)', 'male', '1998-03-02', status='reference', color='أشهب')
gd1 = horse('Moon Pearl (ref.)', 'female', '2000-04-11', status='reference', color='كميت')
gs2 = horse('Royal Falcon (ref.)', 'male', '1999-02-20', status='reference', color='أشقر')
gd2 = horse('Sahara Bloom (ref.)', 'female', '2001-05-05', status='reference', color='أشهب')

stallions = [
    horse('Al Majd', 'male', '2012-03-10', barn_b, sire_id=gs1.id, dam_id=gd1.id, color='أشهب', reg_id='QAT-ST-1012', microchip='634000000001001'),
    horse('Najm Al Shamal', 'male', '2014-02-17', barn_b, sire_id=gs2.id, dam_id=gd2.id, color='كميت', reg_id='QAT-ST-1014', microchip='634000000001002'),
    horse('Saif Al Arab', 'male', '2016-04-01', barn_b, sire_id=gs1.id, dam_id=gd2.id, color='أشقر', reg_id='QAT-ST-1016', microchip='634000000001003'),
]
mare_names = ['Layla', 'Noor', 'Reem', 'Jawaher', 'Hessa', 'Maha', 'Shaikha', 'Dana', 'Lulwa', 'Mozah', 'Ghalia', 'Sara']
mares = []
for n, name in enumerate(mare_names):
    mares.append(horse(name, 'female', f'{2009 + n % 8}-0{1 + n % 8}-1{n % 9}', barn_a, sire_id=(gs1 if n % 2 else gs2).id,
                       dam_id=(gd1 if n % 3 else gd2).id, color=('أشهب', 'كميت', 'أشقر')[n % 3], reg_id=f'QAT-MR-2{n:03d}',
                       microchip=f'63400000000{2000 + n}', invoice_to_id=(client_1 if n in (9, 10) else alfia).id))
young = [
    horse('Fajr', 'female', '2023-02-14', barn_a, sire_id=stallions[0].id, dam_id=mares[0].id, color='أشهب'),
    horse('Badr', 'male', '2023-03-03', yard, sire_id=stallions[1].id, dam_id=mares[1].id, color='كميت'),
    horse('Wasm', 'male', '2024-01-28', yard, sire_id=stallions[2].id, dam_id=mares[2].id, color='أشقر'),
    horse('Hala', 'female', '2024-02-09', barn_a, sire_id=stallions[0].id, dam_id=mares[3].id, color='أشهب'),
]
geldings = [horse('Sultan', 'gelding', '2015-05-05', yard, color='كميت'), horse('Rakan', 'gelding', '2017-06-06', yard, color='أشهب')]
new_arrival = horse('Zahra (new arrival)', 'female', '2019-03-03', quarantine, color='كميت', invoice_to_id=client_2.id)
all_horses = stallions + mares + young + geldings + [new_arrival]

Own = E['alfia.horse.ownership']
for h in stallions + mares[:9] + young + geldings:
    Own.create({'horse_id': h.id, 'partner_id': alfia.id, 'date_from': h.birthdate or '2015-01-01', 'reason': 'مولود في المربط / مشترى'})
for h in mares[9:]:
    Own.create({'horse_id': h.id, 'partner_id': client_1.id, 'date_from': '2022-01-01', 'reason': 'فرس عميل'})
Own.create({'horse_id': new_arrival.id, 'partner_id': client_2.id, 'date_from': d(-5), 'reason': 'فرس عميل'})

# ------------------------------------------------------------------ health and care
Health = E['alfia.health.record']
for i, h in enumerate(all_horses):
    Health.create({'horse_id': h.id, 'date': d(-150 - i), 'type': 'vaccine', 'title': 'إنفلونزا الخيول + الكزاز', 'vet_id': dr_ext.id,
                   'cost': 180, 'next_due': d(30 - i * 3)})
    Health.create({'horse_id': h.id, 'date': d(-60 - i), 'type': 'deworming', 'title': 'إيفرمكتين', 'next_due': d(25 - i)})
E['alfia.vet.visit'].create({'horse_id': mares[4].id, 'date': d(-3), 'vet_id': dr_ext.id, 'reason': 'عرج خفيف، القائمة الأمامية اليسرى',
                             'diagnosis': 'كدمة في نعل الحافر', 'treatment_given': 'كمادة، راحة في الإسطبل', 'cost': 450, 'follow_up': d(4)})
treat = E['alfia.treatment'].create({'horse_id': geldings[0].id, 'drug': 'فينيل بيوتازون', 'dose': '2 g', 'route': 'oral', 'frequency': 'مرة يوميًا',
                                     'start_date': d(-2), 'end_date': d(3), 'withdrawal_days': 7})
E['alfia.treatment.dose'].create({'treatment_id': treat.id, 'horse_id': geldings[0].id, 'dose_given': '2 g'})
E['alfia.lab.test'].create({'horse_id': stallions[0].id, 'date': d(-2), 'test': 'تعداد الدم الكامل', 'lab': 'مختبر الدوحة للخيول', 'result': 'WBC 8.1',
                            'reference_range': '5.5-12.5'})
E['alfia.odontology'].create({'horse_id': mares[2].id, 'date': d(-200), 'floated': True, 'next_date': d(10), 'cost': 300})
E['alfia.farrier.visit'].create({'horse_id': geldings[1].id, 'date': d(-35), 'farrier_id': farrier.id, 'cost': 250, 'next_date': d(7)})
for k in range(6):
    E['alfia.measurement'].create({'horse_id': young[0].id, 'date': d(-150 + k * 30), 'weight': 300 + k * 18, 'height': 135 + k * 2})
    E['alfia.temperature'].create({'horse_id': new_arrival.id, 'taken_at': fields.Datetime.now() - relativedelta(hours=12 * k),
                                   'temperature': 37.6 + (0.4 if k == 2 else 0), 'pulse': 38, 'respiration': 12})
E['alfia.horse.note'].create({'horse_id': mares[5].id, 'content': 'خجولة مع السياس الجدد؛ يُقترب منها من اليسار.'})

# Quarantine of the new arrival (active), with its release review on the vet's list.
E['alfia.quarantine'].create({'horse_id': new_arrival.id, 'start_date': d(-5), 'planned_end': d(16), 'reason': 'وصول جديد من الخارج',
                              'isolation_location_id': quarantine.id})

# Feed plans
Product = E['product.product']
oats = Product.create({'name': 'Oats (kg)', 'type': 'consu', 'is_storable': True, 'standard_price': 2.1, 'list_price': 3})
hay = Product.create({'name': 'Timothy hay (kg)', 'type': 'consu', 'is_storable': True, 'standard_price': 1.4, 'list_price': 2})
alfalfa = Product.create({'name': 'Alfalfa (kg)', 'type': 'consu', 'is_storable': True, 'standard_price': 1.8, 'list_price': 2.5})
for h in mares + stallions:
    E['alfia.diet'].create({'horse_id': h.id, 'name': 'عليقة الشتاء', 'date_from': d(-30), 'line_ids': [
        Command.create({'product_id': hay.id, 'qty_per_meal': 4, 'meals_per_day': 2}),
        Command.create({'product_id': (oats if h.sex == 'male' else alfalfa).id, 'qty_per_meal': 1.5, 'meals_per_day': 2})]})

# ------------------------------------------------------------------ breeding season
rule = ref('alfia_equine.rule_default')
rule.with_user(users['resident.vet']).action_approve()
Svc, Ov = E['alfia.breeding.service'], E['alfia.ovulation']
heat = ref('alfia_equine.symbol_strong_heat')
for n, mare in enumerate(mares[:8]):
    served = d(-240 + n * 12)
    svc = Svc.create({'mare_id': mare.id, 'stallion_id': stallions[n % 3].id, 'date': served, 'method': ('natural', 'ai_fresh', 'ai_frozen')[n % 3]})
    svc.action_done()
    ov = Ov.create({'mare_id': mare.id, 'date': served + relativedelta(days=1), 'service_id': svc.id})
    ov.with_user(users['vet']).action_confirm()
    preg = ov.pregnancy_id
    if n == 6:
        E['alfia.pregnancy.check'].with_user(users['vet']).create({'pregnancy_id': preg.id, 'date': served + relativedelta(days=15), 'result': 'negative'})
        continue
    E['alfia.pregnancy.check'].with_user(users['vet']).create({'pregnancy_id': preg.id, 'date': served + relativedelta(days=15), 'result': 'positive'})
    # Past checks done, so the workboard shows what is really due now.
    preg.task_ids.filtered(lambda t: t.due_date < d(-2) and t.state == 'todo').write({'state': 'done', 'done_by_id': users['vet'].id})
for mare in mares[8:11]:
    E['alfia.teasing'].create({'mare_id': mare.id, 'date': d(-1), 'symbol_id': heat.id})
    E['alfia.reproductive.exam'].create({'mare_id': mare.id, 'date': d(-1), 'left_follicle_mm': 38, 'right_follicle_mm': 12,
                                         'next_action': 'التلقيح غدًا'})
# Last season's foal born last week: recorded by stable staff, awaiting the vet.
old_preg_svc = Svc.create({'mare_id': mares[11].id, 'stallion_id': stallions[0].id, 'date': d(-350), 'method': 'natural'})
old_preg_svc.action_done()
ov = Ov.create({'mare_id': mares[11].id, 'date': d(-349), 'service_id': old_preg_svc.id})
ov.with_user(users['vet']).action_confirm()
E['alfia.pregnancy.check'].with_user(users['vet']).create({'pregnancy_id': ov.pregnancy_id.id, 'date': d(-334), 'result': 'positive'})
ov.pregnancy_id.task_ids.filtered(lambda t: t.state == 'todo').write({'state': 'done'})
E['alfia.foaling'].with_user(users['groom.a']).create({'pregnancy_id': ov.pregnancy_id.id, 'date': d(-6), 'outcome': 'live', 'foal_sex': 'filly',
                                                       'foal_name': 'Amal'})

# Semen collections: one released into Tank 1, one waiting for the vet.
Sem = E['alfia.semen.collection']
col = Sem.create({'stallion_id': stallions[0].id, 'date': d(-20), 'volume_ml': 60, 'concentration': 210, 'motility': 72, 'morphology': 68,
                  'doses': 20, 'tank_id': tank_1.id})
col.with_user(users['vet']).action_release()
Sem.create({'stallion_id': stallions[1].id, 'date': d(-1), 'volume_ml': 55, 'concentration': 190, 'motility': 65, 'morphology': 60,
            'doses': 15, 'tank_id': tank_1.id})
E['alfia.embryo'].create({'donor_id': mares[3].id, 'sire_id': stallions[2].id, 'flush_date': d(-10), 'grade': '1', 'stage': 'كيسة أريمية',
                          'tank_id': tank_1.id}).action_store()

# A semen dispatch to a client, waiting for its two approvals.
customers = ref('stock.stock_location_customers')
dispatch = E['stock.picking'].create({
    'picking_type_id': wh.out_type_id.id, 'partner_id': client_1.id, 'location_id': tank_1.id, 'location_dest_id': customers.id, 'origin': 'طلب سائل منوي',
    'move_ids': [Command.create({'product_id': col.product_id.id, 'product_uom_qty': 2, 'location_id': tank_1.id, 'location_dest_id': customers.id})]})
dispatch.action_confirm()
dispatch.action_assign()

# ------------------------------------------------------------------ training and competition
for h in young[1:3] + geldings:
    for k in range(5):
        E['alfia.training.session'].create({'horse_id': h.id, 'date': d(-k * 2), 'program': 'أساس القدرة والتحمل', 'gait': ('trot', 'canter')[k % 2],
                                            'distance_km': 8 + k, 'duration_min': 45, 'effort': 5 + k % 3, 'rest_day': False})
Clear = E['alfia.clearance']
for h in geldings:
    Clear.create({'horse_id': h.id, 'kind': 'fitness'}).with_user(users['vet']).action_clear()
rule_entry = E['alfia.restriction.rule'].create({'name': 'قدرة وتحمل 80 كم: عمر 6 سنوات فأكثر، لياقة مجازة، دون فترة سحب', 'min_age_years': 6})
event = E['alfia.event'].create({'name': 'كأس الدوحة للقدرة والتحمل 80 كم · Doha Endurance Cup 80 km', 'date_start': d(12), 'venue': 'الدوحة', 'discipline': 'القدرة والتحمل',
                                 'rule_id': rule_entry.id})
for h in geldings + [young[1]]:
    E['alfia.event.entry'].create({'event_id': event.id, 'horse_id': h.id, 'rider_id': rider.id}).action_submit()
E['alfia.competition'].create({'horse_id': geldings[1].id, 'date': d(-40), 'event': 'مسيعيد 40 كم', 'discipline': 'القدرة والتحمل', 'result': 'الثاني',
                               'prize_money': 15000})

# ------------------------------------------------------------------ billing and contracts
board = Product.create({'name': 'Full board (month)', 'type': 'service', 'list_price': 3500})
vet_fee = Product.create({'name': 'Veterinary service', 'type': 'service', 'list_price': 450})
nomination = Product.create({'name': 'Stallion nomination fee', 'type': 'service', 'list_price': 25000})
group_client = E['alfia.horse.group'].create({'name': 'أفراس العملاء', 'member_ids': [Command.set([m.id for m in mares[9:]] + [new_arrival.id])]})
E['alfia.horse.group'].create({'name': 'أفراس إسطبل أ (تلقائية)', 'sex': 'female', 'location_id': barn_a.id})
E['alfia.horse.group'].create({'name': 'الأفراس العشار (تلقائية)', 'reproductive_state': 'pregnant'})
plan = E['alfia.charge.plan'].create({'name': 'أفراس العملاء – إيواء كامل', 'target_kind': 'group', 'group_id': group_client.id,
                                      'product_id': board.id, 'interval': 'monthly', 'next_date': d(-60), 'auto_approve': True})
plan.with_user(users['finance']).action_approve()   # a second person approves the plan before it bills
plan.action_generate()
E['alfia.charge'].create({'horse_id': mares[4].id, 'product_id': vet_fee.id, 'name': 'زيارة العرج · Lameness visit', 'date': d(-3)})
stay = E['alfia.boarding'].create({'horse_id': new_arrival.id, 'client_id': client_2.id, 'product_id': board.id, 'rate': 4200, 'start_date': d(-5),
                                   'location_id': quarantine.id})
stay.action_start()
approved = E['alfia.charge'].search([('state', '=', 'approved'), ('date', '<', d(-25))])
approved.with_user(users['accountant']).action_invoice()

contract = E['alfia.breeding.contract'].create({'client_id': client_1.id, 'stallion_id': stallions[0].id, 'mare_id': mares[9].id, 'season': '2027',
                                                'product_id': nomination.id, 'fee': 25000, 'live_foal_guarantee': True})
contract.action_confirm()
contract.with_user(users['accountant']).action_invoice()
E['alfia.breeding.contract'].create({'client_id': client_2.id, 'stallion_id': stallions[1].id, 'season': '2027', 'product_id': nomination.id, 'fee': 18000})
E['alfia.horse.sale'].with_user(users['breeding.manager']).create({'horse_id': young[3].id, 'buyer_id': buyer.id, 'price': 180000,
                                                                   'notes': 'عرض وصل في مزاد الخريف.'})

# ------------------------------------------------------------------ stock, procurement and sales (ERP basics)
medicine_categ = E['product.category'].create({'name': 'Veterinary medicine', 'alfia_access_group_id': ref('alfia_security.group_alfia_medicine_custodian').id})
penicillin = Product.create({'name': 'Procaine penicillin 100 ml', 'type': 'consu', 'is_storable': True, 'categ_id': medicine_categ.id, 'standard_price': 95})
bute = Product.create({'name': 'Phenylbutazone paste', 'type': 'consu', 'is_storable': True, 'categ_id': medicine_categ.id, 'standard_price': 40})
Quant = E['stock.quant']
for product, qty, where in ((oats, 1200, feed_store), (hay, 4000, feed_store), (alfalfa, 900, feed_store), (penicillin, 24, med_store), (bute, 30, med_store)):
    Quant._update_available_quantity(product, where, qty)
issue = E['alfia.medicine.issue'].create({'horse_id': geldings[0].id, 'product_id': bute.id, 'quantity': 2, 'location_id': med_store.id, 'treatment_id': treat.id})
issue.with_user(users['medicine.custodian']).action_issue()
E['alfia.medicine.issue'].create({'horse_id': mares[4].id, 'product_id': penicillin.id, 'quantity': 1, 'location_id': med_store.id})

PO = E['purchase.order']
po = PO.with_user(users['buyer']).create({'partner_id': vendor_feed.id, 'picking_type_id': wh.in_type_id.id, 'order_line': [
    Command.create({'product_id': hay.id, 'product_qty': 2000, 'price_unit': 1.35}),
    Command.create({'product_id': oats.id, 'product_qty': 500, 'price_unit': 2.0})]})
po.with_user(users['buyer']).button_confirm()     # segregation of duties: waits for the procurement approver
PO.with_user(users['buyer']).create({'partner_id': vendor_vet.id, 'picking_type_id': wh.in_type_id.id, 'order_line': [
    Command.create({'product_id': penicillin.id, 'product_qty': 12, 'price_unit': 92})]})
so = E['sale.order'].create({'partner_id': client_1.id, 'warehouse_id': wh.id, 'order_line': [
    Command.create({'product_id': vet_fee.id, 'product_uom_qty': 2, 'price_unit': 450})]})
so.action_confirm()

# ------------------------------------------------------------------ facilities
Asset = E['alfia.asset']
Asset.create({'name': 'مقطورة خيل لستة خيول', 'kind': 'vehicle', 'reference': 'QA 123456', 'next_service': d(9), 'responsible_id': users['trainer'].id})
Asset.create({'name': 'سيارة إسعاف الخيول', 'kind': 'vehicle', 'reference': 'QA 654321', 'next_service': d(40)})
Asset.create({'name': 'كأس الدوحة 2025', 'kind': 'trophy', 'horse_id': geldings[1].id, 'acquired': '2025-02-01'})
Asset.create({'name': 'سجل أنساب الخيل العربية، المجلد 12', 'kind': 'library', 'reference': 'Shelf B-3', 'state': 'in_use', 'borrower_id': alfia.id})
Asset.create({'name': 'جهاز موجات فوق صوتية محمول', 'kind': 'equipment', 'reference': 'SN-US-2291', 'responsible_id': users['vet'].id, 'value': 85000})

# ------------------------------------------------------------------ people (HR), when alfia_hr is installed
if 'hr.employee' in E and E.ref('alfia_hr.dept_stud_operations', raise_if_not_found=False):
    Dept = E['hr.department']
    ops = ref('alfia_hr.dept_stud_operations')
    dept_a = Dept.create({'name': 'إسطبل أ · Barn A', 'parent_id': ops.id})
    dept_b = Dept.create({'name': 'إسطبل ب · Barn B', 'parent_id': ops.id})
    vet_dept, breed_dept, train_dept = ref('alfia_hr.dept_veterinary'), ref('alfia_hr.dept_breeding'), ref('alfia_hr.dept_training')
    fin_dept, stores_dept, mgmt = ref('alfia_hr.dept_finance'), ref('alfia_hr.dept_stores'), ref('alfia_hr.dept_management')
    # Access profiles also limit HR to departments.
    prof('stable_staff').department_ids = [Command.set([dept_a.id])]
    groom_b.department_ids = [Command.set([dept_b.id])]
    line_mgr = ref('alfia_hr.profile_line_manager')
    stud_line = Profile.create({'name': 'Line manager – stud operations', 'group_ids': [Command.set(line_mgr.group_ids.ids)],
                                'department_ids': [Command.set([ops.id])]})
    for login, profiles in (('hr.officer', [ref('alfia_hr.profile_hr_officer')]), ('hr.manager', [ref('alfia_hr.profile_hr_manager')])):
        users[login] = Users.create({'name': {'hr.officer': 'مسؤول الموارد البشرية', 'hr.manager': 'مدير الموارد البشرية'}[login], 'login': login,
                                     'email': f'{login}@example.com', 'password': DEMO_PASSWORD, 'lang': 'ar_001', 'tz': 'Asia/Qatar',
                                     'group_ids': [Command.link(ref('base.group_user').id)], 'alfia_profile_ids': [Command.set([p.id for p in profiles])]})
    ref('alfia_hr.profile_hr_officer').department_ids = [Command.set([ops.id, stores_dept.id])]
    users['breeding.manager'].alfia_profile_ids = [Command.link(stud_line.id)]
    users['resident.vet'].alfia_profile_ids = [Command.link(stud_line.id)]

    Emp = E['hr.employee'].with_context(alfia_no_onboarding=True)
    def emp(name, login, dept, job, manager=None, qid=None):
        vals = {'name': name, 'department_id': dept.id, 'job_id': ref(f'alfia_hr.{job}').id, 'work_email': f'{login or "staff"}@example.com',
                'parent_id': manager.id if manager else False, 'alfia_qid_expiry': qid or d(300), 'alfia_passport_expiry': d(700),
                'alfia_health_card_expiry': d(200)}
        if login:
            vals['user_id'] = users[login].id
        e = Emp.create(vals)
        e.version_id.write({'date_version': d(-400), 'contract_date_start': d(-400)})
        if manager and manager.user_id:
            e.write({'leave_manager_id': manager.user_id.id, 'expense_manager_id': manager.user_id.id})
        return e
    e_hrm = emp('مدير الموارد البشرية', 'hr.manager', mgmt, 'job_stable_manager')
    e_hro = emp('مسؤول الموارد البشرية', 'hr.officer', mgmt, 'job_stable_manager', e_hrm)
    e_bm = emp('مدير التناسل', 'breeding.manager', breed_dept, 'job_breeding_manager', e_hrm)
    e_rv = emp('د. الطبيب البيطري المقيم', 'resident.vet', vet_dept, 'job_vet', e_hrm)
    e_vet = emp('د. الطبيب البيطري', 'vet', vet_dept, 'job_vet', e_rv, qid=d(18))
    e_ga = emp('سائس – إسطبل أ', 'groom.a', dept_a, 'job_groom', e_bm)
    e_gb = emp('سائس – إسطبل ب', 'groom.b', dept_b, 'job_groom', e_bm)
    e_tm = emp('مدير التدريب والمنافسات', 'training.manager', train_dept, 'job_trainer', e_hrm)
    e_tr = emp('المدرب', 'trainer', train_dept, 'job_trainer', e_tm)
    e_sk = emp('أمين المخزن', 'storekeeper', stores_dept, 'job_storekeeper', e_hrm)
    e_acc = emp('المحاسب', 'accountant', fin_dept, 'job_accountant', e_hrm)
    for name in ('سائس ٣ · Groom 3', 'سائس ٤ · Groom 4'):
        emp(name, None, dept_a, 'job_groom', e_bm)
    # A driver with a login, for evaluators to try offboarding (their access is removed when they leave).
    users['driver'] = Users.create({'name': 'سائق · Driver', 'login': 'driver', 'email': 'driver@example.com', 'password': DEMO_PASSWORD,
                                    'lang': 'ar_001', 'tz': 'Asia/Qatar', 'group_ids': [Command.link(ref('base.group_user').id)],
                                    'alfia_profile_ids': [Command.set([prof('stable_staff').id])]})
    emp('سائق · Driver', 'driver', ops, 'job_driver', e_bm, qid=d(9))

    # Annual leave balances, approved by HR.
    annual = ref('alfia_hr.leave_annual')
    for e in E['hr.employee'].search([('department_id', '!=', False)]):
        allocation = E['hr.leave.allocation'].create({'name': 'الإجازة السنوية 2026 · Annual leave 2026', 'employee_id': e.id,
                                                      'work_entry_type_id': annual.id, 'number_of_days': 21, 'date_from': d(-300)})
        # The HR manager does not approve their own allocation: the administrator does.
        (allocation if e == e_hrm else allocation.with_user(users['hr.manager'])).action_approve()

    def workday(start):
        day = start
        while day.weekday() >= 4:
            day += relativedelta(days=1)
        return day
    Leave = E['hr.leave']
    Leave.with_user(users['groom.a']).create({'employee_id': e_ga.id, 'work_entry_type_id': annual.id, 'name': 'زيارة الأهل · Family visit',
                                              'request_date_from': workday(d(10)), 'request_date_to': workday(d(10)) + relativedelta(days=2)})
    tr_leave = Leave.with_user(users['trainer']).create({'employee_id': e_tr.id, 'work_entry_type_id': annual.id, 'name': 'إجازة · Leave',
                                                         'request_date_from': workday(d(20)), 'request_date_to': workday(d(20))})
    tr_leave.with_user(users['training.manager']).action_approve()        # waits for HR's second approval
    sick = Leave.with_user(users['vet']).create({'employee_id': e_vet.id, 'work_entry_type_id': ref('alfia_hr.leave_sick').id,
                                                 'request_date_from': workday(d(-8)), 'request_date_to': workday(d(-8))})
    sick.with_user(users['hr.officer']).action_approve()

    # Reimbursements: one waiting for the manager, one in draft.
    Expense = E['hr.expense']
    vet_exp = Expense.with_user(users['vet']).create({'name': 'وقود لنقل حصان · Fuel for horse transport', 'employee_id': e_vet.id,
                                                      'product_id': ref('alfia_hr.expense_fuel').id, 'total_amount_currency': 180.0,
                                                      'payment_mode': 'own_account',
                                                      'analytic_distribution': {str(mares[4].analytic_account_id.id): 100}})
    vet_exp.with_user(users['vet']).action_submit()
    Expense.with_user(users['groom.b']).create({'name': 'بدل يومي – كأس الدوحة · Per diem – Doha Cup', 'employee_id': e_gb.id,
                                                'product_id': ref('alfia_hr.expense_per_diem').id, 'total_amount_currency': 150.0,
                                                'payment_mode': 'own_account'})

    # A new hire whose onboarding plan has started, and applicants for the next groom.
    E['hr.employee'].with_user(users['hr.officer']).create({'name': 'سائس جديد · New groom', 'department_id': dept_a.id,
                                                            'job_id': ref('alfia_hr.job_groom').id, 'parent_id': e_bm.id})
    for name, email in (('Applicant One', 'applicant1@example.com'), ('Applicant Two', 'applicant2@example.com')):
        E['hr.applicant'].create({'partner_name': name, 'email_from': email, 'job_id': ref('alfia_hr.job_groom').id})

    # Attendance yesterday for the Barn A groom.
    from datetime import datetime, time, timedelta
    y = datetime.combine(TODAY - timedelta(days=1), time(4, 0))     # 07:00 Doha time
    E['hr.attendance'].create({'employee_id': e_ga.id, 'check_in': y, 'check_out': y + timedelta(hours=9)})

AR_HORSES = {
    'Desert Storm (ref.)': 'عاصفة الصحراء', 'Moon Pearl (ref.)': 'لؤلؤة القمر', 'Royal Falcon (ref.)': 'الصقر الملكي',
    'Sahara Bloom (ref.)': 'زهرة الصحراء', 'Al Majd': 'المجد', 'Najm Al Shamal': 'نجم الشمال', 'Saif Al Arab': 'سيف العرب',
    'Layla': 'ليلى', 'Noor': 'نور', 'Reem': 'ريم', 'Jawaher': 'جواهر', 'Hessa': 'حصة', 'Maha': 'مها', 'Shaikha': 'شيخة',
    'Dana': 'دانة', 'Lulwa': 'لولوة', 'Mozah': 'موزة', 'Ghalia': 'غالية', 'Sara': 'سارة', 'Fajr': 'فجر', 'Badr': 'بدر',
    'Wasm': 'وسم', 'Hala': 'هلا', 'Sultan': 'سلطان', 'Rakan': 'راكان', 'Zahra (new arrival)': 'زهرة (وصول جديد)', 'Amal': 'أمل',
}
for h in Horse.with_context(active_test=False).search([]):
    if h.name in AR_HORSES and not h.alias:
        h.alias = AR_HORSES[h.name]
AR_PRODUCTS = {
    oats: 'شوفان (كغ)', hay: 'تبن التيموثي (كغ)', alfalfa: 'برسيم حجازي (كغ)', board: 'إيواء كامل (شهري)',
    vet_fee: 'خدمة بيطرية', nomination: 'رسوم حجز الفحل', penicillin: 'بنسلين بروكائين 100 مل', bute: 'معجون فينيل بيوتازون',
}
for product, name in AR_PRODUCTS.items():
    product.product_tmpl_id.with_context(lang='ar_001').name = name
col.product_id.product_tmpl_id.with_context(lang='ar_001').name = 'جرعة سائل منوي مجمّد'
medicine_categ.with_context(lang='ar_001').name = 'أدوية بيطرية'
for profile, name in ((med_profile, 'أمين مخزن الأدوية – مخزن الأدوية'), (vet_approver, 'معتمِد الصرف (البيطري)'),
                      (groom_b, 'موظفو الإسطبل – إسطبل ب')):
    profile.with_context(lang='ar_001').name = name

E.flush_all()
E.cr.commit()
print(f"Demo farm ready: {Horse.search_count([])} horses, {len(users) + 1} accounts.")
