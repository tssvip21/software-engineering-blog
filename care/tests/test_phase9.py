"""Display contracts and routine recovery are tested independently of visual preference."""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal as D
from unittest.mock import patch
from urllib.parse import parse_qs,urlparse
import json,uuid

from django.core.exceptions import PermissionDenied,ValidationError
from django.db import OperationalError
from django.test import SimpleTestCase
from django.urls import reverse
from django.utils import timezone

from care import actions7,analytics7,services,routine_completion
from care.display_values import measure,number,duration,common_unit,decorate_report,rounded_originals
from care.forms import RecordForm,RoutineForm
from care.models import Activity,ActivityRevision,ActionReceipt,AuditEvent,Product,RecordPreset,RoutineRun
from care.modules import compact,payload_display,record_summary
from care.presets import PresetForm,save_preset
from care.stats import chart
from .test_functional import CareCase


class DisplayContractTests(SimpleTestCase):
    def test_tail_zeroes_grouping_and_half_up(self):
        self.assertEqual(number('12345.5000'),'12,345.5')
        self.assertEqual(number('1.25'),'1.3')
        self.assertEqual(number('-1.25'),'−1.3')
        self.assertEqual(number('0.00'),'0')

    def test_missing_nonfinite_and_zero_are_distinct(self):
        self.assertEqual(measure(None,'ml').text,'—')
        self.assertTrue(measure(None,'ml').missing)
        self.assertEqual(measure(0,'ml').text,'0 ml')
        self.assertNotEqual(measure('NaN','ml').text,'0 ml')
        self.assertEqual(measure('미기록','분').text,'미기록')

    def test_toothpaste_is_55g_not_fraction_of_a_package(self):
        self.assertEqual(measure('.055','kg').text,'55 g')
        self.assertEqual(measure('55','g').text,'55 g')
        self.assertNotIn('개',measure('.055','kg').text)

    def test_counts_are_counts_and_unknown_package_mass_is_not_invented(self):
        self.assertEqual(measure(38,'piece').text,'38 개')
        self.assertEqual(measure('1.5','회').text,'1.5 회')
        self.assertEqual(measure('0.005','piece').text,'0.005 개')

    def test_mass_and_volume_use_appropriate_magnitude(self):
        cases=[('1000','g','1 kg'),('55','g','55 g'),('.0005','g','0.5 mg'),('0.055','l','55 ml'),('2500','ml','2.5 L')]
        for value,unit,expected in cases:
            with self.subTest(value=value,unit=unit):self.assertEqual(measure(value,unit).text,expected)

    def test_tiny_nonzero_never_appears_as_zero(self):
        for raw in ('0.000001','0.00000000001','-0.000001'):
            with self.subTest(raw=raw):self.assertNotIn(measure(raw,'kcal').value,('0','−0','+0'))
        self.assertIn('<',measure('0.00000000001','kcal').value)

    def test_duration_natural_minute_second_hour_and_carry(self):
        cases=[('1.5','1분 30초'),('1.9999','2분'),('0.001','1초 미만'),('90.25','1시간 30분 15초'),('0','0분')]
        for raw,expected in cases:
            with self.subTest(raw=raw):self.assertEqual(duration(raw).text,expected)
        self.assertEqual(duration('-1.5',signed=True).text,'−1분 30초')

    def test_distance_uses_one_shared_comparison_unit(self):
        target=common_unit(['.9','1.2'],'km');self.assertEqual(target,'km')
        self.assertEqual(measure('.9','km',fixed_unit=target).text,'0.9 km')
        self.assertEqual(measure('1.2','km',fixed_unit=target).text,'1.2 km')
        self.assertEqual(measure('.042','km').text,'42 m')

    def test_money_currency_precision_and_no_exchange(self):
        self.assertEqual(measure('123456','KRW').text,'123,456 원')
        self.assertEqual(measure('123.456','USD').text,'123.46 USD')
        self.assertEqual(measure('2000','JPY').text,'2,000 엔')

    def test_percentage_points_absolute_temperature_and_ph(self):
        self.assertEqual(measure('1.2345','%p',signed=True).text,'+1.2 %p')
        self.assertEqual(measure('-2.555','C').text,'−2.6 °C')
        self.assertEqual(measure('7.12345','ph').text,'7.12 pH')

    def test_weight_display_is_unchanged(self):
        for value,unit in [('6.3123','kg'),('72.000','g'),('0.000001','kg'),('15.123456','lb')]:
            with self.subTest(value=value):
                self.assertEqual(measure(value,unit,kind='weight').value,compact(value))
                self.assertEqual(measure(value,unit,kind='weight').unit,unit)
        self.assertEqual(payload_display('weight',{'weight':'6.3123','unit':'kg'}),[('체중','6.3123'),('단위','kg')])
        self.assertIn('6.312 kg',record_summary('weight',{'weight':'6.3123','unit':'kg'}))

    def test_dose_is_not_changed_into_generic_rounded_count(self):
        self.assertEqual(measure('.125','tablet',kind='dose').value,'0.125')
        self.assertEqual(measure('.001','mg',kind='dose').text,'0.001 mg')

    def test_raw_detail_disclosure_and_summary_are_consistent(self):
        data={'minutes':'1.2345','distance':'1.45678','distance_unit':'km'}
        original=deepcopy(data)
        shown=payload_display('walk',data)
        self.assertIn(('산책 시간','1분 14초'),shown)
        self.assertIn(('이동 거리','1.5 km'),shown)
        self.assertIn('1분 14초',record_summary('walk',data))
        self.assertTrue(rounded_originals('walk',data));self.assertEqual(data,original)

    def test_summed_graph_geometry_is_unchanged_by_labels(self):
        from datetime import date
        from care.display_values import decorate_stats
        day=date(2026,11,1);raw=chart('시간','분',{day:D('1.234567')},day,day,'line')
        before=deepcopy(raw);stat={'key':'walk','charts':[raw]};decorate_stats(stat)
        self.assertEqual(raw['segments'],before['segments'])
        self.assertEqual(raw['points'][0]['y'],before['points'][0]['y'])
        self.assertEqual(raw['points'][0]['_raw_value'],D('1.234567'))
        self.assertEqual(raw['points'][0]['display'].text,'1분 14초')


class DisplayIntegrationTests(CareCase):
    def test_original_payload_nutrition_and_inventory_not_rounded(self):
        a=self.make('walk',minutes='1.234',distance='1.457',distance_unit='km')
        snapshot=deepcopy(a.payload);revisions=ActivityRevision.objects.count();events=AuditEvent.objects.count()
        for name,args in [('today',[self.room.pk]),('modules',[self.room.pk]),('record_detail',[self.room.pk,a.pk]),('overview',[self.room.pk]),('statistics',[self.room.pk,'walk'])]:
            r=self.client.get(reverse(name,args=args));self.assertEqual(r.status_code,200,(name,r.status_code))
        a.refresh_from_db();self.assertEqual(a.payload,snapshot)
        self.assertEqual(ActivityRevision.objects.count(),revisions);self.assertEqual(AuditEvent.objects.count(),events)
        self.assertContains(self.client.get(reverse('record_detail',args=[self.room.pk,a.pk])),'1.234 분')

    def test_stock_natural_unit_is_shared_between_registered_and_stock_tabs(self):
        self.make('inventory',item='치약',movement='set',quantity='0.055',unit='kg')
        Product.objects.create(room=self.room,name='치약',category='hygiene',stock_item='치약',stock_unit='g')
        for query in ('','?tab=stock'):
            response=self.client.get(reverse('supplies8',args=[self.room.pk])+query)
            self.assertContains(response,'55');self.assertNotContains(response,'0.055')
        from care.inventory_ledger import ledger,records_for
        self.assertEqual(ledger(records_for(self.room))['items'][0]['balance'],D(55))

    def test_raw_comparison_mean_and_selected_sources_stay_exact(self):
        for ago,minutes in [(9,'1.111'),(8,'2.222'),(2,'3.333'),(1,'4.444')]:
            self.make('play',minutes=minutes,occurred_at=(timezone.now()-timedelta(days=ago)).isoformat())
        report=analytics7.build_report(self.owner)
        m=next(m for m in report['measurements'] if m['key']=='play_minutes')
        self.assertEqual(m['before']['value'],D('1.6665'))
        self.assertEqual(m['recent']['value'],D('3.8885'))
        self.assertEqual(m['change']['delta'],D('2.2220'))
        self.assertEqual(len(m['recent']['ids']),2)
        self.assertIn('분',m['recent']['display'].text)

    def test_unknown_time_does_not_become_zero_during_display(self):
        data={'client_key':str(uuid.uuid4()),'status':'done','occurred_at':timezone.localtime().isoformat(),'reaction':'unknown'}
        form=RecordForm(data,room=self.room,module='play',member=self.owner,simple=True)
        self.assertTrue(form.is_valid(),form.errors)
        a=services.save_activity(self.owner,'play',form)[0]
        response=self.client.get(reverse('record_detail',args=[self.room.pk,a.pk]));self.assertNotContains(response,'0분')
        a.refresh_from_db();self.assertIsNone(a.payload['minutes'])

    def test_body_weight_ui_values_keep_original_precision(self):
        a=self.make('weight',weight='6.312',unit='kg')
        response=self.client.get(reverse('today',args=[self.room.pk]))
        self.assertContains(response,'6.312')
        a.refresh_from_db();self.assertEqual(a.payload['weight'],'6.312')

    def test_money_record_list_and_detail_have_same_currency(self):
        a=self.make('expense',item='소모품',amount='123456',currency='KRW')
        for url in [reverse('records',args=[self.room.pk])+'?module=expense',reverse('record_detail',args=[self.room.pk,a.pk])]:
            response=self.client.get(url);self.assertContains(response,'123,456 원')

    def test_readable_stylesheet_and_original_input_precision(self):
        a=self.make('walk',minutes='1.234',distance='1.457')
        response=self.client.get(reverse('record_edit',args=[self.room.pk,a.pk]),HTTP_X_TAILY_DIALOG='1')
        self.assertContains(response,'1.234');self.assertContains(response,'1.457')
        self.assertContains(self.client.get(reverse('today',args=[self.room.pk])),'readability.css')


class RoutineRecoveryTests(CareCase):
    def item(self,module,preset=None):
        self.room.refresh_from_db()
        routine=self.routine(modules=(module,));item=routine.items.get(active=True)
        if preset:
            item.preset=preset;item.save(update_fields=['preset'])
            version=routine.versions.first();version.snapshot=services.routine_snapshot(routine);version.save(update_fields=['snapshot'])
        return item

    def action(self,item,**extra):
        return {'token':str(uuid.uuid4()),'operation':'routine_done','module':item.module,'routine_item':str(item.pk),'date':services.local_date(self.room).isoformat(),**extra}

    def get_form(self,item,token=None,member=None):
        day=services.local_date(self.room);member=member or self.owner
        entry,snap,preset=routine_completion.request_context(member,item.pk,day)
        return routine_completion.completion_form(member,entry,snap,preset,day,token or uuid.uuid4())

    def data(self,item,**changes):
        f=self.get_form(item);data={}
        for key,field in f.fields.items():
            v=f[key].value()
            data[key]=v.isoformat() if hasattr(v,'isoformat') else str(v) if v is not None else ''
        data.update(changes);return data

    def post(self,item,data,client=None):
        url=reverse('routine_complete9',args=[self.room.pk,item.pk])+'?date='+services.local_date(self.room).isoformat()
        return (client or self.client).post(url,data,HTTP_X_TAILY_DIALOG='1')

    def test_no_input_water_completes_with_no_guessed_quantity(self):
        item=self.item('water');result=actions7.perform(self.owner,self.action(item))
        a=Activity.objects.get(pk=result['id']);self.assertEqual(a.payload['kind'],'replace');self.assertIsNone(a.payload['amount'])
        self.assertTrue(result.get('routine_key'));self.assertEqual(RoutineRun.objects.get(item=item).activity_id,a.pk)

    def test_hygiene_missing_task_returns_named_form_without_database_write(self):
        item=self.item('hygiene');counts=(Activity.objects.count(),RoutineRun.objects.count(),ActionReceipt.objects.count())
        r=self.client.post(reverse('action7',args=[self.room.pk]),json.dumps(self.action(item)),content_type='application/json')
        self.assertEqual(r.status_code,422);self.assertTrue(r.json()['requires_input']);self.assertEqual(r.json()['fields'][0]['label'],'관리 항목')
        self.assertIn('/complete/',r.json()['form_url'])
        self.assertEqual((Activity.objects.count(),RoutineRun.objects.count(),ActionReceipt.objects.count()),counts)

    def test_each_required_module_has_named_missing_fields(self):
        for module in ('weight','hygiene','cleaning','veterinary','environment','inventory','expense','observation'):
            with self.subTest(module=module):
                item=self.item(module)
                with self.assertRaises(routine_completion.NeedsInput) as caught:actions7.perform(self.owner,self.action(item))
                self.assertTrue(all(x['label'] for x in caught.exception.fields))
                self.assertFalse(RoutineRun.objects.filter(item=item,activity__isnull=False).exists())

    def test_open_and_cancel_form_do_not_complete(self):
        item=self.item('hygiene');before=(Activity.objects.count(),RoutineRun.objects.count(),ActionReceipt.objects.count())
        r=self.client.get(reverse('routine_complete9',args=[self.room.pk,item.pk]),{'date':services.local_date(self.room).isoformat()},HTTP_X_TAILY_DIALOG='1')
        self.assertEqual(r.status_code,200);self.assertContains(r,'관리 항목');self.assertContains(r,'저장하고 완료')
        self.assertEqual(before,(Activity.objects.count(),RoutineRun.objects.count(),ActionReceipt.objects.count()))

    def test_form_validation_failure_retains_values_and_run_uncompleted(self):
        item=self.item('weight');data=self.data(item,weight='-1',unit='kg')
        r=self.post(item,data);self.assertEqual(r.status_code,422);self.assertContains(r,'-1',status_code=422)
        self.assertFalse(RoutineRun.objects.filter(item=item).exists());self.assertEqual(ActionReceipt.objects.count(),0)

    def test_success_links_exactly_one_activity_and_revision(self):
        item=self.item('hygiene');data=self.data(item,task='teeth');r=self.post(item,data)
        self.assertEqual(r.status_code,200,r.content[:1000]);self.assertTrue(r.json()['ok'])
        activity=Activity.objects.get(pk=r.json()['id']);run=RoutineRun.objects.get(item=item)
        self.assertEqual(run.activity,activity);self.assertEqual(activity.payload['task'],'teeth')
        self.assertEqual(activity.revisions.count(),1);self.assertIn('routine_key',r.json())

    def test_retry_same_form_reuses_receipt_without_new_side_effect(self):
        item=self.item('hygiene');data=self.data(item,task='teeth');a=self.post(item,data)
        count=(Activity.objects.count(),ActivityRevision.objects.count(),AuditEvent.objects.count())
        b=self.post(item,data);self.assertEqual(b.status_code,200,b.content[:1000]);self.assertTrue(b.json()['replayed']);self.assertEqual(a.json()['id'],b.json()['id'])
        self.assertEqual((Activity.objects.count(),ActivityRevision.objects.count(),AuditEvent.objects.count()),count)
        self.assertEqual(ActionReceipt.objects.filter(operation='routine.form').count(),1)

    def test_response_loss_receipt_allows_retry_after_product_metadata_changed(self):
        item=self.item('hygiene');p=Product.objects.create(room=self.room,name='치약',category='hygiene')
        data=self.data(item,task='teeth',product=str(p.pk),used_quantity='',used_unit='g');a=self.post(item,data);self.assertEqual(a.status_code,200,a.content[:500])
        p.active=False;p.save(update_fields=['active'])
        b=self.post(item,data);self.assertEqual(b.status_code,200,b.content[:1000]);self.assertEqual(a.json()['id'],b.json()['id'])

    def test_same_uuid_changed_body_conflicts(self):
        item=self.item('hygiene');data=self.data(item,task='teeth');self.assertEqual(self.post(item,data).status_code,200)
        r=self.post(item,{**data,'task':'bath'});self.assertEqual(r.status_code,409)
        self.assertEqual(Activity.objects.filter(module='hygiene').count(),1)

    def test_another_request_for_completed_slot_does_not_double_record(self):
        item=self.item('hygiene');a=self.post(item,self.data(item,task='teeth'));self.assertEqual(a.status_code,200)
        b=self.post(item,self.data(item,task='bath'));self.assertEqual(b.status_code,200)
        self.assertEqual(a.json()['id'],b.json()['id']);self.assertEqual(Activity.objects.filter(module='hygiene').count(),1)

    def test_stale_routine_or_preset_version_requires_review(self):
        item=self.item('hygiene');data=self.data(item,task='teeth');item.routine.version+=1;item.routine.save(update_fields=['version'])
        r=self.post(item,data);self.assertEqual(r.status_code,409);self.assertEqual(Activity.objects.count(),0)

    def test_forged_day_or_item_is_not_saved(self):
        item=self.item('hygiene');data=self.data(item,task='teeth',routine_item=str(uuid.uuid4()))
        self.assertEqual(self.post(item,data).status_code,422);self.assertEqual(Activity.objects.count(),0)

    def test_guest_sensitive_and_foreign_room_are_denied(self):
        item=self.item('medication');url=reverse('routine_complete9',args=[self.room.pk,item.pk])+'?date='+services.local_date(self.room).isoformat()
        self.assertEqual(self.client_for(self.guest).get(url).status_code,403)
        other=reverse('routine_complete9',args=[self.other_room.pk,item.pk])+'?date='+services.local_date(self.room).isoformat()
        self.assertEqual(self.client_for(self.other_room.members.get(role='owner')).get(other).status_code,404)

    def test_skip_then_complete_and_cancel_restores_status(self):
        item=self.item('hygiene');skip=self.action(item,operation='routine_skip');actions7.perform(self.owner,skip)
        r=self.post(item,self.data(item,task='teeth'));self.assertEqual(r.status_code,200)
        run=RoutineRun.objects.get(item=item);self.assertIsNone(run.skipped_at)
        a=run.activity;services.cancel_activity(self.owner,a.pk,a.version,'확인 후 취소');run.refresh_from_db();self.assertIsNone(run.activity)

    def test_stock_use_retry_edit_cancel_do_not_double_deduct(self):
        self.make('inventory',item='치약',movement='set',quantity='100',unit='g',occurred_at=(timezone.now()-timedelta(days=1)).isoformat())
        p=Product.objects.create(room=self.room,name='치약',category='hygiene',stock_item='치약',stock_unit='g')
        item=self.item('hygiene');data=self.data(item,task='teeth',product=str(p.pk),used_quantity='2.123',used_unit='g')
        a=self.post(item,data);self.assertEqual(a.status_code,200,a.content[:1000]);self.post(item,data)
        from care.inventory_ledger import ledger,records_for
        self.assertEqual(ledger(records_for(self.room))['items'][0]['balance'],D('97.877'))
        record=Activity.objects.get(pk=a.json()['id']);services.cancel_activity(self.owner,record.pk,record.version,'실행 취소')
        self.assertEqual(ledger(records_for(self.room))['items'][0]['balance'],D(100))

    def test_not_enough_stock_is_not_completed(self):
        self.make('inventory',item='치약',movement='set',quantity='1',unit='g',occurred_at=(timezone.now()-timedelta(days=1)).isoformat())
        p=Product.objects.create(room=self.room,name='치약',category='hygiene',stock_item='치약',stock_unit='g')
        item=self.item('hygiene');before=Activity.objects.count();r=self.post(item,self.data(item,task='teeth',product=str(p.pk),used_quantity='2',used_unit='g'))
        self.assertEqual(r.status_code,422);self.assertEqual(Activity.objects.count(),before);self.assertFalse(RoutineRun.objects.filter(item=item).exists())

    def test_database_error_is_retryable_not_false_success(self):
        item=self.item('hygiene');data=self.data(item,task='teeth')
        with patch('care.routine_completion.save_completion',side_effect=OperationalError('busy')):r=self.post(item,data)
        self.assertEqual(r.status_code,503);self.assertEqual(Activity.objects.count(),0)

    def test_valid_configured_hygiene_is_still_one_click(self):
        f=PresetForm({'title':'양치','task':'teeth','active':'on','is_default':'on'},member=self.owner,module='hygiene')
        self.assertTrue(f.is_valid(),f.errors);preset=save_preset(self.owner,'hygiene',f)
        item=self.item('hygiene',preset);result=actions7.perform(self.owner,self.action(item))
        self.assertEqual(Activity.objects.get(pk=result['id']).payload['task'],'teeth')
        self.assertTrue(result.get('routine_key'))

    def test_new_completion_request_after_explicit_cancel_is_allowed(self):
        item=self.item('hygiene');data=self.data(item,task='teeth');a=self.post(item,data);self.assertEqual(a.status_code,200)
        record=Activity.objects.get(pk=a.json()['id']);services.cancel_activity(self.owner,record.pk,record.version,'잘못 누름')
        self.assertEqual(self.post(item,data).status_code,409)
        result=self.post(item,self.data(item,task='bath'));self.assertEqual(result.status_code,200)
        self.assertNotEqual(result.json()['id'],a.json()['id'])


class RawValuePresentationTests(CareCase):
    def test_tiny_distance_survives_today_and_board(self):
        a=self.make('walk',minutes='1',distance='.001',distance_unit='m')
        from care.flow7 import board_data
        cards,_=board_data(self.owner)
        tile=next(c['tile'] for c in cards if c['spec'].key=='walk')
        shown=tile['secondary'][0]['display']
        self.assertEqual(shown.text,'0.001 m')
        a.refresh_from_db();self.assertEqual(a.payload['distance'],'0.001')

    def test_tiny_stock_is_not_zero_in_module_statistics_or_card(self):
        self.make('inventory',item='미량 물품',movement='set',quantity='.001',unit='g',minimum='')
        from care.flow7 import board_data
        stats=self.stats('inventory')
        self.assertEqual(stats['breakdown'][0]['display'].text,'1 mg')
        cards,_=board_data(self.owner);tile=next(c['tile'] for c in cards if c['spec'].key=='inventory')
        self.assertEqual(tile['display'].text,'1 mg')

    def test_environment_compound_breakdown_keeps_precision_at_display_boundary(self):
        self.make('environment',metric='temperature',value='23.567',unit='C',location='방')
        s=self.stats('environment');item=s['breakdown'][0]
        self.assertIn('23.6 °C',item['display'].text)
        self.assertEqual(item['_parts'][0][1],D('23.567'))

    def test_relationships_use_raw_values_not_the_old_rounded_strings(self):
        from care.display_values import decorate_report
        report={'relations':[{'facts':[{'name':'활동 시간','unit':'분','before':'0','recent':'0','before_value':D('.0001'),'recent_value':D('.0002')}]}]}
        decorate_report(report)
        self.assertEqual(report['relations'][0]['facts'][0]['recent_display'].text,'1초 미만')


from django.test import TransactionTestCase

class AutocommitReadCompatibilityTests(TransactionTestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from care.forms import RoomForm
        self.user=get_user_model().objects.create_user(username='read-autocommit')
        form=RoomForm({'name':'조회 검증','species':'dog','timezone':'Asia/Seoul','use_defaults':'on'})
        assert form.is_valid(),form.errors
        self.room=services.create_room(self.user,form);self.member=self.room.members.get(role='owner')
        self.client.force_login(self.user)

    def test_read_access_check_works_without_an_outer_transaction(self):
        from django.db import connection
        self.assertFalse(connection.in_atomic_block)
        self.assertEqual(services.fresh_member(self.member).pk,self.member.pk)
        self.assertFalse(connection.in_atomic_block)

    def test_recent_statistics_get_works_in_postgresql_autocommit(self):
        from django.db import connection
        self.assertFalse(connection.in_atomic_block)
        response=self.client.get(reverse('overview',args=[self.room.pk]))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'최근 통계')


class RoutineLocalDateTimeTests(CareCase):
    def test_completion_initial_timestamp_is_valid_for_native_datetime_local(self):
        from care.routine_completion import request_context,completion_form
        routine=self.routine(modules=('weight',));item=routine.items.get(active=True);day=services.local_date(self.room)
        item,snap,preset=request_context(self.owner,item.pk,day)
        form=completion_form(self.owner,item,snap,preset,day,uuid.uuid4())
        field=form['occurred_at'];value=field.field.widget.format_value(field.value())
        self.assertRegex(value,r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$')
        self.assertNotIn('+',value)


class RoutineReceiptTargetTests(CareCase):
    def saved_request(self, module):
        routine=self.routine((module,));item=routine.items.get(active=True)
        day=services.local_date(self.room);snap=services.effective_routine(routine,day);token=uuid.uuid4()
        probe=routine_completion.completion_form(self.owner,item,snap,None,day,token)
        data={name:probe[name].value() for name in probe.fields}
        form=routine_completion.completion_form(self.owner,item,snap,None,day,token,data=data)
        self.assertTrue(form.is_valid(),form.errors)
        result=routine_completion.save_completion(self.owner,item.pk,day,form)
        return item,day,data,result

    def test_receipt_cannot_be_replayed_under_another_routine(self):
        original,day,data,result=self.saved_request('water')
        other=self.routine(('water',));item=other.items.get(active=True)
        form=routine_completion.completion_form(self.owner,item,services.effective_routine(other,day),None,day,data['client_key'],data=data)
        with self.assertRaises((PermissionDenied,ValidationError)):
            routine_completion.save_completion(self.owner,item.pk,day,form)
        self.assertEqual(Activity.objects.count(),1)
        self.assertFalse(RoutineRun.objects.filter(item=item,activity__isnull=False).exists())

    def test_private_receipt_cannot_be_replayed_through_public_item_after_role_change(self):
        original,day,data,result=self.saved_request('medication')
        other=self.routine(('water',));item=other.items.get(active=True)
        type(self.owner).objects.filter(pk=self.owner.pk).update(role='guest')
        self.owner.refresh_from_db()
        form=routine_completion.completion_form(self.owner,item,services.effective_routine(other,day),None,day,data['client_key'],data=data)
        with self.assertRaises((PermissionDenied,ValidationError)):
            routine_completion.save_completion(self.owner,item.pk,day,form)
        self.assertEqual(ActionReceipt.objects.filter(operation='routine.form').count(),1)
        self.assertEqual(Activity.objects.count(),1)
