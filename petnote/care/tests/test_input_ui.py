"""November 12 authorized UI scope; statistical computation remains unchanged."""
import json,uuid
from unittest.mock import patch
from django.core.exceptions import ValidationError
from django.db import OperationalError
from django.test import override_settings
from django.urls import reverse,path
from django.utils import timezone
from care import services
from care.models import Activity,ActivityRevision,AuditEvent,Routine,Membership,Room,Invitation
from care.demo_fixtures import record_data
from care import demo
from care.management.commands.seed_demo import upgrade_local_examples, upgrade_public_seed
from .test_functional import CareCase

from care import urls as care_urls,auth
urlpatterns=[*care_urls.urlpatterns,path('demo/login/',auth.demo_login,name='demo_login'),
             path('enter/', demo.enter, name='public_demo_enter')]
HEADERS={'HTTP_X_TAILY_DIALOG':'1'}

class InputDialogContractTests(CareCase):
    def input_url(self,key='observation'):
        return reverse('record_entry',args=[self.room.pk,key])

    def test_get_is_only_a_fragment_and_original_get_stays_a_page(self):
        page=self.client.get(self.input_url());fragment=self.client.get(self.input_url(),**HEADERS)
        self.assertContains(page,'<!doctype html>')
        self.assertNotContains(fragment,'<!doctype html>')
        self.assertEqual(fragment['X-Taily-Fragment'],'input-form')
        self.assertContains(fragment,'data-input-form')
        self.assertContains(fragment,'name="client_key"')
        self.assertContains(fragment,'input-dialog-title')
        self.assertEqual(Activity.objects.count(),0)

    def test_all_selected_input_forms_can_be_rendered_in_place(self):
        from care.modules import SELECTABLE_MODULES
        for key in SELECTABLE_MODULES:
            with self.subTest(module=key):
                r=self.client.get(self.input_url(key),**HEADERS)
                self.assertEqual(r.status_code,200);self.assertEqual(r['X-Taily-Fragment'],'input-form')
                self.assertContains(r,'type="submit"')

    def test_invalid_post_retains_data_and_key_in_same_form(self):
        key=str(uuid.uuid4())
        payload=record_data('weight',client_key=key,weight='-1',note='남겨둘 메모')
        r=self.client.post(self.input_url('weight'),payload,**HEADERS)
        self.assertEqual(r.status_code,422);self.assertContains(r,'남겨둘 메모',status_code=422)
        self.assertContains(r,key,status_code=422);self.assertEqual(Activity.objects.count(),0)

    def test_success_returns_json_not_a_redirect_or_success_dialog(self):
        key=str(uuid.uuid4());payload=record_data('observation',client_key=key,detail='모달의 실제 메모')
        r=self.client.post(self.input_url(),payload,**HEADERS)
        self.assertEqual(r.status_code,200);self.assertTrue(r.json()['ok'])
        self.assertEqual(Activity.objects.count(),1)
        self.assertEqual(Activity.objects.get().payload['detail'],'모달의 실제 메모')
        self.assertTrue(r.json()['url'].endswith('/'))
        self.assertFalse(list(r.wsgi_request._messages))

    def test_lost_response_retry_uses_same_record_and_revision(self):
        payload=record_data('observation',detail='같은 논리 요청',client_key=str(uuid.uuid4()))
        first=self.client.post(self.input_url(),payload,**HEADERS)
        second=self.client.post(self.input_url(),payload,**HEADERS)
        self.assertEqual(first.json()['id'],second.json()['id']);self.assertFalse(second.json()['created'])
        self.assertEqual(Activity.objects.count(),1);self.assertEqual(ActivityRevision.objects.count(),1)

    def test_same_key_different_body_stays_conflict_not_second_record(self):
        payload=record_data('observation',detail='원래 내용',client_key=str(uuid.uuid4()))
        self.client.post(self.input_url(),payload,**HEADERS)
        r=self.client.post(self.input_url(),{**payload,'detail':'다른 내용'},**HEADERS)
        self.assertEqual(r.status_code,409);self.assertEqual(Activity.objects.count(),1)
        self.assertEqual(r['X-Taily-Fragment'],'input-form')

    def test_503_keeps_input_not_fake_success(self):
        payload=record_data('observation',detail='통신 오류에도 유지')
        with patch('care.services.save_activity',side_effect=OperationalError('busy')):
            r=self.client.post(self.input_url(),payload,**HEADERS)
        self.assertEqual(r.status_code,503);self.assertContains(r,'통신 오류에도 유지',status_code=503)
        self.assertEqual(Activity.objects.count(),0)

    def test_existing_edit_uses_same_permissions_and_revision(self):
        record=self.make('weight',weight='8')
        url=reverse('record_edit',args=[self.room.pk,record.pk])
        get=self.client.get(url,**HEADERS);self.assertEqual(get.status_code,200)
        payload=record_data('weight',weight='8.1',client_key=str(record.client_key),version=str(record.version))
        r=self.client.post(url,payload,**HEADERS);self.assertTrue(r.json()['ok'])
        self.assertEqual(Activity.objects.count(),1);record.refresh_from_db();self.assertEqual(record.version,2)
        r=self.client_for(self.member).get(url,**HEADERS);self.assertEqual(r.status_code,403)

    def test_guest_cannot_use_modal_header_to_read_sensitive_records(self):
        for key in ('medication','veterinary','expense'):
            r=self.client_for(self.guest).get(self.input_url(key),**HEADERS)
            self.assertEqual(r.status_code,403)

    def test_other_room_cannot_be_opened_via_modal(self):
        r=self.client.get(reverse('record_entry',args=[self.other_room.pk,'weight']),**HEADERS)
        self.assertIn(r.status_code,(403,404))

    def test_new_routine_requires_server_form_request_token(self):
        url=reverse('routine_new',args=[self.room.pk]);r=self.client.get(url,**HEADERS)
        self.assertContains(r,'name="request_token"')
        self.assertContains(r,'data-dialog-routine')
        payload={'name':'밤 돌봄','at':'20:00','weekdays':['0','1'],'modules':['meal'],'active':'on'}
        r=self.client.post(url,payload,**HEADERS)
        self.assertEqual(r.status_code,422);self.assertEqual(Routine.objects.count(),0)

    def test_routine_save_and_retry_stay_on_original_surface(self):
        url=reverse('routine_new',args=[self.room.pk])
        payload={'name':'밤 돌봄','at':'20:00','weekdays':['0','1'],'modules':['meal'],'active':'on','request_token':str(uuid.uuid4())}
        a=self.client.post(url,payload,**HEADERS);b=self.client.post(url,payload,**HEADERS)
        self.assertEqual(a.status_code,200);self.assertTrue(a.json()['ok'])
        self.assertEqual(a.json()['id'],b.json()['id']);self.assertEqual(Routine.objects.count(),1)

    def test_routine_invalid_end_date_retains_name(self):
        payload={'name':'확인할 루틴','at':'20:00','weekdays':['0'],'modules':['meal'],'active':'on','request_token':str(uuid.uuid4()),'start_date':'2026-11-12','end_date':'2026-11-01'}
        r=self.client.post(reverse('routine_new',args=[self.room.pk]),payload,**HEADERS)
        self.assertEqual(r.status_code,422);self.assertContains(r,'확인할 루틴',status_code=422)

    def test_guest_cannot_create_routine(self):
        r=self.client_for(self.guest).get(reverse('routine_new',args=[self.room.pk]),**HEADERS)
        self.assertEqual(r.status_code,403)

    def test_preset_form_fragment_and_validation(self):
        url=reverse('preset_new',args=[self.room.pk,'meal'])
        self.assertEqual(self.client.get(url,**HEADERS)['X-Taily-Fragment'],'input-form')
        payload={'title':'저녁 사료','active':'on','is_default':'on','request_token':str(uuid.uuid4()),'product':str(self.product.pk),'quantity':'60','unit':'g'}
        r=self.client.post(url,payload,**HEADERS);self.assertTrue(r.json()['ok'])

class MemberPresentationTests(CareCase):
    def test_member_name_and_menu_use_same_surface(self):
        page=self.client.get(reverse('today',args=[self.room.pk]))
        self.assertContains(page,'data-member-open7')
        self.assertNotContains(page,'class="member-menu-body"')
        panel=self.client.get(reverse('member_panel',args=[self.room.pk,self.member.pk]),HTTP_X_TAILY_PANEL='1')
        self.assertContains(panel,'이 공간에서의 역할');self.assertContains(panel,'공간 접근 관리')
        self.assertContains(panel,'name="revoke_links"');self.assertContains(panel,'name="role"')

    def test_self_profile_does_not_offer_self_promotion(self):
        panel=self.client_for(self.member).get(reverse('member_panel',args=[self.room.pk,self.member.pk]),HTTP_X_TAILY_PANEL='1')
        self.assertContains(panel,'내 설정');self.assertNotContains(panel,'name="role"')

    def test_role_change_preserves_owner_only_rule(self):
        url=reverse('member_panel',args=[self.room.pk,self.member.pk])
        self.assertEqual(self.client_for(self.admin).post(url,{'action':'role','role':'admin'}).status_code,403)
        self.member.refresh_from_db();self.assertEqual(self.member.role,'guest')
        self.assertEqual(self.client.post(url,{'action':'role','role':'admin'}).status_code,200)
        self.member.refresh_from_db();self.assertEqual(self.member.role,'admin')

    def test_guest_without_account_cannot_be_promoted(self):
        r=self.client.post(reverse('member_panel',args=[self.room.pk,self.guest.pk]),{'action':'role','role':'admin'})
        self.assertEqual(r.status_code,422);self.guest.refresh_from_db();self.assertEqual(self.guest.role,'guest')

    def test_removal_can_revoke_old_links_without_deleting_records(self):
        record=self.make('walk',member=self.member)
        invitation,_=services.create_invitation(self.owner,24,5)
        r=self.client.post(reverse('member_panel',args=[self.room.pk,self.member.pk]),{'action':'remove','revoke_links':'1'})
        self.assertEqual(r.status_code,200);self.member.refresh_from_db();self.assertFalse(self.member.active)
        invitation.refresh_from_db();self.assertTrue(invitation.revoked);record.refresh_from_db();self.assertFalse(record.voided)

class DemoRoomTests(CareCase):
    def setUp(self):
        super().setUp()
        self.owner_user.username = 'demo_owner'
        self.owner_user.save(update_fields=['username'])
        self.admin_user.username = 'demo_admin'
        self.admin_user.save(update_fields=['username'])
        self.room.name = '강아지'
        self.room.save(update_fields=['name'])
        self.other_room.name = '고양이'
        self.other_room.owner = self.owner_user
        self.other_room.save(update_fields=['name', 'owner'])
        Membership.objects.create(room=self.other_room, user=self.admin_user, role='admin')

    @override_settings(PUBLIC_DEMO=True, ROOT_URLCONF=__name__)
    def test_public_entry_accepts_only_the_two_demo_rooms(self):
        self.assertEqual(self.client.get(reverse('public_demo_enter')).status_code, 302)
        self.assertEqual(self.client.session['_auth_user_id'], str(self.admin_user.pk))
        self.assertEqual({m.room.name for m in self.client.get(reverse('home')).context['memberships']}, {'강아지', '고양이'})
        Room.objects.create(owner=self.outsider_user, name='개인 공간', species='cat')
        self.assertEqual(self.client.get(reverse('public_demo_enter')).status_code, 403)

    @override_settings(PUBLIC_DEMO=True)
    def test_public_upgrade_removes_seed_bird_and_is_repeatable(self):
        bird = Room.objects.create(owner=self.owner_user, name='앵무새', species='bird')
        Membership.objects.create(room=bird, user=self.admin_user, role='admin')
        upgrade_public_seed()
        upgrade_public_seed()
        self.assertSetEqual(set(Room.objects.values_list('name', flat=True)), {'강아지', '고양이'})

    def test_local_upgrade_only_deletes_known_examples(self):
        old = Room.objects.create(owner=self.owner_user, name='빈 기록', species='dog')
        personal = Room.objects.create(owner=self.owner_user, name='빈 기록', species='dog')
        self.room.name = '7차 검수 · 보리'
        self.room.save(update_fields=['name'])
        fixture = {'synthetic': True, 'rooms': {'rich': str(self.room.pk), 'cat': str(self.other_room.pk), 'empty': str(old.pk)}}
        upgrade_local_examples(fixture)
        upgrade_local_examples(fixture)
        self.room.refresh_from_db()
        self.assertEqual(self.room.name, '강아지')
        self.assertFalse(Room.objects.filter(pk=old.pk).exists())
        self.assertTrue(Room.objects.filter(pk=personal.pk).exists())
        self.assertEqual(set(fixture['rooms']), {'rich', 'cat'})
        self.assertEqual(self.client.get('/demo/inspection-rooms/').status_code, 404)
