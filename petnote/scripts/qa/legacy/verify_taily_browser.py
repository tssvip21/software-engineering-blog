"""End-to-end interaction checks on a separate local demo database only."""
from demo_browser_session import select_fixture_role
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import traceback
import uuid
from urllib.parse import urlparse

ROOT=next(parent for parent in Path(__file__).resolve().parents if (parent / "manage.py").is_file())
OUT=ROOT/'tmp/handoff/phase2/browser'
DATA=ROOT/'tmp/handoff/phase2/browser-data'
OUT.mkdir(parents=True,exist_ok=True)
if not (DATA/'demo.sqlite3').exists():
    raise SystemExit('Prepare the isolated browser database before testing.')
os.environ.update(DJANGO_SETTINGS_MODULE='config.settings',CARE_DATA_DIR=str(DATA),CARE_DEMO_MODE='1',DJANGO_DEBUG='1',APP_ORIGIN='http://127.0.0.1:8012',DJANGO_ALLOWED_HOSTS='127.0.0.1,localhost,testserver')
sys.path.insert(0,str(ROOT))
import django
django.setup()
from django.db import connections
from care.models import Activity, CareCadence, Room
from care.demo_fixtures import record_data
from care.modules import MODULES
from playwright.sync_api import sync_playwright, expect

BASE='http://127.0.0.1:8012'
RESULT={'checks':[],'layout':[],'page_errors':[],'console_errors':[]}
def checkpoint(name,detail=None):
    RESULT['checks'].append({'name':name,'detail':detail})
    (OUT/'results.json').write_text(json.dumps(RESULT,ensure_ascii=False,indent=2),encoding='utf8')

def main():
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1',8012))==0:
            raise RuntimeError('Port 8012 is already used; do not interfere with an unknown process.')
    log=(OUT/'server.log').open('w',encoding='utf8')
    server=subprocess.Popen([sys.executable,'manage.py','runserver','127.0.0.1:8012','--noreload'],cwd=ROOT,env=os.environ.copy(),stdout=log,stderr=subprocess.STDOUT)
    for _ in range(80):
        with socket.socket() as probe:
            if probe.connect_ex(('127.0.0.1',8012))==0:break
        if server.poll() is not None:raise RuntimeError('Verification server stopped during startup.')
        time.sleep(.1)
    # Playwright's synchronous API runs a private event loop in this single-threaded
    # test process. Only this isolated test DB uses direct ORM assertions here.
    # Set after spawning the server: application processes never inherit this flag.
    os.environ['DJANGO_ALLOW_ASYNC_UNSAFE']='1'
    suffix=uuid.uuid4().hex[:6]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        context=browser.new_context(viewport={'width':1440,'height':1000},locale='ko-KR',timezone_id='Asia/Seoul')
        page=context.new_page()
        page.set_default_timeout(12000)
        page.on('pageerror',lambda error:RESULT['page_errors'].append(str(error)))
        page.on('console',lambda msg:RESULT['console_errors'].append(msg.text) if msg.type=='error' else None)
        page.on('dialog',lambda d:d.accept())
        room=None
        try:
            page.goto(BASE+'/',wait_until='networkidle')
            expect(page.locator('.demo-banner')).to_be_visible()
            page.screenshot(path=str(OUT/'landing-desktop.png'),full_page=True)
            select_fixture_role(page, 'owner')
            page.wait_for_url(BASE+'/')
            expect(page.locator('h1')).to_contain_text('내 돌봄 공간')
            checkpoint('csrf-protected-demo-login')
            page.goto(BASE+'/rooms/new/')
            page.locator('#id_name').fill('테일리검증-'+suffix)
            page.locator('#id_species').select_option('dog')
            page.get_by_role('button',name='돌봄 공간 만들기',exact=True).click()
            page.wait_for_url(re.compile(r'/rooms/[0-9a-f-]+/$'))
            roompath=urlparse(page.url).path
            room=Room.objects.get(pk=roompath.split('/')[2])
            RESULT['room_path']=roompath
            expect(page.locator('h1')).to_contain_text('테일리검증-')
            checkpoint('new-room-onboarding-and-empty-today')
            # Width drag, collapse, restore, persistence.
            handle=page.locator('#sidebar-resize');box=handle.bounding_box()
            page.mouse.move(box['x']+4,box['y']+200);page.mouse.down();page.mouse.move(315,box['y']+200,steps=12);page.mouse.up()
            assert int(handle.get_attribute('aria-valuenow'))==315
            page.reload(wait_until='networkidle')
            assert int(page.locator('#sidebar-resize').get_attribute('aria-valuenow'))==315
            page.locator('#sidebar [data-sidebar-toggle]').click()
            expect(page.locator('body')).to_have_class(re.compile('sidebar-closed'))
            page.reload(wait_until='networkidle')
            expect(page.locator('.sidebar-restore')).to_be_visible()
            page.locator('.sidebar-restore').click()
            page.locator('#sidebar-resize').focus();page.keyboard.press('Home')
            assert int(page.locator('#sidebar-resize').get_attribute('aria-valuenow'))==200
            page.keyboard.press('ArrowRight')
            assert int(page.locator('#sidebar-resize').get_attribute('aria-valuenow'))==210
            checkpoint('sidebar-pointer-resize-collapse-keyboard-and-persistence')
            page.goto(BASE+roompath+'modules/settings/',wait_until='networkidle')
            config=page.locator('[data-module-config]')
            def order():return page.locator('#chosen-modules .chosen-row').evaluate_all('(rows)=>rows.map(r=>r.dataset.key)')
            def saved():
                expect(config).to_have_attribute('data-status','saved',timeout=15000)
                assert not page.locator('#config-status').inner_text().startswith('변경')
            before=order()
            page.locator('[data-add-module="hygiene"]').click();saved()
            assert order()==before+['hygiene']
            expect(page.locator('[data-add-module="hygiene"]')).to_be_disabled()
            page.reload(wait_until='networkidle');assert 'hygiene' in order()
            checkpoint('catalog-add-no-duplicate-persisted')
            # Drag an added activity through another category into the first slot.
            row=page.locator('#chosen-modules .chosen-row[data-key="weight"]')
            row.scroll_into_view_if_needed()
            first=page.locator('#chosen-modules .chosen-row').first.bounding_box()
            source=row.locator('.drag-handle').bounding_box()
            page.mouse.move(source['x']+source['width']/2,source['y']+source['height']/2)
            page.mouse.down();page.mouse.move(first['x']+24,first['y']+12,steps=18)
            expect(page.locator('#chosen-modules .chosen-row').first).to_have_attribute('data-key','weight')
            page.mouse.up();saved()
            pointer_order=order();assert pointer_order[0]=='weight'
            page.reload(wait_until='networkidle');assert order()==pointer_order
            checkpoint('mouse-drag-reflows-neighbors-and-persists',pointer_order)
            # Real keyboard pickup/move/drop and cancel.
            last=page.locator('#chosen-modules .chosen-row').last
            key=last.get_attribute('data-key');last.locator('.drag-handle').focus()
            page.keyboard.press('Space');page.keyboard.press('Home');page.keyboard.press('Enter');saved()
            assert order()[0]==key
            stable=order();page.locator('#chosen-modules .drag-handle').first.focus()
            page.keyboard.press('Space');page.keyboard.press('ArrowDown');page.keyboard.press('Escape')
            assert order()==stable
            checkpoint('keyboard-drag-save-and-escape-restore')
            # Pins are independent from activation.
            page.locator('[data-pin-module="hygiene"]').set_checked(False);saved()
            expected=[k for k in order() if page.locator(f'[data-pin-module="{k}"]').is_checked()]
            page.goto(BASE+roompath,wait_until='networkidle')
            today_order=page.locator('.quick-card a[data-quick]').evaluate_all('(els)=>els.map(a=>a.href.split("/modules/")[1].split("/")[0])')
            assert today_order==expected,(today_order,expected)
            page.goto(BASE+roompath+'modules/')
            names=page.locator('.managed-module-info h3').all_inner_texts()
            assert names==[MODULES[k].name for k in stable],names
            checkpoint('today-pins-and-module-list-use-same-persisted-order')
            # Inline minimal meal and undo, without navigation.
            page.goto(BASE+roompath)
            page.locator(f'a[data-quick][href="{roompath}modules/meal/quick/"]').first.click()
            expect(page.locator('#quick-dialog')).to_be_visible()
            expect(page.locator('#quick-content [name="quantity"]')).to_have_value('')
            original_count=Activity.objects.filter(room=room).count()
            page.locator('#quick-content button[type="submit"]').click()
            expect(page.locator('#quick-dialog')).not_to_be_visible()
            expect(page.locator('[data-feedback-message]')).to_contain_text('저장했어요')
            expect(page.locator('.quick-card').filter(has=page.locator('a[href$="/modules/meal/"]')).locator('p')).to_contain_text('1')
            assert page.url==BASE+roompath
            assert Activity.objects.filter(room=room).count()==original_count+1
            minimal=Activity.objects.filter(room=room,module='meal').latest('created_at')
            assert minimal.payload['quantity'] is None and minimal.nutrition['kcal'] is None
            page.locator('[data-feedback-undo]').click()
            expect(page.locator('[data-feedback-message]')).to_contain_text('취소했어요')
            minimal.refresh_from_db();assert minimal.voided
            checkpoint('inline-minimal-record-unknown-values-and-undo')
            # Submission failure retains input. Retry sends the same request key.
            page.locator(f'a[data-quick][href="{roompath}modules/meal/quick/"]').first.click()
            page.locator('#quick-content [name="food_name"]').fill('연결 재시도 검증')
            page.locator('#quick-content [name="quantity"]').fill('35')
            client_key=page.locator('#quick-content [name="client_key"]').input_value()
            def abort_post(route):
                if route.request.method=='POST':route.abort('internetdisconnected')
                else:route.continue_()
            context.route('**/modules/meal/quick/**',abort_post)
            page.locator('#quick-content button[type="submit"]').click()
            expect(page.locator('.quick-save-status')).to_contain_text('연결')
            expect(page.locator('#quick-content [name="quantity"]')).to_have_value('35')
            assert page.locator('#quick-content [name="client_key"]').input_value()==client_key
            context.unroute('**/modules/meal/quick/**',abort_post)
            page.locator('#quick-content button[type="submit"]').click()
            expect(page.locator('#quick-dialog')).not_to_be_visible()
            assert Activity.objects.filter(room=room,client_key=client_key).count()==1
            checkpoint('network-failure-retains-input-and-single-record-on-retry')
            # Walk starts and finishes the same activity.
            page.goto(BASE+roompath)
            page.locator(f'a[data-quick][href="{roompath}modules/walk/quick/"]').first.click()
            page.locator('#quick-content button[type="submit"]').click()
            expect(page.locator('#quick-dialog')).not_to_be_visible()
            expect(page.locator('[data-elapsed]')).to_be_visible()
            walk=Activity.objects.filter(room=room,module='walk').latest('created_at')
            page.locator('.ongoing-card a[data-quick]').click()
            page.locator('#quick-content button[type="submit"]').click()
            expect(page.locator('#quick-dialog')).not_to_be_visible()
            expect(page.locator('.ongoing-card')).to_have_count(0)
            walk.refresh_from_db();assert walk.status=='done' and walk.revisions.count()==2
            checkpoint('walk-inline-start-elapsed-finish-single-record')
            # Activity-specific intervals; monthly interval and one-time snooze are separate.
            page.goto(BASE+roompath+'cadences/new/?activity=hygiene:task:teeth')
            page.locator('#id_interval').fill('1');page.locator('#id_unit').select_option('days')
            page.locator('#id_title').fill('매일 양치')
            page.get_by_role('button',name='주기 저장',exact=True).click()
            page.wait_for_url(BASE+roompath+'cadences/')
            CareCadence.objects.get(room=room,variant_value='teeth')
            page.goto(BASE+roompath+'cadences/new/?activity=hygiene:task:bath')
            page.locator('#id_interval').fill('1');page.locator('#id_unit').select_option('months')
            page.locator('#id_title').fill('목욕 관리')
            page.get_by_role('button',name='주기 저장',exact=True).click()
            page.wait_for_url(BASE+roompath+'cadences/')
            bath=CareCadence.objects.get(room=room,variant_value='bath')
            page.goto(BASE+roompath)
            expect(page.locator('.due-section .cadence-row')).to_have_count(2)
            page.locator('.cadence-row').filter(has_text='매일 양치').locator('a[data-quick]').click()
            expect(page.locator('#quick-content [name="task"]')).to_have_value('teeth')
            page.locator('#quick-content button[type="submit"]').click()
            expect(page.locator('#quick-dialog')).not_to_be_visible()
            expect(page.locator('.due-section .cadence-row')).to_have_count(1)
            expect(page.locator('.due-section')).to_contain_text('목욕 관리')
            page.locator('[data-feedback-undo]').click()
            expect(page.locator('.due-section .cadence-row')).to_have_count(2)
            page.locator('.cadence-row').filter(has_text='목욕 관리').locator('form[data-snooze] button').click()
            expect(page.locator('.due-section .cadence-row')).to_have_count(1)
            bath.refresh_from_db();assert bath.interval==1 and bath.unit=='months' and bath.snoozed_until is not None
            checkpoint('activity-specific-cadence-completion-undo-and-snooze')
            # Broader module inputs in real browser, plus advanced-field disclosure.
            page.goto(BASE+roompath+'modules/settings/')
            for key in MODULES:
                add=page.locator(f'[data-add-module="{key}"]')
                if add.is_enabled():add.click();saved()
            checkpoint('all-twenty-modules-available-via-catalog')
            for key in MODULES:
                page.goto(BASE+roompath+f'modules/{key}/')
                page.locator('a[data-quick]').first.click()
                expect(page.locator('#quick-dialog')).to_be_visible()
                expect(page.locator('#quick-content form[data-quick-form]')).to_be_visible()
                values=record_data(key)
                if key=='reproduction':values.update(kind='environment_change',detail='검증용 생활 공간 변경',eggs='')
                # Use form's current generated timestamp; values represent explicit user input.
                for name,value in values.items():
                    if name in ('client_key','status','occurred_at','note'):continue
                    field=page.locator('#quick-content [name="'+name+'"]')
                    if not field.count() or field.first.get_attribute('type')=='hidden':continue
                    field=field.first
                    if not field.is_visible():page.locator('#quick-content .quick-extra > summary').click()
                    tag=field.evaluate('(el)=>el.tagName')
                    if tag=='SELECT':field.select_option(str(value))
                    else:field.fill(str(value))
                page.locator('#quick-content button[type="submit"]').click()
                expect(page.locator('#quick-dialog')).not_to_be_visible()
                latest=Activity.objects.filter(room=room,module=key).latest('created_at')
                assert latest.payload.get('_entry_mode')=='quick'
                checkpoint('browser-quick-save-'+key)
            # Explicit history reuse, never an automatic measurement.
            page.goto(BASE+roompath+'modules/meal/')
            page.locator('a[data-quick]').first.click()
            expect(page.locator('#quick-content [name="quantity"]')).to_have_value('')
            page.locator('.recent-presets summary').click()
            page.locator('.preset-choice').first.click()
            expect(page.locator('#quick-content [name="quantity"]')).not_to_have_value('')
            page.locator('[data-quick-close]').first.click()
            checkpoint('recent-values-require-explicit-selection')
            # Configuration request failure and conflict both keep the local state.
            page.goto(BASE+roompath+'modules/settings/')
            context.route('**/modules/config/',abort_post)
            page.locator('[data-pin-module="expense"]').set_checked(True)
            expect(config).to_have_attribute('data-status','error')
            expect(page.locator('[data-pin-module="expense"]')).to_be_checked()
            context.unroute('**/modules/config/',abort_post)
            page.locator('#config-retry').click();saved()
            checkpoint('configuration-network-failure-preserves-state-and-retries')
            other=context.new_page();other.on('dialog',lambda d:d.accept());other.goto(BASE+roompath+'modules/settings/')
            page.locator('[data-pin-module="expense"]').set_checked(False);saved()
            other.locator('[data-pin-module="weight"]').set_checked(False)
            expect(other.locator('[data-module-config]')).to_have_attribute('data-status','error')
            expect(other.locator('[data-pin-module="weight"]')).not_to_be_checked()
            expect(other.locator('#config-status')).to_contain_text('다른 참여자')
            other.locator('#config-reload').click()
            expect(other.locator('[data-module-config]')).to_have_attribute('data-status','saved')
            expect(other.locator('[data-pin-module="expense"]')).not_to_be_checked()
            # The confirmed reload adopts the latest server configuration.
            other.close(run_before_unload=False)
            checkpoint('concurrent-configuration-conflict-does-not-overwrite')
            # Remove/re-add an activity; old record IDs and values stay intact.
            count=Activity.objects.filter(room=room,module='meal').count()
            page.locator('.chosen-row[data-key="meal"] [data-remove-module]').click();saved()
            assert Activity.objects.filter(room=room,module='meal').count()==count
            page.locator('[data-add-module="meal"]').click();saved()
            assert Activity.objects.filter(room=room,module='meal').count()==count
            checkpoint('remove-and-readd-keeps-record-history')
            # Genuine touch input (CDP) is routed to the browser, not a DOM reorder shortcut.
            mobile_context=browser.new_context(viewport={'width':390,'height':844},locale='ko-KR',timezone_id='Asia/Seoul',is_mobile=True,has_touch=True,storage_state=context.storage_state())
            m=mobile_context.new_page();m.set_default_timeout(12000);m.on('dialog',lambda d:d.accept());m.on('pageerror',lambda e:RESULT['page_errors'].append(str(e)))
            m.goto(BASE+roompath+'modules/settings/',wait_until='networkidle')
            original=m.locator('#chosen-modules .chosen-row').evaluate_all('(r)=>r.map(e=>e.dataset.key)')
            m.locator('#chosen-modules .chosen-row').first.scroll_into_view_if_needed()
            a=m.locator('#chosen-modules .chosen-row').nth(1).locator('.drag-handle').bounding_box();b=m.locator('#chosen-modules .chosen-row').first.bounding_box()
            x=a['x']+a['width']/2;y=a['y']+a['height']/2;dest=b['y']+12
            cdp=mobile_context.new_cdp_session(m)
            cdp.send('Input.dispatchTouchEvent',{'type':'touchStart','touchPoints':[{'x':x,'y':y,'id':1}]})
            for step in range(1,13):
                cdp.send('Input.dispatchTouchEvent',{'type':'touchMove','touchPoints':[{'x':x,'y':y+(dest-y)*step/12,'id':1}]})
                m.wait_for_timeout(16)
            cdp.send('Input.dispatchTouchEvent',{'type':'touchEnd','touchPoints':[]})
            expect(m.locator('[data-module-config]')).to_have_attribute('data-status','saved')
            current=m.locator('#chosen-modules .chosen-row').evaluate_all('(r)=>r.map(e=>e.dataset.key)')
            assert current[0]==original[1] and current[1]==original[0],(original,current)
            m.reload(wait_until='networkidle')
            assert m.locator('#chosen-modules .chosen-row').first.get_attribute('data-key')==original[1]
            checkpoint('touch-drag-live-reflow-save-and-reload')
            m.goto(BASE+roompath);m.locator('.sidebar-restore').tap()
            expect(m.locator('#sidebar')).to_have_attribute('aria-hidden','false')
            m.keyboard.press('Escape')
            expect(m.locator('#sidebar')).to_have_attribute('aria-hidden','true')
            checkpoint('mobile-sidebar-drawer-close-and-focus')
            m.locator(f'a[data-quick][href="{roompath}modules/meal/quick/"]').first.tap()
            expect(m.locator('#quick-dialog')).to_be_visible()
            m.screenshot(path=str(OUT/'quick-mobile.png'),full_page=False)
            m.locator('[data-quick-close]').first.tap()
            mobile_context.close()
            # Layout checks use the same real content at six sizes.
            for width in (360,390,768,1024,1440,1920):
                page.set_viewport_size({'width':width,'height':1000})
                for path in ('','modules/','modules/settings/','records/','routines/','cadences/','overview/','modules/weight/statistics/','members/','settings/'):
                    response=page.goto(BASE+roompath+path,wait_until='networkidle')
                    assert response.status==200,(path,response.status)
                    overflow=page.evaluate('document.documentElement.scrollWidth>innerWidth+1')
                    RESULT['layout'].append({'width':width,'path':path,'overflow':overflow})
                    assert not overflow,(width,path)
                    assert page.locator('h1').count()==1
                    if (width,path) in ((1440,''),(390,''),(1440,'overview/'),(390,'overview/'),(1440,'modules/settings/'),(390,'modules/settings/'),(1440,'records/')):
                        page.screenshot(path=str(OUT/(('today' if not path else path.strip('/').replace('/','-'))+'-'+str(width)+'.png')),full_page=True)
                checkpoint('responsive-pages-'+str(width))
            assert not RESULT['page_errors'],RESULT['page_errors']
            checkpoint('no-javascript-exceptions')
        except Exception as error:
            RESULT['exception']=type(error).__name__+': '+str(error)
            RESULT['traceback']=traceback.format_exc()
            try:
                page.screenshot(path=str(OUT/'failure.png'),full_page=True)
                (OUT/'failure.html').write_text(page.content(),encoding='utf8')
            except Exception:pass
        finally:
            connections.close_all()
            browser.close()
            server.terminate()
            try:server.wait(timeout=10)
            except subprocess.TimeoutExpired:server.kill()
            log.close()
            (OUT/'results.json').write_text(json.dumps(RESULT,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'checks':len(RESULT['checks']),'layouts':len(RESULT['layout']),'exception':RESULT.get('exception')},ensure_ascii=False))
    if RESULT.get('exception'):raise SystemExit(1)

if __name__=='__main__':main()
