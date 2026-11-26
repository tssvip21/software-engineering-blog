"""Real browser checks against a disposable database; never mutates the user's database."""
from demo_browser_session import select_fixture_role
from pathlib import Path
from contextlib import closing
import json, os, re, socket, sqlite3, subprocess, sys, time, traceback, uuid
from datetime import timedelta
from urllib.parse import urlparse

ROOT=next(parent for parent in Path(__file__).resolve().parents if (parent / "manage.py").is_file())
OUT=ROOT/'tmp/handoff/phase3/browser';OUT.mkdir(parents=True,exist_ok=True)
DATA=ROOT/'tmp/handoff/phase3'/('browser-data-'+uuid.uuid4().hex[:8]);DATA.mkdir()
SOURCE=ROOT/'data/demo.sqlite3'
if SOURCE.exists():
    with closing(sqlite3.connect(SOURCE.as_uri()+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(DATA/'demo.sqlite3')) as dst:src.backup(dst)
PORT=8014;BASE=f'http://127.0.0.1:{PORT}'
os.environ.update(DJANGO_SETTINGS_MODULE='config.settings',CARE_DATA_DIR=str(DATA),CARE_DEMO_MODE='1',DJANGO_DEBUG='1',APP_ORIGIN=BASE,DJANGO_ALLOWED_HOSTS='localhost,127.0.0.1,testserver',PYTHONUTF8='1')
sys.path.insert(0,str(ROOT))
RESULT={'checks':[],'layout':[],'page_errors':[],'data_directory':str(DATA),'network_hosts':[]}
def checkpoint(name):
    RESULT['checks'].append(name);(OUT/'results.json').write_text(json.dumps(RESULT,ensure_ascii=False,indent=2),encoding='utf8')


def main():
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1',PORT))==0:raise RuntimeError('Verification port already used; existing process is not stopped.')
    for args in [('migrate','--noinput'),('seed_demo',),('seed_references',)]:
        p=subprocess.run([sys.executable,'manage.py',*args],cwd=ROOT,env=os.environ.copy(),capture_output=True,text=True,encoding='utf8',timeout=60)
        (OUT/(args[0]+'.log')).write_text(p.stdout+p.stderr,encoding='utf8');assert p.returncode==0,p.stderr
    log=(OUT/'server.log').open('w',encoding='utf8')
    server=subprocess.Popen([sys.executable,'manage.py','runserver',f'127.0.0.1:{PORT}','--noreload'],cwd=ROOT,env=os.environ.copy(),stdout=log,stderr=subprocess.STDOUT)
    for _ in range(100):
        with socket.socket() as probe:
            if probe.connect_ex(('127.0.0.1',PORT))==0:break
        if server.poll() is not None:raise RuntimeError('Isolated verification server stopped.')
        time.sleep(.1)
    import django
    django.setup()
    from django.contrib.auth import get_user_model
    from django.db import connections
    from django.utils import timezone
    from care.models import Room,Activity,Membership
    from care.demo_fixtures import record_data
    from care.forms import RecordForm
    from care import services
    from playwright.sync_api import sync_playwright,expect
    os.environ['DJANGO_ALLOW_ASYNC_UNSAFE']='1'
    browser=None;page=None
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            context=browser.new_context(viewport={'width':1440,'height':1000},locale='ko-KR',timezone_id='Asia/Seoul')
            hosts=set()
            context.on('request',lambda request:hosts.add(urlparse(request.url).hostname))
            page=context.new_page();page.set_default_timeout(12000)
            page.on('pageerror',lambda e:RESULT['page_errors'].append(str(e)))
            page.on('dialog',lambda d:d.accept())
            page.goto(BASE+'/',wait_until='networkidle');select_fixture_role(page, 'owner');page.wait_for_url(BASE+'/')
            checkpoint('real-demo-login')
            page.goto(BASE+'/rooms/new/',wait_until='networkidle')
            page.locator('#id_name').fill('테일리 3차 검증')
            page.locator('#id_species').select_option('dog')
            search=page.locator('#breed-search');search.fill('포메')
            expect(page.get_by_role('option',name=re.compile('포메라니안'))).to_be_visible()
            page.get_by_role('option',name=re.compile('포메라니안')).click()
            expect(page.locator('#id_breed_key')).to_have_value('dog-pomeranian')
            expect(page.locator('#id_size')).to_have_value('small')
            page.locator('#id_size').select_option('medium');expect(page.locator('#id_size_origin')).to_have_value('manual')
            page.locator('#id_size_origin').select_option('auto');expect(page.locator('#id_size')).to_have_value('small')
            page.locator('#id_maturity').select_option('adult')
            page.get_by_role('button',name='돌봄 공간 만들기',exact=True).click();page.wait_for_url(re.compile(r'/rooms/[\da-f-]+/$'))
            path=page.url.removeprefix(BASE);room=Room.objects.get(pk=path.split('/')[2]);owner=room.members.get(role='owner')
            assert room.breed_key=='dog-pomeranian' and room.size=='small'
            checkpoint('korean-breed-search-size-suggestion-override-and-persistence')
            page.goto(BASE+'/rooms/new/')
            page.locator('#id_species').select_option('cat');page.locator('#breed-search').fill('British')
            page.get_by_role('option',name=re.compile('브리티시 쇼트헤어')).click()
            expect(page.locator('#id_breed_key')).to_have_value('cat-british-shorthair')
            expect(page.locator('#id_size')).not_to_be_visible()
            page.locator('#breed-search').fill('Unknown');page.get_by_role('option',name=re.compile('품종 미상')).click()
            page.locator('#id_name').fill('품종 미상 고양이')
            page.get_by_role('button',name='돌봄 공간 만들기',exact=True).click();page.wait_for_url(re.compile(r'/rooms/[\da-f-]+/$'))
            cat=Room.objects.get(pk=page.url.split('/')[-2]);assert cat.breed_key=='unknown' and cat.size==''
            checkpoint('english-breed-search-and-unknown-cat-without-dog-size')
            page.goto(BASE+'/rooms/new/');page.locator('#id_species').select_option('dog');page.locator('#breed-search').fill('믹스');page.get_by_role('option',name=re.compile('믹스')).click();page.locator('#id_size').select_option('large');page.locator('#id_name').fill('믹스 검증');page.get_by_role('button',name='돌봄 공간 만들기',exact=True).click();page.wait_for_url(re.compile(r'/rooms/[\da-f-]+/$'))
            mixed=Room.objects.get(pk=page.url.split('/')[-2]);assert mixed.breed_key=='mixed' and mixed.size=='large'
            checkpoint('mixed-breed-manual-size-registration')
            page.goto(BASE+path+'modules/settings/',wait_until='networkidle')
            assert page.locator('[data-order-step]').count()==0
            handle=page.locator('.chosen-row .drag-handle').first
            assert handle.locator('circle').count()==6
            assert handle.bounding_box()['width']>=40
            assert page.locator('.chosen-row [data-remove-module]').first.inner_text().strip()==''
            checkpoint('visible-six-dot-handles-and-icon-only-removal-no-arrows')
            page.locator('[data-module-filter="living"]').click()
            assert page.locator('.catalog-item:not([hidden])').count()==6
            page.locator('[data-add-module="cleaning"]').click()
            expect(page.locator('[data-module-config]')).to_have_attribute('data-status','saved')
            page.locator('[data-module-filter="all"]').click();page.locator('#catalog-search').fill('청소');assert page.locator('.catalog-item:not([hidden])').count()==1
            page.locator('#catalog-search').fill('')
            before=page.locator('#chosen-modules .chosen-row').evaluate_all('(rows)=>rows.map(x=>x.dataset.key)')
            a=page.locator('#chosen-modules .chosen-row').last.locator('.drag-handle');b=page.locator('#chosen-modules .chosen-row').first
            a.scroll_into_view_if_needed();box=a.bounding_box();target=b.bounding_box()
            page.mouse.move(box['x']+box['width']/2,box['y']+box['height']/2);page.mouse.down();page.mouse.move(target['x']+40,target['y']+12,steps=20);page.mouse.up()
            expect(page.locator('[data-module-config]')).to_have_attribute('data-status','saved')
            after=page.locator('#chosen-modules .chosen-row').evaluate_all('(rows)=>rows.map(x=>x.dataset.key)');assert after[0]==before[-1],(before,after)
            first=page.locator('#chosen-modules .drag-handle').first;first.focus();page.keyboard.press('Space');page.keyboard.press('Home');page.keyboard.press('Enter')
            assert page.locator('#chosen-modules .chosen-row').first.get_attribute('data-key')==before[-1]
            page.reload(wait_until='networkidle');assert page.locator('#chosen-modules .chosen-row').first.get_attribute('data-key')==before[-1]
            checkpoint('catalog-category-search-mouse-drag-home-boundary-and-persistence')
            page.goto(BASE+path+'groups/');page.locator('[data-group-add]').click();row=page.locator('.group-editor-row').last;row.locator('[data-group-name]').fill('아침 돌봄');row.locator('[data-group-key="meal"]').check();row.locator('[data-group-key="walk"]').check()
            page.locator('[data-group-add]').click();row=page.locator('.group-editor-row').last;row.locator('[data-group-name]').fill('주말 관리');row.locator('[data-group-key="cleaning"]').check();page.locator('#group-editor button[type="submit"]').click();expect(page.locator('#group-editor')).to_have_attribute('data-status','saved')
            handle=page.locator('.group-drag-handle').last;handle.focus();page.keyboard.press('Space');page.keyboard.press('ArrowUp');page.keyboard.press('Enter');page.locator('#group-editor button[type="submit"]').click();expect(page.locator('#group-editor')).to_have_attribute('data-status','saved')
            page.reload(wait_until='networkidle');expect(page.locator('[data-group-name]').first).to_have_value('주말 관리')
            checkpoint('groups-create-select-modules-keyboard-reorder-save-reload')
            # An interrupted request retains edits; confirmed responses and stale writes stay distinct.
            def fail_group_post(route):
                if route.request.method=='POST':route.abort('internetdisconnected')
                else:route.continue_()
            context.route('**/groups/config/',fail_group_post)
            page.locator('[data-group-name]').first.fill('주말 돌봄')
            page.locator('#group-editor button[type="submit"]').click()
            expect(page.locator('#group-editor')).to_have_attribute('data-status','error')
            expect(page.locator('[data-group-name]').first).to_have_value('주말 돌봄')
            context.unroute('**/groups/config/',fail_group_post)
            page.locator('#group-editor button[type="submit"]').click()
            expect(page.locator('#group-editor')).to_have_attribute('data-status','saved')
            checkpoint('group-offline-failure-retains-input-and-retry-persists')
            peer=context.new_page();peer.on('dialog',lambda d:d.accept());peer.goto(BASE+path+'groups/')
            page.locator('[data-group-name]').first.fill('주말 관리')
            page.locator('#group-editor button[type="submit"]').click()
            expect(page.locator('#group-editor')).to_have_attribute('data-status','saved')
            peer.locator('[data-group-name]').first.fill('겹친 수정')
            peer.locator('#group-editor button[type="submit"]').click()
            expect(peer.locator('#group-editor')).to_have_attribute('data-status','error')
            expect(peer.locator('[data-group-name]').first).to_have_value('겹친 수정')
            assert room.module_groups.first().name=='주말 관리'
            peer.locator('[data-group-reload]').click()
            expect(peer.locator('#group-editor')).to_have_attribute('data-status','saved')
            expect(peer.locator('[data-group-name]').first).to_have_value('주말 관리')
            peer.close()
            checkpoint('group-concurrent-write-conflict-keeps-server-winner')

            page.goto(BASE+path+'modules/');page.locator('[data-custom-filter]').select_option(label='아침 돌봄');assert page.locator('.module-tile:not([hidden])').count()==2
            page.locator('[data-module-filter="activity"]').click();assert page.locator('.module-tile:not([hidden])').count()==1
            checkpoint('module-workspace-intersects-category-custom-group')
            page.locator('[data-module-filter="nutrition"]').click()
            page.locator('#workspace-search').fill('식사')
            chosen_group=page.locator('[data-custom-filter]').input_value()
            previous=page.locator('[data-module-browser="workspace"]').element_handle()
            page.locator(f'a[data-quick][href="{path}modules/meal/quick/"]').click()
            expect(page.locator('#quick-content form')).to_be_visible()
            page.locator('#quick-content button[type="submit"]').click()
            expect(page.locator('#quick-dialog')).not_to_be_visible()
            page.wait_for_function('(node)=>!node.isConnected',arg=previous)
            expect(page.locator('[data-module-filter="nutrition"]')).to_have_attribute('aria-pressed','true')
            expect(page.locator('[data-custom-filter]')).to_have_value(chosen_group)
            expect(page.locator('#workspace-search')).to_have_value('식사')
            assert page.locator('.module-tile:not([hidden])').count()==1
            previous=page.locator('[data-module-browser="workspace"]').element_handle()
            page.locator('[data-feedback-undo]').click()
            page.wait_for_function('(node)=>!node.isConnected',arg=previous)
            expect(page.locator('[data-feedback-message]')).to_contain_text('취소')
            expect(page.locator('[data-module-filter="nutrition"]')).to_have_attribute('aria-pressed','true')
            expect(page.locator('[data-custom-filter]')).to_have_value(chosen_group)
            expect(page.locator('#workspace-search')).to_have_value('식사')
            checkpoint('quick-record-and-undo-preserve-category-group-and-search')
            page.locator('[data-module-filter="all"]').click()
            page.locator('[data-custom-filter]').select_option('all')
            page.locator('#workspace-search').fill('')
            # Seed a controlled within-pet history using the existing record service, only in the disposable DB.
            for offset in range(35):
                day=services.local_date(room)-timedelta(days=offset);at=timezone.localtime().replace(year=day.year,month=day.month,day=day.day,hour=0,minute=0,second=0,microsecond=0)
                for key,value in [('weight','9' if offset<7 else '8'),('meal','160' if offset<7 else '100'),('walk','10' if offset<7 else '30')]:
                    values=record_data(key,occurred_at=at.strftime('%Y-%m-%dT%H:%M:%S'))
                    values.update({'weight':value} if key=='weight' else {'quantity':value,'product':''} if key=='meal' else {'minutes':value})
                    f=RecordForm(values,room=room,module=key,member=owner);assert f.is_valid(),f.errors.as_json();services.save_activity(owner,key,f)
            page.goto(BASE+path+'overview/',wait_until='networkidle');expect(page.locator('#overall-title')).to_contain_text('관련된 기록')
            assert page.locator('.relationship').count()>=2
            page.locator('.metric-evidence').first.locator('summary').click();link=page.locator('.metric-evidence').first.get_by_role('link');url=link.get_attribute('href');assert 'modules=' in url
            page.goto(BASE+url,wait_until='networkidle');assert page.locator('.ledger-entry').count()>0
            assert page.get_by_role('link',name='오늘 기록하기',exact=True).count()==0
            checkpoint('integrated-pattern-relationships-with-real-evidence-link')
            User=get_user_model();admin=User.objects.get(username='demo_admin');member_user=User.objects.get(username='demo_member')
            Membership.objects.get_or_create(room=room,user=admin,defaults={'display_name':'민서','role':'admin'});Membership.objects.get_or_create(room=room,user=member_user,defaults={'display_name':'도윤','role':'guest'})
            page.goto(BASE+path+'members/',wait_until='networkidle')
            menu=page.locator('.member-row').filter(has_text='도윤').locator('.member-menu');menu.locator('summary').click()
            with page.expect_response(lambda r:r.request.method=='POST' and '/members/' in r.url) as post:menu.get_by_role('button',name='관리자로 지정',exact=True).click()
            assert post.value.status==200
            assert room.members.get(user=member_user).role=='admin'
            page.goto(BASE+path);menu=page.locator('.sidebar-members li').filter(has_text='도윤').locator('.member-menu');menu.locator('summary').click()
            with page.expect_response(lambda r:r.request.method=='POST' and '/members/' in r.url) as post:menu.get_by_role('button',name='관리자 권한 해제',exact=True).click()
            assert post.value.status==200;assert room.members.get(user=member_user).role=='guest'
            checkpoint('member-ellipsis-promote-in-list-demote-in-sidebar')
            # Distinct session and server response, not merely hidden controls.
            other=browser.new_context(viewport={'width':390,'height':844},locale='ko-KR',timezone_id='Asia/Seoul');g=other.new_page();g.goto(BASE+'/');select_fixture_role(g, 'member');g.goto(BASE+path+'modules/')
            assert g.locator('.member-menu').count()==0
            assert g.request.get(BASE+path+'groups/config/').status==403
            assert g.request.get(BASE+path+'modules/medication/quick/').status==403
            g.goto(BASE+path+'overview/');expect(g.locator('#overall-title')).to_be_visible();other.close()
            checkpoint('guest-hidden-management-and-forbidden-group-sensitive-endpoints')
            # Real touch input reorders the module list in a mobile viewport.
            mobile=browser.new_context(viewport={'width':390,'height':844},locale='ko-KR',timezone_id='Asia/Seoul',is_mobile=True,has_touch=True,storage_state=context.storage_state());m=mobile.new_page();m.on('dialog',lambda d:d.accept());m.goto(BASE+path+'modules/settings/',wait_until='networkidle')
            original=m.locator('#chosen-modules .chosen-row').evaluate_all('(r)=>r.map(x=>x.dataset.key)');m.locator('#chosen-modules .chosen-row').first.scroll_into_view_if_needed();a=m.locator('#chosen-modules .chosen-row').nth(1).locator('.drag-handle').bounding_box();b=m.locator('#chosen-modules .chosen-row').first.bounding_box();x=a['x']+a['width']/2;y=a['y']+a['height']/2;end=b['y']+10;cdp=mobile.new_cdp_session(m)
            cdp.send('Input.dispatchTouchEvent',{'type':'touchStart','touchPoints':[{'x':x,'y':y,'id':1}]})
            for step in range(1,15):cdp.send('Input.dispatchTouchEvent',{'type':'touchMove','touchPoints':[{'x':x,'y':y+(end-y)*step/14,'id':1}]});m.wait_for_timeout(16)
            cdp.send('Input.dispatchTouchEvent',{'type':'touchEnd','touchPoints':[]});expect(m.locator('[data-module-config]')).to_have_attribute('data-status','saved');m.reload(wait_until='networkidle');assert m.locator('#chosen-modules .chosen-row').first.get_attribute('data-key')==original[1]
            checkpoint('touch-drag-persistent-without-sort-arrows')
            m.goto(BASE+path+'groups/',wait_until='networkidle')
            original_groups=m.locator('.group-editor-row').evaluate_all('(rows)=>rows.map(r=>r.dataset.id)')
            m.locator('.group-editor-row').first.scroll_into_view_if_needed()
            a=m.locator('.group-drag-handle').nth(1).bounding_box();b=m.locator('.group-editor-row').first.bounding_box()
            x=a['x']+a['width']/2;y=a['y']+a['height']/2;end=b['y']+12
            cdp.send('Input.dispatchTouchEvent',{'type':'touchStart','touchPoints':[{'x':x,'y':y,'id':2}]})
            for step in range(1,15):
                cdp.send('Input.dispatchTouchEvent',{'type':'touchMove','touchPoints':[{'x':x,'y':y+(end-y)*step/14,'id':2}]})
                m.wait_for_timeout(16)
            cdp.send('Input.dispatchTouchEvent',{'type':'touchEnd','touchPoints':[]})
            assert m.locator('.group-editor-row').first.get_attribute('data-id')==original_groups[1]
            m.locator('#group-editor button[type="submit"]').click()
            expect(m.locator('#group-editor')).to_have_attribute('data-status','saved')
            m.reload(wait_until='networkidle')
            assert m.locator('.group-editor-row').first.get_attribute('data-id')==original_groups[1]
            mobile.close();checkpoint('touch-custom-group-sort-save-and-reload')
            # Compare a small active set against all twenty modules, preserving every activity.
            for width in (390,1440):
                page.set_viewport_size({'width':width,'height':1000});page.goto(BASE+path+'modules/',wait_until='networkidle')
                assert page.locator('.module-tile:not([hidden])').count()==6
                page.screenshot(path=str(OUT/f'modules-few-{width}.png'),full_page=True)
            page.goto(BASE+path+'modules/settings/',wait_until='networkidle')
            old_ids=set(Activity.objects.filter(room=room).values_list('pk',flat=True))
            page.locator('.chosen-row[data-key="meal"] [data-remove-module]').click()
            expect(page.locator('[data-module-config]')).to_have_attribute('data-status','saved')
            assert set(Activity.objects.filter(room=room).values_list('pk',flat=True))==old_ids
            page.locator('[data-add-module="meal"]').click()
            expect(page.locator('[data-module-config]')).to_have_attribute('data-status','saved')
            while page.locator('[data-add-module]:not([disabled])').count():
                page.locator('[data-add-module]:not([disabled])').first.click()
                expect(page.locator('[data-module-config]')).to_have_attribute('data-status','saved')
            assert page.locator('#chosen-modules .chosen-row').count()==20
            for width in (390,1440):
                page.set_viewport_size({'width':width,'height':1000});page.goto(BASE+path+'modules/',wait_until='networkidle')
                assert page.locator('.module-tile:not([hidden])').count()==20
                assert not page.evaluate('document.documentElement.scrollWidth>innerWidth+1')
                page.screenshot(path=str(OUT/f'modules-many-{width}.png'),full_page=True)
                page.locator('[data-module-filter="living"]').click()
                assert page.locator('.module-tile:not([hidden])').count()==6
            checkpoint('few-and-all-twenty-modules-discoverable-removal-retains-history')
            page.goto(BASE+path+'routines/',wait_until='networkidle')
            field=page.locator('.routine-date input[type="date"]').bounding_box()
            action=page.locator('.routine-date button').bounding_box()
            assert field and action and field['width']<=220 and action['x']-(field['x']+field['width'])<40
            checkpoint('routine-date-control-compact-and-action-adjacent')
            for width in (360,390,768,1024,1440,1920):
                page.set_viewport_size({'width':width,'height':1000})
                for suffix in ('','modules/','modules/settings/','groups/','records/','routines/','cadences/','overview/','members/','settings/','analysis/method/'):
                    response=page.goto(BASE+path+suffix,wait_until='networkidle');assert response.status==200,(suffix,response.status)
                    overflow=page.evaluate('document.documentElement.scrollWidth>innerWidth+1');RESULT['layout'].append({'width':width,'path':suffix,'overflow':overflow});assert not overflow,(width,suffix)
                    if width in (390,1440) and suffix in ('','modules/','modules/settings/','records/','overview/','groups/'):
                        page.screenshot(path=str(OUT/((suffix.strip('/').replace('/','-') or 'today')+f'-{width}.png')),full_page=True)
                checkpoint('responsive-'+str(width))
            RESULT['network_hosts']=sorted(h for h in hosts if h)
            assert set(RESULT['network_hosts'])<={'127.0.0.1','localhost'},RESULT['network_hosts']
            checkpoint('browser-service-requests-stay-on-loopback')
            assert not RESULT['page_errors'],RESULT['page_errors'];checkpoint('no-unhandled-javascript-exceptions')
            RESULT['room_path']=path
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
        log.close();(OUT/'results.json').write_text(json.dumps(RESULT,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'checks':len(RESULT['checks']),'layouts':len(RESULT['layout']),'exception':RESULT.get('exception')},ensure_ascii=False))
    if RESULT.get('exception'):raise SystemExit(1)

if __name__=='__main__':main()
