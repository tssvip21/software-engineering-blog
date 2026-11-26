"""Browser verification against the explicit local demo environment only."""
from demo_browser_session import select_fixture_role
import argparse
import json
import os
from pathlib import Path
import re
import sys
import traceback
import uuid

ROOT=next(parent for parent in Path(__file__).resolve().parents if (parent / "manage.py").is_file())
sys.path.insert(0,str(ROOT))
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
import django
django.setup()
from care.demo_fixtures import record_data
from care.modules import MODULES
from playwright.sync_api import sync_playwright, expect


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=8001)
    args=parser.parse_args()
    base=f'http://127.0.0.1:{args.port}'
    out=ROOT/'tmp/handoff/verification';out.mkdir(parents=True,exist_ok=True)
    result={'checks':[],'js_errors':[],'layout':[]}
    suffix=uuid.uuid4().hex[:6]
    room_name='브라우저검증-'+suffix
    def passed(name):
        result['checks'].append(name)
        (out/'browser_results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        owner_context=browser.new_context(viewport={'width':1440,'height':1000},locale='ko-KR',timezone_id='Asia/Seoul')
        page=owner_context.new_page()
        page.on('pageerror',lambda error:result['js_errors'].append(str(error)))
        page.on('dialog',lambda dialog:dialog.accept())
        try:
            page.goto(base+'/',wait_until='networkidle')
            if not page.locator('.demo-banner').is_visible():
                raise RuntimeError('Refusing to run on a non-demo application.')
            select_fixture_role(page, 'owner')
            expect(page.get_by_role('heading',name='내 돌봄 공간',exact=True)).to_be_visible()
            passed('real-browser-csrf-login')
            page.goto(base+'/rooms/new/',wait_until='networkidle')
            page.locator('#id_name').fill(room_name)
            page.locator('#id_species').select_option('dog')
            page.get_by_role('button',name='돌봄 공간 만들기',exact=True).click()
            page.wait_for_url(re.compile(r'/rooms/[0-9a-f-]+/$'))
            room_path=urlparse_path(page.url)
            result['test_room']=room_path
            passed('room-create-empty-onboarding')
            page.goto(base+room_path+'modules/settings/',wait_until='networkidle')
            for key in MODULES:
                page.locator('#enable-'+key).check()
            original=page.locator('.module-order-row').nth(1).get_attribute('data-key')
            page.locator('.module-order-row').nth(1).get_by_role('button',name=re.compile('위로 이동')).click()
            expect(page.locator('.module-order-row').first).to_have_attribute('data-key',original)
            page.get_by_role('button',name='구성 저장',exact=True).click()
            page.wait_for_url(base+room_path+'modules/')
            passed('twenty-modules-enabled-and-reordered')
            created={}
            for key,spec in MODULES.items():
                page.goto(base+room_path+'modules/'+key+'/new/',wait_until='networkidle')
                values=record_data(key)
                for name,value in values.items():
                    if name=='client_key':continue
                    field=page.locator('[name="'+name+'"]')
                    if field.count()==0 or not field.first.is_visible():continue
                    field=field.first
                    if field.evaluate('(e)=>e.tagName')=='SELECT':field.select_option(str(value))
                    elif field.get_attribute('type')=='checkbox':field.set_checked(bool(value))
                    else:field.fill(str(value))
                page.get_by_role('button',name='기록 저장',exact=True).click()
                page.wait_for_url(re.compile(r'/records/[0-9a-f-]+/$'),timeout=15000)
                created[key]=page.url
                expect(page.locator('h1')).to_contain_text(spec.name)
                page.reload(wait_until='networkidle')
                expect(page.locator('.detail-list').first).to_be_visible()
                passed('module-browser-save-'+key)
            page.goto(created['meal'],wait_until='networkidle')
            page.get_by_role('link',name='수정',exact=True).click()
            page.locator('#id_note').fill('브라우저에서 수정하고 새로고침으로 확인')
            page.get_by_role('button',name='기록 저장',exact=True).click()
            page.wait_for_url(created['meal'])
            expect(page.locator('.record-note')).to_contain_text('브라우저에서 수정')
            passed('record-edit-audit')
            page.goto(base+room_path+'modules/meal/new/',wait_until='networkidle')
            page.locator('#id_food_name').fill('오류 보존 확인')
            page.locator('#id_quantity').fill('3')
            page.locator('#id_consumed').fill('4')
            page.get_by_role('button',name='기록 저장',exact=True).click()
            expect(page.locator('.field-error')).to_contain_text('제공량보다')
            expect(page.locator('#id_food_name')).to_have_value('오류 보존 확인')
            page.locator('#id_consumed').fill('2')
            page.get_by_role('button',name='기록 저장',exact=True).click()
            page.wait_for_url(re.compile(r'/records/[0-9a-f-]+/$'))
            passed('validation-error-preserves-input-and-retry')
            page.goto(base+room_path+'routines/new/',wait_until='networkidle')
            page.locator('#id_name').fill('매일 물 확인')
            page.locator('#id_at').fill('19:00')
            page.locator('input[name="modules"][value="water"]').check()
            page.get_by_role('button',name='루틴 저장',exact=True).click()
            page.wait_for_url(base+room_path+'routines/')
            page.get_by_role('link',name='기록하기',exact=True).click()
            page.locator('#id_kind').select_option('replace')
            page.get_by_role('button',name='기록 저장',exact=True).click()
            page.wait_for_url(re.compile(r'/records/[0-9a-f-]+/$'))
            routine_record=page.url
            page.goto(base+room_path+'routines/',wait_until='networkidle')
            expect(page.locator('.routine-item')).to_contain_text('완료')
            passed('routine-completion-linked-to-record')
            page.goto(routine_record,wait_until='networkidle')
            page.locator('.danger-zone summary').click()
            page.locator('#id_reason').fill('브라우저 취소 검증')
            page.get_by_role('button',name='기록 취소',exact=True).click()
            expect(page.locator('h1')).to_contain_text('취소됨')
            page.goto(base+room_path+'routines/',wait_until='networkidle')
            expect(page.locator('.routine-item')).to_contain_text('완료 기록 없음')
            passed('cancel-updates-routine-and-statistics')
            page.goto(base+room_path+'members/',wait_until='networkidle')
            page.locator('#id_max_uses').fill('5')
            page.get_by_role('button',name='초대 링크 만들기',exact=True).click()
            invite_url=page.locator('#created-invite').input_value()
            assert '#' in invite_url
            guest_context=browser.new_context(viewport={'width':390,'height':844},locale='ko-KR',timezone_id='Asia/Seoul')
            guest=guest_context.new_page()
            guest.goto(invite_url,wait_until='networkidle')
            expect(guest.locator('h1')).to_contain_text('참여')
            assert '#' not in guest.url
            guest.locator('#id_display_name').fill('검증 게스트')
            guest.locator('#id_agree').check()
            guest.get_by_role('button',name='참여하기',exact=True).click()
            guest.wait_for_url(base+room_path)
            passed('guest-no-login-invitation-consent')
            response=guest.goto(base+room_path+'modules/settings/',wait_until='networkidle')
            assert response.status==403
            response=guest.goto(base+room_path+'modules/expense/',wait_until='networkidle')
            assert response.status==403
            passed('guest-server-permissions-and-sensitive-access')
            guest.goto(base+room_path+'modules/water/new/',wait_until='networkidle')
            guest.locator('#id_kind').select_option('replace')
            guest.locator('#id_container').fill('브라우저 공동 기록')
            guest.get_by_role('button',name='기록 저장',exact=True).click()
            guest.wait_for_url(re.compile(r'/records/[0-9a-f-]+/$'))
            guest_record=guest.url
            page.goto(guest_record,wait_until='networkidle')
            expect(page.locator('.detail-list').first).to_contain_text('브라우저 공동 기록')
            passed('guest-record-shared-with-owner')
            admin_context=browser.new_context(locale='ko-KR',timezone_id='Asia/Seoul')
            admin=admin_context.new_page();admin.goto(base+'/',wait_until='networkidle')
            admin.get_by_role('button',name='관리자로 보기',exact=True).click()
            admin.goto(invite_url,wait_until='networkidle')
            admin.locator('#id_display_name').fill('검증 관리자')
            admin.locator('#id_agree').check();admin.get_by_role('button',name='참여하기',exact=True).click()
            admin.wait_for_url(base+room_path)
            assert admin.goto(base+room_path+'modules/settings/',wait_until='networkidle').status==403
            page.goto(base+room_path+'members/',wait_until='networkidle')
            row=page.locator('.member-row').filter(has_text='검증 관리자')
            row.locator('summary').click();row.get_by_role('button',name='관리자로 지정',exact=True).click()
            assert admin.goto(base+room_path+'modules/settings/',wait_until='networkidle').status==200
            assert admin.goto(base+room_path+'invitations/new/',wait_until='networkidle').status==405
            passed('google-identity-not-equivalent-to-room-admin-role')
            page.goto(base+room_path+'members/',wait_until='networkidle')
            row=page.locator('.member-row').filter(has_text='검증 게스트')
            row.locator('summary').click();row.get_by_role('button',name='내보내기',exact=True).click()
            assert guest.goto(guest_record,wait_until='networkidle').status==404
            revoked=browser.new_context();revoked_page=revoked.new_page();revoked_page.goto(invite_url,wait_until='networkidle')
            expect(revoked_page.locator('h1')).to_contain_text('초대를 확인할 수 없습니다')
            revoked.close()
            passed('remove-member-and-revoke-links')
            for width in (1440,768,390,360):
                page.set_viewport_size({'width':width,'height':900})
                for path in ('','modules/','records/','overview/','modules/weight/statistics/','members/','settings/','modules/meal/new/'):
                    page.goto(base+room_path+path,wait_until='networkidle')
                    overflow=page.evaluate('document.documentElement.scrollWidth > window.innerWidth + 1')
                    result['layout'].append({'width':width,'path':path,'overflow':overflow})
                    assert not overflow, f'Horizontal overflow: {width} {path}'
                passed('responsive-layout-'+str(width))
            page.set_viewport_size({'width':1440,'height':1000})
            page.goto(base+room_path+'overview/',wait_until='networkidle');page.screenshot(path=str(out/'browser_overview_verified.png'),full_page=True)
            page.goto(base+room_path+'settings/',wait_until='networkidle')
            page.locator('summary').filter(has_text='방과 기록 영구 삭제').click()
            page.locator('#delete-confirm').fill(room_name+' 삭제')
            page.get_by_role('button',name='영구 삭제',exact=True).click()
            page.wait_for_url(base+'/')
            assert page.goto(base+room_path,wait_until='networkidle').status==404
            passed('test-room-delete-and-isolation')
            guest_context.close();admin_context.close()
        except Exception as exc:
            result['exception']=type(exc).__name__+': '+str(exc)
            result['traceback']=traceback.format_exc()
            page.screenshot(path=str(out/'browser_test_failure.png'),full_page=True)
        finally:
            (out/'browser_results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            browser.close()
    print(json.dumps({'checks':len(result['checks']),'layout_checks':len(result['layout']),'js_errors':result['js_errors'],'exception':result.get('exception')},ensure_ascii=True))
    return 1 if result.get('exception') or result['js_errors'] else 0


def urlparse_path(url):
    from urllib.parse import urlparse
    return urlparse(url).path


if __name__=='__main__':
    sys.exit(main())
