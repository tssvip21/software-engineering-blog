"""Focused visual/accessibility checks, reusing only the isolated browser fixture."""
from demo_browser_session import select_fixture_role
from pathlib import Path
import json,os,socket,subprocess,sys,time,traceback
ROOT=next(parent for parent in Path(__file__).resolve().parents if (parent / "manage.py").is_file());OUT=ROOT/'tmp/handoff/phase4/visual';OUT.mkdir(parents=True,exist_ok=True)
BASE='http://127.0.0.1:8016'
source=json.loads((ROOT/'tmp/handoff/phase4/browser/results.json').read_text(encoding='utf8'))
DATA=Path(source['data']).resolve();path=source['room_path']
assert DATA.is_relative_to((ROOT/'tmp/handoff/phase4').resolve()) and DATA.name.startswith('browser-data-')
RESULT={'checks':[],'layout':[],'contrast':[],'errors':[]}

def main():
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1',8016))==0:raise SystemExit('Port occupied; no process was stopped.')
    env=os.environ.copy();env.update(CARE_DATA_DIR=str(DATA),CARE_DEMO_MODE='1',DJANGO_DEBUG='1',APP_ORIGIN=BASE,DJANGO_ALLOWED_HOSTS='localhost,127.0.0.1',PYTHONUTF8='1')
    log=(OUT/'server.log').open('w',encoding='utf8');server=subprocess.Popen([sys.executable,'manage.py','runserver','127.0.0.1:8016','--noreload'],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
    from playwright.sync_api import sync_playwright,expect
    page=None
    try:
        for _ in range(100):
            with socket.socket() as probe:
                if probe.connect_ex(('127.0.0.1',8016))==0:break
            time.sleep(.1)
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True);ctx=browser.new_context(viewport={'width':1440,'height':1000},locale='ko-KR')
            page=ctx.new_page();page.on('pageerror',lambda e:RESULT['errors'].append(str(e)))
            page.goto(BASE);select_fixture_role(page, 'owner');page.wait_for_url(BASE+'/')
            # A same-origin test stylesheet obeys the app CSP. No production policy changes.
            test_style=BASE+'/static/care/verification-spacing.css'
            stylesheet='html{font-size:200%!important} *{line-height:1.5!important;letter-spacing:.12em!important;word-spacing:.16em!important} p{margin-bottom:2em!important}'
            ctx.route(test_style,lambda route:route.fulfill(status=200,content_type='text/css',body=stylesheet))
            for suffix in ('modules/','records/?category=nutrition','overview/','settings/','routines/'):
                page.goto(BASE+path+suffix,wait_until='networkidle');page.add_style_tag(url=test_style)
                overflow=page.evaluate('document.documentElement.scrollWidth>innerWidth+1')
                RESULT['layout'].append({'page':suffix,'width':1440,'font':'200%','spacing':True,'overflow':overflow})
                if overflow:
                    RESULT['overflow_nodes']=page.locator('body *').evaluate_all('(es)=>es.filter(e=>e.getBoundingClientRect().right>innerWidth+2&&e.getBoundingClientRect().width>0).slice(-12).map(e=>({tag:e.tagName,cls:e.className,text:e.innerText?.slice(0,80),right:e.getBoundingClientRect().right}))')
                assert not overflow,('text-spacing',suffix)
                if suffix in ('modules/','overview/'):page.screenshot(path=str(OUT/(suffix.strip('/')+'-text200.png')),full_page=True)
            RESULT['checks'].append('200-percent-text-and-spacing-five-main-screens')
            stylesheet='*{line-height:1.5!important;letter-spacing:.12em!important;word-spacing:.16em!important} p{margin-bottom:2em!important}'
            for suffix in ('modules/','records/','overview/','routines/'):
                page.set_viewport_size({'width':320,'height':900});page.goto(BASE+path+suffix,wait_until='networkidle');page.add_style_tag(url=test_style)
                overflow=page.evaluate('document.documentElement.scrollWidth>innerWidth+1');RESULT['layout'].append({'page':suffix,'width':320,'spacing':True,'overflow':overflow});assert not overflow,('mobile-spacing',suffix)
            RESULT['checks'].append('320px-with-user-text-spacing')
            page.set_viewport_size({'width':1440,'height':1000});page.goto(BASE+path+'overview/',wait_until='networkidle')
            # Measure actual foreground and nearest opaque background on representative text roles.
            contrast_js='''(selectors)=>{
              const rgb=s=>(s.match(/[\\d.]+/g)||[]).map(Number);
              const lum=c=>c.slice(0,3).map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4}).reduce((s,v,i)=>s+v*[.2126,.7152,.0722][i],0);
              return selectors.flatMap(selector=>[...document.querySelectorAll(selector)].filter(e=>e.getClientRects().length&&!e.closest('[hidden]')).slice(0,5).map(e=>{
                const cs=getComputedStyle(e);let p=e,bg=[255,255,255];
                while(p){const c=rgb(getComputedStyle(p).backgroundColor);if(c.length===3||c[3]===1){bg=c;break;}p=p.parentElement;}
                const fg=rgb(cs.color),l1=lum(fg),l2=lum(bg),ratio=(Math.max(l1,l2)+.05)/(Math.min(l1,l2)+.05);
                return {selector,text:e.textContent.trim().slice(0,50),size:parseFloat(cs.fontSize),weight:cs.fontWeight,ratio:+ratio.toFixed(2),foreground:cs.color,background:bg};
              }));}'''
            colors=page.evaluate(contrast_js,['.status-overall h2','.status-overall p','.status-overall small','.result-label','.status-result summary strong','.status-result summary small','.result-open','.nav-list a[aria-current]','.period-results p'])
            RESULT['contrast']+=colors
            for item in colors:
                minimum=3 if item['size']>=24 or (item['size']>=18.66 and int(item['weight'])>=700) else 4.5
                assert item['ratio']>=minimum,('contrast',item)
            RESULT['checks'].append('measured-result-and-navigation-text-contrast')
            # Interaction targets remain usable on a real touch-emulated browser context.
            mobile=browser.new_context(viewport={'width':390,'height':844},locale='ko-KR',is_mobile=True,has_touch=True,storage_state=ctx.storage_state())
            m=mobile.new_page();m.goto(BASE+path+'modules/',wait_until='networkidle');m.locator('[data-category-manage="living"]').tap();expect(m.locator('[data-category-choice]')).to_have_count(6)
            item=m.locator('[data-category-choice][value="cleaning"]');old=item.is_checked();item.tap();assert item.is_checked()!=old
            m.locator('#category-form button[type=submit]').tap();expect(m.locator('#category-dialog')).not_to_be_visible();expect(m.locator('[data-category-manage="living"]')).to_be_visible()
            RESULT['checks'].append('touch-select-apply-and-return')
            m.goto(BASE+path+'modules/settings/',wait_until='networkidle')
            original=m.locator('#chosen-modules .chosen-row').evaluate_all('(r)=>r.map(e=>e.dataset.key)')
            m.locator('#chosen-modules .chosen-row').first.scroll_into_view_if_needed()
            a=m.locator('#chosen-modules .chosen-row').nth(1).locator('.drag-handle').bounding_box();b=m.locator('#chosen-modules .chosen-row').first.bounding_box()
            x=a['x']+a['width']/2;y=a['y']+a['height']/2;target=b['y']+10
            cdp=mobile.new_cdp_session(m);cdp.send('Input.dispatchTouchEvent',{'type':'touchStart','touchPoints':[{'x':x,'y':y,'id':1}]})
            for step in range(1,13):
                cdp.send('Input.dispatchTouchEvent',{'type':'touchMove','touchPoints':[{'x':x,'y':y+(target-y)*step/12,'id':1}]});m.wait_for_timeout(16)
            cdp.send('Input.dispatchTouchEvent',{'type':'touchEnd','touchPoints':[]})
            expect(m.locator('[data-module-config]')).to_have_attribute('data-status','saved')
            m.reload(wait_until='networkidle');assert m.locator('#chosen-modules .chosen-row').first.get_attribute('data-key')==original[1]
            mobile.close();RESULT['checks'].append('touch-drag-reflow-and-persistence')
            page.goto(BASE+'/rooms/new/',wait_until='networkidle');page.set_viewport_size({'width':390,'height':1000})
            page.locator('#id_species').select_option('cat');page.locator('#breed-search').fill('British');page.get_by_role('option',name='브리티시 쇼트헤어 British Shorthair').click()
            expect(page.locator('#id_size')).not_to_be_visible();page.screenshot(path=str(OUT/'registration-mobile.png'),full_page=True)
            RESULT['checks'].append('english-cat-breed-search-and-species-exception')
            assert not RESULT['errors'];browser.close()
    except Exception as error:
        RESULT['exception']=type(error).__name__+': '+str(error);RESULT['traceback']=traceback.format_exc()
        if page:
            try:page.screenshot(path=str(OUT/'failure.png'),full_page=True)
            except Exception:pass
    finally:
        server.terminate()
        try:server.wait(timeout=10)
        except subprocess.TimeoutExpired:server.kill()
        log.close();(OUT/'results.json').write_text(json.dumps(RESULT,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'checks':len(RESULT['checks']),'layouts':len(RESULT['layout']),'exception':RESULT.get('exception')},ensure_ascii=False))
    if RESULT.get('exception'):raise SystemExit(1)
if __name__=='__main__':main()
