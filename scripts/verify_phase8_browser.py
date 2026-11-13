"""Phase8 browser verification on a disposable copy; no real care data or external services are modified."""
from contextlib import closing
from datetime import datetime,timezone
from pathlib import Path
import json,os,re,socket,sqlite3,subprocess,sys,time,traceback,uuid
from playwright.sync_api import sync_playwright,expect

ROOT=Path(__file__).resolve().parents[1];WORK=ROOT/'tmp/handoff/phase8';OUT=WORK/'browser';OUT.mkdir(parents=True,exist_ok=True)
DATA=WORK/('browser-data-'+uuid.uuid4().hex[:8]);PORT=8049;BASE=f'http://127.0.0.1:{PORT}'
FIXTURE=json.loads((ROOT/'tmp/handoff/phase7/fixture.json').read_text(encoding='utf8'));RID=FIXTURE['rooms']['rich'];PATH=f'/rooms/{RID}/'
RESULT={'at':datetime.now(timezone.utc).isoformat(),'checks':[],'layouts':[],'failures':[],'screens':[],'data':str(DATA),'physical_phone_tested':False}

def save():(WORK/'browser-results.json').write_text(json.dumps(RESULT,ensure_ascii=False,indent=2),encoding='utf8')
def check(name,engine,detail=None):RESULT['checks'].append({'name':name,'engine':engine,'detail':detail});save()
def query(sql,args=()):
    with closing(sqlite3.connect((DATA/'demo.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=10)) as con:return con.execute(sql,args).fetchall()
def total(table,where='',args=()):return query('SELECT COUNT(*) FROM '+table+(' WHERE '+where if where else ''),args)[0][0]
def screenshot(page,label):
    page.screenshot(path=str(OUT/(label+'.png')),full_page=False);RESULT['screens'].append(label);save()
def layout(p,engine,label,width):
    v=p.evaluate('({width:innerWidth,scroll:document.documentElement.scrollWidth})')
    RESULT['layouts'].append({'engine':engine,'page':label,'width':width,'overflow':v['scroll']>v['width']+1});save()
    assert v['scroll']<=v['width']+1,(label,width,v)
def fit(p):
    box=p.locator('#input-dialog').bounding_box();footer=p.locator('#input-dialog .input-dialog-footer').bounding_box();height=p.viewport_size['height']
    assert box and box['x']>=0 and box['y']>=-1 and box['x']+box['width']<=p.viewport_size['width']+1 and box['y']+box['height']<=height+1,(box,p.viewport_size)
    assert footer and footer['y']+footer['height']<=height+1,footer
    assert not p.locator('#input-dialog').evaluate('e=>e.scrollWidth>e.clientWidth+1')
def menu(p):
    if p.viewport_size['width']<1024 and not p.locator('body').evaluate('e=>e.classList.contains("mobile-menu-open")'):
        p.locator('.workspace-topbar [data-sidebar-toggle]').click()
def open_setting(p,suffix):
    menu(p);a=p.locator(f'.sidebar-settings a[href="{PATH}{suffix}"]');a.click();expect(p.locator('#input-dialog [data-input-form]')).to_be_visible();return a
def close_dialog(p):
    p.keyboard.press('Escape');expect(p.locator('#input-dialog')).not_to_be_visible()
    if p.viewport_size['width']<1024 and p.locator('body').evaluate('e=>e.classList.contains("mobile-menu-open")'):p.keyboard.press('Escape')
def submit(p):p.locator('#input-dialog button[type=submit]').click();expect(p.locator('#input-dialog')).not_to_be_visible();p.wait_for_timeout(150)
def last_record(p):return p.locator('[data-toast-link7]').get_attribute('href').rstrip('/').split('/')[-1]

def run_engine(pw,engine):
    browser=getattr(pw,engine).launch(headless=True)
    context=browser.new_context(viewport={'width':1920,'height':1080},locale='ko-KR',timezone_id='Asia/Seoul',reduced_motion='reduce')
    p=context.new_page();p.set_default_timeout(12000);errors=[]
    p.on('pageerror',lambda e:errors.append(str(e)))
    try:
        p.goto(BASE,wait_until='networkidle');p.get_by_role('button',name='방장으로 보기',exact=True).click()
        expect(p.locator('.room-card')).to_have_count(3)
        assert not any('검수' in t for t in p.locator('.room-card h2,.room-card h3').all_text_contents())
        check('three-existing-showcase-rooms-no-regeneration',engine)
        for width,height in [(320,740),(375,812),(768,1000),(1024,900),(1440,1000),(1920,1080)]:
            p.set_viewport_size({'width':width,'height':height})
            for label,suffix in [('today',''),('modules','modules/'),('supplies','supplies/'),('stock','supplies/?tab=stock'),('overview','overview/')]:
                r=p.goto(BASE+PATH+suffix,wait_until='networkidle');assert r.status==200,(label,r.status);layout(p,engine,label,width)
                if label in ('today','modules'):
                    expect(p.locator('[data-schedule-panel]')).to_have_count(1)
                    rail=p.locator('.schedule-rail').bounding_box();main=p.locator('.workspace-primary').bounding_box()
                    if width>=1200:
                        assert rail['x']>=main['x']+main['width']+15,(rail,main)
                        assert p.locator('[data-schedule-panel]').get_attribute('open') is not None
                    else:
                        assert rail['y']<main['y'];assert p.locator('[data-schedule-panel]').get_attribute('open') is None
                if width in (375,1920) and label in ('today','modules','supplies'):screenshot(p,f'{engine}-{label}-{width}')
        check('30-responsive-layouts-and-single-purpose-schedule-panel',engine)
        p.set_viewport_size({'width':1920,'height':1080});p.goto(BASE+PATH,wait_until='networkidle')
        p.locator('.quick-card .tile-caption').first.evaluate('e=>e.textContent="확인할 내용이 여러 줄이어도 버튼은 같은 위치에 있어야 해요. ".repeat(7)')
        boxes=p.locator('.quick-card').evaluate_all('es=>es.map(e=>({top:e.getBoundingClientRect().top,bottom:e.querySelector(".flow-primary7").getBoundingClientRect().bottom}))')
        groups={}
        for x in boxes:groups.setdefault(round(x['top']),[]).append(x['bottom'])
        assert all(max(v)-min(v)<2 for v in groups.values()),groups
        p.mouse.wheel(0,850);p.wait_for_timeout(100)
        rail=p.locator('.schedule-rail').bounding_box();assert rail['y']>=-1 and rail['y']<160,rail
        check('long-card-copy-bottom-alignment-and-sticky-schedule',engine)
        # Mobile disclosure exposes actual pending/complete/skip actions, not a dummy summary.
        p.set_viewport_size({'width':375,'height':812});p.goto(BASE+PATH,wait_until='networkidle')
        p.locator('[data-schedule-panel]>summary').click();expect(p.locator('.schedule-panel-content')).to_be_visible()
        action=p.locator('.schedule-rail [data-action7="routine_skip"]').first
        if action.count():
            before=total('care_routinerun');action.click();expect(p.locator('#flow-toast7')).to_contain_text('건너뛴');p.wait_for_timeout(150)
            expect(p.locator('[data-schedule-panel]')).to_have_attribute('open','')
            assert total('care_routinerun')>=before
        complete=p.locator('.schedule-rail [data-action7="routine_done"]').first
        if complete.count():complete.click();expect(p.locator('#flow-toast7')).to_contain_text('저장');p.wait_for_timeout(150)
        notice=p.locator('.schedule-rail [data-dismiss-notice7]').first
        if notice.count():
            key=notice.get_attribute('data-key');notice.click();expect(p.locator(f'[data-dismiss-notice7][data-key="{key}"]')).to_have_count(0)
        check('schedule-complete-skip-and-personal-notice-dismissal',engine)
        # All settings and item forms open in place at desktop, mobile and short mobile height.
        for width,height in [(1440,1000),(375,812),(375,380)]:
            p.set_viewport_size({'width':width,'height':height});p.goto(BASE+PATH,wait_until='networkidle')
            for name,suffix in [('pet','settings/'),('preferences','preferences/')]:
                before_url=p.url;a=open_setting(p,suffix);fit(p);assert p.url==before_url
                for _ in range(12):p.keyboard.press('Tab');assert p.evaluate('document.querySelector("#input-dialog").contains(document.activeElement)')
                p.keyboard.press('Shift+Tab');p.keyboard.press('Escape');expect(p.locator('#input-dialog')).not_to_be_visible();expect(a).to_be_focused()
                if width<1024:p.keyboard.press('Escape')
            p.goto(BASE+PATH+'supplies/',wait_until='networkidle');p.get_by_role('link',name='물품 등록',exact=True).click();expect(p.locator('[data-supply-editor]')).to_be_visible();fit(p);close_dialog(p)
        check('9-bounded-dialogs-keyboard-focus-and-short-height',engine)
        # Breed suggestions are editable; opening doesn't rewrite an existing exception.
        p.set_viewport_size({'width':1440,'height':1000});p.goto(BASE+PATH,wait_until='networkidle');open_setting(p,'settings/')
        p.locator('#input-dialog [name="breed_key"]').select_option('mixed');p.locator('#input-dialog [name="maturity"]').select_option('adult')
        p.locator('#input-dialog [name="breed_key"]').select_option('dog-bichon-frise');expect(p.locator('#input-dialog [name="size"]')).to_have_value('small')
        p.locator('#input-dialog [name="size"]').select_option('large');p.locator('#input-dialog [name="name"]').fill('검증 보리 '+engine)
        before=total('care_auditevent');p.locator('#input-dialog button[type=submit]').dblclick();expect(p.locator('#input-dialog')).not_to_be_visible();p.wait_for_timeout(150)
        assert query('SELECT size,size_origin FROM care_room WHERE id=?',(RID.replace('-',''),))[0]==('large','manual')
        assert total('care_auditevent')==before+1
        expect(p.locator('.workspace-name')).to_have_text('검증 보리 '+engine)
        open_setting(p,'settings/');expect(p.locator('#input-dialog [name="size"]')).to_have_value('large')
        screenshot(p,f'{engine}-pet-settings');close_dialog(p)
        check('breed-suggestion-manual-exception-idempotent-pet-save-shell-refresh',engine)
        open_setting(p,'preferences/');screenshot(p,f'{engine}-general-settings')
        me= query('SELECT id,notices_enabled FROM care_membership WHERE room_id=? AND role="owner"',(RID.replace('-',''),))[0]
        others=query('SELECT id,notices_enabled FROM care_membership WHERE room_id=? AND role!="owner"',(RID.replace('-',''),))
        p.locator('#input-dialog [name="notices_enabled"]').uncheck();submit(p)
        assert query('SELECT notices_enabled FROM care_membership WHERE id=?',(me[0],))[0][0]==0
        assert query('SELECT id,notices_enabled FROM care_membership WHERE room_id=? AND role!="owner"',(RID.replace('-',''),))==others
        expect(p.locator('[data-notice7]')).to_have_count(0)
        open_setting(p,'preferences/');p.locator('#input-dialog [name="notices_enabled"]').check();submit(p)
        check('personal-setting-does-not-change-other-members',engine)
        # Changed settings receive one result even when the original response is lost.
        if engine=='chromium':
            open_setting(p,'settings/');p.locator('#input-dialog [name="name"]').fill('응답 확인 보리')
            url=BASE+PATH+'settings/';seen=[];token=p.locator('#input-dialog [name="request_token"]').input_value();before=total('care_auditevent')
            def loss(route):
                if route.request.method=='POST':
                    seen.append(route.request.post_data)
                    if len(seen)==1:route.fetch();route.abort('connectionreset')
                    else:route.continue_()
                else:route.continue_()
            context.route(url,loss);p.locator('#input-dialog button[type=submit]').click()
            expect(p.locator('[data-input-status]')).to_contain_text('응답');expect(p.locator('#input-dialog [name="name"]')).to_be_disabled()
            p.get_by_role('button',name='저장 결과 다시 확인',exact=True).click();expect(p.locator('#input-dialog')).not_to_be_visible();context.unroute(url,loss)
            assert len(seen)==2 and all(token in x for x in seen);assert total('care_auditevent')==before+1
            check('lost-settings-response-same-token-one-audit-event',engine)
        # Register non-food -> stocktake -> use -> edit -> cancel through the actual UI.
        p.goto(BASE+PATH+'supplies/',wait_until='networkidle');p.get_by_role('link',name='물품 등록',exact=True).click()
        p.locator('#input-dialog [name="category"]').select_option('hygiene');expect(p.locator('#input-dialog [data-field="energy"]')).not_to_be_visible()
        name='합성 칫솔 '+engine+' '+uuid.uuid4().hex[:5];p.locator('#input-dialog [name="name"]').fill(name)
        p.locator('#input-dialog [name="track_stock"]').check();p.locator('#input-dialog [name="stock_unit"]').select_option('piece')
        before=total('care_product');p.locator('#input-dialog button[type=submit]').dblclick();expect(p.locator('#input-dialog')).not_to_be_visible();p.wait_for_timeout(150)
        assert total('care_product')==before+1
        item_id=query('SELECT id FROM care_product WHERE room_id=? AND name=?',(RID.replace('-',''),name))[0][0]
        item_uuid=str(uuid.UUID(item_id));card=p.locator(f'[data-supply="{item_uuid}"]')
        expect(card).to_be_visible();card.get_by_role('link',name='입고·재고 기록',exact=True).click()
        expect(p.locator('#input-dialog [name="item"]')).to_have_value(name)
        expect(p.locator('#input-dialog [name="quantity"]')).to_have_value('')
        p.locator('#input-dialog [name="movement"]').select_option('set');p.locator('#input-dialog [name="quantity"]').fill('10')
        before=total('care_activity');submit(p);assert total('care_activity')==before+1
        card=p.locator(f'[data-supply="{item_uuid}"]');expect(card).to_contain_text('10')
        card.get_by_role('link',name='사용 기록',exact=True).click();p.locator('#input-dialog [name="task"]').select_option('teeth')
        expect(p.locator('#input-dialog [name="product"]')).to_have_value(item_uuid)
        p.locator('#input-dialog [name="used_quantity"]').fill('2');submit(p);activity=last_record(p)
        expect(p.locator(f'[data-supply="{item_uuid}"] .supply-balance')).to_contain_text('8')
        r=p.goto(BASE+PATH+'records/'+activity+'/',wait_until='networkidle');assert r.status==200
        p.get_by_role('link',name='수정',exact=True).click();expect(p.locator('#input-dialog [name="product"]')).to_have_value(item_uuid)
        expect(p.locator('#input-dialog [name="used_quantity"]')).to_have_value('2');p.locator('#input-dialog [name="used_quantity"]').fill('3');submit(p)
        p.goto(BASE+PATH+'supplies/',wait_until='networkidle');expect(p.locator(f'[data-supply="{item_uuid}"] .supply-balance')).to_contain_text('7')
        p.goto(BASE+PATH+'records/'+activity+'/',wait_until='networkidle')
        version=query('SELECT version FROM care_activity WHERE id=?',(activity.replace('-',''),))[0][0]
        # Cancellation uses the same authorized endpoint, with the browser's session and CSRF token.
        csrf=p.locator('#flow-csrf7 input').input_value()
        res=context.request.post(BASE+PATH+'records/'+activity+'/cancel/',form={'version':str(version),'reason':'브라우저 검증 기록 취소','csrfmiddlewaretoken':csrf})
        assert res.status==200
        p.goto(BASE+PATH+'supplies/',wait_until='networkidle');expect(p.locator(f'[data-supply="{item_uuid}"] .supply-balance')).to_contain_text('10')
        assert total('care_activity','room_id=? AND module="inventory" AND json_extract(payload,"$.item")=?',(RID.replace('-',''),name))==1
        check('nonfood-register-stock-use-edit-cancel-single-ledger',engine)
        screenshot(p,f'{engine}-supplies-after-use')
        # Selecting a registered item from a generic form also fills its identity, never its amount.
        p.goto(BASE+PATH+'modules/',wait_until='networkidle');p.locator('[data-module-key="inventory"] a[href$="/entry/"]').first.click()
        p.locator('#input-dialog [name="product"]').select_option(item_uuid);expect(p.locator('#input-dialog [name="item"]')).to_have_value(name)
        expect(p.locator('#input-dialog [name="quantity"]')).to_have_value('');p.keyboard.press('Escape');expect(p.locator('[data-input-discard]')).to_be_visible()
        p.locator('[data-input-discard-confirm]').click();check('generic-record-reuses-product-without-invented-quantity',engine)
        # Mobile keyboard-size geometry and enlargement, explicitly not a physical keyboard test.
        p.set_viewport_size({'width':375,'height':812});p.goto(BASE+PATH+'supplies/',wait_until='networkidle')
        p.get_by_role('link',name='물품 등록',exact=True).click();expect(p.locator('[data-supply-editor]')).to_be_visible()
        p.evaluate("Object.defineProperty(window.visualViewport,'height',{configurable:true,value:360});window.visualViewport.dispatchEvent(new Event('resize'))")
        p.wait_for_timeout(120);footer=p.locator('#input-dialog .input-dialog-footer').bounding_box();assert footer['y']+footer['height']<=361,footer
        screenshot(p,f'{engine}-keyboard-height-simulation')
        p.evaluate("delete window.visualViewport.height;window.visualViewport.dispatchEvent(new Event('resize'))");close_dialog(p)
        p.evaluate("document.documentElement.style.fontSize='200%'");layout(p,engine,'supplies-text-200%',375)
        p.get_by_role('link',name='물품 등록',exact=True).click();expect(p.locator('[data-supply-editor]')).to_be_visible();fit(p);close_dialog(p)
        p.goto(BASE+PATH,wait_until='networkidle');p.evaluate("document.documentElement.style.fontSize='200%'");layout(p,engine,'today-text-200%',375)
        screenshot(p,f'{engine}-today-text-200-percent');check('mobile-visual-height-simulation-and-200-percent-text',engine)
        # Guest sees read-only pet data, own preferences and no item editor.
        guest=browser.new_context(viewport={'width':375,'height':812},locale='ko-KR',timezone_id='Asia/Seoul');g=guest.new_page()
        g.goto(BASE);g.get_by_role('button',name='일반 참여자로 보기',exact=True).click();g.goto(BASE+PATH,wait_until='networkidle')
        open_setting(g,'settings/');expect(g.locator('#input-dialog [name="name"]')).to_be_disabled();expect(g.locator('#input-dialog button[type=submit]')).to_have_count(0);close_dialog(g)
        open_setting(g,'preferences/');expect(g.locator('#input-dialog [name="timezone"]')).to_be_disabled();expect(g.locator('#input-dialog [name="guest_sensitive"]')).to_be_disabled();close_dialog(g)
        g.goto(BASE+PATH+'supplies/');expect(g.get_by_role('link',name='물품 등록',exact=True)).to_have_count(0)
        assert guest.request.get(BASE+PATH+'products/'+item_uuid+'/').status==403
        guest.close();check('guest-readonly-animal-personal-settings-no-item-management',engine)
        if engine=='chromium':
            p.set_viewport_size({'width':1440,'height':1000});p.goto(BASE+PATH,wait_until='networkidle')
            url=BASE+PATH+'preferences/';held=[];context.route(url,lambda route:held.append(route))
            p.locator(f'.sidebar-settings a[href="{PATH}preferences/"]').click();expect(p.locator('[data-input-loading]')).to_be_visible()
            assert held;held[0].fulfill(status=503,body='synthetic failure');expect(p.locator('[data-input-load-error]')).to_be_visible()
            context.unroute(url);p.get_by_role('button',name='다시 불러오기',exact=True).click();expect(p.locator('[data-preferences-form]')).to_be_visible();close_dialog(p)
            check('settings-loading-503-retry-without-navigation',engine)
        # Existing auxiliary cases remain separate from the three normal showcase rooms.
        for edge in ('empty','sparse'):
            for width,height in ((375,812),(1920,1080)):
                p.set_viewport_size({'width':width,'height':height})
                root=BASE+'/rooms/'+FIXTURE['rooms'][edge]+'/'
                response=p.goto(root+'supplies/',wait_until='networkidle');assert response.status==200
                layout(p,engine,edge+'-supplies',width)
                if edge=='empty':expect(p.locator('.supply-grid .empty-state')).to_be_visible()
                else:
                    expect(p.locator('.supply-card').filter(has_text='긴 이름 확인용')).to_have_count(1)
                    response=p.goto(root,wait_until='networkidle');assert response.status==200
                    if width<1200:p.locator('[data-schedule-panel]>summary').click()
                    expect(p.locator('.schedule-rail .schedule-item7')).to_have_count(32)
                    panel=p.locator('.schedule-panel-content')
                    assert panel.evaluate('e=>e.scrollHeight>e.clientHeight+100')
                    p.locator('.schedule-rail .schedule-item7').last.scroll_into_view_if_needed()
                    layout(p,engine,'many-schedules',width)
                    screenshot(p,f'{engine}-many-schedules-{width}')
        check('empty-items-long-names-and-32-real-schedule-rows',engine)
        assert not errors,errors;check('no-unhandled-client-errors',engine)
    except Exception as exc:
        RESULT['failures'].append({'engine':engine,'error':repr(exc),'trace':traceback.format_exc()[-7500:]})
        try:screenshot(p,engine+'-failure');(OUT/(engine+'-failure.html')).write_text(p.content(),encoding='utf8')
        except Exception:pass
    finally:browser.close();save()


def main():
    with socket.socket() as s:assert s.connect_ex(('127.0.0.1',PORT))!=0,'Port occupied; no other server will be stopped'
    DATA.mkdir(parents=True)
    source=ROOT/'tmp/handoff/phase7/preview-data/demo.sqlite3'
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as a,closing(sqlite3.connect(DATA/'demo.sqlite3')) as b:a.backup(b)
    env=os.environ.copy();env.update(CARE_DATA_DIR=str(DATA),CARE_DEMO_MODE='1',DJANGO_DEBUG='1',DJANGO_ALLOWED_HOSTS='127.0.0.1,localhost,testserver',APP_ORIGIN=BASE,POSTGRES_HOST='',GOOGLE_CLIENT_ID='',GOOGLE_CLIENT_SECRET='',PYTHONUTF8='1')
    log=(OUT/'server.log').open('w',encoding='utf8')
    subprocess.run([sys.executable,'manage.py','migrate','--noinput'],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=90)
    edge_script = """from pathlib import Path
from datetime import time
import json
from django.conf import settings
from care.models import Room, Product, Routine, RoutineItem, RoutineVersion
from care import services
root=Path(settings.BASE_DIR); data=Path(settings.DATA_DIR)
assert data.parent==root/'tmp/handoff/phase8' and data.name.startswith('browser-data-'), 'Disposable database required'
fixture=json.loads((root/'tmp/handoff/phase7/fixture.json').read_text(encoding='utf8'))
room=Room.objects.get(pk=fixture['rooms']['sparse'])
Product.objects.create(room=room,name='긴 이름 확인용 '+('다양한 생활용품 이름 '*7)[:84],category='supply',notes='긴 메모의 줄바꿈을 확인하는 합성 자료. '*30)
for index in range(32):
    routine=Routine.objects.create(room=room,name='많은 일정 확인 '+str(index+1)+' · 긴 돌봄 이름',at=time(7+index//2,index%2*30),weekdays=list(range(7)),start_date=services.local_date(room))
    RoutineItem.objects.create(routine=routine,module='meal',position=0)
    RoutineVersion.objects.create(routine=routine,effective_on=services.local_date(room),snapshot=services.routine_snapshot(routine))
print('Disposable edge cases: one long item and 32 schedules; original data untouched.')
"""
    subprocess.run([sys.executable,'manage.py','shell','-c',edge_script],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=45)
    server=subprocess.Popen([sys.executable,'manage.py','runserver',f'127.0.0.1:{PORT}','--noreload'],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
    try:
        for _ in range(100):
            if server.poll() is not None:raise RuntimeError('Owned test server stopped')
            with socket.socket() as s:
                if s.connect_ex(('127.0.0.1',PORT))==0:break
            time.sleep(.1)
        with sync_playwright() as pw:
            for engine in ('chromium','firefox'):run_engine(pw,engine)
    finally:
        server.terminate()
        try:server.wait(timeout=10)
        except subprocess.TimeoutExpired:server.kill()
        log.close();save()
    print(json.dumps({'checks':len(RESULT['checks']),'layouts':len(RESULT['layouts']),'failures':[{'engine':x['engine'],'error':x['error']} for x in RESULT['failures']]},ensure_ascii=False))
    return bool(RESULT['failures'])
if __name__=='__main__':raise SystemExit(main())
