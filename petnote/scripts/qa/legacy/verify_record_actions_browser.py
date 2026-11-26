"""Actual browser/HTTP verification using a disposable copy, never the user's databases."""
from demo_browser_session import select_fixture_role
from contextlib import closing
from pathlib import Path
from datetime import datetime,timezone
from urllib.parse import urlparse
import json,os,socket,sqlite3,subprocess,sys,time,traceback,uuid
from playwright.sync_api import sync_playwright,expect

ROOT=next(parent for parent in Path(__file__).resolve().parents if (parent / "manage.py").is_file());OUT=ROOT/'tmp/handoff/phase7/browser';OUT.mkdir(parents=True,exist_ok=True)
SOURCE=ROOT/'tmp/handoff/phase7/preview-data/demo.sqlite3';FIXTURE=ROOT/'tmp/handoff/phase7/fixture.json'
DATA=ROOT/('tmp/handoff/phase7/browser-data-'+uuid.uuid4().hex[:8]);PORT=8032;BASE=f'http://127.0.0.1:{PORT}'
RESULT={'at':datetime.now(timezone.utc).isoformat(),'checks':[],'layouts':[],'forms':[],'errors':[],'hosts':[],'data':str(DATA),'port':PORT}
FAST_MODE=os.environ.get('PHASE7_BROWSER_FAST')=='1'
RESULT['fast_mode']=FAST_MODE
def save():(OUT/'results.json').write_text(json.dumps(RESULT,ensure_ascii=False,indent=2),encoding='utf8')
def check(name,detail=None):RESULT['checks'].append({'name':name,'detail':detail});save()
def db(query,args=()):
    with closing(sqlite3.connect((DATA/'demo.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=10)) as conn:return conn.execute(query,args).fetchall()
def count(module=None):
    sql="SELECT COUNT(*) FROM care_activity WHERE room_id=? AND voided=0";args=[room_id.replace('-','')]
    if module:sql+=' AND module=?';args.append(module)
    return db(sql,args)[0][0]

def main():
    global room_id
    assert SOURCE.exists() and FIXTURE.exists(),'Create local phase7 synthetic examples first'
    with socket.socket() as s:
        if s.connect_ex(('127.0.0.1',PORT))==0:raise RuntimeError('Port occupied; no other server terminated')
    DATA.mkdir(parents=True)
    with closing(sqlite3.connect(SOURCE.as_uri()+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(DATA/'demo.sqlite3')) as dst:src.backup(dst)
    fixture=json.loads(FIXTURE.read_text(encoding='utf8'));room_id=fixture['rooms']['rich'];path=f'/rooms/{room_id}/'
    RESULT['room_path']=path;RESULT['rooms']=fixture['rooms']
    env=os.environ.copy();env.update(CARE_DATA_DIR=str(DATA),CARE_DEMO_MODE='1',DJANGO_DEBUG='1',DJANGO_ALLOWED_HOSTS='127.0.0.1,localhost,testserver',APP_ORIGIN=BASE,PYTHONUTF8='1')
    log=(OUT/'server.log').open('w',encoding='utf8');server=subprocess.Popen([sys.executable,'manage.py','runserver',f'127.0.0.1:{PORT}','--noreload'],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
    page=browser=None
    try:
        for _ in range(100):
            with socket.socket() as s:
                if s.connect_ex(('127.0.0.1',PORT))==0:break
            if server.poll() is not None:raise RuntimeError('Test server exited')
            time.sleep(.1)
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True)
            context=browser.new_context(viewport={'width':1440,'height':1000},locale='ko-KR',timezone_id='Asia/Seoul',reduced_motion='reduce')
            page=context.new_page();page.set_default_timeout(12000)
            page.on('pageerror',lambda e:RESULT['errors'].append(str(e)))
            page.on('request',lambda r:RESULT['hosts'].append(urlparse(r.url).hostname) if urlparse(r.url).hostname not in RESULT['hosts'] else None)
            page.goto(BASE);select_fixture_role(page, 'owner');page.wait_for_url(BASE+'/')
            pages={'today':'','modules':'modules/','overview':'overview/','config':'modules/settings/','records':'records/','routines':'routines/','settings':'settings/','walk':'modules/walk/','statistics':'modules/walk/statistics/','presets':'modules/meal/presets/','entry':'modules/medication/entry/'}
            if not FAST_MODE:
                for width in [320,375,390,768,1024,1440,1920]:
                    page.set_viewport_size({'width':width,'height':1000})
                    for name,suffix in pages.items():
                        response=page.goto(BASE+path+suffix,wait_until='networkidle')
                        overflow=page.evaluate('document.documentElement.scrollWidth>innerWidth+1')
                        RESULT['layouts'].append({'page':name,'width':width,'status':response.status,'overflow':overflow})
                        if width in (375,1440):page.screenshot(path=str(OUT/f'{name}-{width}.png'),full_page=True)
                        if response.status!=200 or overflow:
                            RESULT.setdefault('bad_layouts',[]).append({'page':name,'width':width,'status':response.status,'nodes':page.locator('#main *').evaluate_all('es=>es.filter(e=>e.getBoundingClientRect().width>0&&e.getBoundingClientRect().right>innerWidth+1).slice(-12).map(e=>({tag:e.tagName,cls:e.className,text:(e.innerText||"").slice(0,80),width:e.getBoundingClientRect().width}))')})
                    save()
                assert not RESULT.get('bad_layouts'),RESULT.get('bad_layouts')
                check('77-responsive-screen-cases-including-375px')
                for key in ('cat','bird','empty','sparse'):
                    for width in (375,1440):
                        page.set_viewport_size({'width':width,'height':1000});response=page.goto(BASE+'/rooms/'+fixture['rooms'][key]+'/overview/',wait_until='networkidle')
                        assert response.status==200 and not page.evaluate('document.documentElement.scrollWidth>innerWidth+1')
                        page.screenshot(path=str(OUT/f'{key}-overview-{width}.png'),full_page=True)
                check('cat-parrot-empty-sparse-weight-views')
            page.set_viewport_size({'width':1440,'height':1000});page.goto(BASE+path+'overview/',wait_until='networkidle')
            expect(page.locator('[data-comparison-module="walk"]')).to_have_count(1)
            expect(page.locator('[data-comparison-module="walk"] [data-metric]')).to_have_count(2)
            assert page.locator('[data-comparison-module]').count()>=8
            assert page.locator('[data-relation]').count()>=4
            check('additional-measurement-modules-and-single-walk-card',{'modules':page.locator('[data-comparison-module]').count(),'relations':page.locator('[data-relation]').count()})
            page.locator('[data-metric="weight"] .metric-evidence7 summary').click()
            evidence_url=page.locator('[data-metric="weight"] .metric-evidence7 a').last.get_attribute('href')
            response=page.goto(BASE+evidence_url,wait_until='networkidle');assert response.status==200
            expect(page.locator('h1')).to_contain_text('비교 근거');check('exact-source-record-drilldown')
            page.goto(BASE+path+'modules/',wait_until='networkidle')
            keys=page.locator('[data-module-key]').evaluate_all('es=>es.map(e=>e.dataset.moduleKey)');assert len(keys)==18
            if not FAST_MODE:
                for width in (375,1440):
                    page.set_viewport_size({'width':width,'height':1000})
                    for key in keys:
                        response=page.goto(BASE+path+f'modules/{key}/entry/',wait_until='networkidle')
                        assert response.status==200,(key,response.status)
                        assert not page.evaluate('document.documentElement.scrollWidth>innerWidth+1'),key
                        RESULT['forms'].append({'module':key,'width':width,'status':response.status})
                check('all-18-entry-forms-at-375-and-desktop')
            page.set_viewport_size({'width':375,'height':900});page.goto(BASE+path,wait_until='networkidle')
            before=count('meal');button=page.locator('.quick-card[data-module="meal"] [data-action7]').first
            button.dblclick();expect(page.locator('#flow-toast7')).to_be_visible();expect(page.locator('#flow-toast7')).to_contain_text('저장')
            expect(page.locator('.quick-card[data-module="meal"] [data-action7]').first).to_be_enabled()
            assert count('meal')==before+1
            assert page.locator('dialog[open]').count()==0
            page.locator('[data-toast-undo7]').click();expect(page.locator('[data-toast-text7]')).to_contain_text('기록을 취소했어요');assert count('meal')==before
            check('double-click-one-record-and-stock-undo-no-success-modal')
            # Lost response: server saves, browser receives a network failure, same token is retried.
            action_url=BASE+path+'actions/';seen=[]
            def lose_response(route):
                if route.request.method=='POST':
                    seen.append(route.request.post_data_json)
                    if len(seen)==1:route.fetch();route.abort('connectionreset')
                    else:route.continue_()
                else:route.continue_()
            context.route(action_url,lose_response);before=count('meal')
            page.locator('.quick-card[data-module="meal"] [data-action7]').first.click()
            expect(page.locator('.quick-card[data-module="meal"] .flow-feedback')).to_have_class(__import__('re').compile('is-error'))
            assert count('meal')==before+1
            page.locator('.quick-card[data-module="meal"] [data-action7]').first.click();expect(page.locator('#flow-toast7')).to_contain_text('중복')
            context.unroute(action_url,lose_response);assert len(seen)==2 and seen[0]['token']==seen[1]['token'];assert count('meal')==before+1
            check('response-loss-retry-reuses-token-and-has-no-second-side-effect')
            # Concurrent same-token requests can return a retryable 503 under SQLite contention, never two rows.
            page.goto(BASE+path+'modules/',wait_until='networkidle');before=count('toilet');tok=str(uuid.uuid4())
            payload={'token':tok,'operation':'quick','module':'toilet'}
            answers=page.evaluate('''async ({url,data})=>{const csrf=document.querySelector('#flow-csrf7 input').value;return Promise.all([1,2].map(async()=>{let r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':csrf},body:JSON.stringify(data)});return {status:r.status,data:await r.json()}}))}''',{'url':action_url,'data':payload})
            assert any(r['status']==200 for r in answers),answers
            csrf=page.locator('#flow-csrf7 input').input_value()
            again=context.request.post(action_url,data=payload,headers={'X-CSRFToken':csrf});assert again.status==200
            assert count('toilet')==before+1
            assert db('SELECT COUNT(*) FROM care_actionreceipt WHERE room_id=? AND token=?',[room_id.replace('-',''),tok.replace('-','')])[0][0]==1
            check('concurrent-request-idempotence-and-receipt',answers)
            # A distinct intentional event remains allowed.
            page.locator('[data-module-key="toilet"] [data-action7]').first.click();expect(page.locator('#flow-toast7')).to_contain_text('저장');assert count('toilet')==before+2
            check('intentional-new-observation-is-not-blocked-by-debounce')
            page.goto(BASE+path+'modules/',wait_until='networkidle')
            start=page.locator('[data-module-key="play"] [data-action7="start"]').first;start.focus();page.keyboard.press('Enter')
            expect(page.locator('[data-timer-module="play"]')).to_be_visible();expect(page.locator('[data-timer-module="play"] [data-elapsed7]')).to_contain_text('분')
            finish=page.locator('[data-timer-module="play"] [data-action7="finish"]');finish.focus();page.keyboard.press('Enter')
            expect(page.locator('[data-timer-module="play"]')).to_have_count(0)
            check('keyboard-start-finish-with-persistent-server-time')
            # Pure keyboard open/Tab containment/Escape/return for personal member panel.
            page.set_viewport_size({'width':1440,'height':1000});page.goto(BASE+path,wait_until='networkidle')
            opener=page.locator('[data-member-open7]').first;opener.focus();page.keyboard.press('Enter');expect(page.locator('#member-dialog7')).to_be_visible()
            expect(page.locator('#member-panel-title7')).to_be_visible()
            for _ in range(14):page.keyboard.press('Tab');assert page.evaluate('document.querySelector("#member-dialog7").contains(document.activeElement)')
            page.keyboard.press('Shift+Tab');page.keyboard.press('Escape');expect(page.locator('#member-dialog7')).not_to_be_visible();expect(opener).to_be_focused()
            check('member-modal-keyboard-only-focus-containment-and-return')
            page.goto(BASE+path+'modules/',wait_until='networkidle')
            trigger=page.locator('[data-category-manage="nutrition"]');trigger.focus();page.keyboard.press('Enter');expect(page.locator('#category-dialog')).to_be_visible()
            page.keyboard.press('Tab');page.keyboard.press('Escape');expect(trigger).to_be_focused();check('existing-module-dialog-keyboard-return')
            page.locator('#workspace-search').fill('존재하지않는검색결과');expect(page.locator('[data-browser-empty]')).to_be_visible();page.locator('[data-clear-browse]').click()
            check('empty-filter-state-and-recovery')
            page.goto(BASE+path+'overview/',wait_until='networkidle')
            target=BASE+path+'overview/?days=30';held=[]
            def hold(route):held.append(route)
            context.route(target,hold)
            page.locator('.statistics-periods a').filter(has_text='30일').click()
            expect(page.locator('#flow-page-state7')).to_contain_text('불러오는 중')
            assert page.locator('[data-comparison-module="walk"]').count()==1
            assert held;held[0].continue_();context.unroute(target,hold)
            expect(page.locator('.statistics-periods a[aria-current]')).to_have_text('30일');check('loading-state-preserves-existing-statistics')
            target=BASE+path+'overview/?days=90'
            context.route(target,lambda route:route.fulfill(status=503,content_type='text/plain',body='synthetic server failure'))
            page.locator('.statistics-periods a').filter(has_text='90일').click();expect(page.locator('#flow-page-state7')).to_contain_text('불러오지 못했어요')
            expect(page.locator('.statistics-periods a[aria-current]')).to_have_text('30일');context.unroute(target)
            page.get_by_role('button',name='다시 불러오기',exact=True).click();expect(page.locator('.statistics-periods a[aria-current]')).to_have_text('90일')
            check('server-error-preserves-data-and-retry-succeeds')
            page.goto(BASE+path+'overview/',wait_until='networkidle')
            # Guest receives neither sensitive controls nor hidden source facts.
            guest=browser.new_context(locale='ko-KR',viewport={'width':375,'height':900});g=guest.new_page();g.goto(BASE);select_fixture_role(g, 'member');g.goto(BASE+path+'overview/',wait_until='networkidle')
            assert g.locator('[data-statistic-module="medication"]').count()==0
            assert g.request.get(BASE+path+'modules/settings/').status==403
            assert g.request.get(BASE+path+'records/?module=expense').status==403
            guest.close();check('guest-sensitive-statistics-and-source-links-protected')
            assert not RESULT['errors'],RESULT['errors'];assert RESULT['hosts']==['127.0.0.1'],RESULT['hosts']
            check('no-unhandled-javascript-or-external-runtime-requests')
            browser.close();browser=None
    except Exception as exc:
        RESULT['exception']=repr(exc);RESULT['traceback']=traceback.format_exc()
        if page:
            try:page.screenshot(path=str(OUT/'failure.png'),full_page=True);(OUT/'failure.html').write_text(page.content(),encoding='utf8')
            except Exception:pass
    finally:
        if browser:
            try:browser.close()
            except Exception:pass
        server.terminate()
        try:server.wait(timeout=10)
        except subprocess.TimeoutExpired:server.kill()
        log.close();save()
    print(json.dumps({'checks':len(RESULT['checks']),'layouts':len(RESULT['layouts']),'forms':len(RESULT['forms']),'exception':RESULT.get('exception')},ensure_ascii=False))
    return 1 if RESULT.get('exception') else 0

if __name__=='__main__':raise SystemExit(main())
