from datetime import timedelta
from decimal import Decimal as D
import uuid
from django.urls import reverse
from care.tests.test_functional import CareCase
from care.models import Product,Activity
from care.forms import RecordForm
from care.supplies import SupplyForm,stock_view
from care import services,routine_completion
from care.modules import record_summary, payload_display
class PackageContextTests(CareCase):
    def pack_product(self,**kw):
        values=dict(room=self.room,name='포장 사료',category='meal',stock_item='포장 사료',stock_unit='g',package_label='봉지',package_amount=D('2'),package_unit='kg')
        values.update(kw);return Product.objects.create(**values)

    def stock_form(self,p,**kw):
        data=dict(item=p.stock_item,movement='in',quantity='2',unit='package',product=str(p.pk),package_version=p.version,minimum='')
        data.update(kw);return self.form('inventory',**data)

    def stock(self,p,**kw):
        form=self.stock_form(p,**kw);self.assertTrue(form.is_valid(),form.errors)
        return services.save_activity(self.owner,'inventory',form)[0]

    def test_package_receipt_and_exact_total(self):
        p=self.pack_product();r=self.stock(p)
        self.assertEqual(D(r.payload['quantity']),D(4000));self.assertEqual(r.payload['unit'],'g')
        self.assertEqual(r.payload['_package']['count'],'2')
        self.assertIn('2 봉지',record_summary('inventory',r.payload))
        self.assertIn(('포장 정보','1봉지 · 2 kg'),payload_display('inventory',r.payload))
        stock=stock_view(self.owner)['items'][0]
        self.assertEqual(stock['context']['value'],'4')
        self.assertEqual(stock['context']['unit'],'kg')
        self.assertIn('2 봉지 분량',stock['context']['equivalent'])

    def test_food_use_changes_contents_not_fabricated_unopened_count(self):
        p = self.pack_product()
        receipt = self.stock(p, quantity='1')
        self.make('meal', product=str(p.pk), quantity='55', unit='g',
                  occurred_at=(receipt.occurred_at + timedelta(minutes=1)).isoformat())
        item=stock_view(self.owner)['items'][0]
        self.assertEqual(item['balance'],D(1945))
        response=self.client.get(reverse('supplies',args=[self.room.pk]), {'tab': 'stock'})
        self.assertContains(response,'미개봉 포장 개수와는 달라요.')
        self.assertContains(response,'남은 내용물')

    def test_countable_container_shows_count_and_nominal_capacity(self):
        p=self.pack_product(name='치약',category='hygiene',stock_item='치약',stock_unit='piece',package_label='튜브',package_amount=D(75),package_unit='g')
        self.stock(p)
        ctx=stock_view(self.owner)['items'][0]['context']
        self.assertEqual((ctx['value'],ctx['unit']),('2','튜브'))
        self.assertEqual(ctx['nominal'],'포장 기준 총 150 g')
        self.assertEqual(ctx['equivalent'],'')

    def test_pack_of_pieces_counts_contents(self):
        p=self.pack_product(name='배변패드',category='supply',stock_item='패드',stock_unit='piece',package_label='팩',package_amount=D(50),package_unit='piece')
        r=self.stock(p)
        self.assertEqual(D(r.payload['quantity']),D(100))
        ctx=stock_view(self.owner)['items'][0]['context']
        self.assertEqual((ctx['value'],ctx['unit']),('100','개'))
        self.assertIn('2 팩 분량',ctx['equivalent'])

    def test_loose_remainder_uses_measured_units(self):
        p=self.pack_product();self.stock(p,movement='set',quantity='725',unit='g')
        item=stock_view(self.owner)['items'][0]
        self.assertEqual(item['context']['value'],'725')
        self.assertIn('0.4 봉지 분량',item['context']['equivalent'])
        self.assertNotIn('_package',Activity.objects.get().payload)

    def test_missing_capacity_and_fractional_packages_rejected(self):
        p=self.pack_product(package_amount=None,package_unit='')
        form=self.stock_form(p);self.assertFalse(form.is_valid());self.assertIn('unit',form.errors)
        p.package_amount=2;p.package_unit='kg';p.save()
        form=self.stock_form(p,quantity='1.5');self.assertFalse(form.is_valid());self.assertIn('quantity',form.errors)

    def test_threshold_uses_same_package_basis(self):
        p=self.pack_product();r=self.stock(p,minimum='1')
        self.assertEqual(D(r.payload['minimum']),D(2000))
        self.assertEqual(r.payload['_package']['minimum_count'],'1')

    def test_retry_records_once_and_cancel_removes_total(self):
        p=self.pack_product();form=self.stock_form(p);self.assertTrue(form.is_valid(),form.errors)
        first,_=services.save_activity(self.owner,'inventory',form)
        second,created=services.save_activity(self.owner,'inventory',form)
        self.assertEqual(first.pk,second.pk);self.assertFalse(created)
        self.assertEqual(stock_view(self.owner)['items'][0]['balance'],D(4000))
        services.cancel_activity(self.owner,first.pk,first.version,'확인 취소')
        self.assertEqual(stock_view(self.owner)['items'],[])

    def test_old_package_size_survives_product_change_and_record_edit(self):
        p=self.pack_product();r=self.stock(p)
        p.package_amount=3;p.version+=1;p.save()
        form=RecordForm(room=self.room,module='inventory',member=self.owner,instance=r)
        self.assertEqual(form.initial['unit'],'package');self.assertEqual(form.initial['quantity'],'2')
        data={name:form[name].value() for name in form.fields};data['quantity']='3'
        edited=RecordForm(data,room=self.room,module='inventory',member=self.owner,instance=r)
        self.assertTrue(edited.is_valid(),edited.errors)
        saved,_=services.save_activity(self.owner,'inventory',edited,r.pk)
        self.assertEqual(D(saved.payload['quantity']),D(6000))
        self.assertEqual(saved.payload['_package']['amount'],'2.000')

    def test_stale_form_rejected_without_changing_stock(self):
        p=self.pack_product();old=p.version;p.version+=1;p.save()
        f=self.stock_form(p,package_version=old)
        self.assertFalse(f.is_valid());self.assertIn('unit',f.errors)
        self.assertFalse(Activity.objects.exists())

    def test_product_changes_after_validation_are_detected(self):
        p=self.pack_product();f=self.stock_form(p);self.assertTrue(f.is_valid(),f.errors)
        Product.objects.filter(pk=p.pk).update(version=2)
        with self.assertRaises(services.Conflict):services.save_activity(self.owner,'inventory',f)
        self.assertFalse(Activity.objects.exists())

    def test_lost_response_retry_keeps_original_package_after_product_change(self):
        p=self.pack_product();f=self.stock_form(p);self.assertTrue(f.is_valid(),f.errors)
        first,_=services.save_activity(self.owner,'inventory',f)
        data=f.data.copy();p.package_amount=3;p.version+=1;p.save()
        retry=RecordForm(data,room=self.room,module='inventory',member=self.owner)
        self.assertTrue(retry.is_valid(),retry.errors)
        record,created=services.save_activity(self.owner,'inventory',retry)
        self.assertEqual(first.pk,record.pk);self.assertFalse(created)
        self.assertEqual(stock_view(self.owner)['items'][0]['balance'],D(4000))

    def test_product_form_saves_package_metadata_for_nonfood(self):
        data=dict(name='치약',category='hygiene',package_label='튜브',package_amount='75',package_unit='g',track_stock='on',stock_unit='piece',active='on',request_token=str(uuid.uuid4()))
        form=SupplyForm(data,member=self.owner)
        self.assertTrue(form.is_valid(),form.errors)
        p=services.save_product(self.owner,form)
        self.assertEqual(p.package_amount,D(75));self.assertIsNone(p.grams_per_piece)

    def test_package_unit_dimension_and_nutrition_piece_are_distinct(self):
        base=dict(name='사료',category='meal',package_label='봉지',package_amount='2',package_unit='kg',track_stock='on',stock_unit='ml',active='on',request_token=str(uuid.uuid4()))
        form=SupplyForm(base,member=self.owner);self.assertFalse(form.is_valid());self.assertIn('package_unit',form.errors)
        base['stock_unit']='piece'
        form=SupplyForm(base,member=self.owner);self.assertFalse(form.is_valid());self.assertIn('stock_unit',form.errors)

    def test_private_package_metadata_not_visible_to_guest(self):
        p=self.pack_product(name='비공개 약',category='medicine',stock_item='비공개 약',stock_unit='piece',package_label='병',package_amount=30,package_unit='piece')
        self.stock(p)
        response=self.client_for(self.guest).get(reverse('supplies',args=[self.room.pk]))
        self.assertNotContains(response,'비공개 약')
        self.assertEqual(stock_view(self.guest)['items'],[])

class RoutineUnavailableTests(CareCase):
    def test_future_date_returns_useful_conflict_not_server_error(self):
        r=self.routine(('weight',));item=r.items.get(active=True);day=services.local_date(self.room)+timedelta(days=1)
        url=reverse('routine_complete',args=[self.room.pk,item.pk])+'?date='+day.isoformat()
        response=self.client.get(url,HTTP_X_TAILY_DIALOG='1')
        self.assertEqual(response.status_code,409);self.assertContains(response,'아직 오지 않은 날',status_code=409)
        self.assertEqual(response.headers['X-Taily-Fragment'],'input-form')
        self.assertNotContains(response,'type="submit"',status_code=409)

    def test_disabled_module_preserves_submitted_input(self):
        r=self.routine(('weight',));item=r.items.get(active=True);day=services.local_date(self.room)
        snap=services.effective_routine(r,day);probe=routine_completion.completion_form(self.owner,item,snap,None,day,uuid.uuid4())
        data={name:probe[name].value() for name in probe.fields};data.update(weight='6.3',unit='kg',note='작성한 내용을 유지')
        self.room.module_settings.filter(key='weight').update(enabled=False)
        url=reverse('routine_complete',args=[self.room.pk,item.pk])+'?date='+day.isoformat()
        response=self.client.post(url,{k:'' if v is None else v for k,v in data.items()},HTTP_X_TAILY_DIALOG='1')
        self.assertEqual(response.status_code,409);self.assertContains(response,'작성한 내용을 유지',status_code=409)
        self.assertContains(response,'잠시 멈췄어요',status_code=409);self.assertFalse(Activity.objects.exists())
