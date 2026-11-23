"""Module selection, typed comparisons, and preserved historical record contracts."""
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo
import json
import uuid

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase
from django.urls import reverse
from django.utils import timezone

from care import cadences, services
from care.breeds import catalogue, reference_for
from care.comparisons import change, compare_metric, daily_values, measurement, recent_statistics, reference_geometry
from care.dashboard import module_board_tiles, today_tiles
from care.demo_fixtures import record_data
from care.forms import RecordForm, RoutineForm
from care.interactions import config_state
from care.models import Activity, ActivityRevision, CareCadence, ModuleGroup
from care.modules import MODULES, SELECTABLE_MODULES, RETIRED_MODULES, MAX_MODULES
from care.organization import browse_context
from care.stats import get_statistics
from .test_functional import CareCase

END = date(2026, 11, 8)
TZ = ZoneInfo('Asia/Seoul')


def row(module, day, hour=12, **payload):
    dt = datetime.combine(day, time(hour), TZ)
    return SimpleNamespace(pk=uuid.uuid4(), module=module, payload=payload, nutrition={}, occurred_at=dt,
                           created_at=dt, ended_at=None, status='done', voided=False)


class ComparisonMathTests(SimpleTestCase):
    def test_weight_change_is_numeric_with_same_length_windows(self):
        rows = [row('weight', END-timedelta(days=i), weight='8.4' if i<7 else '8', unit='kg') for i in range(14)]
        result = compare_metric(rows, 'weight', END, 7, 'Asia/Seoul')
        self.assertEqual(result['before']['average'], Decimal('8'))
        self.assertEqual(result['recent']['average'], Decimal('8.4'))
        self.assertEqual(result['change']['delta'], Decimal('.4'))
        self.assertEqual(result['change']['percent'], Decimal(5))
        self.assertEqual(result['recent']['days'], 7)
        self.assertIn('늘었어요', result['title'])

    def test_last_daily_weight_not_measurement_count_weighting(self):
        rows = [row('weight', END, hour=9, weight='6', unit='kg'), row('weight', END, hour=18, weight='8', unit='kg'),
                row('weight', END-timedelta(days=1), weight='10', unit='kg')]
        result = compare_metric(rows, 'weight', END, 7, 'Asia/Seoul')
        self.assertEqual(result['recent']['average'], Decimal(9))
        self.assertEqual(result['recent']['days'], 2)
        self.assertEqual(result['recent']['records'], 2)
        self.assertFalse(result['change']['available'])

    def test_distance_units_zero_and_partial_days_are_independent_of_time(self):
        rows = [row('walk', END, hour=9, distance='1000', distance_unit='m', minutes='10'),
                row('walk', END, hour=18, distance='1', distance_unit='km', minutes='20'),
                row('walk', END-timedelta(days=1), distance='0', distance_unit='km', minutes='5'),
                row('walk', END-timedelta(days=2), distance=None, distance_unit='km', minutes='8')]
        distance = compare_metric(rows, 'walk_distance', END, 7, 'Asia/Seoul')
        duration = compare_metric(rows, 'walk_minutes', END, 7, 'Asia/Seoul')
        self.assertEqual(distance['recent']['average'], Decimal(1))
        self.assertEqual(distance['recent']['days'], 2)
        self.assertEqual(distance['recent']['partial_days'], 1)
        self.assertEqual(duration['recent']['days'], 3)

    def test_one_missing_walk_does_not_create_a_false_small_daily_total(self):
        rows = [row('walk', END, distance='2', distance_unit='km', minutes='10'),
                row('walk', END, hour=18, distance=None, distance_unit='km', minutes='15')]
        data = compare_metric(rows, 'walk_distance', END, 7, 'Asia/Seoul')
        self.assertEqual(data['recent']['days'], 0)
        self.assertIsNone(data['recent']['average'])
        self.assertEqual(data['recent']['partial_days'], 1)

    def test_recording_days_are_not_filled_with_zero(self):
        rows = [row('walk', END, distance='6', distance_unit='km', minutes='60'),
                row('walk', END-timedelta(days=8), distance='3', distance_unit='km', minutes='30')]
        result = compare_metric(rows, 'walk_distance', END, 7, 'Asia/Seoul')
        self.assertEqual(result['before']['average'], 3)
        self.assertEqual(result['recent']['average'], 6)
        self.assertEqual(result['change']['percent'], 100)
        self.assertIn('기록일이 적어', result['note'])

    def test_zero_baseline_has_no_infinite_percentage(self):
        result = change(Decimal(0), Decimal(2))
        self.assertEqual(result['delta'], 2)
        self.assertIsNone(result['percent'])
        self.assertIn('0이라', result['percent_text'])
        self.assertEqual(change(Decimal(2), Decimal(0))['percent'], -100)

    def test_negative_nonfinite_and_unknown_units_are_not_values(self):
        for value in ['NaN', 'Infinity', '-1', '', None]:
            self.assertIsNone(measurement(row('weight', END, weight=value, unit='kg'), 'weight'))
        self.assertIsNone(measurement(row('walk', END, distance=1, distance_unit='mi'), 'walk_distance'))
        self.assertEqual(measurement(row('weight', END, weight=8400, unit='g'), 'weight'), Decimal('8.4'))

    def test_duplicate_cancelled_active_and_outside_window_not_compared(self):
        a = row('walk', END, distance=2, distance_unit='km', minutes=20)
        b = row('walk', END, distance=500, distance_unit='km', minutes=20); b.voided = True
        c = row('walk', END, distance=500, distance_unit='km', minutes=20); c.status = 'active'
        result = compare_metric([a,a,b,c,row('walk', END+timedelta(days=1), distance=500, distance_unit='km')], 'walk_distance', END, 7, 'Asia/Seoul')
        self.assertEqual(result['recent']['average'], 2)
        self.assertEqual(result['recent']['records'], 1)

    def test_period_boundaries_and_alternate_calendar(self):
        rows = [row('weight', END-timedelta(days=13), weight=8, unit='kg'), row('weight', END-timedelta(days=7), weight=8, unit='kg'),
                row('weight', END-timedelta(days=6), weight=9, unit='kg'), row('weight', END, weight=9, unit='kg')]
        result = compare_metric(rows, 'weight', END, 7, 'Asia/Seoul')
        self.assertEqual((result['before']['days'], result['recent']['days']), (2,2))
        late = row('weight', END, hour=1, weight=8, unit='kg')
        vals,_ = daily_values([late], 'weight', 'America/Los_Angeles')
        self.assertEqual(vals[0]['date'], END-timedelta(days=1))

    def test_decrease_same_and_absent_are_distinct(self):
        self.assertEqual(change(Decimal(8), Decimal('7.5'))['direction'], 'down')
        self.assertEqual(change(Decimal(8), Decimal(8))['direction'], 'same')
        self.assertEqual(change(None, Decimal(8))['direction'], 'missing')

    def test_reference_position_clips_outlier_without_losing_number(self):
        result = reference_geometry({'available':True,'low':5,'high':8,'value':1000})
        self.assertTrue(result['clipped'])
        self.assertEqual(result['value_x'], 300)
        self.assertEqual(result['value_text'], '1000')


class ModuleLifecycleTests(CareCase):
    def test_eighteen_selectable_twenty_historical_without_deleting_data(self):
        self.assertEqual(len(MODULES), 20)
        self.assertEqual(len(SELECTABLE_MODULES), 18)
        self.assertLessEqual(len(SELECTABLE_MODULES), MAX_MODULES)
        self.assertNotIn('grooming', SELECTABLE_MODULES)
        self.assertEqual(MODULES['hygiene'].name, '위생·손질')
        response = self.client.get(reverse('module_settings', args=[self.room.pk]))
        self.assertEqual(len(response.context['rows']), 18)
        self.assertNotIn('reproduction', SELECTABLE_MODULES)
        self.assertNotIn('grooming', config_state(self.room)['order'])
        self.assertNotContains(response, 'data-add-module="grooming"')

    def test_old_grooming_records_versions_and_legacy_settings_survive_board_save(self):
        old = self.make('grooming', task='brush')
        before = list(Activity.objects.values()), list(ActivityRevision.objects.values())
        setting = self.room.module_settings.get(key='grooming'); signature=(setting.enabled,setting.pinned,setting.position)
        self.room.refresh_from_db()
        services.update_modules(self.owner, list(SELECTABLE_MODULES), list(SELECTABLE_MODULES)+list(RETIRED_MODULES), self.room.settings_version, ['hygiene'])
        setting.refresh_from_db()
        self.assertEqual((setting.enabled,setting.pinned,setting.position),signature)
        self.assertEqual(before,(list(Activity.objects.values()),list(ActivityRevision.objects.values())))
        self.assertEqual(self.client.get(reverse('record_detail',args=[self.room.pk,old.pk])).status_code,200)
        response = self.client.get(reverse('module',args=[self.room.pk,'grooming']))
        self.assertContains(response,'위생·손질')
        self.assertContains(response,'이전 미용 기록을 보존')

    def test_new_room_cannot_activate_retired_type(self):
        self.room.module_settings.filter(key='grooming').update(enabled=False)
        self.room.refresh_from_db()
        with self.assertRaises(ValidationError):
            services.update_modules(self.owner,list(MODULES),list(MODULES),self.room.settings_version,[])

    def test_new_hygiene_variants_are_valid_and_old_keys_stay_editable(self):
        for task in ['bath','teeth','ear','paw','brush','hair','nail']:
            self.assertEqual(self.make('hygiene',task=task).payload['task'],task)
        old=self.make('grooming',task='nail')
        form=self.form('grooming',instance=old,task='nail',detail='수정',version=str(old.version),client_key=str(old.client_key))
        self.assertTrue(form.is_valid(),form.errors)
        changed,_=services.save_activity(self.owner,'grooming',form,old.pk)
        self.assertEqual(changed.module,'grooming')
        self.assertEqual(changed.payload['detail'],'수정')

    def test_legacy_routine_and_cadence_keep_working_but_not_in_new_choices(self):
        new=RoutineForm(room=self.room,visible_keys=list(MODULES))
        self.assertNotIn('grooming',dict(new.fields['modules'].choices))
        routine=self.routine(('hygiene',));routine.items.update(module='grooming')
        edit=RoutineForm(room=self.room,visible_keys=list(MODULES),instance=routine)
        self.assertIn('grooming',dict(edit.fields['modules'].choices))
        cadence=CareCadence.objects.create(room=self.room,module='grooming',variant_field='task',variant_value='brush',title='이전 빗질',interval=7,unit='days',first_due=services.local_date(self.room))
        edit_cadence=cadences.CadenceForm(member=self.owner,instance=cadence)
        self.assertIn('grooming:task:brush',dict(edit_cadence.fields['activity'].choices))
        self.assertNotIn('grooming:task:brush',dict(cadences.CadenceForm(member=self.owner).fields['activity'].choices))
        a=self.make('grooming',task='brush')
        self.assertTrue(cadences.matches(cadence,a))

    def test_group_history_not_rewritten_but_replacement_visible(self):
        group=ModuleGroup.objects.create(room=self.room,name='손질',keys=['grooming'],position=0)
        data=browse_context(self.owner)
        keys=next(g['keys'] for g in data['custom_groups'] if g['label']=='손질')
        self.assertEqual(set(keys),{'grooming','hygiene'})
        group.refresh_from_db();self.assertEqual(group.keys,['grooming'])

    def test_category_choice_omits_retired_and_preserves_old_activities(self):
        old=self.make('grooming');url=reverse('category_config',args=[self.room.pk,'living'])
        values=self.client.get(url).json()
        self.assertNotIn('grooming',{o['key'] for o in values['items']})
        data={'version':values['version'],'selected':['hygiene']}
        self.assertEqual(self.client.post(url,json.dumps(data),content_type='application/json').status_code,200)
        old.refresh_from_db();self.assertFalse(old.voided)


class StatisticsIntegrationTests(CareCase):
    def make_at(self,key,ago,**changes):
        when=timezone.localtime().replace(hour=0,minute=0,second=0,microsecond=0)-timedelta(days=ago)
        return self.make(key,occurred_at=when.strftime('%Y-%m-%dT%H:%M:%S'),**changes)

    def test_top_comparison_has_delta_baseline_dates_and_separate_latest(self):
        for ago in range(1,15):self.make_at('weight',ago,weight='8.4' if ago<=7 else '8')
        today=self.make('weight',weight='9')
        result=recent_statistics(self.owner)
        weight=next(c for c in result['comparison_cards'] if c['key']=='weight')
        self.assertEqual(weight['change']['delta'],Decimal('.4'))
        self.assertEqual(result['latest_weight']['value'],Decimal(9))
        self.assertEqual(result['latest_weight']['ids'],[str(today.pk)])
        self.assertEqual(result['comparison_end'],services.local_date(self.room)-timedelta(days=1))
        response=self.client.get(reverse('overview',args=[self.room.pk]))
        for text in ['최근 통계','측정값 비교','+0.4','+5%','평균','기록한']:
            self.assertContains(response,text)
        self.assertNotContains(response,'최근 변화')

    def test_only_enabled_core_modules_and_no_irrelevant_external_average(self):
        self.room.module_settings.filter(key='walk').update(enabled=False)
        result=recent_statistics(self.owner)
        self.assertEqual([c['key'] for c in result['comparison_cards']],['weight'])
        self.assertNotIn('grooming',{x['key'] for x in result['other_statistics']})
        self.assertIn('cleaning',{x['key'] for x in result['other_statistics']})

    def test_sensitive_and_other_room_are_excluded_from_comparison_summaries(self):
        a=self.make('expense',item='비공개 항목');self.make('medication',medicine='비공개 약')
        result=recent_statistics(self.guest)
        self.assertNotIn('expense',{m['key'] for m in result['other_statistics']})
        self.assertNotIn('medication',{m['key'] for m in result['other_statistics']})
        a.room_id=self.other_room.pk
        self.assertNotIn('expense',{m['key'] for m in recent_statistics(self.guest,records=[a])['other_statistics']})
        response=self.client_for(self.guest).get(reverse('overview',args=[self.room.pk]))
        self.assertNotContains(response,'비공개')

    def test_no_external_network_or_llm_is_needed(self):
        self.make_at('weight',1)
        with patch('requests.sessions.Session.request',side_effect=AssertionError('network')),patch('socket.socket.connect',side_effect=AssertionError('network')):
            self.assertTrue(recent_statistics(self.owner)['latest_weight'])

    def test_breed_scope_adult_sex_unknown_mix_and_cross_species(self):
        breed=next(b for b in catalogue().values() if b['species']=='dog' and b.get('reference') and b['key']=='dog-bichon-frise')
        self.room.breed_key=breed['key'];self.room.maturity='adult';self.room.sex='male';self.room.save()
        ref=reference_for(self.room,{'value':'8.4','date':services.local_date(self.room)})
        self.assertTrue(ref['available']);self.assertIn('평균',ref['message'])
        self.room.maturity='growing';self.assertFalse(reference_for(self.room)['available'])
        self.room.maturity='adult';self.room.breed_key='mixed';self.assertFalse(reference_for(self.room)['available'])
        self.room.breed_key=breed['key'];self.room.species='cat';self.assertFalse(reference_for(self.room)['available'])

    def test_bcs_is_direct_assessed_value_and_not_derived_from_weight(self):
        self.make('weight',bcs='',bcs_by='')
        self.assertIsNone(recent_statistics(self.owner)['bcs_card'])
        self.make('weight',bcs='4',bcs_by='vet')
        data=recent_statistics(self.owner)['bcs_card']
        self.assertEqual((data['low'],data['high'],data['score']),(4,5,4))
        self.assertEqual(data['assessor'],'수의사 평가')
        self.room.species='cat';self.room.save()
        data=recent_statistics(self.owner)['bcs_card'];self.assertEqual((data['low'],data['high']),(5,5))
        self.room.species='bird';self.room.save();self.assertIsNone(recent_statistics(self.owner)['bcs_card'])

    def test_distance_is_on_both_cards_and_quick_finish_input(self):
        a=self.make('walk',distance='1500',distance_unit='m')
        tile=module_board_tiles(self.owner,[{'spec':MODULES['walk']}])['walk']
        self.assertEqual(tile['secondary'][0]['value'],'1.5')
        self.assertEqual(tile['secondary'][0]['unit'],'km')
        response=self.client.get(reverse('quick_record',args=[self.room.pk,'walk'])+'?done=1',HTTP_X_TAILY_UI='1')
        self.assertIn('distance',[f.name for f in response.context['core_fields']])
        self.assertContains(self.client.get(reverse('modules',args=[self.room.pk])),'걸은 거리')

    def test_zero_walk_distance_is_not_missing(self):
        self.make('walk',distance='0')
        tile=module_board_tiles(self.owner,[{'spec':MODULES['walk']}])['walk']
        self.assertEqual(tile['secondary'][0]['value'],'0')
        self.assertEqual(tile['secondary'][0]['note'],'')

    def test_midnight_sleep_is_shared_between_today_and_detail(self):
        today=services.local_date(self.room);begin=datetime.combine(today-timedelta(days=1),time(23),ZoneInfo(self.room.timezone))
        self.make('sleep',occurred_at=begin.strftime('%Y-%m-%dT%H:%M'),minutes='120')
        tile=module_board_tiles(self.owner,[{'spec':MODULES['sleep']}])['sleep']
        self.assertEqual((tile['value'],tile['unit']),('60','분'))
        stats=get_statistics(self.room,'sleep',today,today)
        self.assertEqual(stats['primary'],'60')

    def test_food_basis_separation_applies_to_metrics_charts_and_cards(self):
        self.make('meal',product=str(self.product.pk),quantity='100')
        self.make('meal',product=str(self.product.pk),quantity='100',consumed='50')
        stats=self.stats('meal');self.assertEqual(stats['primary'],'기준별 보기')
        self.assertEqual(len(stats['charts']),2)
        totals=[m['value'] for m in stats['metrics'] if m['unit']=='kcal']
        self.assertEqual(set(totals),{'380','190'})
        tile=module_board_tiles(self.owner,[{'spec':MODULES['meal']}])['meal']
        self.assertEqual({s['value'] for s in tile['secondary']},{'190','380'})

    def test_inventory_carry_forward_is_visible_without_today_entry(self):
        self.make_at('inventory',4,item='패드',quantity='10',unit='piece',minimum='3')
        tile=module_board_tiles(self.owner,[{'spec':MODULES['inventory']}])['inventory']
        self.assertEqual((tile['value'],tile['unit']),('10','개'))

    def test_duration_computed_from_end_cannot_bypass_maximum(self):
        start=timezone.localtime()-timedelta(days=3);end=start+timedelta(days=2)
        form=self.form('walk',occurred_at=start.strftime('%Y-%m-%dT%H:%M'),ended_at=end.strftime('%Y-%m-%dT%H:%M'),minutes='25')
        self.assertFalse(form.is_valid());self.assertIn('ended_at',form.errors)

    def test_piece_counts_cannot_be_fractional(self):
        for field in ('quantity','minimum'):
            data={'quantity':'2','minimum':'1','unit':'piece',field:'1.5'}
            form=self.form('inventory',**data);self.assertFalse(form.is_valid());self.assertIn(field,form.errors)

    def test_existing_bcs_choice_rejects_fraction_and_other_species(self):
        form=self.form('weight',bcs='4.5');self.assertFalse(form.is_valid())
        self.room.species='bird';self.room.save()
        form=self.form('weight',bcs='5');self.assertNotIn('bcs',form.fields)

    def test_feather_option_and_legacy_egg_records_remain_valid(self):
        self.assertEqual(self.make('shedding',kind='feather').payload['kind'],'feather')
        old=self.make('reproduction',kind='egg',eggs='2')
        form=self.form('reproduction',instance=old,kind='egg',eggs='3')
        self.assertFalse(form.fields['eggs'].widget.is_hidden)
        self.assertTrue(form.is_valid(),form.errors)
        modern=RecordForm(room=self.room,module='reproduction',member=self.owner)
        self.assertTrue(modern.fields['eggs'].widget.is_hidden)

    def test_training_success_rates_are_split_by_skill(self):
        self.make('training',skill='앉아',attempts='10',successes='8')
        self.make('training',skill='기다려',attempts='2',successes='1')
        stats=self.stats('training')
        rows={r['label']:r for r in stats['breakdown'] if '시도 대비' in r['label']}
        self.assertEqual(rows['앉아 · 시도 대비 성공']['value'],'8 / 10')
        self.assertEqual(rows['기다려 · 시도 대비 성공']['value'],'1 / 2')

    def test_all_registered_types_still_support_current_or_historical_detail(self):
        for module in MODULES:
            activity=self.make(module)
            for route,args in [('module',[self.room.pk,module]),('statistics',[self.room.pk,module]),('record_detail',[self.room.pk,activity.pk])]:
                response=self.client.get(reverse(route,args=args));self.assertEqual(response.status_code,200,(module,route))

    def test_reading_new_statistics_is_nonmutating(self):
        self.make_at('weight',1);before=list(Activity.objects.values()),list(ActivityRevision.objects.values())
        for days in [7,30,90,'bad',-10]:
            self.assertEqual(self.client.get(reverse('overview',args=[self.room.pk]),{'days':days}).status_code,200)
        self.assertEqual(before,(list(Activity.objects.values()),list(ActivityRevision.objects.values())))


class AdditionalDisplayBoundaries(CareCase):
    def test_small_converted_value_does_not_round_to_zero(self):
        from care.modules import compact
        self.assertEqual(compact(Decimal("0.000001")), "0.000001")
        self.assertEqual(compact(0), "0")

    def test_planned_medication_is_not_worded_as_already_given(self):
        a=self.make("medication",status="planned")
        response=self.client.get(reverse("record_detail",args=[self.room.pk,a.pk]))
        self.assertContains(response,"투약 예정")
        self.assertNotContains(response,"· 투약함")


class RoundingAndSpeciesTests(SimpleTestCase):
    def test_rounded_endpoints_do_not_hide_a_real_small_difference(self):
        rows=[row('weight',END,weight='8.0001',unit='kg'),row('weight',END-timedelta(days=8),weight='8',unit='kg')]
        result=compare_metric(rows,'weight',END,7,'Asia/Seoul')
        self.assertNotEqual(result['before']['text'],result['recent']['text'])
        self.assertNotEqual(result['change']['percent_text'],'0%')
        self.assertIn('0.0001',result['change']['delta_text'])


class OtherSpeciesReferenceTests(CareCase):
    def test_bird_weight_has_personal_comparison_without_canine_reference_control(self):
        self.room.species='bird';self.room.save()
        response=self.client.get(reverse('overview',args=[self.room.pk]))
        self.assertContains(response,'이 동물종에 개·고양이의 체중 기준을 적용하지 않아요')
        self.assertNotContains(response,'품종·성장 상태 확인')
        self.assertNotContains(response,'기록한 체형 점수')
