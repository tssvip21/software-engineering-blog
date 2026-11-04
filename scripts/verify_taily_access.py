"""Browser permission and overview checks, restricted to the isolated test database."""
from pathlib import Path
import json
import os
import socket
import subprocess
import sys
import time
import traceback
import uuid

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'tmp/handoff/phase2/browser-data'
OUT = ROOT / 'tmp/handoff/phase2/access-browser'
BASE = 'http://127.0.0.1:8013'
OUT.mkdir(parents=True, exist_ok=True)
assert (DATA / 'demo.sqlite3').is_file(), 'Prepare the isolated browser test data first.'
os.environ.update(DJANGO_SETTINGS_MODULE='config.settings', CARE_DATA_DIR=str(DATA), CARE_DEMO_MODE='1',
                  DJANGO_DEBUG='1', APP_ORIGIN=BASE, DJANGO_ALLOWED_HOSTS='127.0.0.1,localhost,testserver')
sys.path.insert(0, str(ROOT))
import django
django.setup()
from django.contrib.auth import get_user_model
from django.db import connections
from care.models import Activity, Membership, Room
from playwright.sync_api import sync_playwright, expect

RESULT = {'checks': [], 'page_errors': [], 'expected_denials': []}
def checkpoint(name):
    RESULT['checks'].append(name)
    (OUT / 'results.json').write_text(json.dumps(RESULT, ensure_ascii=False, indent=2), encoding='utf8')

def main():
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1', 8013)) == 0:
            raise RuntimeError('Port 8013 already in use; refusing to affect an existing process.')
    source = json.loads((ROOT / 'tmp/handoff/phase2/browser/results.json').read_text(encoding='utf8'))
    roompath = source['room_path']
    room = Room.objects.get(pk=roompath.split('/')[2])
    assert room.name.startswith('테일리검증-') and not room.guest_sensitive
    owner = room.members.get(role='owner')
    admin, _ = Membership.objects.get_or_create(room=room, user=get_user_model().objects.get(username='demo_admin'),
                                                defaults={'display_name': '검증 관리자', 'role': 'admin'})
    assert admin.role == 'admin'
    Activity.objects.filter(room=room, module='medication').update(note='검증용 비공개 투약 메모')
    log = (OUT / 'server.log').open('w', encoding='utf8')
    server = subprocess.Popen([sys.executable, 'manage.py', 'runserver', '127.0.0.1:8013', '--noreload'],
                              cwd=ROOT, env=os.environ.copy(), stdout=log, stderr=subprocess.STDOUT)
    os.environ['DJANGO_ALLOW_ASYNC_UNSAFE'] = '1'
    try:
        for _ in range(80):
            with socket.socket() as probe:
                if probe.connect_ex(('127.0.0.1', 8013)) == 0: break
            if server.poll() is not None: raise RuntimeError('Test server failed to start.')
            time.sleep(.1)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            contexts = []
            def tab():
                c = browser.new_context(viewport={'width':1440, 'height':1000}, locale='ko-KR', timezone_id='Asia/Seoul')
                contexts.append(c)
                page = c.new_page(); page.set_default_timeout(12000)
                page.on('pageerror', lambda e: RESULT['page_errors'].append(str(e)))
                page.on('dialog', lambda d: d.accept())
                return c, page
            context, page = tab()
            page.goto(BASE + '/'); page.get_by_role('button', name='방장으로 보기', exact=True).click()
            page.wait_for_url(BASE + '/')
            page.goto(BASE + roompath + 'overview/')
            expect(page.locator('[data-summary-primary] > article')).to_have_count(4)
            more = page.locator('.summary-more'); expect(more).to_be_visible()
            assert more.get_attribute('open') is None
            expect(more.locator('article').first).not_to_be_visible()
            more.locator('summary').first.click()
            expect(more.locator('article').first).to_be_visible()
            link = more.locator('.quiet-link').first.get_attribute('href')
            assert context.request.get(BASE + link).status == 200
            checkpoint('overview-four-primary-and-expand-keeps-all-details')
            for width in (390, 1440):
                page.set_viewport_size({'width':width, 'height':1000})
                page.goto(BASE + roompath + 'overview/', wait_until='networkidle')
                assert not page.evaluate('document.documentElement.scrollWidth > innerWidth + 1')
                page.screenshot(path=str(OUT / f'overview-final-{width}.png'), full_page=True)
            checkpoint('overview-final-desktop-and-mobile')
            page.set_viewport_size({'width':1440, 'height':1000})
            page.goto(BASE + roompath + 'members/')
            page.get_by_role('button', name='초대 링크 만들기', exact=True).click()
            invite = page.locator('#created-invite').input_value()
            gc, guest = tab(); guest.goto(invite, wait_until='networkidle')
            expect(guest.locator('h1')).to_contain_text('돌봄에 참여')
            guest.locator('#id_display_name').fill('게스트검증-' + uuid.uuid4().hex[:5])
            guest.locator('input[type="checkbox"]').check()
            guest.get_by_role('button', name='참여하기', exact=True).click()
            guest.wait_for_url(BASE + roompath)
            checkpoint('guest-real-invitation-and-explicit-sharing-consent')
            guest.goto(BASE + roompath + 'overview/')
            expect(guest.locator('main')).not_to_contain_text('투약 확인')
            expect(guest.locator('main')).not_to_contain_text('검증용 비공개 투약 메모')
            expect(guest.locator('main')).not_to_contain_text('병원·예방접종 확인')
            expect(guest.locator('main a[href*="/medication/"]')).to_have_count(0)
            checkpoint('guest-integrated-summary-keeps-sensitive-data-private')
            for path in ('modules/config/', 'cadences/new/', 'modules/medication/quick/', 'modules/expense/statistics/'):
                response = gc.request.get(BASE + roompath + path)
                RESULT['expected_denials'].append({'path':path, 'status':response.status})
                assert response.status == 403, (path, response.status)
            token = next(x['value'] for x in gc.cookies() if x['name'] == 'care_demo_csrf')
            denied = gc.request.post(BASE + roompath + 'modules/config/', data={}, headers={'X-CSRFToken':token, 'Content-Type':'application/json', 'Origin':BASE})
            assert denied.status == 403
            checkpoint('guest-cannot-bypass-management-or-sensitive-access-via-endpoints')
            guest.goto(BASE + roompath + 'modules/meal/')
            guest.locator('a[data-quick]').first.click()
            guest.locator('#quick-content [name="food_name"]').fill('게스트검증급여')
            guest.locator('#quick-content [name="quantity"]').fill('12')
            guest.locator('#quick-content button[type="submit"]').click()
            expect(guest.locator('#quick-dialog')).not_to_be_visible()
            created = Activity.objects.filter(room=room, module='meal', payload__food_name='게스트검증급여').latest('created_at')
            assert created.author.role == 'guest' and created.author_id != owner.pk
            page.goto(BASE + roompath + 'records/')
            expect(page.locator('main')).to_contain_text('게스트검증급여')
            checkpoint('guest-quick-entry-shares-the-authoritative-record-with-owner')
            ac, ap = tab(); ap.goto(BASE + '/'); ap.get_by_role('button', name='관리자로 보기', exact=True).click(); ap.wait_for_url(BASE + '/')
            ap.goto(BASE + roompath + 'modules/settings/')
            expect(ap.locator('[data-module-config]')).to_be_visible()
            ap.locator('[data-pin-module="weight"]').set_checked(False)
            expect(ap.locator('[data-module-config]')).to_have_attribute('data-status', 'saved')
            ap.goto(BASE + roompath + 'overview/')
            expect(ap.locator('main')).to_contain_text('투약 확인')
            ap.goto(BASE + roompath + 'members/')
            expect(ap.get_by_role('button', name='초대 링크 만들기', exact=True)).to_have_count(0)
            checkpoint('admin-can-configure-and-see-sensitive-data-but-cannot-invite')
            page.goto(BASE + roompath + 'members/')
            member = room.members.get(pk=created.author_id)
            row = page.locator('.member-row').filter(has_text=member.display_name)
            row.locator('summary').click()
            row.get_by_role('button', name='내보내기', exact=True).click()
            assert gc.request.get(BASE + roompath + 'modules/meal/quick/').status == 404
            assert Activity.objects.filter(pk=created.pk, room=room).exists()
            checkpoint('member-removal-revokes-new-ui-access-without-deleting-shared-history')
            outsiderc, outsider = tab(); outsider.goto(BASE + '/'); outsider.get_by_role('button', name='빈 계정으로 시작', exact=True).click(); outsider.wait_for_url(BASE + '/')
            assert outsiderc.request.get(BASE + roompath).status == 404
            checkpoint('unrelated-account-cannot-open-another-room')
            for c in contexts: c.close()
            browser.close()
            assert not RESULT['page_errors'], RESULT['page_errors']
            checkpoint('no-unhandled-javascript-errors-in-permission-flows')
    except Exception as e:
        RESULT['exception'] = type(e).__name__ + ': ' + str(e)
        RESULT['traceback'] = traceback.format_exc()
    finally:
        connections.close_all(); server.terminate()
        try: server.wait(timeout=10)
        except subprocess.TimeoutExpired: server.kill(); server.wait(timeout=5)
        log.close()
        (OUT / 'results.json').write_text(json.dumps(RESULT, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps({'checks':len(RESULT['checks']), 'exception':RESULT.get('exception')}, ensure_ascii=False))
    if RESULT.get('exception'): raise SystemExit(1)

if __name__ == '__main__': main()
