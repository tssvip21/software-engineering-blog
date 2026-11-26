"""Authorized local end-to-end UI tests on a disposable database, never real user records."""
from demo_browser_session import select_fixture_role
from pathlib import Path
from contextlib import closing
from datetime import datetime,timezone
import json,re,sqlite3,traceback,uuid
from playwright.sync_api import sync_playwright,expect

ROOT=next(parent for parent in Path(__file__).resolve().parents if (parent / "manage.py").is_file());WORK=ROOT/'tmp/handoff/review-20261112';OUT=WORK/'browser';OUT.mkdir(exist_ok=True)
BASE='http://127.0.0.1:8037';FIX=json.loads((ROOT/'tmp/handoff/phase7/fixture.json').read_text(encoding='utf8'));RID=FIX['rooms']['rich'];PATH=f'/rooms/{RID}/'
RESULT={'at':datetime.now(timezone.utc).isoformat(),'checks':[],'layouts':[],'failures':[],'screens':[]}
def save():(WORK/'browser-results.json').write_text(json.dumps(RESULT,ensure_ascii=False,indent=2),encoding='utf8')
def check(name,engine,extra=None):RESULT['checks'].append({'name':name,'engine':engine,'extra':extra});save()
def db(sql,args=()):
    with closing(sqlite3.connect((WORK/'test-data/demo.sqlite3').as_uri()+'?mode=ro',uri=True)) as c:return c.execute(sql,args).fetchall()
def count(module):return db('SELECT COUNT(*) FROM care_activity WHERE room_id=? AND module=? AND voided=0',(RID.replace('-',''),module))[0][0]
def screenshot(page,name,full=False):
    page.screenshot(path=str(OUT/(name+'.png')),full_page=full);RESULT['screens'].append(name);save()
def layout(page,label,engine,width):
    r=page.evaluate('({w:innerWidth,sw:document.documentElement.scrollWidth})')
    RESULT['layouts'].append({'engine':engine,'label':label,'width':width,'overflow':r['sw']>r['w']+1})
    assert r['sw']<=r['w']+1,(label,r)
def modal_fit(page):
    box=page.locator('#input-dialog').bounding_box();vp=page.viewport_size
    assert box and box['width']<=min(680,vp['width']) and box['x']>=0 and box['y']>=-1,(box,vp)
    footer=page.locator('.input-dialog-footer').bounding_box();assert footer and footer['y']+footer['height']<=vp['height']+1


def verify_loading_races(p,engine):
    # Delayed response bodies may finish after the caller has opened another form.
    p.goto(BASE+PATH+'modules/',wait_until='networkidle')
    p.evaluate("""() => {const real=window.fetch;window.__restoreFetch=()=>{window.fetch=real;};window.__oldReady=false;
      window.fetch=async (...args)=>{const r=await real(...args);if(String(args[0]).endsWith('/modules/weight/entry/')&&args[1]?.headers?.['X-Taily-Dialog']==='1') {
        const html=await r.text();window.__oldReady=true;
        return {ok:r.ok,status:r.status,headers:r.headers,text:()=>new Promise(resolve=>{window.__releaseOld=()=>resolve(html);})};
      }return r;};} """)
    try:
        p.locator('[data-module-key="weight"] a[href$="/entry/"]').first.click()
        for _ in range(20):
            if p.evaluate('() => Boolean(window.__oldReady&&window.__releaseOld)'):break
            p.wait_for_timeout(100)
        assert p.evaluate('() => Boolean(window.__oldReady&&window.__releaseOld)')
        p.keyboard.press('Escape');p.locator('[data-module-key="observation"] a[href$="/entry/"]').first.click()
        expect(p.locator('#input-dialog [name="detail"]')).to_be_visible()
        p.evaluate('() => window.__releaseOld()');p.wait_for_timeout(200)
        expect(p.locator('#input-dialog-title')).to_contain_text('특이사항')
        assert p.locator('#input-dialog [name="weight"]').count()==0
        p.keyboard.press('Escape');check('late-old-body-cannot-replace-new-dialog',engine)
    finally:p.evaluate('() => window.__restoreFetch()')
    p.goto(BASE+PATH+'modules/',wait_until='networkidle')
    p.evaluate("""() => {const realFetch=window.fetch,realTimeout=window.setTimeout;
      window.__restoreLoad=()=>{window.fetch=realFetch;window.setTimeout=realTimeout;};
      window.setTimeout=(fn,delay,...rest)=>realTimeout(fn,delay===15000?400:delay,...rest);
      window.fetch=(url,opts)=>{if(opts?.headers?.['X-Taily-Dialog']==='1') {
        const old=String(url).endsWith('/modules/weight/entry/');
        return new Promise((resolve,reject)=>opts.signal.addEventListener('abort',()=>realTimeout(()=>reject(new DOMException('Aborted','AbortError')),old?150:0),{once:true}));
      }return realFetch(url,opts);};} """)
    try:
        p.locator('[data-module-key="weight"] a[href$="/entry/"]').first.click();p.keyboard.press('Escape')
        p.locator('[data-module-key="observation"] a[href$="/entry/"]').first.click()
        expect(p.locator('[data-input-load-error]')).to_be_visible(timeout=4000)
        expect(p.locator('[data-input-load-error]')).to_contain_text('응답이 늦어요')
        p.keyboard.press('Escape');check('new-dialog-timeout-survives-cancelled-request-cleanup',engine)
    finally:p.evaluate('() => window.__restoreLoad()')

def run():
    with sync_playwright() as pw:
        for engine in ('chromium','firefox'):
            b=getattr(pw,engine).launch(headless=True);c=b.new_context(viewport={'width':1440,'height':1000},locale='ko-KR',timezone_id='Asia/Seoul',reduced_motion='reduce');p=c.new_page();p.set_default_timeout(12000);errors=[]
            p.on('pageerror',lambda e:errors.append(str(e)))
            try:
                p.goto(BASE,wait_until='networkidle');select_fixture_role(p, 'owner');p.wait_for_load_state('networkidle')
                expect(p.locator('.room-card')).to_have_count(3)
                order=p.locator('.room-card .meta').all_text_contents();assert order==['강아지','고양이','조류·앵무새'],order
                p.get_by_role('link',name=re.compile('추가 검수 자료')).click();assert p.locator('.room-card').count()>=2
                check('three-demo-species-and-preserved-auxiliary',engine,order)
                for width in (375,768,1024,1440,1920):
                    p.set_viewport_size({'width':width,'height':1000})
                    for label,suffix in [('today',''),('modules','modules/'),('routines','routines/'),('overview','overview/'),('members','members/')]:
                        r=p.goto(BASE+PATH+suffix,wait_until='networkidle');assert r.status==200,(label,r.status)
                        layout(p,label,engine,width)
                        if width in (375,1440) and label in ('today','routines','overview'):screenshot(p,engine+'-'+label+'-'+str(width),full=True)
                        if label=='today':
                            boxes=p.locator('.quick-card').evaluate_all('es=>es.map(e=>{const c=e.getBoundingClientRect(),a=e.querySelector(".flow-primary7").getBoundingClientRect();return {top:c.y,bottom:c.bottom,action:a.bottom}})')
                            groups={}
                            for row in boxes:groups.setdefault(round(row['top']),[]).append(row)
                            for rows in groups.values():
                                assert max(x['action'] for x in rows)-min(x['action'] for x in rows)<2,rows
                        if label=='routines' and width>=1440:
                            widths=p.locator('.schedule-item7').evaluate_all('es=>es.map(e=>e.getBoundingClientRect().width)')
                            assert not widths or max(widths)<=514,widths
                check('25-layout-cases-and-bottom-aligned-actions',engine)
                # Hover is tied to the concrete link; neighbouring labels do not acquire it.
                p.set_viewport_size({'width':1440,'height':1000});p.goto(BASE+PATH+'overview/',wait_until='networkidle')
                names=p.locator('.member-link7');source=p.locator('.metric-pair7 a').first
                p.mouse.move(5,5)
                assert all(x=='none' for x in names.evaluate_all('es=>es.map(e=>getComputedStyle(e).textDecorationLine)'))
                source.hover();assert 'underline' in source.evaluate('e=>getComputedStyle(e).textDecorationLine')
                assert all(x=='none' for x in names.evaluate_all('es=>es.map(e=>getComputedStyle(e).textDecorationLine)'))
                neighbours=p.locator('.metric-pair7 a').evaluate_all('es=>es.slice(1).map(e=>getComputedStyle(e).textDecorationLine)')
                assert all(x=='none' for x in neighbours),neighbours
                check('hover-only-on-target-link',engine)
                # All selected entry forms open in the same surface on phone width.
                p.set_viewport_size({'width':375,'height':812});p.goto(BASE+PATH+'modules/',wait_until='networkidle')
                keys=p.locator('[data-module-key]').evaluate_all('es=>es.map(e=>e.dataset.moduleKey)')
                for key in keys:
                    a=p.locator(f'[data-module-key="{key}"] a[href$="/entry/"]').first
                    a.click();expect(p.locator('#input-dialog [data-input-form]')).to_be_visible();modal_fit(p);assert p.url==BASE+PATH+'modules/'
                    p.keyboard.press('Escape');expect(p.locator('#input-dialog')).not_to_be_visible();expect(a).to_be_focused()
                check('18-in-place-input-forms-and-return-focus',engine,len(keys))
                # Keyboard entry, dirty-close confirmation within the same modal, error retaining values.
                p.goto(BASE+PATH,wait_until='networkidle');anchor=p.locator('.quick-card[data-module="weight"] a[href$="/entry/"]').first
                anchor.focus();p.keyboard.press('Enter');expect(p.locator('#input-dialog [name="weight"]')).to_be_visible()
                for _ in range(24):p.keyboard.press('Tab');assert p.evaluate('document.querySelector("#input-dialog").contains(document.activeElement)')
                p.locator('#input-dialog [name="weight"]').fill('8.3');p.keyboard.press('Escape');expect(p.locator('[data-input-discard]')).to_be_visible()
                p.get_by_role('button',name='계속 작성',exact=True).click();expect(p.locator('#input-dialog [name="weight"]')).to_have_value('8.3')
                p.locator('#input-dialog [name="weight"]').fill('-1');p.locator('[data-input-form]').evaluate('f=>f.noValidate=true')
                before=count('weight');p.locator('#input-dialog button[type="submit"]').click();expect(p.locator('#input-dialog .has-error')).to_be_visible();assert count('weight')==before
                expect(p.locator('#input-dialog [name="weight"]')).to_have_value('-1')
                p.locator('#input-dialog [name="weight"]').fill('8.3');p.locator('#input-dialog button[type="submit"]').dblclick()
                expect(p.locator('#input-dialog')).not_to_be_visible();expect(p.locator('#flow-toast7')).to_contain_text('저장')
                p.wait_for_timeout(300);assert count('weight')==before+1;assert p.url==BASE+PATH
                expect(p.locator('.quick-card[data-module="weight"] a[href$="/entry/"]').first).to_be_focused()
                check('keyboard-dirty-error-double-submit-one-record',engine)
                # Filter/search/scroll remain after saving on a filtered board.
                p.goto(BASE+PATH+'modules/',wait_until='networkidle');p.locator('[data-module-filter="nutrition"]').click();p.locator('#workspace-search').fill('간식')
                link=p.locator('[data-module-key="treat"] a[href$="/entry/"]').first;link.scroll_into_view_if_needed();scroll=p.evaluate('scrollY');link.click()
                p.locator('#input-dialog [name="quantity"]').fill('8');p.locator('#input-dialog button[type="submit"]').click();expect(p.locator('#input-dialog')).not_to_be_visible()
                p.wait_for_timeout(300);expect(p.locator('#workspace-search')).to_have_value('간식');expect(p.locator('[data-module-filter="nutrition"]')).to_have_attribute('aria-pressed','true');assert abs(p.evaluate('scrollY')-scroll)<8,(scroll,p.evaluate('scrollY'))
                check('filter-search-scroll-preserved-after-save',engine)
                # New routine input is bounded, scrollable, and saved once with request UUID.
                p.goto(BASE+PATH+'routines/',wait_until='networkidle');p.get_by_role('link',name='요일·시간 루틴 추가',exact=True).click();expect(p.locator('[data-dialog-routine]')).to_be_visible();modal_fit(p)
                title='검증 루틴 '+engine+' '+uuid.uuid4().hex[:5];p.locator('#input-dialog [name="name"]').fill(title);p.locator('#input-dialog [name="at"]').fill('20:00');p.locator('#input-dialog [name="modules"][value="meal"]').check()
                screenshot(p,engine+'-routine-modal-375');p.locator('#input-dialog button[type="submit"]').dblclick();expect(p.locator('#input-dialog')).not_to_be_visible();p.wait_for_timeout(300)
                assert db('SELECT COUNT(*) FROM care_routine WHERE room_id=? AND name=?',(RID.replace('-',''),title))[0][0]==1;assert p.url==BASE+PATH+'routines/'
                check('routine-modal-save-and-duplicate-protection',engine)
                # Existing member role policy with redesigned hierarchy; no destructive action submitted.
                p.set_viewport_size({'width':1440,'height':1000});p.goto(BASE+PATH,wait_until='networkidle')
                other=p.locator('.member-link7').last;other.click();expect(p.locator('#member-dialog7 [name="role"]')).to_be_visible()
                expect(p.locator('#member-dialog7 .member-danger7')).not_to_have_attribute('open','')
                screenshot(p,engine+'-member-modal');p.keyboard.press('Escape');expect(other).to_be_focused()
                p.locator('.member-link7').first.click();expect(p.locator('#member-name7')).to_be_visible();assert p.locator('#member-dialog7 [name="role"]').count()==0;p.keyboard.press('Escape')
                check('member-self-and-role-management-separated',engine)
                if engine=='chromium':
                    # A response lost after server commit reuses the frozen form UUID/body.
                    p.goto(BASE+PATH,wait_until='networkidle');a=p.locator('.quick-card[data-module="weight"] a[href$="/entry/"]').first;a.click();p.locator('#input-dialog [name="weight"]').fill('8.4');token=p.locator('#input-dialog [name="client_key"]').input_value();url=BASE+PATH+'modules/weight/entry/';seen=[]
                    def loss(route):
                        if route.request.method=='POST':
                            seen.append(route.request.post_data)
                            if len(seen)==1:route.fetch();route.abort('connectionreset')
                            else:route.continue_()
                        else:route.continue_()
                    c.route(url,loss);before=count('weight');p.locator('#input-dialog button[type="submit"]').click();expect(p.locator('[data-input-status]')).to_contain_text('응답')
                    assert count('weight')==before+1;expect(p.locator('#input-dialog [name="weight"]')).to_be_disabled()
                    p.get_by_role('button',name='저장 결과 다시 확인',exact=True).click();expect(p.locator('#input-dialog')).not_to_be_visible();assert count('weight')==before+1
                    assert len(seen)==2 and all(token in x for x in seen);c.unroute(url,loss);check('lost-response-same-uuid-no-second-record',engine)
                    # GET loading and recoverable 503: caller page is unchanged.
                    p.goto(BASE+PATH+'modules/',wait_until='networkidle');url=BASE+PATH+'modules/weight/entry/';held=[]
                    c.route(url,lambda route:held.append(route));p.locator('[data-module-key="weight"] a[href$="/entry/"]').first.click();expect(p.locator('[data-input-loading]')).to_be_visible();assert held;held[0].fulfill(status=503,body='test failure');expect(p.locator('[data-input-load-error]')).to_be_visible();assert p.url==BASE+PATH+'modules/'
                    c.unroute(url);p.get_by_role('button',name='다시 불러오기',exact=True).click();expect(p.locator('[data-input-form]')).to_be_visible();p.keyboard.press('Escape');check('input-loading-503-retry-preserves-caller',engine)
                verify_loading_races(p,engine)
                p.set_viewport_size({'width':375,'height':900});p.goto(BASE+PATH+'modules/',wait_until='networkidle');p.evaluate("document.documentElement.style.fontSize='200%'");layout(p,'modules-200%',engine,375)
                p.locator('[data-module-key="observation"] a[href$="/entry/"]').first.click();expect(p.locator('[data-input-form]')).to_be_visible();modal_fit(p);screenshot(p,engine+'-input-200-percent');p.keyboard.press('Escape')
                check('200-percent-text-and-modal-controls-visible',engine)
                assert not errors,errors;check('no-unhandled-javascript-errors',engine)
            except Exception as exc:
                RESULT['failures'].append({'engine':engine,'error':repr(exc),'trace':traceback.format_exc()[-6500:]})
                try:screenshot(p,engine+'-failure',full=True);(OUT/(engine+'-failure.html')).write_text(p.content(),encoding='utf8')
                except Exception:pass
            finally:b.close();save()
    print(json.dumps({'checks':len(RESULT['checks']),'layouts':len(RESULT['layouts']),'failures':[{'engine':x['engine'],'error':x['error']} for x in RESULT['failures']]},ensure_ascii=False))
    return bool(RESULT['failures'])
if __name__=='__main__':raise SystemExit(run())
