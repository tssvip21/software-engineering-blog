"""Phase9 UI/record checks on a disposable SQLite copy. No production or original-demo writes."""
from contextlib import closing
from datetime import datetime,timezone
from pathlib import Path
import json,os,platform,re,socket,sqlite3,subprocess,sys,time,traceback,uuid
from playwright.sync_api import sync_playwright,expect

ROOT=Path(__file__).resolve().parents[1];WORK=ROOT/'tmp/handoff/phase9';OUT=WORK/'browser';OUT.mkdir(exist_ok=True)
DATA=WORK/('flow-data-'+uuid.uuid4().hex[:8]);PORT=8051;BASE=f'http://127.0.0.1:{PORT}'
FAST=os.environ.get('PHASE9_BROWSER_FAST')=='1'
RESULT={'at':datetime.now(timezone.utc).isoformat(),'fast_mode':FAST,'os':platform.platform(),'cpu':platform.machine(),'actual_mac':False,'checks':[],'layouts':[],'errors':[],'failures':[],'data':str(DATA)}

def save():(WORK/'browser-results.json').write_text(json.dumps(RESULT,ensure_ascii=False,indent=2),encoding='utf8')
def check(name,engine,detail=None):RESULT['checks'].append({'name':name,'engine':engine,'detail':detail});save()
def query(sql,args=()):
    with closing(sqlite3.connect((DATA/'demo.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=15)) as db:return db.execute(sql,args).fetchall()
def count(module,rid):return query('SELECT COUNT(*) FROM care_activity WHERE room_id=? AND module=? AND voided=0',(rid.replace('-',''),module))[0][0]
def snapshot(rid):
    rid=rid.replace('-','')
    return {t:query('SELECT COUNT(*) FROM '+t+' WHERE '+('activity_id IN (SELECT id FROM care_activity WHERE room_id=?)' if t=='care_activityrevision' else 'room_id=?'),(rid,))[0][0] for t in ('care_activity','care_activityrevision','care_actionreceipt','care_auditevent')}
def fit(p):
    box=p.locator('#input-dialog').bounding_box();foot=p.locator('#input-dialog .input-dialog-footer').bounding_box();vp=p.viewport_size
    assert box and box['x']>=-1 and box['y']>=-1 and box['x']+box['width']<=vp['width']+1 and box['y']+box['height']<=vp['height']+1,(box,vp)
    assert foot and foot['y']+foot['height']<=vp['height']+1,foot
    assert not p.locator('#input-dialog').evaluate('e=>e.scrollWidth>e.clientWidth+1')
def layout(p,label,engine):
    state=p.evaluate('({width:innerWidth,height:innerHeight,scroll:document.documentElement.scrollWidth})')
    row={'name':label,'engine':engine,**state,'overflow':state['scroll']>state['width']+1};RESULT['layouts'].append(row);save()
    assert not row['overflow'],row

def setup_fixture(env,log):
    code=r'''from pathlib import Path
from datetime import time,timedelta
import json,uuid
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from care.models import Room,Membership,Product,Routine,RoutineItem,RoutineVersion
from care.forms import RoomForm,RecordForm
from care import services
from care.demo_fixtures import record_data
from care.modules import SELECTABLE_MODULES,MODULES
D=Path(settings.DATA_DIR);W=Path(settings.BASE_DIR)/'tmp/handoff/phase9'
assert D.parent==W and D.name.startswith('flow-data-'),'Disposable database required'
owner=get_user_model().objects.get(username='demo_owner');guest=get_user_model().objects.get(username='demo_member')
result={}
for engine in ('chromium','firefox'):
 f=RoomForm({'name':'동작 확인 '+engine,'species':'dog','maturity':'adult','timezone':'Asia/Seoul','size':'small','use_defaults':'on'})
 assert f.is_valid(),f.errors
 room=services.create_room(owner,f);member=room.members.get(role='owner');Membership.objects.create(room=room,user=guest,role='guest',display_name='확인 참여자')
 room.module_settings.update(enabled=True,pinned=True);room.module_versions.update(effective_at=timezone.now()-timedelta(days=30),settings={'enabled':list(SELECTABLE_MODULES),'order':list(MODULES)})
 stock='양치 물품 '+engine;p=Product.objects.create(room=room,name=stock,category='hygiene',stock_item=stock,stock_unit='g')
 def add(module,**kw):
  data=record_data(module,occurred_at=(timezone.now()-timedelta(minutes=10)).isoformat(),**kw)
  form=RecordForm(data,room=room,module=module,member=member);assert form.is_valid(),form.errors
  return services.save_activity(member,module,form)[0]
 add('inventory',item=stock,movement='set',quantity='100',unit='g',minimum='')
 walk=add('walk',minutes='1.234',distance='.001',distance_unit='m')
 weight=add('weight',weight='6.312',unit='kg');add('water',kind='measured',amount='.055',unit='l');add('expense',item='소모품',amount='123456',currency='KRW')
 ids={}
 for label,module in [('water','water'),('weight','weight'),('hygiene','hygiene'),('cleaning','cleaning'),('note','observation'),('retry','hygiene'),('concurrent','hygiene'),('blocked','medication')]:
  title={'water':'물 교체 확인','weight':'체중 측정 확인','hygiene':'양치 물품 사용','cleaning':'생활공간 청소','note':'오늘의 특이사항','retry':'응답 확인 양치','concurrent':'중복 요청 확인','blocked':'권한 제한 일정'}[label]
  r=Routine.objects.create(room=room,name=title,at=time(7,30),weekdays=list(range(7)),start_date=services.local_date(room))
  item=RoutineItem.objects.create(routine=r,module=module,position=0);RoutineVersion.objects.create(routine=r,effective_on=services.local_date(room),snapshot=services.routine_snapshot(r));ids[label]=str(item.pk)
 result[engine]={'room':str(room.pk),'items':ids,'product':str(p.pk),'stock_name':stock,'walk':str(walk.pk),'weight':str(weight.pk),'day':services.local_date(room).isoformat()}
(W/'browser-fixture.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
print('Created independent synthetic task fixtures only.')
'''
    subprocess.run([sys.executable,'manage.py','shell','-c',code],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=50)


def run_engine(pw,engine,fixture):
    browser=getattr(pw,engine).launch(headless=True);ctx=browser.new_context(viewport={'width':1440,'height':1000},locale='ko-KR',timezone_id='Asia/Seoul',reduced_motion='reduce');p=ctx.new_page();p.set_default_timeout(12000)
    errors=[];p.on('pageerror',lambda e:errors.append(str(e)))
    rid=fixture['room'];root=f'/rooms/{rid}/';day=fixture['day'];items=fixture['items']
    def panel():
        d=p.locator('[data-schedule-panel]')
        if d.get_attribute('open') is None:d.locator(':scope>summary').click()
    def row(key):return p.locator('[data-schedule-key="'+items[key]+':'+day+'"]')
    def open_routine(key):
        panel();row(key).locator('[data-action7="routine_done"]').click();expect(p.locator('#input-dialog [data-input-form]')).to_be_visible();fit(p)
    def submit():p.locator('#input-dialog button[type=submit]').click();expect(p.locator('#input-dialog')).not_to_be_visible()
    def balance():
        values=query('SELECT module,payload FROM care_activity WHERE room_id=? AND voided=0 AND status="done"',(rid.replace('-',''),));amount=100
        for module,payload in values:
            data=json.loads(payload)
            if module!='inventory' and data.get('_stock',{}).get('item')==fixture['stock_name']:amount-=float(data['_stock'].get('quantity') or 0)
        return round(amount,6)
    try:
        p.goto(BASE,wait_until='networkidle');p.get_by_role('button',name='방장으로 보기',exact=True).click()
        RESULT.setdefault('browser_versions',{})[engine]=browser.version
        # Inspect representative held-out species and widths without changing their records.
        nav=json.loads((ROOT/'tmp/handoff/phase7/demo-navigation.json').read_text(encoding='utf8'))['showcase']
        for pet_id in ([] if FAST else nav[1:]):
            for width,height in ((1280,800),(360,740)):
                p.set_viewport_size({'width':width,'height':height})
                for suffix in ('','modules/','overview/','supplies/?tab=stock'):
                    response=p.goto(BASE+'/rooms/'+pet_id+'/'+suffix,wait_until='networkidle');assert response.status==200;layout(p,'held-out-species-'+suffix,engine)
        if not FAST:check('held-out-cat-and-parrot-two-widths',engine)
        for width,height in (() if FAST else ((1440,1000),(375,812),(1920,1080),(320,740))):
            p.set_viewport_size({'width':width,'height':height})
            for suffix in ('','modules/','records/','overview/','routines/','supplies/','supplies/?tab=stock'):
                response=p.goto(BASE+root+suffix,wait_until='networkidle');assert response.status==200;layout(p,suffix or 'today',engine)
        if not FAST:check('whole-main-route-widths-no-overflow',engine)
        # A record with a nonzero amount smaller than the old formatter's step stays nonzero.
        p.set_viewport_size({'width':1440,'height':1000});p.goto(BASE+root,wait_until='networkidle')
        expect(p.locator('.quick-card[data-module="walk"]')).to_contain_text('0.001')
        expect(p.locator('.quick-card[data-module="weight"]')).to_contain_text('6.312')
        p.goto(BASE+root+'records/'+fixture['walk']+'/',wait_until='networkidle')
        expect(p.locator('#main')).to_contain_text('1분 14초');expect(p.locator('#main')).to_contain_text('0.001 m')
        p.locator('.stored-values>summary').click();expect(p.locator('.stored-values')).to_contain_text('1.234 분')
        p.get_by_role('link',name='수정',exact=True).click();expect(p.locator('#input-dialog [name="minutes"]')).to_have_value('1.234');expect(p.locator('#input-dialog [name="distance"]')).to_have_value('0.001');p.keyboard.press('Escape')
        check('natural-display-tiny-distance-and-original-edit-precision',engine)
        # Quick cards retain alignment with actual longer content.
        p.goto(BASE+root,wait_until='networkidle');p.locator('.quick-card .tile-caption').first.evaluate('e=>e.textContent="문구가 길어져도 실제 값과 실행 버튼이 가려지지 않도록 확인해요. ".repeat(4)')
        boxes=p.locator('.quick-card').evaluate_all('es=>es.map(e=>({top:Math.round(e.getBoundingClientRect().top),bottom:e.querySelector(".flow-primary7").getBoundingClientRect().bottom}))')
        groups={}
        for box in boxes:groups.setdefault(box['top'],[]).append(box['bottom'])
        assert all(max(v)-min(v)<2 for v in groups.values()),groups
        check('quick-actions-aligned-with-long-copy',engine)
        p.goto(BASE+root,wait_until='networkidle');before=count('water',rid);panel();row('water').locator('button[data-action7="routine_done"]').click()
        expect(p.locator('[data-schedule-disclosure="completed"]')).to_have_attribute('open','');expect(row('water')).to_have_class(re.compile('is-done'));expect(p.locator('#flow-toast7')).to_contain_text('완료한 루틴')
        assert count('water',rid)==before+1;water=query('SELECT payload FROM care_activity WHERE room_id=? AND module="water" ORDER BY created_at DESC LIMIT 1',(rid.replace('-',''),))[0][0];assert json.loads(water)['amount'] is None
        check('no-input-completion-no-fabricated-amount-and-visible-move',engine)
        before=snapshot(rid);open_routine('hygiene');expect(p.locator('.routine-input-intro')).to_contain_text('관리 항목')
        p.keyboard.press('Escape');expect(p.locator('#input-dialog')).not_to_be_visible();assert snapshot(rid)==before
        expect(row('hygiene').locator('[data-action7="routine_done"]')).to_be_focused()
        check('required-input-named-and-cancel-with-no-side-effect',engine)
        p.set_viewport_size({'width':375,'height':812});p.goto(BASE+root,wait_until='networkidle');open_routine('weight')
        assert p.locator('#input-dialog [name="occurred_at"]').input_value(),'The browser discarded the initial local timestamp'
        for _ in range(14):p.keyboard.press('Tab');assert p.evaluate('document.querySelector("#input-dialog").contains(document.activeElement)')
        p.locator('#input-dialog [name="weight"]').fill('-1');p.locator('[data-input-form]').evaluate('f=>f.noValidate=true');p.locator('#input-dialog button[type=submit]').click();expect(p.locator('#input-dialog .has-error[data-field="weight"]')).to_be_visible();expect(p.locator('#input-dialog [name="weight"]')).to_have_value('-1')
        assert query('SELECT COUNT(*) FROM care_routinerun WHERE item_id=? AND activity_id IS NOT NULL',(items['weight'].replace('-',''),))[0][0]==0
        p.locator('#input-dialog [name="weight"]').fill('6.312');p.locator('#input-dialog button[type=submit]').dblclick();expect(p.locator('#input-dialog')).not_to_be_visible();expect(row('weight')).to_have_class(re.compile('is-done'))
        check('mobile-keyboard-validation-retains-value-and-one-completion',engine)
        # Non-measurement required fields are not silently filled.
        open_routine('cleaning');expect(p.locator('.routine-input-intro')).to_contain_text('청소');p.locator('#input-dialog [name="target"]').select_option('bedding');submit();expect(row('cleaning')).to_have_class(re.compile('is-done'))
        open_routine('note');p.locator('#input-dialog [name="detail"]').fill('확인한 변화만 입력');p.keyboard.press('Escape');expect(p.locator('[data-input-discard]')).to_be_visible();p.locator('[data-input-continue]').click();expect(p.locator('#input-dialog [name="detail"]')).to_have_value('확인한 변화만 입력');submit();expect(row('note')).to_have_class(re.compile('is-done'))
        check('cleaning-and-note-required-fields-dirty-close-and-save',engine)
        # A stock-bearing routine writes one source activity; no extra stock transaction.
        open_routine('hygiene');p.locator('#input-dialog [name="task"]').select_option('teeth');p.locator('#input-dialog [name="product"]').select_option(fixture['product']);p.locator('#input-dialog [name="used_quantity"]').fill('2.123');p.locator('#input-dialog [name="used_unit"]').select_option('g')
        before=count('hygiene',rid);p.locator('#input-dialog button[type=submit]').dblclick();expect(p.locator('#input-dialog')).not_to_be_visible();expect(row('hygiene')).to_have_class(re.compile('is-done'));assert count('hygiene',rid)==before+1;assert balance()==97.877
        link=p.locator('[data-toast-link7]').get_attribute('href');record_id=link.rstrip('/').split('/')[-1]
        p.goto(BASE+link,wait_until='networkidle');p.get_by_role('link',name='수정',exact=True).click();expect(p.locator('#input-dialog [name="used_quantity"]')).to_have_value('2.123');p.locator('#input-dialog [name="used_quantity"]').fill('3.456');submit();assert balance()==96.544
        version=query('SELECT version FROM care_activity WHERE id=?',(record_id.replace('-',''),))[0][0];csrf=p.locator('#flow-csrf7 input').input_value()
        response=ctx.request.post(BASE+root+'records/'+record_id+'/cancel/',form={'csrfmiddlewaretoken':csrf,'version':str(version),'reason':'별도 검증 자료 취소'});assert response.status==200;assert balance()==100
        p.goto(BASE+root,wait_until='networkidle');panel();expect(row('hygiene').locator('[data-action7="routine_done"]')).to_be_visible();assert count('inventory',rid)==1
        check('routine-stock-use-edit-cancel-single-source-ledger',engine)
        # The response is deliberately lost after the server committed. Reuse the exact form body.
        open_routine('retry');p.locator('#input-dialog [name="task"]').select_option('teeth');url=p.locator('[data-input-form]').get_attribute('action');url=BASE+url if url.startswith('/') else url
        token=p.locator('#input-dialog [name="client_key"]').input_value();seen=[];before=count('hygiene',rid)
        def lost(route):
            if route.request.method=='POST':
                seen.append(route.request.post_data)
                if len(seen)==1:route.fetch();route.abort('connectionreset')
                else:route.continue_()
            else:route.continue_()
        ctx.route(url,lost);p.locator('#input-dialog button[type=submit]').click();expect(p.locator('[data-input-status]')).to_contain_text('응답');assert count('hygiene',rid)==before+1
        p.get_by_role('button',name='저장 결과 다시 확인',exact=True).click();expect(p.locator('#input-dialog')).not_to_be_visible();ctx.unroute(url,lost)
        assert len(seen)==2 and all(token in s for s in seen);assert count('hygiene',rid)==before+1;expect(row('retry')).to_have_class(re.compile('is-done'))
        check('routine-lost-response-same-token-single-record',engine)
        # Concurrent same request using the browser session and its original form data.
        open_routine('concurrent');p.locator('#input-dialog [name="task"]').select_option('teeth');before=count('hygiene',rid)
        results=p.evaluate('''async()=>{const f=document.querySelector('[data-input-form]'),entries=[...new FormData(f).entries()];return Promise.all([1,2].map(async()=>{const body=new FormData();entries.forEach(([k,v])=>body.append(k,v));const r=await fetch(f.action,{method:'POST',body,headers:{'X-Taily-Dialog':'1','X-CSRFToken':body.get('csrfmiddlewaretoken')}});return {status:r.status,body:await r.json()}}))}''')
        assert all(r['status']==200 for r in results),results;assert len({r['body']['id'] for r in results})==1;assert count('hygiene',rid)==before+1
        p.locator('[data-input-form]').evaluate('f=>f.reset()');p.keyboard.press('Escape')
        if p.locator('[data-input-discard]').is_visible():p.locator('[data-input-discard-confirm]').click()
        p.goto(BASE+root,wait_until='networkidle');panel();expect(row('concurrent')).to_have_class(re.compile('is-done'))
        check('two-concurrent-routine-requests-one-result',engine)
        # New intentional quick actions must not be merged with the previous completed event.
        before=count('water',rid);button=p.locator('.quick-card[data-module="water"] [data-action7]').first;button.click();expect(p.locator('#flow-toast7')).to_contain_text('저장');expect(button).to_be_enabled();button.click();expect(button).to_be_enabled();assert count('water',rid)==before+2
        check('separate-intentional-events-remain-separate',engine)
        # Tiny-height and large-text forms keep the required action in reach.
        p.goto(BASE+root,wait_until='networkidle');p.set_viewport_size({'width':375,'height':380});open_routine('hygiene');fit(p);p.keyboard.press('Escape')
        p.set_viewport_size({'width':375,'height':812});p.evaluate("document.documentElement.style.fontSize='200%'");layout(p,'200-percent-today',engine);open_routine('hygiene');fit(p);p.keyboard.press('Escape')
        p.screenshot(path=str(OUT/(engine+'-large-text.png')))
        check('short-mobile-height-and-200-percent-text',engine)
        # Guest role may not read a private routine by its count, name, or direct form URL.
        guest=browser.new_context(viewport={'width':375,'height':812},locale='ko-KR',timezone_id='Asia/Seoul');g=guest.new_page();g.goto(BASE);g.get_by_role('button',name='일반 참여자로 보기',exact=True).click();g.goto(BASE+root,wait_until='networkidle')
        assert '권한 제한 일정' not in g.locator('#main').inner_text()
        denied=guest.request.get(BASE+root+'routines/items/'+items['blocked']+'/complete/?date='+day);assert denied.status==403
        guest.close();check('private-routine-not-exposed-to-guest',engine)
        assert not errors,errors;check('no-unhandled-javascript',engine)
    except Exception as e:
        RESULT['failures'].append({'engine':engine,'error':repr(e),'trace':traceback.format_exc()[-6000:]})
        try:p.screenshot(path=str(OUT/(engine+'-failure.png')),full_page=True);(OUT/(engine+'-failure.html')).write_text(p.content(),encoding='utf8')
        except Exception:pass
    finally:RESULT['errors']+=errors;browser.close();save()


def main():
    with socket.socket() as s:assert s.connect_ex(('127.0.0.1',PORT))!=0,'Port occupied; existing server left alone'
    DATA.mkdir(parents=True)
    source=WORK/'test-data/demo.sqlite3'
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(DATA/'demo.sqlite3')) as dst:src.backup(dst)
    env=os.environ.copy();env.update(CARE_DATA_DIR=str(DATA),CARE_DEMO_MODE='1',DJANGO_DEBUG='1',DJANGO_ALLOWED_HOSTS='127.0.0.1,localhost,testserver',APP_ORIGIN=BASE,POSTGRES_HOST='',GOOGLE_CLIENT_ID='',GOOGLE_CLIENT_SECRET='',PYTHONUTF8='1')
    log=(OUT/'server.log').open('w',encoding='utf8');server=None
    try:
        setup_fixture(env,log);fixture=json.loads((WORK/'browser-fixture.json').read_text(encoding='utf8'))
        server=subprocess.Popen([sys.executable,'manage.py','runserver',f'127.0.0.1:{PORT}','--noreload'],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
        for _ in range(100):
            if server.poll() is not None:raise RuntimeError('Owned test server exited')
            with socket.socket() as s:
                if s.connect_ex(('127.0.0.1',PORT))==0:break
            time.sleep(.1)
        with sync_playwright() as pw:
            for engine in ('chromium','firefox'):run_engine(pw,engine,fixture[engine])
    except Exception as e:RESULT['failures'].append({'stage':'setup','error':repr(e),'trace':traceback.format_exc()[-3000:]})
    finally:
        if server:
            server.terminate()
            try:server.wait(timeout=10)
            except subprocess.TimeoutExpired:server.kill()
        log.close();save()
    print(json.dumps({'checks':len(RESULT['checks']),'layouts':len(RESULT['layouts']),'failures':[{'engine':x.get('engine'), 'error':x['error']} for x in RESULT['failures']]},ensure_ascii=False))
    return bool(RESULT['failures'])

if __name__=='__main__':raise SystemExit(main())
