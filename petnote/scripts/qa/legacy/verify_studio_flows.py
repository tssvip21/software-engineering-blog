"""Cross-feature UI regression checks in a separate copy, never the review or original DB."""
from demo_browser_session import select_fixture_role
from contextlib import closing
from datetime import timedelta
from pathlib import Path
import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
import traceback
import uuid

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "manage.py").is_file())
OUT = ROOT / 'tmp/handoff/phase5-round2/flows'
BASE = 'http://127.0.0.1:8024'
RESULT = {'checks': [], 'errors': []}


def main():
    with socket.socket() as sock:
        if sock.connect_ex(('127.0.0.1', 8024)) == 0:
            raise RuntimeError('Port occupied; no existing server stopped')
    OUT.mkdir(parents=True, exist_ok=True)
    source = json.loads((ROOT/'tmp/handoff/phase5-round2/browser/results.json').read_text(encoding='utf8'))
    data = ROOT/'tmp/handoff/phase5-round2'/('flow-data-'+uuid.uuid4().hex[:8])
    data.mkdir()
    with closing(sqlite3.connect((Path(source['data'])/'demo.sqlite3').as_uri()+'?mode=ro', uri=True)) as src, closing(sqlite3.connect(data/'demo.sqlite3')) as dst:
        src.backup(dst)
    env = os.environ.copy()
    env.update(DJANGO_SETTINGS_MODULE='config.settings', CARE_DATA_DIR=str(data), CARE_DEMO_MODE='1',
               DJANGO_DEBUG='1', APP_ORIGIN=BASE, DJANGO_ALLOWED_HOSTS='localhost,127.0.0.1,testserver', PYTHONUTF8='1')
    os.environ.update(env)
    sys.path.insert(0, str(ROOT))
    import django
    django.setup()
    from django.contrib.auth import get_user_model
    from django.db import connections
    from django.utils import timezone
    from care import services
    from care.models import Room, Activity, Membership, Routine, RoutineItem, RoutineVersion, RoutineRun, CareCadence
    from care.forms import RecordForm
    from care.demo_fixtures import record_data
    room = Room.objects.get(pk=source['rooms']['rich'])
    owner = room.members.get(role='owner')
    day = services.local_date(room)
    member_user = get_user_model().objects.get(username='demo_member')
    person, _ = Membership.objects.get_or_create(room=room, user=member_user, defaults={'display_name':'검수 참여자', 'role':'guest'})
    person.role='guest';person.active=True;person.save(update_fields=['role','active'])
    routine = Routine.objects.create(room=room, name='연결 확인용 루틴', at='19:00', weekdays=list(range(7)), start_date=day, assignee=owner)
    item = RoutineItem.objects.create(routine=routine, module='meal', position=0)
    routine.refresh_from_db()
    RoutineVersion.objects.create(routine=routine, effective_on=day, snapshot=services.routine_snapshot(routine))
    form = RecordForm(record_data('meal', food_name='루틴 연결 검수 먹이'), room=room, module='meal', member=owner)
    assert form.is_valid(), form.errors
    record = services.save_activity(owner,'meal',form)[0]
    cadence, _ = CareCadence.objects.get_or_create(room=room, module='grooming', variant_field='task', variant_value='nail',
        defaults={'title':'발톱 검수 알림','interval':21,'unit':'days','first_due':day,'configured_at':timezone.now()})
    cadence.first_due=day;cadence.configured_at=timezone.now();cadence.snoozed_until=None;cadence.snoozed_at=None;cadence.save()
    connections.close_all()
    path = source['room_path']
    log=(OUT/'server.log').open('w',encoding='utf8')
    server=subprocess.Popen([sys.executable,'manage.py','runserver','127.0.0.1:8024','--noreload'],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
    page=None
    try:
        for _ in range(100):
            with socket.socket() as s:
                if s.connect_ex(('127.0.0.1',8024))==0:break
            if server.poll() is not None:raise RuntimeError('Test server exited')
            time.sleep(.1)
        os.environ['DJANGO_ALLOW_ASYNC_UNSAFE']='1'  # Test-side assertions only.
        from playwright.sync_api import sync_playwright, expect
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True)
            context=browser.new_context(viewport={'width':1440,'height':1000},locale='ko-KR',reduced_motion='reduce')
            page=context.new_page();page.set_default_timeout(10000);page.on('dialog',lambda d:d.accept());page.on('pageerror',lambda e:RESULT['errors'].append(str(e)))
            page.goto(BASE);select_fixture_role(page, 'owner')
            page.goto(BASE+path+'groups/',wait_until='networkidle');page.locator('[data-group-add]').click()
            last=page.locator('.group-editor-row').last;last.locator('[data-group-name]').fill('5차 확인 그룹');last.locator('[data-group-key="meal"]').check();last.locator('[data-group-key="walk"]').check()
            endpoint=page.locator('#group-editor').get_attribute('data-endpoint')
            def offline(route):
                route.abort('internetdisconnected') if route.request.method=='POST' else route.continue_()
            context.route('**'+endpoint,offline);page.get_by_role('button',name='변경 저장',exact=True).click()
            expect(page.locator('#group-editor')).to_have_attribute('data-status','error');expect(last.locator('[data-group-name]')).to_have_value('5차 확인 그룹')
            context.unroute('**'+endpoint,offline);page.get_by_role('button',name='변경 저장',exact=True).click();expect(page.locator('#group-editor')).to_have_attribute('data-status','saved')
            RESULT['checks'].append('group-network-failure-keeps-input-and-retries')
            second=context.new_page();second.set_default_timeout(10000);second.on('dialog',lambda d:d.accept());second.goto(BASE+path+'groups/',wait_until='networkidle')
            page.locator('[data-group-name]').last.fill('먼저 저장한 그룹');page.get_by_role('button',name='변경 저장',exact=True).click();expect(page.locator('#group-editor')).to_have_attribute('data-status','saved')
            second.locator('[data-group-name]').last.fill('충돌한 내 입력');second.get_by_role('button',name='변경 저장',exact=True).click();expect(second.locator('#group-editor')).to_have_attribute('data-status','error');expect(second.locator('[data-group-name]').last).to_have_value('충돌한 내 입력');second.locator('[data-group-reload]').click();expect(second.locator('[data-group-name]').last).to_have_value('먼저 저장한 그룹');second.close()
            RESULT['checks'].append('group-two-tab-conflict-does-not-overwrite')
            page.goto(BASE+path+'routines/',wait_until='networkidle');before=Activity.objects.filter(room=room).count()
            target=page.locator('.routine-card').filter(has_text='연결 확인용 루틴')
            target.get_by_role('link',name='기존 기록 연결',exact=True).click();page.locator(f'input[name="activity"][value="{record.pk}"]').check();page.get_by_role('button',name='이 기록 연결',exact=True).click();page.wait_for_load_state('networkidle')
            assert Activity.objects.filter(room=room).count()==before
            assert RoutineRun.objects.filter(item=item,date=day,activity=record).exists()
            page.goto(BASE+path+'records/'+str(record.pk)+'/');page.locator('.danger-zone>summary').click();page.locator('#id_reason').fill('연결 해제 검수');page.get_by_role('button',name='기록 취소',exact=True).click();page.wait_for_load_state('networkidle')
            record.refresh_from_db();assert record.voided and not RoutineRun.objects.filter(item=item,activity=record).exists()
            RESULT['checks'].append('routine-link-and-cancel-share-one-record')
            page.goto(BASE+path+'cadences/',wait_until='networkidle');row=page.locator(f'[data-cadence="{cadence.pk}"]');row.get_by_role('button',name='내일로 미루기',exact=True).click();expect(page.locator('[data-feedback-message]')).to_contain_text('미뤘어요');cadence.refresh_from_db();assert cadence.interval==21 and cadence.snoozed_until==day+timedelta(days=1)
            RESULT['checks'].append('cadence-snooze-keeps-interval')
            page.goto(BASE+path+'members/',wait_until='networkidle')
            row=page.locator('#main .member-row').filter(has_text=person.display_name);row.locator('.member-menu>summary').click()
            with page.expect_navigation(wait_until='networkidle'):row.get_by_role('button',name='관리자로 지정',exact=True).click()
            person.refresh_from_db();assert person.role=='admin'
            row=page.locator('#main .member-row').filter(has_text=person.display_name);row.locator('.member-menu>summary').click()
            with page.expect_navigation(wait_until='networkidle'):row.get_by_role('button',name='관리자 권한 해제',exact=True).click()
            person.refresh_from_db();assert person.role=='guest'
            RESULT['checks'].append('member-menu-promote-demote-with-server-role')
            page.get_by_role('button',name='초대 링크 만들기',exact=True).click();expect(page.locator('#created-invite')).to_be_visible();invite=page.locator('#created-invite').input_value()
            guest=browser.new_context(viewport={'width':390,'height':844},locale='ko-KR',reduced_motion='reduce');g=guest.new_page();g.set_default_timeout(10000);g.goto(invite,wait_until='networkidle');g.locator('#id_display_name').fill('검수 새 게스트')
            g.locator('#id_agree').check()
            with g.expect_navigation(wait_until='networkidle'):
                g.get_by_role('button',name='참여하기',exact=True).click()
            expect(g).to_have_url(BASE+path)
            assert g.request.get(BASE+path+'modules/settings/').status==403
            g.goto(BASE+path+'modules/',wait_until='networkidle');g.locator('[data-module-key="meal"] a[data-quick]').click();g.locator('#quick-content button[type="submit"]').click();expect(g.locator('#quick-dialog')).not_to_be_visible()
            new=Membership.objects.get(room=room,display_name='검수 새 게스트');saved=Activity.objects.filter(author=new).count();assert saved==1
            page.goto(BASE+path+'members/',wait_until='networkidle');row=page.locator('#main .member-row').filter(has_text='검수 새 게스트');row.locator('.member-menu>summary').click()
            with page.expect_navigation(wait_until='networkidle'):row.get_by_role('button',name='이 공간에서 내보내기',exact=True).click()
            new.refresh_from_db();assert not new.active and Activity.objects.filter(author=new).count()==saved
            assert g.request.get(BASE+path).status==404;guest.close()
            RESULT['checks'].append('invite-guest-record-remove-preserves-authored-record')
            page.goto(BASE+path+'modules/settings/',wait_until='networkidle');first=page.locator('#chosen-modules .chosen-row').first;last=page.locator('#chosen-modules .chosen-row').nth(2);moved=first.get_attribute('data-key');first.locator('.drag-handle').scroll_into_view_if_needed();a=first.locator('.drag-handle').bounding_box();b=last.bounding_box()
            page.mouse.move(a['x']+20,a['y']+20);page.mouse.down();page.mouse.move(b['x']+b['width']/2,b['y']+b['height']/2,steps=20);page.mouse.up();expect(page.locator('[data-module-config]')).to_have_attribute('data-status','saved');page.reload(wait_until='networkidle');assert page.locator('#chosen-modules .chosen-row').nth(2).get_attribute('data-key')==moved
            RESULT['checks'].append('mouse-drag-persists-in-desktop-config')
            separator=page.locator('#sidebar-resize');separator.focus();page.keyboard.press('End');expect(separator).to_have_attribute('aria-valuenow','360');page.reload(wait_until='networkidle');expect(page.locator('#sidebar-resize')).to_have_attribute('aria-valuenow','360');page.locator('#sidebar [data-sidebar-toggle]').click();expect(page.locator('body')).to_have_class(re.compile('sidebar-closed'));page.reload(wait_until='networkidle');page.locator('.workspace-topbar [data-sidebar-toggle]').click();expect(page.locator('#sidebar')).to_be_visible()
            RESULT['checks'].append('desktop-sidebar-resize-and-collapse-persist')
            assert not RESULT['errors'],RESULT['errors'];browser.close()
    except Exception as exc:
        RESULT['exception']=repr(exc);RESULT['traceback']=traceback.format_exc()
        if page:
            try:page.screenshot(path=str(OUT/'failure.png'),full_page=True)
            except Exception:pass
    finally:
        connections.close_all();server.terminate()
        try:server.wait(timeout=10)
        except subprocess.TimeoutExpired:server.kill()
        log.close();(OUT/'results.json').write_text(json.dumps(RESULT,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'checks':len(RESULT['checks']),'exception':RESULT.get('exception')},ensure_ascii=False))
    return 1 if RESULT.get('exception') else 0


if __name__=='__main__':
    import re
    raise SystemExit(main())
