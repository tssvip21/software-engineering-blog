"""Record action contracts: values/denominators, provenance, transactions, scope and compatibility."""
from datetime import datetime, time, timedelta
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo
import json
import uuid

from django.core.exceptions import ValidationError
from django.db import OperationalError
from django.urls import reverse
from django.utils import timezone

from care import record_actions, inventory_ledger, schedule, services
from care.reporting import module_reports
from care.forms import RecordForm, RoutineForm
from care.models import Activity, ActivityRevision, ActionReceipt, AuditEvent, RecordPreset, Product, RoutineRun, CareCadence
from care.modules import MODULES, SELECTABLE_MODULES
from care.presets import PresetForm, save_preset
from .test_functional import CareCase


class RecordActionCase(CareCase):
    def make_at(self,key,days,**fields):
        day=services.local_date(self.room)-timedelta(days=days)
        at=datetime.combine(day,time(8),ZoneInfo(self.room.timezone))
        if days==0:at=timezone.localtime()-timedelta(hours=2)
        return self.make(key,occurred_at=at.strftime('%Y-%m-%dT%H:%M:%S'),**fields)

    def report(self,member=None,days=7):
        return module_reports.build_report(member or self.owner,days)

    def metric(self,key,member=None,days=7):
        return next(x for x in self.report(member,days)['measurements'] if x['key']==key)

    def action(self,module,operation='quick',**changes):
        data={'token':str(uuid.uuid4()),'module':module,'operation':operation,**changes}
        return record_actions.perform(self.owner,data),data

    def preset(self,module='meal',**changes):
        data={'title':'기본 급여','active':'on','is_default':'on','product':str(self.product.pk),'quantity':'60','unit':'g',**changes}
        if module not in ('meal','treat'):data.pop('product',None)
        form=PresetForm(data,member=self.owner,module=module)
        self.assertTrue(form.is_valid(),form.errors)
        return save_preset(self.owner,module,form)


class TypedComparisonTests(RecordActionCase):
    def test_comparison_modules_expand_beyond_weight_and_walk(self):
        report=self.report();keys={x['key'] for x in report['module_cards']}
        self.assertTrue({'weight','walk','meal','treat','water','play','training','sleep'}<=keys)
        self.assertEqual([x['key'] for x in report['module_cards']].count('walk'),1)
        self.assertEqual({m['key'] for c in report['module_cards'] if c['key']=='walk' for m in c['metrics']},{'walk_minutes','walk_distance'})

    def test_closed_windows_exclude_today_and_do_not_zero_fill(self):
        self.make_at('play',8,minutes='20');self.make_at('play',1,minutes='30');self.make_at('play',0,minutes='999')
        m=self.metric('play_minutes');self.assertEqual((m['before']['value'],m['recent']['value']),(20,30))
        self.assertEqual((m['before']['observed_days'],m['recent']['observed_days']),(1,1))
        self.assertEqual(m['change']['percent'],50)
        self.assertIn('기록일이 적어',m['note'])

    def test_weight_daily_last_measurement_not_weighted_by_count(self):
        self.make_at('weight',8,weight='8');a=self.make_at('weight',1,weight='9')
        second=self.make_at('weight',1,weight='9000',unit='g')
        Activity.objects.filter(pk=second.pk).update(occurred_at=a.occurred_at+timedelta(hours=1))
        m=self.metric('weight');self.assertEqual(m['recent']['value'],9);self.assertEqual(m['recent']['records'],1)
        self.assertEqual(m['recent']['ids'],[str(second.pk)])
        self.assertFalse(m['show_total'])

    def test_exact_evidence_excludes_unused_same_day_measurements(self):
        old=self.make_at('weight',8,weight='8');first=self.make_at('weight',1,weight='9');last=self.make_at('weight',1,weight='10')
        Activity.objects.filter(pk=last.pk).update(occurred_at=first.occurred_at+timedelta(hours=1))
        m=self.metric('weight');response=self.client.get(m['recent']['url']);self.assertEqual(response.status_code,200)
        ids={str(a.pk) for a in response.context['records']};self.assertEqual(ids,{str(last.pk)})
        self.assertNotIn(str(first.pk),ids);self.assertNotIn(str(old.pk),ids)

    def test_walk_distance_and_duration_have_independent_missingness(self):
        self.make_at('walk',8,minutes='20',distance='1000',distance_unit='m')
        self.make_at('walk',1,minutes='25',distance='2');self.make_at('walk',1,minutes='5',distance='')
        duration=self.metric('walk_minutes');distance=self.metric('walk_distance')
        self.assertEqual(duration['recent']['value'],30);self.assertIsNone(distance['recent']['value'])
        self.assertEqual(distance['recent']['partial_days'],1)

    def test_explicit_zero_distance_differs_from_missing(self):
        self.make_at('walk',8,distance='0');self.make_at('walk',1,distance='0')
        m=self.metric('walk_distance');self.assertEqual(m['recent']['value'],0)
        self.assertTrue(m['change']['available']);self.assertIsNone(m['change']['percent'])

    def test_incomplete_gps_is_not_used_as_complete_distance(self):
        a=self.make_at('walk',1,distance='1');a.payload['_gps']={'complete':False,'gaps':1};a.save(update_fields=['payload'])
        self.assertIsNone(self.metric('walk_distance')['recent']['value']);self.assertEqual(self.metric('walk_minutes')['recent']['value'],25)

    def test_food_provided_and_consumed_calories_are_separate(self):
        self.make_at('meal',8,product=str(self.product.pk),quantity='100',consumed='50')
        self.make_at('meal',1,product=str(self.product.pk),quantity='200',consumed='40')
        self.assertEqual(self.metric('meal_provided_kcal')['recent']['value'],760)
        self.assertEqual(self.metric('meal_consumed_kcal')['recent']['value'],152)
        self.assertEqual(self.metric('meal_consumed_g')['recent']['value'],40)
        self.assertEqual(self.metric('meal_provided_g')['recent']['value'],200)

    def test_one_unknown_food_amount_invalidates_that_metric_day(self):
        self.make_at('meal',1,quantity='100')
        at=(timezone.localtime()-timedelta(days=1)).isoformat()
        form=RecordForm({'client_key':str(uuid.uuid4()),'occurred_at':at,'status':'done','unit':'g'},room=self.room,module='meal',member=self.owner,simple=True)
        self.assertTrue(form.is_valid(),form.errors);services.save_activity(self.owner,'meal',form)
        self.assertIsNone(self.metric('meal_provided_g')['recent']['value'])

    def test_replacement_water_is_not_measured_intake(self):
        self.make_at('water',1,kind='replace',amount='9999');self.make_at('water',1,kind='measured',amount='.25',unit='l')
        m=self.metric('water_measured');self.assertEqual(m['recent']['value'],250)
        c=next(c for c in self.report()['counts'] if c['module']=='water');self.assertEqual(c['recent'],1)

    def test_sleep_union_cross_midnight_and_alarm_exclusion(self):
        today=services.local_date(self.room);begin=datetime.combine(today-timedelta(days=2),time(23),ZoneInfo(self.room.timezone))
        self.make('sleep',minutes='120',occurred_at=begin.isoformat());self.make('sleep',minutes='120',occurred_at=(begin+timedelta(minutes=30)).isoformat())
        result,_=self.action('sleep',fields={'routine_title':'휴식 확인','kind':'rest','occurred_at':(begin-timedelta(hours=1)).isoformat()})
        m=self.metric('sleep_observed');self.assertEqual(m['recent']['total'],150)
        self.assertEqual(m['recent']['observed_days'],2)
        self.assertEqual(Activity.objects.get(pk=result['id']).payload['_entry_kind'],'schedule')

    def test_temperature_delta_is_absolute_not_celsius_percent(self):
        self.make_at('environment',8,metric='temperature',value='32',unit='F',location='케이지')
        self.make_at('environment',1,metric='temperature',value='10',unit='C',location='케이지')
        m=next(m for m in self.report()['measurements'] if m['module']=='environment')
        self.assertEqual(m['before']['value'],0);self.assertEqual(m['change']['delta'],10)
        self.assertEqual(m['change']['percent_text'],'');self.assertFalse(m['show_total'])

    def test_humidity_delta_is_percentage_points(self):
        self.make_at('environment',8,metric='humidity',value='40',unit='percent',location='케이지')
        self.make_at('environment',1,metric='humidity',value='50',unit='percent',location='케이지')
        m=next(m for m in self.report()['measurements'] if m['module']=='environment')
        self.assertEqual(m['change']['delta'],10);self.assertEqual(m['change']['unit_override'],'%p');self.assertIsNone(m['change']['percent'])

    def test_ph_uses_last_value_not_linear_average_or_percent(self):
        self.make_at('environment',8,metric='ph',value='6',unit='ph',location='수조')
        self.make_at('environment',2,metric='ph',value='8',unit='ph',location='수조')
        self.make_at('environment',1,metric='ph',value='7',unit='ph',location='수조')
        m=next(m for m in self.report()['measurements'] if m['module']=='environment')
        self.assertEqual(m['recent']['value'],7);self.assertEqual(m['aggregation'],'latest');self.assertEqual(m['change']['percent_text'],'')

    def test_environment_different_locations_are_not_merged(self):
        self.make_at('environment',1,value='20',location='A');self.make_at('environment',1,value='30',location='B')
        ms=[m for m in self.report()['measurements'] if m['module']=='environment'];self.assertEqual(len(ms),2)
        self.assertEqual({m['recent']['value'] for m in ms},{20,30})

    def test_training_ratios_use_same_skill_and_denominators(self):
        self.make_at('training',8,skill='앉아',attempts='10',successes='5')
        self.make_at('training',1,skill='앉아',attempts='2',successes='2')
        self.make_at('training',1,skill='기다려',attempts='10',successes='1')
        rates={r['skill']:r for r in self.report()['training_rates']}
        self.assertEqual(rates['앉아']['change']['delta'],50);self.assertEqual(rates['앉아']['change']['unit_override'],'%p')
        self.assertIsNone(rates['기다려']['change']['delta'])

    def test_counts_are_logged_observations_not_fabricated_measurements(self):
        self.make_at('toilet',1,kind='observed',condition='unknown')
        c=next(c for c in self.report()['counts'] if c['module']=='toilet')
        self.assertEqual((c['before'],c['recent']),(0,1));self.assertIn('실제 활동',c['note'])

    def test_sparse_weight_still_has_actual_measurement_interval_context(self):
        self.make_at('weight',40,weight='8');self.make_at('weight',1,weight='9');self.make_at('meal',20,quantity='100')
        report=self.report();self.assertIsNone(self.metric('weight')['before']['value'])
        self.assertEqual(report['latest_weight_change']['delta'],1)
        self.assertEqual(report['weight_interval']['facts'][0]['value'],'100')

    def test_new_note_combines_legacy_history_without_retyping(self):
        old=self.make_at('reproduction',2,kind='food_change',detail='이전 사료 변경')
        self.make_at('observation',1,detail='지금 메모')
        notes=self.report()['notes'];self.assertEqual(len(notes),2)
        self.assertIn('이전 생활 변화',{n['source'] for n in notes});old.refresh_from_db();self.assertEqual(old.module,'reproduction')
        self.assertContains(self.client.get(reverse('module',args=[self.room.pk,'observation'])),'이전 사료 변경')

    def test_disabled_replacements_do_not_reappear_due_to_old_flags(self):
        self.room.module_settings.filter(key__in=['hygiene','observation']).update(enabled=False)
        self.room.module_settings.filter(key__in=['grooming','reproduction']).update(enabled=True)
        report=self.report();self.assertNotIn('hygiene',report['enabled']);self.assertNotIn('observation',report['enabled'])
        response=self.client.get(reverse('modules',args=[self.room.pk]));self.assertNotIn('hygiene',{c['spec'].key for c in response.context['enabled_modules']})

    def test_sensitive_records_and_relationships_are_filtered_first(self):
        self.make_at('medication',1,medicine='비밀 약');self.make_at('expense',1,item='비밀 지출')
        report=self.report(self.guest)
        self.assertFalse({'medication','veterinary','expense'}&report['enabled'])
        self.assertFalse(report['expenses']);response=self.client_for(self.guest).get(reverse('overview',args=[self.room.pk]))
        self.assertNotContains(response,'비밀 약');self.assertNotContains(response,'비밀 지출')

    def test_foreign_cancelled_and_future_records_are_excluded(self):
        a=self.make_at('weight',1,weight='9');b=self.make_at('weight',1,weight='100');services.cancel_activity(self.owner,b.pk,b.version,'취소')
        future=self.make_at('weight',0,weight='500');Activity.objects.filter(pk=future.pk).update(occurred_at=timezone.now()+timedelta(days=1))
        b.refresh_from_db();future.refresh_from_db();a.room_id=self.other_room.pk
        report=module_reports.build_report(self.owner,records=[a,b,future]);m=next(m for m in report['measurements'] if m['key']=='weight')
        self.assertFalse(m['has_any'])

    def test_reading_statistics_is_nonmutating_and_no_network(self):
        self.make_at('weight',1)
        before=list(Activity.objects.values()),list(ActivityRevision.objects.values()),list(AuditEvent.objects.values())
        with patch('socket.create_connection',side_effect=AssertionError('unexpected external call')):
            for days in [7,30,90,'bad']:
                self.assertEqual(self.client.get(reverse('overview',args=[self.room.pk]),{'days':days}).status_code,200)
        self.assertEqual(before,(list(Activity.objects.values()),list(ActivityRevision.objects.values()),list(AuditEvent.objects.values())))


class PresetAndActionTests(RecordActionCase):
    def test_same_token_retries_create_one_record_revision_audit_and_receipt(self):
        self.preset();before=AuditEvent.objects.count()
        result,data=self.action('meal');repeat=record_actions.perform(self.owner,data)
        self.assertEqual(result['id'],repeat['id']);self.assertTrue(repeat['replayed'])
        self.assertEqual(Activity.objects.filter(module='meal').count(),1)
        self.assertEqual(ActivityRevision.objects.count(),1);self.assertEqual(ActionReceipt.objects.count(),1)
        self.assertEqual(AuditEvent.objects.count(),before+1)

    def test_same_token_different_body_rejected(self):
        _,data=self.action('toilet')
        with self.assertRaises(services.Conflict):record_actions.perform(self.owner,{**data,'fields':{'note':'different'}})
        self.assertEqual(Activity.objects.count(),1)

    def test_request_token_does_not_allow_other_actor_to_replay(self):
        _,data=self.action('toilet')
        with self.assertRaises(services.Conflict):record_actions.perform(self.member,data)

    def test_two_intentional_clicks_after_success_are_two_records(self):
        self.action('toilet');self.action('toilet');self.assertEqual(Activity.objects.count(),2)

    def test_default_preset_is_frozen_in_activity(self):
        preset=self.preset();r,_=self.action('meal')
        activity=Activity.objects.get(pk=r['id']);before=dict(activity.payload)
        data={'title':'변경한 기본','active':'on','is_default':'on','version':preset.version,'product':str(self.product.pk),'quantity':'90','unit':'g'}
        form=PresetForm(data,member=self.owner,module='meal',instance=preset);self.assertTrue(form.is_valid(),form.errors);save_preset(self.owner,'meal',form,preset)
        activity.refresh_from_db();self.assertEqual(activity.payload,before);self.assertEqual(activity.payload['quantity'],'60')
        self.assertEqual(activity.payload['_preset']['title'],'기본 급여')

    def test_only_one_default_per_module(self):
        first=self.preset();second=self.preset(title='두 번째')
        first.refresh_from_db();self.assertFalse(first.is_default);self.assertTrue(second.is_default)
        self.assertEqual(RecordPreset.objects.filter(is_default=True,active=True,module='meal').count(),1)

    def test_preset_version_conflict_and_foreign_product(self):
        p=self.preset()
        with self.assertRaises(services.Conflict):self.action('meal',preset_id=str(p.pk),preset_version=p.version+1)
        other=Product.objects.create(room=self.other_room,name='외부 제품')
        f=PresetForm({'title':'오류','active':'on','product':str(other.pk),'quantity':'1','unit':'g'},member=self.owner,module='meal')
        self.assertFalse(f.is_valid())

    def test_presets_cannot_cache_measured_water(self):
        f=PresetForm({'title':'매일 음수','kind':'measured','amount':'100','unit':'ml','active':'on'},member=self.owner,module='water')
        self.assertFalse(f.is_valid());self.assertIn('kind',f.errors)

    def test_medication_new_flow_needs_name_not_dose(self):
        r,_=self.action('medication',fields={'routine_title':'영양제','note':'라벨 확인'})
        a=Activity.objects.get(pk=r['id']);self.assertEqual(a.payload['_entry_kind'],'routine')
        self.assertEqual(a.payload['routine_title'],'영양제');self.assertIsNone(a.payload['dose'])
        self.assertEqual(self.stats('medication')['primary'],'1')

    def test_notes_do_not_require_condition_or_category(self):
        r,_=self.action('observation',fields={'detail':'오늘 옮긴 침대'})
        a=Activity.objects.get(pk=r['id']);self.assertEqual(a.payload['condition'],'unknown')
        self.assertEqual(a.payload['detail'],'오늘 옮긴 침대')

    def test_shedding_simple_observation_not_episode(self):
        r,_=self.action('shedding');a=Activity.objects.get(pk=r['id'])
        self.assertEqual(a.status,'done');self.assertEqual(a.payload['_entry_kind'],'observation');self.assertIsNone(a.ended_at)

    def test_play_start_with_saved_kind_does_not_force_reaction_field(self):
        self.preset(module='play',title='장난감 놀이',kind='장난감 놀이')
        result,_=self.action('play','start')
        activity=Activity.objects.get(pk=result['id'])
        self.assertEqual(activity.status,'active')
        self.assertEqual(activity.payload['reaction'],'unknown')

    def test_timer_start_retry_and_second_token_reuses_active_record(self):
        first,data=self.action('play','start');again,_=self.action('play','start')
        self.assertEqual(first['id'],again['id']);self.assertEqual(Activity.objects.filter(status='active').count(),1)
        self.assertEqual(record_actions.perform(self.owner,data)['id'],first['id'])

    def test_timer_finish_computes_elapsed_from_server_start(self):
        first,_=self.action('training','start');a=Activity.objects.get(pk=first['id'])
        Activity.objects.filter(pk=a.pk).update(occurred_at=timezone.now()-timedelta(minutes=5))
        result,data=self.action('training','finish',activity_id=str(a.pk),version=a.version)
        a.refresh_from_db();self.assertEqual(a.status,'done');self.assertTrue(Decimal('4.9')<=Decimal(a.payload['minutes'])<=Decimal('5.1'))
        self.assertEqual(record_actions.perform(self.owner,data)['id'],result['id']);self.assertEqual(ActivityRevision.objects.filter(activity=a).count(),2)

    def test_wrong_finish_version_and_foreign_activity_fail(self):
        r,_=self.action('walk','start')
        with self.assertRaises(services.Conflict):self.action('walk','finish',activity_id=r['id'],version=999)
        self.assertEqual(Activity.objects.get(pk=r['id']).status,'active')

    def test_untrusted_private_metadata_rejected(self):
        with self.assertRaises(ValidationError):self.action('toilet',fields={'_stock':{'item':'x'}})
        self.assertEqual(Activity.objects.count(),0)

    def test_gps_gaps_cannot_be_marked_complete(self):
        m=record_actions.gps_metadata({'samples':10,'gaps':1,'complete':True})
        self.assertFalse(m['complete']);self.assertNotIn('coordinates',m)


class FoodStockLedgerTests(RecordActionCase):
    def bind(self,stock=1000):
        self.make_at('inventory',5,item='사료',movement='set',quantity=str(stock),unit='g',minimum='100')
        self.product.stock_item='사료';self.product.stock_unit='g';self.product.save(update_fields=['stock_item','stock_unit'])

    def balance(self):
        return inventory_ledger.ledger(inventory_ledger.records_for(self.room))['items'][0]['balance']

    def test_food_consumes_supplied_not_eaten_quantity(self):
        self.bind();a=self.make('meal',product=str(self.product.pk),quantity='100',consumed='50')
        self.assertEqual(self.balance(),900);self.assertEqual(a.payload['_stock']['quantity'],'100')
        self.assertEqual(Activity.objects.filter(module='inventory').count(),1)

    def test_retry_does_not_create_second_consumption(self):
        self.bind();self.preset(quantity='60');r,data=self.action('meal');record_actions.perform(self.owner,data)
        self.assertEqual(self.balance(),940);self.assertEqual(Activity.objects.filter(module='meal').count(),1)

    def test_edit_and_cancel_recompute_one_derived_consumption(self):
        self.bind();a=self.make('meal',product=str(self.product.pk),quantity='100')
        form=self.form('meal',instance=a,product=str(self.product.pk),quantity='200');self.assertTrue(form.is_valid(),form.errors)
        services.save_activity(self.owner,'meal',form,a.pk);self.assertEqual(self.balance(),800)
        a.refresh_from_db();services.cancel_activity(self.owner,a.pk,a.version,'취소');self.assertEqual(self.balance(),1000)

    def test_negative_stock_rolls_back_activity_revision_and_receipt(self):
        self.bind(stock=10);self.preset(quantity='60');before=(Activity.objects.count(),ActivityRevision.objects.count(),AuditEvent.objects.count())
        with self.assertRaises(ValidationError):self.action('meal')
        self.assertEqual((Activity.objects.count(),ActivityRevision.objects.count(),AuditEvent.objects.count()),before)
        self.assertEqual(ActionReceipt.objects.count(),0);self.assertEqual(self.balance(),10)

    def test_incompatible_volume_mass_is_rejected(self):
        self.bind()
        with self.assertRaises(ValidationError):self.make('meal',product=str(self.product.pk),quantity='5',unit='ml')
        self.assertEqual(self.balance(),1000)

    def test_old_unbound_food_stays_unbound_after_product_link_change(self):
        a=self.make_at('meal',2,product=str(self.product.pk),quantity='100');self.bind()
        form=self.form('meal',instance=a,product=str(self.product.pk),quantity='50');self.assertTrue(form.is_valid(),form.errors);services.save_activity(self.owner,'meal',form,a.pk)
        a.refresh_from_db();self.assertNotIn('_stock',a.payload);self.assertEqual(self.balance(),1000)

    def test_missing_quantity_is_not_silently_zero_stock_usage(self):
        self.bind();f=RecordForm({'client_key':str(uuid.uuid4()),'occurred_at':timezone.localtime().isoformat(),'status':'done','product':str(self.product.pk),'unit':'g'},room=self.room,module='meal',member=self.owner,simple=True)
        self.assertTrue(f.is_valid(),f.errors);a=services.save_activity(self.owner,'meal',f)[0]
        report=inventory_ledger.ledger(inventory_ledger.records_for(self.room));self.assertFalse(report['items'][0]['complete'])
        self.assertEqual(report['items'][0]['unknown_ids'],[str(a.pk)])

    def test_stocktake_resets_balance_and_prior_unknown_coverage(self):
        self.bind();self.make('meal',product=str(self.product.pk),quantity='100')
        now=timezone.localtime();self.make('inventory',item='사료',movement='set',quantity='2',unit='kg',minimum='0.1',occurred_at=now.isoformat())
        self.assertEqual(self.balance(),2000)

    def test_kg_display_and_consumption_basis_are_explicit(self):
        self.bind(stock=2000);self.make_at('meal',1,product=str(self.product.pk),quantity='100')
        r=self.report()['inventory']['items'][0]
        self.assertEqual((r['shown'],r['display_unit']),('1.9','kg'));self.assertEqual(r['linked_used'],'0.1')


class ScheduleAndScopeTests(RecordActionCase):
    def routine7(self,modules=('medication',),**changes):
        today=services.local_date(self.room)
        data={'name':'영양제','at':'00:01','weekdays':[str(i) for i in range(7)],'modules':list(modules),'active':'on','start_date':today.isoformat(),'end_date':(today+timedelta(days=2)).isoformat(),**changes}
        form=RoutineForm(data,room=self.room,visible_keys=list(MODULES));self.assertTrue(form.is_valid(),form.errors)
        return services.save_routine(self.owner,form)

    def test_routine_free_named_completion_has_no_required_dose(self):
        r=self.routine7();item=r.items.get(active=True);day=services.local_date(self.room)
        result,data=self.action('medication','routine_done',routine_item=str(item.pk),date=day.isoformat())
        a=Activity.objects.get(pk=result['id']);self.assertEqual(a.payload['routine_title'],'영양제');self.assertIsNone(a.payload['dose'])
        self.assertEqual(schedule.completion_summary(self.owner,day,day)['done'],1)
        second,_=self.action('medication','routine_done',routine_item=str(item.pk),date=day.isoformat())
        self.assertEqual(second['id'],result['id']);self.assertEqual(Activity.objects.filter(module='medication').count(),1)

    def test_skip_is_distinct_and_idempotent(self):
        r=self.routine7();item=r.items.get(active=True);day=services.local_date(self.room)
        result,data=self.action('medication','routine_skip',routine_item=str(item.pk),date=day.isoformat())
        before=AuditEvent.objects.count();record_actions.perform(self.owner,data)
        self.assertEqual(AuditEvent.objects.count(),before);self.assertEqual(Activity.objects.count(),0)
        summary=schedule.completion_summary(self.owner,day,day);self.assertEqual((summary['done'],summary['skipped'],summary['unrecorded']),(0,1,0))

    def test_completion_after_explicit_skip_clears_skip(self):
        r=self.routine7();item=r.items.get(active=True);day=services.local_date(self.room)
        self.action('medication','routine_skip',routine_item=str(item.pk),date=day.isoformat())
        self.action('medication','routine_done',routine_item=str(item.pk),date=day.isoformat())
        run=RoutineRun.objects.get(item=item,date=day);self.assertIsNone(run.skipped_at);self.assertIsNotNone(run.activity)

    def test_period_ends_without_inventing_future_opportunities(self):
        self.routine7();day=services.local_date(self.room)
        rows=schedule.opportunities(self.owner,day,day+timedelta(days=6),now=timezone.now()+timedelta(days=7))
        self.assertEqual(len(rows),3)

    def test_sleep_schedule_confirmation_does_not_create_actual_minutes(self):
        r=self.routine7(modules=('sleep',),name='휴식');item=r.items.get(active=True);day=services.local_date(self.room)
        result,_=self.action('sleep','routine_done',routine_item=str(item.pk),date=day.isoformat())
        a=Activity.objects.get(pk=result['id']);self.assertIsNone(a.payload['minutes']);self.assertEqual(a.payload['_entry_kind'],'schedule')
        self.assertEqual(self.stats('sleep')['primary_unit'],'건')

    def test_notice_dismissal_is_personal_not_schedule_deletion(self):
        today=services.local_date(self.room)
        c=CareCadence.objects.create(room=self.room,module='cleaning',variant_field='target',variant_value='cage',title='케이지',interval=7,unit='days',first_due=today+timedelta(days=1))
        row=schedule.notices(self.owner)[0];before=c.version
        self.assertTrue(schedule.dismiss(self.owner,row['key'],row['stage']));self.assertFalse(schedule.dismiss(self.owner,row['key'],row['stage']))
        self.assertFalse(schedule.notices(self.owner));self.assertTrue(schedule.notices(self.member));c.refresh_from_db();self.assertEqual(c.version,before)

    def test_old_grooming_notes_and_new_selection_count_preserved(self):
        self.assertEqual(len(MODULES),20);self.assertEqual(len(SELECTABLE_MODULES),18)
        a=self.make_at('grooming',1,task='brush');resp=self.client.get(reverse('module',args=[self.room.pk,'hygiene']))
        self.assertContains(resp,'빗질');a.refresh_from_db();self.assertEqual(a.module,'grooming')
        self.assertNotIn('reproduction',SELECTABLE_MODULES)

    def test_member_profile_owner_only_role_management(self):
        url=reverse('member_panel',args=[self.room.pk,self.member.pk])
        response=self.client_for(self.admin).post(url,{'action':'promote'});self.assertEqual(response.status_code,403)
        self.assertEqual(self.client.post(url,{'action':'promote'}).status_code,200);self.member.refresh_from_db();self.assertEqual(self.member.role,'admin')

    def test_member_profile_is_room_scoped_and_hides_email(self):
        response=self.client.get(reverse('member_panel',args=[self.room.pk,self.member.pk]))
        self.assertEqual(response.status_code,200);self.assertNotContains(response,'@')
        foreign=self.other_room.members.get(role='owner')
        self.assertEqual(self.client.get(reverse('member_panel',args=[self.room.pk,foreign.pk])).status_code,404)

    def test_settings_no_longer_duplicates_module_configuration(self):
        response=self.client.get(reverse('room_settings',args=[self.room.pk]))
        self.assertNotContains(response,'사용할 기능·표시 순서');self.assertContains(response,'반려동물 상태')
        self.assertNotContains(response,'사료·간식 정보')
        self.assertEqual(self.client.get(reverse('supplies',args=[self.room.pk])).status_code,200)
        self.assertEqual(self.client.get(reverse('module_settings',args=[self.room.pk])).status_code,200)

    def test_action_api_invalid_json_and_forbidden_scope(self):
        url=reverse('record_action',args=[self.room.pk])
        self.assertEqual(self.client.post(url,'{bad',content_type='application/json').status_code,400)
        data={'token':str(uuid.uuid4()),'operation':'quick','module':'medication','fields':{'routine_title':'비공개'}}
        self.assertEqual(self.client_for(self.guest).post(url,json.dumps(data),content_type='application/json').status_code,403)

    def test_busy_action_returns_retryable_failure_not_false_success(self):
        url=reverse('record_action',args=[self.room.pk]);data={'token':str(uuid.uuid4()),'operation':'quick','module':'toilet'}
        with patch('care.record_actions.perform',side_effect=OperationalError('busy')):
            response=self.client.post(url,json.dumps(data),content_type='application/json')
        self.assertEqual(response.status_code,503);self.assertFalse(response.json()['ok']);self.assertEqual(Activity.objects.count(),0)
