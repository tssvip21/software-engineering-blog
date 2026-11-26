"""Room settings, shared supplies and retained event/permission semantics."""
from datetime import datetime,timedelta
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo
import uuid

from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from care import record_actions, inventory_ledger, services, supplies
from care.reporting import module_reports
from care.breeds import catalogue,suggest_size
from care.forms import RecordForm
from care.models import Activity,ActivityRevision,ActionReceipt,AuditEvent,Product,Room
from care.presets import PresetForm,save_preset
from care.profile_settings import PetProfileForm,save_pet
from .test_functional import CareCase

HEADERS={'HTTP_X_TAILY_DIALOG':'1'}

class WorkspaceCase(CareCase):
    def pet_data(self,**values):
        self.room.refresh_from_db()
        return {'name':self.room.name,'species':self.room.species,'breed_key':self.room.breed_key,
                'subspecies':self.room.subspecies,'sex':self.room.sex,'birth_date':self.room.birth_date or '',
                'maturity':self.room.maturity,'size':self.room.size,'size_origin':self.room.size_origin,
                'version':self.room.settings_version,'request_token':str(uuid.uuid4()),**values}

    def preferences_data(self,member=None,**changes):
        member=member or self.owner;member.refresh_from_db();self.room.refresh_from_db()
        data={'timezone':self.room.timezone,'version':self.room.settings_version,
              'personal_before':'1' if member.notices_enabled else '0','request_token':str(uuid.uuid4())}
        if member.notices_enabled:data['notices_enabled']='on'
        if self.room.guest_sensitive:data['guest_sensitive']='on'
        data.update(changes);return data

    def supply(self,category='hygiene',**values):
        data={'name':'검증용 '+category,'category':category,'active':'on','request_token':str(uuid.uuid4()),**values}
        f=supplies.SupplyForm(data,member=self.owner)
        self.assertTrue(f.is_valid(),f.errors)
        result=supplies.save_supply(self.owner,f)
        return Product.objects.get(pk=result['id'])

    def stock(self,product,quantity='100',unit='piece'):
        return self.make('inventory',item=product.stock_item,movement='set',quantity=quantity,unit=unit,minimum='1',
                         occurred_at=(timezone.localtime()-timedelta(days=2)).isoformat())

    def use(self,product,module='hygiene',amount='2',unit='piece',member=None,instance=None,**fields):
        actor=member or self.owner
        data={'client_key':str(instance.client_key if instance else uuid.uuid4()),'status':'done','occurred_at':(timezone.localtime()-timedelta(minutes=1)).isoformat(),
              'product':str(product.pk),'used_quantity':amount,'used_unit':unit,'performer':str(actor.pk),**fields}
        if module=='hygiene':data.setdefault('task','teeth')
        if module=='cleaning':data.update(target='cage',task='청소')
        if module=='medication':data.setdefault('result','given')
        if instance:data['version']=instance.version
        f=RecordForm(data,room=self.room,module=module,member=actor,instance=instance,simple=True)
        self.assertTrue(f.is_valid(),f.errors)
        return services.save_activity(actor,module,f,instance.pk if instance else None)[0]

    def balance(self,product):
        rows=inventory_ledger.ledger(inventory_ledger.records_for(self.room))['items']
        return next(x for x in rows if x['name']==product.stock_item)


class PetSettingsTests(WorkspaceCase):
    def test_registration_retry_returns_original_room_and_rejects_changed_form(self):
        url = reverse("room_new")
        data = {"name": "첫 공간", "species": "cat", "request_token": str(uuid.uuid4())}
        first = self.client.post(url, data)
        self.assertEqual(first.status_code, 302)
        self.assertEqual(self.client.post(url, data).url, first.url)
        self.assertEqual(self.client.post(url, {**data, "name": "다른 이름"}).status_code, 409)
        self.assertEqual(Room.objects.filter(owner=self.owner_user, name="첫 공간").count(), 1)
        second = self.client.post(url, {**data, "request_token": str(uuid.uuid4())})
        self.assertNotEqual(second.url, first.url)

    def test_empty_registration_shows_errors_and_old_form_without_token_still_works(self):
        url = reverse("room_new")
        self.assertContains(self.client.post(url, {}), "field-error", status_code=422)
        self.assertEqual(self.client.post(url, {"name": "보리", "species": "dog"}).status_code, 302)

    def test_animal_form_excludes_shared_and_item_settings(self):
        f=PetProfileForm(instance=self.room,member=self.owner)
        self.assertTrue({'name','species','breed_key','sex','birth_date','maturity','size'}<=set(f.fields))
        self.assertFalse({'timezone','guest_sensitive','product'}&set(f.fields))
        self.assertTrue(f['size_origin'].is_hidden)
        response=self.client.get(reverse('room_settings',args=[self.room.pk]),**HEADERS)
        self.assertEqual(response.status_code,200);self.assertContains(response,'data-pet-profile')
        self.assertNotContains(response,'체격 선택 방법');self.assertNotContains(response,'name="timezone"')

    def test_general_form_has_personal_and_shared_sections(self):
        r=self.client.get(reverse('preferences',args=[self.room.pk]),**HEADERS)
        self.assertContains(r,'내 안내');self.assertContains(r,'공동 설정');self.assertNotContains(r,'name="birth_date"')
        self.assertNotContains(r,'name="reduce_motion"')

    def test_same_request_save_twice_has_one_mutation(self):
        data=self.pet_data(name='수정한 이름');before=AuditEvent.objects.count()
        url=reverse('room_settings',args=[self.room.pk])
        first=self.client.post(url,data,**HEADERS);second=self.client.post(url,data,**HEADERS)
        self.assertEqual(first.status_code,200,first.content);self.assertEqual(second.status_code,200,second.content)
        self.assertTrue(second.json()['replayed']);self.assertEqual(AuditEvent.objects.count(),before+1)
        self.assertEqual(ActionReceipt.objects.filter(operation='pet.update').count(),1)

    def test_changed_body_cannot_reuse_settings_token(self):
        data=self.pet_data(name='첫 변경');url=reverse('room_settings',args=[self.room.pk])
        self.assertEqual(self.client.post(url,data,**HEADERS).status_code,200)
        response=self.client.post(url,{**data,'name':'다른 변경'},**HEADERS)
        self.assertEqual(response.status_code,409);self.room.refresh_from_db();self.assertEqual(self.room.name,'첫 변경')

    def test_stale_settings_version_rejects_without_losing_input(self):
        data=self.pet_data(name='내 미저장 이름');Room.objects.filter(pk=self.room.pk).update(settings_version=self.room.settings_version+1)
        r=self.client.post(reverse('room_settings',args=[self.room.pk]),data,**HEADERS)
        self.assertEqual(r.status_code,409);self.assertContains(r,'내 미저장 이름',status_code=409)

    def test_known_breed_proposes_size_and_manual_exception_wins(self):
        breed=next(b for b in catalogue().values() if b['species']=='dog' and suggest_size(b))
        suggested=suggest_size(breed);alternative=next(x for x in ('small','medium','large') if x!=suggested)
        for requested,expected in [('',suggested),(alternative,alternative)]:
            f=PetProfileForm(self.pet_data(breed_key=breed['key'],maturity='adult',size_origin='auto',size=requested),instance=self.room,member=self.owner)
            self.assertTrue(f.is_valid(),f.errors);self.assertEqual(f.cleaned_data['size'],expected)
            self.assertEqual(f.cleaned_data['size_origin'],'auto' if expected==suggested else 'manual')

    def test_mixed_unknown_and_growing_do_not_get_invented_size(self):
        for key,maturity in [('mixed','adult'),('unknown','adult'),('dog-bichon-frise','growing')]:
            f=PetProfileForm(self.pet_data(breed_key=key,maturity=maturity,size='',size_origin='auto'),instance=self.room,member=self.owner)
            self.assertTrue(f.is_valid(),f.errors);self.assertEqual(f.cleaned_data['size'],'');self.assertEqual(f.cleaned_data['size_origin'],'manual')

    def test_saved_manual_size_survives_reopening_and_other_changes(self):
        self.room.breed_key='dog-bichon-frise';self.room.maturity='adult';self.room.size='large';self.room.size_origin='manual';self.room.save()
        f=PetProfileForm(self.pet_data(name='이름만 변경'),instance=self.room,member=self.owner)
        self.assertTrue(f.is_valid(),f.errors);save_pet(self.owner,f)
        self.room.refresh_from_db();self.assertEqual((self.room.size,self.room.size_origin),('large','manual'))

    def test_guest_can_read_animal_but_cannot_modify(self):
        c=self.client_for(self.guest);url=reverse('room_settings',args=[self.room.pk])
        r=c.get(url,**HEADERS);self.assertEqual(r.status_code,200)
        self.assertTrue(r.context['read_only']);self.assertNotContains(r,'type="submit"')
        self.assertEqual(c.post(url,self.pet_data(name='금지'),**HEADERS).status_code,403)

    def test_admin_can_change_pet_but_not_sensitive_sharing(self):
        c=self.client_for(self.admin)
        r=c.post(reverse('room_settings',args=[self.room.pk]),self.pet_data(name='관리자 변경'),**HEADERS)
        self.assertEqual(r.status_code,200,r.content)
        data=self.preferences_data(self.admin,guest_sensitive='on')
        r=c.post(reverse('preferences',args=[self.room.pk]),data,**HEADERS)
        self.assertEqual(r.status_code,200,r.content);self.room.refresh_from_db();self.assertFalse(self.room.guest_sensitive)

    def test_guest_preferences_only_change_own_notice(self):
        data=self.preferences_data(self.guest,timezone='Europe/London',guest_sensitive='on',notices_enabled='')
        response=self.client_for(self.guest).post(reverse('preferences',args=[self.room.pk]),data,**HEADERS)
        self.assertEqual(response.status_code,200,response.content)
        self.room.refresh_from_db();self.guest.refresh_from_db();self.owner.refresh_from_db()
        self.assertEqual(self.room.timezone,'Asia/Seoul');self.assertFalse(self.room.guest_sensitive)
        self.assertFalse(self.guest.notices_enabled);self.assertTrue(self.owner.notices_enabled)

    def test_preferences_retry_does_not_repeat_audit(self):
        data=self.preferences_data(notices_enabled='');url=reverse('preferences',args=[self.room.pk]);n=AuditEvent.objects.count()
        a=self.client.post(url,data,**HEADERS);b=self.client.post(url,data,**HEADERS)
        self.assertEqual(a.status_code,200);self.assertEqual(b.status_code,200);self.assertTrue(b.json()['replayed'])
        self.assertEqual(AuditEvent.objects.count(),n+1)

    def test_time_zone_change_does_not_rewrite_event_timestamp(self):
        a=self.make('weight');stamp=a.occurred_at;payload=dict(a.payload)
        r=self.client.post(reverse('preferences',args=[self.room.pk]),self.preferences_data(timezone='Europe/London'),**HEADERS)
        self.assertEqual(r.status_code,200,r.content);a.refresh_from_db();self.assertEqual(a.occurred_at,stamp);self.assertEqual(a.payload,payload)

    def test_animal_save_does_not_change_modules_or_other_records(self):
        a=self.make('weight');before=list(self.room.module_settings.values());payload=dict(a.payload)
        r=self.client.post(reverse('room_settings',args=[self.room.pk]),self.pet_data(name='반려동물 이름'),**HEADERS)
        self.assertEqual(r.status_code,200);self.assertEqual(list(self.room.module_settings.values()),before)
        a.refresh_from_db();self.assertEqual(a.payload,payload)

    def test_invalid_future_birthday_and_wrong_species_breed(self):
        cat=next(b for b in catalogue().values() if b['species']=='cat')
        f=PetProfileForm(self.pet_data(birth_date=timezone.localdate()+timedelta(days=1),breed_key=cat['key']),instance=self.room,member=self.owner)
        self.assertFalse(f.is_valid());self.assertTrue({'birth_date','breed_key'}<=set(f.errors))

    def test_new_registration_uses_same_simplified_fields(self):
        r=self.client.get(reverse('room_new'))
        self.assertContains(r,'data-pet-profile');self.assertNotContains(r,'체격 선택 방법');self.assertNotContains(r,'name="timezone"')

    def test_owner_management_links_remain_separate(self):
        self.assertEqual(self.client.get(reverse('space_management',args=[self.room.pk])).status_code,200)
        self.assertEqual(self.client_for(self.admin).get(reverse('space_management',args=[self.room.pk])).status_code,403)


class UnifiedSuppliesTests(WorkspaceCase):
    def test_all_seven_categories_persist_without_duplicate_records(self):
        before=Activity.objects.count()
        for kind,_ in supplies.CATEGORIES:
            p=self.supply(kind);self.assertEqual(p.category,kind)
        self.assertEqual(Activity.objects.count(),before)
        self.assertEqual(Product.objects.filter(room=self.room).count(),8)

    def test_nonfood_does_not_require_or_store_nutrition(self):
        p=self.supply('hygiene',energy='200',protein='20',source='')
        self.assertIsNone(p.energy);self.assertIsNone(p.protein)
        self.assertEqual(p.notes,'')

    def test_food_unknown_energy_is_optional_but_known_energy_needs_source(self):
        p=self.supply('meal');self.assertIsNone(p.energy)
        f=supplies.SupplyForm({'name':'식품','category':'meal','energy':'200','energy_basis':'100g','active':'on','request_token':uuid.uuid4()},member=self.owner)
        self.assertFalse(f.is_valid());self.assertIn('source',f.errors)

    def test_supply_create_and_edit_retries_preserve_identity(self):
        url=reverse('product_new',args=[self.room.pk]);data={'name':'물품','category':'supply','active':'on','request_token':str(uuid.uuid4())}
        a=self.client.post(url,data,**HEADERS);b=self.client.post(url,data,**HEADERS)
        self.assertEqual(a.status_code,200,a.content);self.assertEqual(b.status_code,200,b.content);self.assertEqual(a.json()['id'],b.json()['id'])
        p=Product.objects.get(pk=a.json()['id']);url=reverse('product_edit',args=[self.room.pk,p.pk])
        updated={**data,'name':'수정 물품','version':p.version,'request_token':str(uuid.uuid4())}
        c=self.client.post(url,updated,**HEADERS);d=self.client.post(url,updated,**HEADERS)
        self.assertEqual(c.status_code,200,c.content);self.assertEqual(d.status_code,200,d.content);self.assertTrue(d.json()['replayed'])
        self.assertEqual(Product.objects.filter(pk=p.pk).count(),1)

    def test_nonfood_stock_decrements_actual_amount_once(self):
        p=self.supply('hygiene',track_stock='on',stock_unit='piece');self.stock(p)
        a=self.use(p,amount='2');self.assertEqual(self.balance(p)['balance'],98)
        self.assertEqual(Activity.objects.filter(module='inventory').count(),1)
        self.assertEqual(a.payload['_supply']['name'],p.name);self.assertEqual(a.payload['_stock']['basis'],'used')

    def test_usage_edit_cancel_recalculate_same_source(self):
        p=self.supply('hygiene',track_stock='on',stock_unit='piece');self.stock(p)
        a=self.use(p,amount='2');a=self.use(p,amount='5',instance=a)
        self.assertEqual(self.balance(p)['balance'],95)
        services.cancel_activity(self.owner,a.pk,a.version,'잘못 입력');self.assertEqual(self.balance(p)['balance'],100)
        self.assertEqual(Activity.objects.filter(module='inventory').count(),1)

    def test_usage_mg_converts_to_g_but_does_not_recommend_dose(self):
        p=self.supply('medicine',track_stock='on',stock_unit='g');self.stock(p,'1','g')
        a=self.use(p,'medication','250','mg');self.assertEqual(self.balance(p)['balance'],Decimal('.75'))
        self.assertEqual(a.payload['used_quantity'],'250');self.assertEqual(a.payload['_stock']['quantity'],'0.25')

    def test_missing_usage_is_unknown_not_automatic_single_piece(self):
        p=self.supply('hygiene',track_stock='on',stock_unit='piece');self.stock(p)
        a=self.use(p,amount='');balance=self.balance(p)
        self.assertEqual(balance['balance'],100);self.assertFalse(balance['complete']);self.assertIn(str(a.pk),balance['unknown_ids'])

    def test_skipped_medication_does_not_consume(self):
        p=self.supply('medicine',track_stock='on',stock_unit='piece');self.stock(p)
        a=self.use(p,'medication','','piece',result='skipped')
        self.assertNotIn('_stock',a.payload);self.assertEqual(self.balance(p)['balance'],100)

    def test_incompatible_usage_rejected_transactionally(self):
        p=self.supply('hygiene',track_stock='on',stock_unit='piece');self.stock(p)
        before=(Activity.objects.count(),ActivityRevision.objects.count(),AuditEvent.objects.count())
        with self.assertRaises(ValidationError):self.use(p,amount='2',unit='ml')
        self.assertEqual((Activity.objects.count(),ActivityRevision.objects.count(),AuditEvent.objects.count()),before)

    def test_insufficient_stock_rolls_back_every_side_effect(self):
        p=self.supply('hygiene',track_stock='on',stock_unit='piece');self.stock(p,'1')
        before=(Activity.objects.count(),ActivityRevision.objects.count(),AuditEvent.objects.count())
        with self.assertRaises(ValidationError):self.use(p,amount='2')
        self.assertEqual((Activity.objects.count(),ActivityRevision.objects.count(),AuditEvent.objects.count()),before)

    def test_product_changes_do_not_rewrite_existing_used_identity(self):
        p=self.supply('hygiene',track_stock='on',stock_unit='piece');self.stock(p)
        a=self.use(p);old_name=p.name;old_stock=p.stock_item
        p.name='이후 바뀐 이름';p.stock_item='새 재고 연결';p.version+=1;p.save()
        a=self.use(p,amount='3',instance=a)
        self.assertEqual(a.payload['_supply']['name'],old_name);self.assertEqual(a.payload['_stock']['item'],old_stock)
        self.assertEqual(inventory_ledger.ledger(inventory_ledger.records_for(self.room))['items'][0]['balance'],97)

    def test_original_food_product_and_snapshot_stay_unchanged(self):
        a=self.make('meal',product=str(self.product.pk),quantity='100');old=dict(a.nutrition)
        self.supply('hygiene');a.refresh_from_db();self.assertEqual(a.nutrition,old)
        self.assertEqual(a.payload['product_id'],str(self.product.pk))

    def test_existing_food_stock_flow_is_still_single_source(self):
        self.product.stock_item='기존 사료';self.product.stock_unit='g';self.product.save()
        self.stock(self.product,'1000','g');a=self.make('meal',product=str(self.product.pk),quantity='100',consumed='20')
        self.assertEqual(self.balance(self.product)['balance'],900)
        self.assertEqual(a.payload['_stock']['basis'],'provided')

    def test_supply_choice_can_prefill_inventory_name_not_amount(self):
        p=self.supply('supply',track_stock='on',stock_unit='piece')
        r=self.client.get(reverse('record_entry',args=[self.room.pk,'inventory']),{'product':str(p.pk)},**HEADERS)
        self.assertEqual(r.status_code,200);f=r.context['form'];self.assertEqual(f['item'].value(),p.stock_item)
        self.assertIsNone(f['quantity'].value())

    def test_foreign_room_product_cannot_be_used(self):
        p=Product.objects.create(room=self.other_room,name='다른 공간 물품',category='hygiene')
        f=RecordForm({'client_key':uuid.uuid4(),'status':'done','occurred_at':timezone.localtime().isoformat(),'task':'teeth','product':str(p.pk),'used_quantity':'1','used_unit':'piece'},room=self.room,module='hygiene',member=self.owner,simple=True)
        self.assertFalse(f.is_valid());self.assertIn('product',f.errors)

    def test_nonfood_product_never_appears_in_food_selector(self):
        p=self.supply('medicine')
        f=RecordForm(room=self.room,module='meal',member=self.owner,simple=True)
        self.assertFalse(f.fields['product'].queryset.filter(pk=p.pk).exists())

    def test_linked_product_cannot_change_care_category(self):
        p=self.supply('hygiene');self.use(p,amount='')
        f=supplies.SupplyForm({'name':p.name,'category':'medicine','active':'on','version':p.version,'request_token':uuid.uuid4()},member=self.owner,instance=p)
        self.assertFalse(f.is_valid());self.assertIn('category',f.errors)

    def test_preset_reuses_nonfood_product_without_retyping(self):
        p=self.supply('hygiene',track_stock='on',stock_unit='piece');self.stock(p)
        f=PresetForm({'title':'양치 소모품','active':'on','is_default':'on','product':str(p.pk),'task':'teeth','used_quantity':'1','used_unit':'piece'},member=self.owner,module='hygiene')
        self.assertTrue(f.is_valid(),f.errors);setting=save_preset(self.owner,'hygiene',f)
        request={'token':str(uuid.uuid4()),'operation':'quick','module':'hygiene','preset_id':str(setting.pk),'preset_version':setting.version}
        a=record_actions.perform(self.owner,request);b=record_actions.perform(self.owner,request)
        self.assertEqual(a['id'],b['id']);self.assertEqual(self.balance(p)['balance'],99)
        self.assertEqual(ActionReceipt.objects.filter(token=request['token']).count(),1)

    def test_private_item_and_stock_hidden_in_every_read_path(self):
        p=self.supply('medicine',name='비공개검증약',track_stock='on',stock_unit='piece');opening=self.stock(p)
        self.use(p,'medication','1','piece');c=self.client_for(self.guest)
        for name,args,query in [('supplies',[self.room.pk],{}),('supplies',[self.room.pk],{'tab':'stock'}),('records',[self.room.pk],{}),
                                ('module',[self.room.pk,'inventory'],{}),('statistics',[self.room.pk,'inventory'],{}),('overview',[self.room.pk],{}),('today',[self.room.pk],{})]:
            r=c.get(reverse(name,args=args),query);self.assertEqual(r.status_code,200,(name,r.status_code));self.assertNotContains(r,p.name)
        self.assertEqual(c.get(reverse('record_detail',args=[self.room.pk,opening.pk])).status_code,403)
        self.assertEqual(c.get(reverse('record_edit',args=[self.room.pk,opening.pk])).status_code,403)
        self.assertFalse(services.can_edit_record(self.guest,opening))
        self.assertFalse(supplies.visible_products(self.guest).filter(pk=p.pk).exists())

    def test_private_inventory_stays_private_after_item_disconnect(self):
        p=self.supply('medicine',track_stock='on',stock_unit='piece');a=self.stock(p)
        self.assertTrue(a.payload.get('_supply_private'))
        p.stock_item='';p.stock_unit='';p.save()
        self.assertFalse(supplies.record_visible(self.guest,a))
        self.assertEqual(self.client_for(self.guest).get(reverse('record_detail',args=[self.room.pk,a.pk])).status_code,403)

    def test_existing_nonfood_record_reopens_with_original_product(self):
        p=self.supply('hygiene',track_stock='on',stock_unit='piece');self.stock(p);a=self.use(p)
        r=self.client.get(reverse('record_edit',args=[self.room.pk,a.pk]),**HEADERS)
        self.assertEqual(r.status_code,200)
        self.assertEqual(str(r.context['form']['product'].value()),str(p.pk))
        self.assertEqual(str(r.context['form']['used_quantity'].value()),'2')

    def test_public_supply_remains_usable_by_guest(self):
        p=self.supply('hygiene',track_stock='on',stock_unit='piece');self.stock(p)
        a=self.use(p,member=self.guest);self.assertEqual(self.balance(p)['balance'],98)
        self.assertTrue(services.can_edit_record(self.guest,a))
        self.assertEqual(self.client_for(self.guest).get(reverse('record_detail',args=[self.room.pk,a.pk])).status_code,200)
        self.assertEqual(self.client_for(self.guest).get(reverse('product_edit',args=[self.room.pk,p.pk])).status_code,403)

    def test_private_stock_cannot_be_shared_with_public_product(self):
        p=self.supply('medicine',track_stock='on',stock_unit='piece',stock_item='공용이름')
        f=supplies.SupplyForm({'name':'일반물품','category':'supply','track_stock':'on','stock_item':p.stock_item,'stock_unit':'piece','request_token':uuid.uuid4()},member=self.owner)
        self.assertFalse(f.is_valid());self.assertIn('stock_item',f.errors)

    def test_inventory_statistics_use_the_same_nonfood_ledger(self):
        p=self.supply('hygiene',track_stock='on',stock_unit='piece');self.stock(p);self.use(p,amount='4')
        report=self.stats('inventory');row=next(x for x in report['breakdown'] if p.stock_item in x['label'])
        self.assertEqual(row['value'],'96')


class WorkspaceScopeTests(WorkspaceCase):
    def test_both_workspaces_include_exactly_one_shared_panel(self):
        self.routine(modules=('meal',))
        for name in ('today','modules'):
            r=self.client.get(reverse(name,args=[self.room.pk]));self.assertEqual(r.status_code,200)
            self.assertContains(r,'id="workspace-schedule"',count=1)
            self.assertContains(r,'data-schedule-panel',count=1)
            self.assertTrue(r.context['schedule_entries'])

    def test_sidebar_has_supplies_and_two_settings_without_old_link(self):
        r=self.client.get(reverse('today',args=[self.room.pk]))
        self.assertContains(r,'물품 관리');self.assertContains(r,'반려동물 상태');self.assertContains(r,'일반 설정')
        self.assertNotContains(r,'공간 설정</a>')
        html=r.content.decode();self.assertLess(html.index('물품 관리'),html.index('sidebar-members'))

    def test_no_changes_on_read_only_gets(self):
        before=(Activity.objects.count(),AuditEvent.objects.count(),ActionReceipt.objects.count(),Product.objects.count())
        for name in ('today','modules','room_settings','preferences','supplies'):
            self.assertEqual(self.client.get(reverse(name,args=[self.room.pk])).status_code,200)
        self.assertEqual((Activity.objects.count(),AuditEvent.objects.count(),ActionReceipt.objects.count(),Product.objects.count()),before)

    def test_settings_error_keeps_same_fragment_and_values(self):
        r=self.client.post(reverse('room_settings',args=[self.room.pk]),self.pet_data(name=''),**HEADERS)
        self.assertEqual(r.status_code,422);self.assertEqual(r['X-Taily-Fragment'],'input-form');self.assertContains(r,'has-error',status_code=422)

    def test_no_live_external_calls_needed_to_render_settings(self):
        with patch('socket.create_connection',side_effect=AssertionError('unexpected external request')):
            for name in ('room_settings','preferences','supplies'):
                self.assertEqual(self.client.get(reverse(name,args=[self.room.pk])).status_code,200)

    def test_product_metadata_does_not_apply_new_health_statistics(self):
        p=self.supply('supp');self.assertIsNone(p.energy)
        self.assertEqual(module_reports.differences(Decimal('8.45'),Decimal('8.4'))['delta'],Decimal('-.05'))

    def test_explicit_midnight_boundary_keeps_today_and_yesterday_separate(self):
        from care.dashboard import today_tiles
        boundary=datetime(2026,11,14,0,15,tzinfo=ZoneInfo('Asia/Seoul'))
        with patch('django.utils.timezone.now',return_value=boundary):
            yesterday=self.make('water',kind='replace',amount='',occurred_at=(boundary-timedelta(minutes=20)).isoformat())
            today=self.make('water',kind='replace',amount='',occurred_at=(boundary-timedelta(minutes=5)).isoformat())
            tile=today_tiles(self.owner,[{'spec':__import__('care.modules',fromlist=['MODULES']).MODULES['water']}],[yesterday,today],services.local_date(self.room))[0]['tile']
            self.assertEqual(tile['value'],'1');self.assertEqual(tile['unit'],'건')
