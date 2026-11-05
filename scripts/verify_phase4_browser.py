"""Fourth-iteration browser and developer task inspection on a disposable DB only."""
from contextlib import closing
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import json, os, re, socket, sqlite3, subprocess, sys, time, traceback, uuid
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'tmp/handoff/phase4/browser';OUT.mkdir(parents=True,exist_ok=True)
DATA=ROOT/'tmp/handoff/phase4'/('browser-data-'+uuid.uuid4().hex[:8]);DATA.mkdir()
SOURCE=ROOT/'data/demo.sqlite3'
if SOURCE.exists():
    with closing(sqlite3.connect(SOURCE.as_uri()+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(DATA/'demo.sqlite3')) as dst:src.backup(dst)
PORT=8016;BASE=f'http://127.0.0.1:{PORT}'
os.environ.update(DJANGO_SETTINGS_MODULE='config.settings',CARE_DATA_DIR=str(DATA),CARE_DEMO_MODE='1',DJANGO_DEBUG='1',APP_ORIGIN=BASE,DJANGO_ALLOWED_HOSTS='127.0.0.1,localhost,testserver',PYTHONUTF8='1')
sys.path.insert(0,str(ROOT))
RESULT={'checks':[],'layout':[],'accessibility':[],'page_errors':[],'network_hosts':[],'tasks':[],'data':str(DATA)}
def checkpoint(name,detail=None):
    RESULT['checks'].append({'name':name,'detail':detail});save()
def save():
    (OUT/'results.json').write_text(json.dumps(RESULT,ensure_ascii=False,indent=2,default=str),encoding='utf8')
def main():
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1',PORT))==0:raise RuntimeError('Verification port occupied; existing processes are not stopped.')
    for args in [('migrate','--noinput'),('seed_demo',),('seed_references',)]:
        proc=subprocess.run([sys.executable,'manage.py',*args],cwd=ROOT,env=os.environ.copy(),capture_output=True,text=True,encoding='utf8',timeout=60)
        (OUT/(args[0]+'.log')).write_text(proc.stdout+proc.stderr,encoding='utf8');assert proc.returncode==0,proc.stderr[-1000:]
    import django
    django.setup()
    from django.contrib.auth import get_user_model
    from django.db import connections
    from django.utils import timezone
    from care import services
    from care.models import Room,Activity,Membership
    from care.demo_fixtures import record_data
    from care.forms import RecordForm,RoomForm
    from care.modules import MODULES
    from playwright.sync_api import sync_playwright,expect
    log=(OUT/'server.log').open('w',encoding='utf8')
    server=subprocess.Popen([sys.executable,'manage.py','runserver',f'127.0.0.1:{PORT}','--noreload'],cwd=ROOT,env=os.environ.copy(),stdout=log,stderr=subprocess.STDOUT)
    for _ in range(100):
        with socket.socket() as probe:
            if probe.connect_ex(('127.0.0.1',PORT))==0:break
        if server.poll() is not None:raise RuntimeError('Verification server exited')
        time.sleep(.1)
    os.environ['DJANGO_ALLOW_ASYNC_UNSAFE']='1' # Test-side ORM only; spawned server does not inherit this.
    browser=page=None
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            context=browser.new_context(viewport={'width':1440,'height':1000},locale='ko-KR',timezone_id='Asia/Seoul')
            page=context.new_page();page.set_default_timeout(10000);page.on('dialog',lambda d:d.accept())
            page.on('pageerror',lambda e:RESULT['page_errors'].append(str(e)))
            page.on('request',lambda r:RESULT['network_hosts'].append(urlparse(r.url).hostname) if urlparse(r.url).hostname not in RESULT['network_hosts'] else None)
            page.goto(BASE+'/',wait_until='networkidle');page.get_by_role('button',name='방장으로 보기',exact=True).click()
            page.wait_for_url(BASE+'/');checkpoint('csrf-demo-login-and-korean-landing')
            page.goto(BASE+'/rooms/new/');page.locator('#id_name').fill('4차 검증');page.locator('#id_species').select_option('dog')
            page.locator('#breed-search').fill('포메');page.get_by_role('option',name=re.compile('포메라니안')).click()
            expect(page.locator('#id_size')).to_have_value('small')
            page.locator('#id_maturity').select_option('adult');page.get_by_role('button',name='돌봄 공간 만들기',exact=True).click()
            page.wait_for_url(re.compile(r'/rooms/[\da-f-]+/$'));path=urlparse(page.url).path
            room=Room.objects.get(pk=path.split('/')[2]);owner=room.members.get(role='owner')
            RESULT['room_path']=path;checkpoint('registration-breed-suggestion-still-saves')
            # Existing records, sorting and pins are independent of the local selection dialog.
            page.goto(BASE+path+'modules/',wait_until='networkidle')
            page.locator('[data-module-filter="living"]').click()
            button=page.locator('[data-category-manage="living"]');expect(button).to_be_visible()
            before={s.key:(s.enabled,s.pinned,s.position) for s in room.module_settings.all()}
            endpoint=button.get_attribute('data-endpoint');url=page.url
            button.click();expect(page.locator('#category-dialog')).to_be_visible()
            expect(page.locator('[data-category-choice]')).to_have_count(6)
            assert page.url==url
            page.keyboard.press('Escape');expect(page.locator('#category-dialog')).not_to_be_visible();expect(button).to_be_focused()
            checkpoint('empty-category-discoverable-dialog-scoped-escape-focus-return')
            button.click();page.locator('[data-category-choice][value="cleaning"]').check()
            page.locator('#category-form button[type="submit"]').click();expect(page.locator('#category-dialog')).not_to_be_visible()
            expect(page.locator('[data-module-filter="living"]')).to_have_attribute('aria-pressed','true')
            expect(page.locator('[data-module-key="cleaning"]')).to_be_visible();assert page.url==url
            for s in room.module_settings.all():
                assert (s.pinned,s.position)==before[s.key][1:]
                if s.key not in ('hygiene','grooming','cleaning','environment','inventory','expense'):assert s.enabled==before[s.key][0]
            checkpoint('category-apply-inline-preserves-order-pins-other-categories-and-location')
            RESULT['tasks'].append({'task':'청소 기능 추가 후 현재 분류에서 확인','actions':['분류의 기능 선택','청소 선택','적용'],'page_transitions':0,'selection_pool':6,'result_visible':True})
            button.click();page.locator('[data-category-choice][value="cleaning"]').uncheck();page.locator('[data-category-close]').last.click()
            assert room.module_settings.get(key='cleaning').enabled
            checkpoint('dirty-cancel-keeps-server-selection')
            def fail_post(route):
                if route.request.method=='POST':route.abort('internetdisconnected')
                else:route.continue_()
            context.route('**'+endpoint,fail_post)
            button.click();page.locator('[data-category-choice][value="hygiene"]').check();page.locator('#category-form button[type="submit"]').click()
            expect(page.locator('#category-dialog')).to_have_attribute('data-status','error')
            expect(page.locator('[data-category-choice][value="hygiene"]')).to_be_checked()
            context.unroute('**'+endpoint,fail_post);page.locator('#category-form button[type="submit"]').click();expect(page.locator('#category-dialog')).not_to_be_visible()
            assert room.module_settings.get(key='hygiene').enabled
            checkpoint('failed-apply-preserves-selection-and-retry')
            button.click();expect(page.locator('[data-category-choice]')).to_have_count(6)
            other=context.new_page();other.goto(BASE+path+'modules/');other.locator('[data-category-manage="living"]').click()
            other.locator('[data-category-choice][value="grooming"]').check();other.locator('#category-form button[type="submit"]').click();expect(other.locator('#category-dialog')).not_to_be_visible()
            page.locator('[data-category-choice][value="environment"]').check();page.locator('#category-form button[type="submit"]').click()
            expect(page.locator('#category-dialog')).to_have_attribute('data-status','error');expect(page.locator('#category-status')).to_contain_text('다른 사람')
            assert not room.module_settings.get(key='environment').enabled
            page.locator('#category-reload').click();expect(page.locator('[data-category-choice][value="grooming"]')).to_be_checked();page.keyboard.press('Escape');other.close()
            checkpoint('concurrent-category-change-rejected-with-explicit-reload')
            # Save once; repeat delivery with a lost response must recognize the server result.
            def lose_response(route):
                if route.request.method=='POST':
                    response=route.fetch();assert response.status==200;route.abort('failed')
                else:route.continue_()
            context.route('**'+endpoint,lose_response)
            button.click();page.locator('[data-category-choice][value="environment"]').check();page.locator('#category-form button[type="submit"]').click();expect(page.locator('#category-dialog')).not_to_be_visible()
            assert room.module_settings.get(key='environment').enabled
            context.unroute('**'+endpoint,lose_response);checkpoint('saved-but-lost-response-confirmed-without-false-failure')
            page.locator('[data-module-filter="all"]').click()
            page.locator(f'a[data-quick][href="{path}modules/meal/quick/"]').first.click();expect(page.locator('#quick-content form')).to_be_visible()
            page.locator('#quick-content button[type="submit"]').click();expect(page.locator('#quick-dialog')).not_to_be_visible()
            page.locator('[data-feedback-undo]').click();expect(page.locator('[data-feedback-message]')).to_contain_text('취소')
            checkpoint('quick-save-and-undo-from-current-workspace')
            # Same 35-day numerical example as the pre-change walkthrough, with completed-day cutoff explicit.
            for offset in range(35):
                day=services.local_date(room)-timedelta(days=offset)
                for key,value in [('weight','9' if offset<7 else '8'),('meal','160' if offset<7 else '100'),('walk','10' if offset<7 else '30')]:
                    at=timezone.localtime().replace(year=day.year,month=day.month,day=day.day,hour=0,minute=0,second=0,microsecond=0)
                    values=record_data(key,occurred_at=at.strftime('%Y-%m-%dT%H:%M:%S'),note='비교용 기록')
                    values.update({'weight':value} if key=='weight' else {'quantity':value,'product':''} if key=='meal' else {'minutes':value})
                    f=RecordForm(values,room=room,module=key,member=owner);assert f.is_valid(),f.errors.as_json();services.save_activity(owner,key,f)
            page.goto(BASE+path+'records/',wait_until='networkidle')
            expect(page.locator('[data-category-first]')).to_be_visible();expect(page.locator('.record-collection')).to_have_count(4)
            assert page.locator('.history-date').count()==0
            page.locator('.record-kind-links a').filter(has_text='식사').first.click()
            assert parse_qs(urlparse(page.url).query)['module']==['meal']
            expect(page.locator('.category-history')).to_be_visible()
            page.locator('#record-query').fill('비교용');page.locator('.record-search button').first.click();page.wait_for_load_state('networkidle')
            page.locator('.pagination a').filter(has_text='다음').click();page.wait_for_load_state('networkidle')
            back=page.url;page.locator('[data-record-link]').first.click();page.wait_for_load_state('networkidle')
            expected=page.locator('[data-record-back]').get_attribute('href');assert BASE+expected==back
            page.locator('a.button.primary').first.click();page.locator('#id_note').fill('비교용 기록 수정')
            page.locator('button[type="submit"]').filter(has_text='기록 저장').click();page.wait_for_load_state('networkidle')
            page.locator('[data-record-back]').click();page.wait_for_load_state('networkidle');assert page.url==back
            checkpoint('category-first-history-search-pagination-edit-return-keeps-context')
            RESULT['tasks'].append({'task':'식사 기록 검색 후 수정하고 같은 목록으로 복귀','starting_structure':'분류별 기록 모음','category':'nutrition','module':'meal','context_preserved':['query','category','module','page'],'success':True})
            page.goto(BASE+path+'overview/',wait_until='networkidle')
            expect(page.locator('#overall-title')).to_contain_text('같은 시기')
            initial=page.locator('#main').inner_text()
            assert page.locator('#main svg polyline, #main svg path').count()==0
            for detail in page.locator('#main details').all():detail.evaluate('(e)=>e.open=true')
            all_text=page.locator('#main').inner_text()
            for term in ('중앙값','MAD','기울기','유효 관측','계산 방법','실측'):assert term not in all_text,term
            expect(page.locator('[data-result="weight"] .result-values')).to_be_visible()
            assert '9 kg' in page.locator('[data-result="weight"]').inner_text()
            RESULT['tasks'].append({'task':'최근 변화 의미와 관련 값 확인','initial_graphs':0,'initial_characters':len(initial),'expanded_has_method_jargon':False,'expansion_actions':1,'summary':page.locator('#overall-title').inner_text()})
            checkpoint('plain-conclusion-and-date-value-evidence-without-methods-or-required-graphs')
            User=get_user_model();membership,_=Membership.objects.get_or_create(room=room,user=User.objects.get(username='demo_member'),defaults={'display_name':'검증 참여자','role':'guest'})
            guest=browser.new_context(locale='ko-KR');g=guest.new_page();g.goto(BASE+'/');g.get_by_role('button',name='일반 참여자로 보기',exact=True).click();g.goto(BASE+path+'modules/')
            assert g.locator('[data-category-manage]').count()==0
            assert g.request.get(BASE+endpoint).status==403
            assert g.request.get(BASE+path+'records/?modules=medication').status==403
            g.goto(BASE+path+'overview/');expect(g.locator('#overall-title')).to_be_visible();guest.close();checkpoint('guest-management-and-sensitive-evidence-boundaries')
            # Density checks at few/many modules use the same database, not user records.
            room.refresh_from_db();services.update_modules(owner,list(MODULES),list(MODULES),room.settings_version,[])
            page.goto(BASE+path+'modules/');expect(page.locator('.module-tile')).to_have_count(20)
            page.locator('[data-module-filter="living"]').click();expect(page.locator('.module-tile:not([hidden])')).to_have_count(6)
            page.locator('[data-module-filter="all"]').click()
            checkpoint('twenty-modules-still-navigable-by-purpose')
            # Regression: pointer/keyboard ordering, no duplicate arrow controls.
            page.goto(BASE+path+'modules/settings/');assert page.locator('[data-order-step]').count()==0
            last=page.locator('#chosen-modules .drag-handle').last;key=last.locator('..').get_attribute('data-key')
            last.focus();page.keyboard.press('Space');page.keyboard.press('Home');page.keyboard.press('Enter')
            expect(page.locator('[data-module-config]')).to_have_attribute('data-status','saved');page.reload();assert page.locator('#chosen-modules .chosen-row').first.get_attribute('data-key')==key
            checkpoint('keyboard-module-order-persists-after-copy-and-layout-changes')
            pages=['','modules/','modules/settings/','groups/','records/','records/?category=nutrition','routines/','cadences/','overview/','members/','settings/','modules/weight/statistics/']
            for width in (320,390,768,1024,1440,1920):
                page.set_viewport_size({'width':width,'height':1000})
                for suffix in pages:
                    response=page.goto(BASE+path+suffix,wait_until='networkidle');assert response.status==200,(suffix,response.status)
                    overflow=page.evaluate('document.documentElement.scrollWidth>innerWidth+1')
                    RESULT['layout'].append({'width':width,'path':suffix,'overflow':overflow});assert not overflow,(width,suffix)
                    if width in (390,1440) and suffix in ('modules/','records/','records/?category=nutrition','overview/','routines/'):
                        page.screenshot(path=str(OUT/(suffix.strip('/').replace('/','-').replace('?','_').replace('=','-')+f'-{width}.png')),full_page=True)
                checkpoint('responsive-'+str(width))
            for width in (320,1440):
                page.set_viewport_size({'width':width,'height':1000});page.goto(BASE+path+'modules/',wait_until='networkidle')
                page.locator('[data-category-manage="living"]').click();expect(page.locator('[data-category-choice]')).to_have_count(6)
                for _ in range(12):
                    page.keyboard.press('Tab');assert page.evaluate('document.querySelector("#category-dialog").contains(document.activeElement)')
                page.locator('#category-form button[type="submit"]').scroll_into_view_if_needed()
                box=page.locator('#category-dialog').bounding_box();assert box['x']>=0 and box['x']+box['width']<=width+1
                page.screenshot(path=str(OUT/f'category-dialog-{width}.png'),full_page=True);page.keyboard.press('Escape')
            checkpoint('modal-keyboard-containment-scroll-and-320px')
            RESULT['network_hosts']=sorted(set(RESULT['network_hosts']));assert RESULT['network_hosts']==['127.0.0.1'],RESULT['network_hosts']
            assert not RESULT['page_errors'],RESULT['page_errors'];checkpoint('no-unhandled-javascript-or-external-runtime-requests')
            browser.close();browser=None
    except Exception as error:
        RESULT['exception']=type(error).__name__+': '+str(error);RESULT['traceback']=traceback.format_exc()
        if page:
            try:page.screenshot(path=str(OUT/'failure.png'),full_page=True);(OUT/'failure.html').write_text(page.content(),encoding='utf8')
            except Exception:pass
    finally:
        if browser:
            try:browser.close()
            except Exception:pass
        connections.close_all();server.terminate()
        try:server.wait(timeout=10)
        except subprocess.TimeoutExpired:server.kill()
        log.close();save()
    print(json.dumps({'checks':len(RESULT['checks']),'layouts':len(RESULT['layout']),'exception':RESULT.get('exception')},ensure_ascii=False))
    if RESULT.get('exception'):raise SystemExit(1)
if __name__=='__main__':main()
