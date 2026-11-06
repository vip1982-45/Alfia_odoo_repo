from dateutil.relativedelta import relativedelta

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

OVULATION = fields.Date.from_string('2026-03-01')


class EquineCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        wh = env['stock.warehouse'].search([('company_id', '=', env.company.id)], limit=1)
        Location = env['stock.location']
        cls.barn_a = Location.create({'name': 'Eq Barn A', 'location_id': wh.view_location_id.id, 'usage': 'internal'})
        cls.barn_b = Location.create({'name': 'Eq Barn B', 'location_id': wh.view_location_id.id, 'usage': 'internal'})
        cls.tank = Location.create({'name': 'Eq Tank room', 'location_id': wh.view_location_id.id, 'usage': 'internal'})
        cls.wh = wh
        Horse = env['alfia.horse']
        cls.mare = Horse.create({'name': 'Cleopatra', 'sex': 'female', 'initial_location_id': cls.barn_a.id, 'microchip': 'CH-1'})
        cls.mare2 = Horse.create({'name': 'Nefertiti', 'sex': 'female', 'initial_location_id': cls.barn_a.id})
        cls.stallion = Horse.create({'name': 'Carlomagno', 'sex': 'male', 'initial_location_id': cls.tank.id})
        cls.far_mare = Horse.create({'name': 'Faraway', 'sex': 'female', 'initial_location_id': cls.barn_b.id})
        cls.all_loc = 'alfia_security.group_alfia_all_locations'
        cls.vet = cls._user('vet', ['vet_user'])
        cls.vet_mgr = cls._user('vetmgr', ['vet_manager'])
        cls.stable = cls._user('stable', ['stable_user'])
        cls.breeder = cls._user('breeder', ['breeding_user'])
        cls.breed_mgr = cls._user('breedmgr', ['breeding_manager'])
        cls.trainer = cls._user('trainer', ['training_user'])
        cls.train_mgr = cls._user('trainmgr', ['training_manager'])
        cls.rule = env.ref('alfia_equine.rule_default')

    @classmethod
    def _user(cls, login, groups, scoped=False):
        gs = [f'alfia_security.group_alfia_{g}' for g in groups] + ([] if scoped else [cls.all_loc])
        return cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': login, 'login': f'{login}@eq.local', 'email': f'{login}@eq.local',
            'group_ids': [Command.link(cls.env.ref(g).id) for g in gs + ['base.group_user']]})

    def _approve_rule(self):
        if self.rule.state == 'draft':
            self.rule.with_user(self.vet_mgr).action_approve()

    def _pregnancy(self, mare=None, ovulation=OVULATION):
        self._approve_rule()
        mare = mare or self.mare
        ov = self.env['alfia.ovulation'].create({'mare_id': mare.id, 'date': ovulation})
        ov.with_user(self.vet).action_confirm()
        return ov.pregnancy_id


@tagged('post_install', '-at_install', 'alfia_equine')
class TestHorse(EquineCase):

    def test_pedigree_and_inbreeding(self):
        Horse = self.env['alfia.horse']
        gs, gd = Horse.create({'name': 'GS', 'sex': 'male'}), Horse.create({'name': 'GD', 'sex': 'female'})
        sire = Horse.create({'name': 'S', 'sex': 'male', 'sire_id': gs.id, 'dam_id': gd.id})
        dam = Horse.create({'name': 'D', 'sex': 'female', 'sire_id': gs.id, 'dam_id': gd.id})
        foal = Horse.create({'name': 'F', 'sex': 'female', 'sire_id': sire.id, 'dam_id': dam.id})
        self.assertEqual(foal.inbreeding, 25.0, "a foal of full siblings has F = 25%")
        self.assertEqual(Horse.create({'name': 'Out', 'sex': 'male', 'sire_id': gs.id}).inbreeding, 0.0)
        self.assertEqual(foal.sibling_count, 0)
        self.assertEqual(sire.offspring_count, 1)
        self.assertIn('S', foal.pedigree_html)
        with self.assertRaises(ValidationError):
            gs.sire_id = foal                                  # no ancestor loops
        with self.assertRaises(ValidationError):
            Horse.create({'name': 'Bad', 'sex': 'male', 'sire_id': dam.id})   # a mare cannot be a sire
        with self.assertRaises(ValidationError):
            Horse.create({'name': 'Bad', 'sex': 'male', 'dam_id': sire.id})
        with self.assertRaises(ValidationError):
            Horse.create({'name': 'Future', 'sex': 'male', 'birthdate': fields.Date.today() + relativedelta(days=2)})

    def test_duplicate_microchip_is_refused(self):
        with self.assertRaises(Exception):
            with self.env.cr.savepoint():
                self.env['alfia.horse'].create({'name': 'Twin', 'sex': 'female', 'microchip': 'CH-1'})

    def test_ownership_is_effective_dated(self):
        owner1, owner2 = self.env['res.partner'].create({'name': 'O1'}), self.env['res.partner'].create({'name': 'O2'})
        Own = self.env['alfia.horse.ownership']
        Own.create({'horse_id': self.mare.id, 'partner_id': owner1.id, 'date_from': '2025-01-01'})
        self.assertEqual(self.mare.owner_ids, owner1)
        with self.assertRaises(ValidationError):
            Own.create({'horse_id': self.mare.id, 'partner_id': owner1.id, 'date_from': '2025-06-01'})   # already an owner then
        with self.assertRaises(ValidationError):
            Own.create({'horse_id': self.mare.id, 'partner_id': owner2.id, 'date_from': '2025-06-01', 'share': 50})  # would exceed 100%
        Own.search([('horse_id', '=', self.mare.id)]).action_transfer_all(owner2, date=fields.Date.from_string('2026-01-01'), reason='Sale')
        history = Own.search([('horse_id', '=', self.mare.id)], order='date_from')
        self.assertEqual(history[0].date_to, fields.Date.from_string('2025-12-31'), "the previous owner stops the day before")
        self.assertEqual(self.mare.owner_ids, owner2)
        with self.assertRaises(ValidationError):
            Own.create({'horse_id': self.mare.id, 'partner_id': owner1.id, 'date_from': '2025-05-01', 'date_to': '2025-04-01'})

    def test_movements_drive_location(self):
        self.assertEqual(self.mare.location_id, self.barn_a)
        Move = self.env['alfia.horse.movement']
        move = Move.create({'horse_id': self.mare.id, 'kind': 'internal', 'location_to_id': self.barn_b.id})
        self.assertEqual(self.mare.location_id, self.barn_a, "a planned move changes nothing")
        move.action_done()
        self.assertEqual(self.mare.location_id, self.barn_b)
        self.assertEqual(move.location_from_id, self.barn_a)
        with self.assertRaises(UserError):
            move.action_done()
        with self.assertRaises(UserError):
            move.write({'location_to_id': self.barn_a.id})        # completed movements are not edited
        with self.assertRaises(UserError):
            move.action_cancel()
        with self.assertRaises(ValidationError):
            Move.create({'horse_id': self.mare.id, 'kind': 'internal'})   # an internal move needs a destination
        dep = Move.create({'horse_id': self.mare.id, 'kind': 'departure', 'reason': 'Show'})
        dep.action_done()
        self.assertFalse(self.mare.on_site)
        self.assertFalse(self.mare.location_id)
        with self.assertRaises(UserError):
            Move.create({'horse_id': self.mare.id, 'kind': 'internal', 'location_to_id': self.barn_a.id}).action_done()

    def test_status_history_is_kept_and_protected(self):
        self.mare.status = 'retired'
        logs = self.mare.status_log_ids
        self.assertEqual(logs.sorted('id')[-1].status, 'retired')
        self.assertEqual(len(logs), 2, "created + retired")
        with self.assertRaises(UserError):
            logs[0].write({'status': 'dead'})
        with self.assertRaises(UserError):
            logs[0].with_user(self.breed_mgr).unlink()

    def test_merge_moves_everything_and_archives_the_duplicate(self):
        Horse = self.env['alfia.horse']
        keep = Horse.create({'name': 'Keep', 'sex': 'female'})
        dup = Horse.create({'name': 'Keep (dup)', 'sex': 'female', 'microchip': 'CH-9', 'breed': 'Arabian'})
        self.env['alfia.horse.note'].create({'horse_id': dup.id, 'content': 'dup note'})
        kid = Horse.create({'name': 'Kid', 'sex': 'male', 'dam_id': dup.id})
        self.env['alfia.horse.merge'].with_user(self.breed_mgr).create({'duplicate_id': dup.id, 'keep_id': keep.id}).action_merge()
        self.assertFalse(dup.active)
        self.assertEqual(self.env['alfia.horse.note'].search_count([('horse_id', '=', keep.id)]), 1)
        self.assertEqual(kid.dam_id, keep)
        self.assertEqual((keep.microchip, keep.breed), ('CH-9', 'Arabian'), "identifiers the kept horse lacked are carried over")
        with self.assertRaises(UserError):
            self.env['alfia.horse.merge'].create({'duplicate_id': keep.id, 'keep_id': keep.id}).action_merge()
        with self.assertRaises(AccessError):
            self.env['alfia.horse.merge'].with_user(self.stable).create({'duplicate_id': kid.id, 'keep_id': keep.id})


@tagged('post_install', '-at_install', 'alfia_equine')
class TestClinical(EquineCase):

    def test_next_due_creates_and_follows_a_work_item(self):
        rec = self.env['alfia.health.record'].create({'horse_id': self.mare.id, 'title': 'Flu', 'type': 'vaccine', 'date': '2026-01-10', 'next_due': '2026-07-10'})
        task = self.env['alfia.task'].search([('horse_id', '=', self.mare.id), ('kind', '=', 'health_due')])
        self.assertEqual((len(task), task.due_date), (1, fields.Date.from_string('2026-07-10')))
        rec.next_due = '2026-08-10'
        self.assertEqual(task.due_date, fields.Date.from_string('2026-08-10'), "the work item follows the corrected date")
        rec.write({'title': 'Flu booster'})
        self.assertEqual(self.env['alfia.task'].search_count([('horse_id', '=', self.mare.id), ('kind', '=', 'health_due')]), 1, "no duplicates")
        rec.next_due = False
        self.assertEqual(task.state, 'cancelled')
        with self.assertRaises(ValidationError):
            rec.next_due = '2025-01-01'

    def test_prescription_and_doses_are_separate(self):
        treatment = self.env['alfia.treatment'].create({'horse_id': self.mare.id, 'drug': 'Penicillin', 'dose': '10 ml', 'start_date': '2026-02-01', 'end_date': '2026-02-05', 'withdrawal_days': 7})
        self.assertEqual(treatment.withdrawal_until, fields.Date.from_string('2026-02-12'))
        self.env['alfia.treatment.dose'].create([{'treatment_id': treatment.id, 'dose_given': '10 ml'}, {'treatment_id': treatment.id, 'missed': True}])
        self.assertEqual((len(treatment.dose_ids), treatment.given_count), (2, 1))
        self.assertEqual(treatment.dose, '10 ml', "the prescription is untouched by what was given")

    def test_lab_review_needs_a_vet(self):
        test = self.env['alfia.lab.test'].create({'horse_id': self.mare.id, 'test': 'Coggins', 'result': 'Negative'})
        with self.assertRaises(AccessError):
            test.with_user(self.stable).action_review()
        test.with_user(self.vet).action_review()
        self.assertEqual((test.state, test.reviewed_by_id), ('reviewed', self.vet))

    def test_fitness_and_eligibility_are_separate_decisions(self):
        horse = self.mare
        self.assertEqual((horse.fitness_state, horse.eligibility_state), ('pending', 'pending'), "no record never means cleared")
        fit = self.env['alfia.clearance'].create({'horse_id': horse.id, 'kind': 'fitness'})
        elig = self.env['alfia.clearance'].create({'horse_id': horse.id, 'kind': 'eligibility'})
        with self.assertRaises(AccessError):
            fit.with_user(self.trainer).action_clear()          # the trainer cannot declare a horse fit
        with self.assertRaises(AccessError):
            elig.with_user(self.vet).action_clear()             # the vet cannot declare eligibility
        fit.with_user(self.vet).action_clear()
        horse.invalidate_recordset()
        self.assertEqual((horse.fitness_state, horse.eligibility_state), ('cleared', 'pending'), "fit, but not cleared to race")
        elig.with_user(self.train_mgr).action_clear()
        horse.invalidate_recordset()
        self.assertEqual(horse.eligibility_state, 'cleared')
        with self.assertRaises(AccessError):
            self.env['alfia.clearance'].with_user(self.stable).create({'horse_id': horse.id, 'kind': 'fitness', 'state': 'cleared'})

    def test_implausible_readings_are_refused(self):
        with self.assertRaises(ValidationError):
            self.env['alfia.temperature'].create({'horse_id': self.mare.id, 'temperature': 98.6})
        self.env['alfia.temperature'].create({'horse_id': self.mare.id, 'temperature': 37.8, 'pulse': 36})
        with self.assertRaises(ValidationError):
            self.env['alfia.training.session'].create({'horse_id': self.mare.id, 'effort': 11})

    def test_work_items_keep_completed_work(self):
        task = self.env['alfia.task'].create({'name': 'x', 'horse_id': self.mare.id, 'due_date': '2026-01-01', 'key': 'k1'})
        task.with_user(self.stable).action_done()
        self.assertEqual(task.done_by_id, self.stable)
        with self.assertRaises(UserError):
            task.action_cancel()
        with self.assertRaises(UserError):
            task.with_user(self.breed_mgr).unlink()
        with self.assertRaises(Exception):
            with self.env.cr.savepoint():
                self.env['alfia.task'].create({'name': 'dup', 'horse_id': self.mare.id, 'due_date': '2026-01-01', 'key': 'k1'})


@tagged('post_install', '-at_install', 'alfia_equine')
class TestBreeding(EquineCase):

    def test_nothing_is_scheduled_from_an_unapproved_rule_set(self):
        ov = self.env['alfia.ovulation'].create({'mare_id': self.mare.id, 'date': OVULATION})
        with self.assertRaises(UserError):
            ov.with_user(self.vet).action_confirm()
        with self.assertRaises(AccessError):
            self.rule.with_user(self.vet).action_approve()       # only the resident vet approves
        with self.assertRaises(AccessError):
            self.rule.with_user(self.breed_mgr).action_approve()
        self.rule.with_user(self.vet_mgr).action_approve()
        self.assertEqual((self.rule.state, self.rule.approved_by_id), ('approved', self.vet_mgr))
        with self.assertRaises(UserError):
            self.rule.write({'gestation_days': 330})            # approved sets are frozen
        v2 = self.rule.action_new_version()
        v2.write({'gestation_days': 335})
        v2.with_user(self.vet_mgr).action_approve()
        self.assertEqual(self.rule.state, 'retired', "approving a new version retires the old")
        with self.assertRaises(ValidationError):
            v2.copy({'check_offsets': '21,14'})

    def test_confirmed_ovulation_schedules_exactly_the_approved_work(self):
        self._approve_rule()
        ov = self.env['alfia.ovulation'].create({'mare_id': self.mare.id, 'date': OVULATION})
        with self.assertRaises(AccessError):
            ov.with_user(self.stable).action_confirm()
        ov.with_user(self.vet).action_confirm()
        preg = ov.pregnancy_id
        checks = preg.task_ids.filtered(lambda t: t.kind == 'pregnancy_check')
        self.assertEqual(sorted((t.due_date - OVULATION).days for t in checks), [14, 18, 21, 28, 35, 45, 60, 75, 90, 120])
        vaccines = preg.task_ids.filtered(lambda t: t.kind == 'pregnancy_vaccine')
        self.assertEqual(sorted(vaccines.mapped('due_date')), [OVULATION + relativedelta(months=m) for m in (3, 5, 7, 9)])
        self.assertEqual(preg.expected_foaling, OVULATION + relativedelta(days=340))
        prep = preg.task_ids.filtered(lambda t: t.kind == 'foaling_prep')
        self.assertEqual(prep.due_date, preg.expected_foaling - relativedelta(days=30))
        self.assertEqual(checks[0].rule_id, self.rule, "each item records the rule version that created it")
        # repeating the event creates nothing new
        ov.with_user(self.vet).action_confirm()
        preg._generate_tasks()
        self.assertEqual(len(preg.task_ids), 15)
        self.assertEqual(self.mare.reproductive_state, 'bred')

    def test_correcting_the_ovulation_date_moves_pending_work_only(self):
        preg = self._pregnancy()
        ov = preg.ovulation_id
        first = preg.task_ids.filtered(lambda t: (t.due_date - OVULATION).days == 14)
        first.with_user(self.vet).action_done()
        done_date = first.due_date
        with self.assertRaises(UserError):
            ov.write({'date': OVULATION + relativedelta(days=2)})        # corrections go through the dedicated action
        with self.assertRaises(UserError):
            ov.with_user(self.vet).action_correct_date(OVULATION + relativedelta(days=2), '')
        ov.with_user(self.vet).action_correct_date(OVULATION + relativedelta(days=2), 'Scan showed ovulation a day later')
        self.assertEqual(first.due_date, done_date, "completed work is never re-dated")
        pending = preg.task_ids.filtered(lambda t: (t.due_date - OVULATION).days == 20)   # old day 18 moved to day 20
        self.assertTrue(pending)
        self.assertEqual(len(preg.task_ids), 15)
        self.assertEqual(preg.expected_foaling, OVULATION + relativedelta(days=342))

    def test_results_come_from_vets_and_loss_closes_only_that_pregnancy(self):
        preg = self._pregnancy()
        other = self._pregnancy(self.mare2, OVULATION + relativedelta(days=1))
        Check = self.env['alfia.pregnancy.check']
        with self.assertRaises(AccessError):
            Check.with_user(self.stable).create({'pregnancy_id': preg.id, 'result': 'loss'})      # staff observations never close a pregnancy
        c14 = preg.task_ids.filtered(lambda t: (t.due_date - OVULATION).days == 14)
        Check.with_user(self.vet).create({'pregnancy_id': preg.id, 'task_id': c14.id, 'date': OVULATION + relativedelta(days=14), 'result': 'positive'})
        self.assertEqual((preg.state, c14.state), ('pregnant', 'done'))
        self.assertEqual(self.mare.reproductive_state, 'pregnant')
        Check.with_user(self.vet).create({'pregnancy_id': preg.id, 'date': OVULATION + relativedelta(days=40), 'result': 'loss'})
        self.assertEqual(preg.state, 'lost')
        self.assertEqual(self.mare.reproductive_state, 'open', "the mare is barren again")
        self.assertFalse(preg.task_ids.filtered(lambda t: t.state == 'todo'), "remaining work of the lost pregnancy is cancelled")
        self.assertEqual(c14.state, 'done', "completed work and results are kept")
        self.assertTrue(other.task_ids.filtered(lambda t: t.state == 'todo'), "another mare's work is untouched")
        self.assertEqual(len(preg.check_ids), 2)
        with self.assertRaises(UserError):
            Check.with_user(self.vet).create({'pregnancy_id': preg.id, 'result': 'positive'})     # a closed pregnancy takes no more results
        # a new attempt starts cleanly and keeps the history
        again = self._pregnancy(self.mare, OVULATION + relativedelta(days=60))
        self.assertEqual(self.mare.active_pregnancy_id, again)
        self.assertEqual(len(self.mare.pregnancy_ids), 2)

    def test_inconclusive_result_needs_a_vet_chosen_date_and_negative_closes(self):
        preg = self._pregnancy()
        Check = self.env['alfia.pregnancy.check']
        with self.assertRaises(ValidationError):
            Check.with_user(self.vet).create({'pregnancy_id': preg.id, 'date': OVULATION + relativedelta(days=18), 'result': 'inconclusive'})
        nxt = OVULATION + relativedelta(days=25)
        chk = Check.with_user(self.vet).create({'pregnancy_id': preg.id, 'date': OVULATION + relativedelta(days=18), 'result': 'inconclusive', 'next_check_date': nxt})
        extra = self.env['alfia.task'].search([('key', '=', f"preg:{preg.id}:uncertain:{chk.id}")])
        self.assertEqual(extra.due_date, nxt, "the vet's date, not an automatic interval")
        self.assertEqual(preg.state, 'bred')
        Check.with_user(self.vet).create({'pregnancy_id': preg.id, 'date': nxt, 'result': 'negative'})
        self.assertEqual((preg.state, self.mare.reproductive_state), ('negative', 'open'))

    def test_heat_in_a_pregnant_mare_alerts_the_vet_without_ending_the_pregnancy(self):
        preg = self._pregnancy()
        heat = self.env.ref('alfia_equine.symbol_heat')
        self.env['alfia.teasing'].with_user(self.stable).create({'mare_id': self.mare.id, 'symbol_id': heat.id, 'date': OVULATION + relativedelta(days=30)})
        alert = self.env['alfia.task'].search([('horse_id', '=', self.mare.id), ('kind', '=', 'heat_alert')])
        self.assertTrue(alert.urgent)
        self.assertEqual(alert.assigned_group_id, self.env.ref('alfia_security.group_alfia_vet_user'))
        self.assertEqual(preg.state, 'bred', "only a vet's confirmed result ends a pregnancy")
        self.assertTrue(preg.task_ids.filtered(lambda t: t.state == 'todo'))
        # an open mare in heat is simply a chart entry
        self.env['alfia.teasing'].create({'mare_id': self.mare2.id, 'symbol_id': heat.id})
        self.assertFalse(self.env['alfia.task'].search([('horse_id', '=', self.mare2.id), ('kind', '=', 'heat_alert')]))
        with self.assertRaises(ValidationError):
            self.env['alfia.teasing'].create({'mare_id': self.stallion.id, 'symbol_id': heat.id})

    def test_who_is_due_for_teasing(self):
        self._approve_rule()
        in_season, out_of_season = fields.Date.from_string('2026-11-20'), fields.Date.from_string('2026-08-01')
        cross_year = fields.Date.from_string('2027-01-05')
        self.assertTrue(self.mare2._is_teasing_due(in_season), "barren mares are teased from 1 October...")
        self.assertTrue(self.mare2._is_teasing_due(cross_year), "...across the new year...")
        self.assertFalse(self.mare2._is_teasing_due(out_of_season), "...until 15 June")
        self.assertTrue(self.mare2._is_teasing_due(fields.Date.from_string('2026-06-15')))
        self.assertFalse(self.mare2._is_teasing_due(fields.Date.from_string('2026-06-16')))
        self.assertFalse(self.stallion._is_teasing_due(in_season))
        preg = self._pregnancy(self.mare, fields.Date.from_string('2026-10-10'))
        self.assertTrue(self.mare._is_teasing_due(fields.Date.from_string('2026-10-10') + relativedelta(days=120)), "pregnant mares through day 120")
        self.assertFalse(self.mare._is_teasing_due(fields.Date.from_string('2026-10-10') + relativedelta(days=121)))
        self.assertTrue(preg)
        listed = self.env['alfia.horse'].search([('teasing_due', '=', True)])
        self.assertTrue(set(listed.ids) <= set(self.env['alfia.horse'].search([('sex', '=', 'female')]).ids))

    def test_foaling_has_three_steps_and_never_duplicates_the_foal(self):
        self.stallion.sex  # noqa
        service = self.env['alfia.breeding.service'].create({'mare_id': self.mare.id, 'stallion_id': self.stallion.id, 'date': OVULATION, 'method': 'ai_fresh'})
        self._approve_rule()
        ov = self.env['alfia.ovulation'].create({'mare_id': self.mare.id, 'date': OVULATION, 'service_id': service.id})
        ov.with_user(self.vet).action_confirm()
        preg = ov.pregnancy_id
        self.assertEqual(preg.sire_id, self.stallion)
        Foaling = self.env['alfia.foaling']
        draft = Foaling.with_user(self.stable).create({'pregnancy_id': preg.id, 'date': fields.Date.today(), 'foal_sex': 'filly', 'foal_name': 'Cleo II'})
        self.assertEqual(draft.state, 'draft')
        with self.assertRaises(AccessError):
            draft.with_user(self.stable).action_vet_confirm()
        with self.assertRaises(UserError):
            draft.with_user(self.breed_mgr).action_complete()        # the vet confirms first
        draft.with_user(self.vet).action_vet_confirm()
        with self.assertRaises(AccessError):
            draft.with_user(self.stable).action_complete()
        draft.with_user(self.breed_mgr).action_complete()
        foal = draft.foal_id
        self.assertEqual((foal.name, foal.sex, foal.sire_id, foal.dam_id), ('Cleo II', 'female', self.stallion, self.mare))
        self.assertFalse(foal.carrier_mare_id, "the dam carried it herself")
        self.assertEqual(foal.location_id, self.barn_a, "the foal arrives where its mother is")
        draft.with_user(self.breed_mgr).action_complete()
        self.assertEqual(self.env['alfia.horse'].search_count([('name', '=', 'Cleo II')]), 1, "completing twice never makes a second foal")
        self.assertEqual((preg.state, self.mare.reproductive_state), ('foaled', 'open'))
        self.assertFalse(preg.task_ids.filtered(lambda t: t.state == 'todo'))
        with self.assertRaises(Exception):
            with self.env.cr.savepoint():
                Foaling.create({'pregnancy_id': preg.id})

    def test_embryo_transfer_separates_genetic_dam_from_carrier(self):
        recipient_preg = self._pregnancy(self.mare2)
        embryo = self.env['alfia.embryo'].create({'donor_id': self.mare.id, 'sire_id': self.stallion.id, 'tank_id': self.tank.id})
        embryo.action_store()
        with self.assertRaises(UserError):
            embryo.action_transfer(self._pregnancy(self.mare, OVULATION + relativedelta(days=5)))      # donor cannot carry its own transfer
        embryo.action_transfer(recipient_preg)
        self.assertEqual((embryo.state, recipient_preg.genetic_dam_id, recipient_preg.mare_id), ('transferred', self.mare, self.mare2))
        with self.assertRaises(UserError):
            embryo.action_transfer(recipient_preg)
        draft = self.env['alfia.foaling'].create({'pregnancy_id': recipient_preg.id, 'foal_sex': 'colt', 'foal_name': 'Embryo colt'})
        draft.with_user(self.vet).action_vet_confirm()
        draft.with_user(self.breed_mgr).action_complete()
        foal = draft.foal_id
        self.assertEqual((foal.dam_id, foal.carrier_mare_id), (self.mare, self.mare2), "genetic dam and carrier are recorded apart")
        self.assertEqual(self.env['alfia.horse'].search_count([('name', '=', 'Embryo colt')]), 1)

    def test_aborted_birth_closes_the_pregnancy_as_a_loss(self):
        preg = self._pregnancy()
        draft = self.env['alfia.foaling'].create({'pregnancy_id': preg.id, 'outcome': 'aborted'})
        draft.with_user(self.vet).action_vet_confirm()
        draft.with_user(self.breed_mgr).action_complete()
        self.assertEqual(preg.state, 'lost')
        self.assertFalse(draft.foal_id)


@tagged('post_install', '-at_install', 'alfia_equine')
class TestSemen(EquineCase):

    def _collection(self, **extra):
        vals = {'stallion_id': self.stallion.id, 'doses': 10, 'concentration': 200, 'motility': 70, 'tank_id': self.tank.id}
        return self.env['alfia.semen.collection'].create({**vals, **extra})

    def test_a_vet_releases_a_batch_into_controlled_stock(self):
        col = self._collection()
        with self.assertRaises(AccessError):
            col.with_user(self.stable).action_release()
        with self.assertRaises(UserError):
            self._collection(motility=0).with_user(self.vet).action_release()      # quality values are required
        col.with_user(self.vet).action_release()
        self.assertEqual((col.state, col.released_by_id), ('released', self.vet))
        quant = self.env['stock.quant'].search([('lot_id', '=', col.lot_id.id), ('location_id', '=', self.tank.id)])
        self.assertEqual(quant.quantity, 10)
        self.assertTrue(self.env['stock.move.line'].search([('lot_id', '=', col.lot_id.id)]), "the doses arrive through a stock move")
        with self.assertRaises(UserError):
            col.unlink()
        with self.assertRaises(UserError):
            col.with_user(self.vet).action_release()

    def test_semen_is_controlled_stock_with_dual_dispatch_approval(self):
        categ = self.env.ref('alfia_equine.category_semen')
        self.assertEqual(categ.alfia_effective_group_id, self.env.ref('alfia_security.group_alfia_semen_custodian'))
        col = self._collection()
        col.with_user(self.vet).action_release()
        site = self.env['alfia.access.profile'].create({'name': 'Eq tank site', 'group_ids': [Command.set([self.env.ref('stock.group_stock_user').id])], 'location_ids': [Command.set([self.tank.id])]})
        plain = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'plain', 'login': 'plain@eq.local', 'email': 'plain@eq.local', 'alfia_profile_ids': [Command.set([site.id])]})
        self.assertEqual(self.env['stock.quant'].with_user(plain).search_count([('lot_id', '=', col.lot_id.id)]), 0, "stock staff without the custodian role cannot see semen")
        custodian_profile = self.env['alfia.access.profile'].create({'name': 'Eq custodian', 'group_ids': [Command.set([self.env.ref('alfia_security.group_alfia_semen_custodian').id])], 'location_ids': [Command.set([self.tank.id])]})
        custodian = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'cust', 'login': 'cust@eq.local', 'email': 'cust@eq.local', 'alfia_profile_ids': [Command.set([custodian_profile.id])]})
        self.assertEqual(self.env['stock.quant'].with_user(custodian).search_count([('lot_id', '=', col.lot_id.id)]), 1)
        customers = self.env.ref('stock.stock_location_customers')
        picking = self.env['stock.picking'].create({
            'picking_type_id': self.wh.out_type_id.id, 'location_id': self.tank.id, 'location_dest_id': customers.id,
            'move_ids': [Command.create({'product_id': col.product_id.id, 'product_uom_qty': 2, 'location_id': self.tank.id, 'location_dest_id': customers.id})]})
        picking.action_confirm()
        picking.action_assign()
        self.assertEqual(picking.alfia_required_group_ids, categ.alfia_approver_group_ids)
        with self.assertRaises(UserError):
            picking.with_user(custodian).button_validate()


@tagged('post_install', '-at_install', 'alfia_equine')
class TestSegregation(EquineCase):

    def test_function_segregation_by_default_deny(self):
        Health = self.env['alfia.health.record']
        Health.create({'horse_id': self.mare.id, 'title': 'Flu', 'type': 'vaccine'})
        with self.assertRaises(AccessError):
            Health.with_user(self.stable).search([])        # stable staff have no access to veterinary records at all
        with self.assertRaises(AccessError):
            Health.with_user(self.trainer).search([])
        self.assertTrue(Health.with_user(self.breeder).search([]), "breeding staff may read them")
        with self.assertRaises(AccessError):
            Health.with_user(self.breeder).create({'horse_id': self.mare.id, 'title': 'x', 'type': 'other'})
        Health.with_user(self.vet).create({'horse_id': self.mare.id, 'title': 'Booster', 'type': 'vaccine'})
        with self.assertRaises(AccessError):
            Health.with_user(self.vet).search([]).unlink()               # vets cannot delete, only the resident vet
        # stable staff record daily care but not training
        self.env['alfia.temperature'].with_user(self.stable).create({'horse_id': self.mare.id, 'temperature': 38.0})
        with self.assertRaises(AccessError):
            self.env['alfia.training.session'].with_user(self.stable).create({'horse_id': self.mare.id})
        self.env['alfia.training.session'].with_user(self.trainer).create({'horse_id': self.mare.id, 'gait': 'canter'})
        with self.assertRaises(AccessError):
            self.env['alfia.lab.test'].with_user(self.trainer).search([])
        # only managers maintain the horse record itself
        with self.assertRaises(AccessError):
            self.mare.with_user(self.stable).write({'breed': 'x'})
        self.mare.with_user(self.breed_mgr).write({'breed': 'Arabian'})

    def test_site_segregation_covers_horses_and_their_records(self):
        site_a = self.env['alfia.access.profile'].create({'name': 'Eq site A vet', 'group_ids': [Command.set([self.env.ref('alfia_security.group_alfia_vet_user').id])], 'location_ids': [Command.set([self.barn_a.id])]})
        vet_a = self.env['res.users'].with_context(no_reset_password=True).create({'name': 'veta', 'login': 'veta@eq.local', 'email': 'veta@eq.local', 'alfia_profile_ids': [Command.set([site_a.id])]})
        Health = self.env['alfia.health.record']
        h_a = Health.create({'horse_id': self.mare.id, 'title': 'A', 'type': 'other'})
        h_b = Health.create({'horse_id': self.far_mare.id, 'title': 'B', 'type': 'other'})
        horses = self.env['alfia.horse'].with_user(vet_a).search([])
        self.assertIn(self.mare, horses)
        self.assertNotIn(self.far_mare, horses, "a horse in another barn is out of scope")
        self.assertEqual(Health.with_user(vet_a).search([('id', 'in', (h_a | h_b).ids)]), h_a)
        with self.assertRaises(AccessError):
            h_b.with_user(vet_a).read(['title'])
        self.assertFalse(self.env['alfia.task'].with_user(vet_a).search([('horse_id', '=', self.far_mare.id)]))
        unplaced = self.env['alfia.horse'].create({'name': 'Unplaced', 'sex': 'male'})
        self.assertNotIn(unplaced, self.env['alfia.horse'].with_user(vet_a).search([]), "a horse with no location is visible to nobody who is scoped")
        self.assertIn(unplaced, self.env['alfia.horse'].with_user(self.vet).search([]))       # global roles see it
        # moving the horse moves it between scopes immediately
        self.env['alfia.horse.movement'].create({'horse_id': self.far_mare.id, 'kind': 'internal', 'location_to_id': self.barn_a.id}).action_done()
        self.assertIn(self.far_mare, self.env['alfia.horse'].with_user(vet_a).search([]))

    def test_auditors_read_but_never_write(self):
        auditor = self._user('auditor', ['security_auditor'])
        self.assertIn(self.mare, self.env['alfia.horse'].with_user(auditor).search([]))
        with self.assertRaises(AccessError):
            self.mare.with_user(auditor).write({'breed': 'x'})

    def test_record_shortcuts_open_only_known_models(self):
        action = self.mare.with_context(records_model='alfia.health.record').action_open_records()
        self.assertEqual(action['domain'], [('horse_id', '=', self.mare.id)])
        with self.assertRaises(UserError):
            self.mare.with_context(records_model='res.users').action_open_records()
