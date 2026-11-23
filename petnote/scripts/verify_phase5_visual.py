"""Bounded visual/accessibility probes; not a user study or accessibility certification."""
from demo_browser_session import select_fixture_role
from pathlib import Path
import json
import os
import socket
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tmp/handoff/phase5/visual"
OUT.mkdir(parents=True, exist_ok=True)
SOURCE = json.loads((ROOT / "tmp/handoff/phase5/browser/results.json").read_text(encoding="utf8"))
BASE = "http://127.0.0.1:8019"
PATH = SOURCE["room_path"]
RESULT = {"checks": [], "layouts": [], "contrast": [], "exceptions": []}

CONTRAST_JS = """el=>{
const rgb=s=>{const a=s.match(/[\\d.]+/g)?.map(Number)||[0,0,0];return [a[0],a[1],a[2],a.length>3?a[3]:1]};
const over=(top,bottom)=>top.slice(0,3).map((x,i)=>x*top[3]+bottom[i]*(1-top[3]));
const luminance=c=>c.map(x=>x/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4).reduce((a,v,i)=>a+v*[.2126,.7152,.0722][i],0);
let parents=[],p=el;while(p){parents.unshift(p);p=p.parentElement}let bg=[255,255,255];parents.forEach(p=>{bg=over(rgb(getComputedStyle(p).backgroundColor),bg)});
const s=getComputedStyle(el),fg=over(rgb(s.color),bg),a=luminance(fg),b=luminance(bg),size=parseFloat(s.fontSize),weight=Number(s.fontWeight)||400;
return {text:el.innerText.trim().slice(0,90),size,weight,foreground:s.color,background:bg,ratio:(Math.max(a,b)+.05)/(Math.min(a,b)+.05),required:(size>=24||(size>=18.66&&weight>=700))?3:4.5};}
"""


def main():
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", 8019)) == 0:
            raise RuntimeError("Port occupied; no other server stopped")
    env = os.environ.copy()
    env.update(CARE_DATA_DIR=SOURCE["data"], CARE_DEMO_MODE="1", DJANGO_DEBUG="1", APP_ORIGIN=BASE,
               DJANGO_ALLOWED_HOSTS="localhost,127.0.0.1", PYTHONUTF8="1")
    log = (OUT / "server.log").open("w", encoding="utf8")
    server = subprocess.Popen([sys.executable, "manage.py", "runserver", "127.0.0.1:8019", "--noreload"], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        for _ in range(100):
            with socket.socket() as probe:
                if probe.connect_ex(("127.0.0.1", 8019)) == 0:
                    break
            time.sleep(.1)
        from playwright.sync_api import sync_playwright, expect
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width":1440,"height":1000}, locale="ko-KR", reduced_motion="reduce")
            page = context.new_page();page.set_default_timeout(10000)
            page.goto(BASE);select_fixture_role(page, 'owner')
            scopes = [
                ("", [".pulse-main h2", ".pulse-number", ".pulse-main p", ".pulse-kicker", ".pulse-schedule p", ".tile-caption", ".quick-card .button", ".tile-value strong", ".nav-list a[aria-current]", ".demo-banner"]),
                ("modules/", [".cluster-heading h2", ".category-manage", ".module-tile-name strong", ".module-tile-name small", ".module-context", ".module-tile .button", ".category-pills button"]),
                ("records/", [".record-collection h2 a", ".record-collection header>span", ".collection-all", ".record-kind-links a", ".record-line time", ".record-person"]),
                ("overview/", [".status-overall h2", ".status-overall p", ".status-overall small", ".result-scope span", ".status-result summary strong", ".result-current", ".result-open", ".period-results p"]),
                ("modules/walk/statistics/", [".date-shortcuts a", ".metric strong", ".metric span", ".metric p", ".chart-head span"]),
            ]
            for suffix, selectors in scopes:
                page.goto(BASE+PATH+suffix,wait_until="networkidle")
                for selector in selectors:
                    for element in page.locator(selector).all()[:6]:
                        if not element.is_visible() or not element.inner_text().strip():
                            continue
                        item=element.evaluate(CONTRAST_JS);item.update(screen=suffix or "today",selector=selector);RESULT["contrast"].append(item)
            for suffix in ["", "modules/", "records/", "overview/"]:
                for mode in ["double", "spacing", "narrow"]:
                    page.set_viewport_size({"width":320 if mode=="narrow" else 1440,"height":1000})
                    page.goto(BASE+PATH+suffix,wait_until="networkidle")
                    if mode!="narrow":
                        css="html{font-size:200%!important}" if mode=="double" else "body *{line-height:1.5!important;letter-spacing:.12em!important;word-spacing:.16em!important}p{margin-bottom:2em!important}"
                        url=BASE+'/static/phase5-test-'+mode+'.css'
                        page.route(url,lambda route:route.fulfill(status=200,content_type='text/css',body=css))
                        page.evaluate("url=>new Promise((resolve,reject)=>{const l=document.createElement('link');const timer=setTimeout(()=>reject(new Error('Test stylesheet load timeout')),8000);l.rel='stylesheet';l.href=url;l.onload=()=>{clearTimeout(timer);resolve()};l.onerror=()=>{clearTimeout(timer);reject(new Error('Test stylesheet failed'))};document.head.append(l)})",url)
                    item={"screen":suffix or "today","mode":mode,"overflow":page.evaluate('document.documentElement.scrollWidth>innerWidth+1')}
                    RESULT["layouts"].append(item);page.screenshot(path=str(OUT/((suffix.strip('/').replace('/','-') or 'today')+'-'+mode+'.png')),full_page=True)
            RESULT["checks"].append('text-resize-and-spacing-under-original-CSP')
            page.set_viewport_size({"width":390,"height":844});page.goto(BASE+PATH,wait_until="networkidle")
            for element in page.locator('.mobile-dock a').all():
                item=element.evaluate(CONTRAST_JS);item.update(screen='mobile-nav',selector='.mobile-dock a');RESULT['contrast'].append(item)
                box=element.bounding_box();assert box['width']>=44 and box['height']>=44
            RESULT['checks'].append('mobile-navigation-contrast-and-touch-targets')
            for suffix in ['','modules/','records/','overview/']:
                page.goto(BASE+PATH+suffix,wait_until='networkidle');page.screenshot(path=str(OUT/((suffix.strip('/') or 'today')+'-viewport.png')))
            # Focus at the end of a long page must remain clear of the fixed bottom navigation.
            page.goto(BASE+PATH+'modules/settings/',wait_until='networkidle')
            handle=page.locator('#chosen-modules .drag-handle').last;handle.focus()
            handle.evaluate("e=>e.scrollIntoView({block:'nearest',behavior:'instant'})")
            b=handle.bounding_box();dock=page.locator('.mobile-dock').bounding_box();assert b['y']+b['height']<=dock['y']+1
            RESULT['checks'].append('focused-control-clear-of-bottom-nav')
            page.goto(BASE+PATH+'modules/',wait_until='networkidle');page.locator('[data-module-key="meal"] a[data-quick]').click();expect(page.locator('#quick-content form')).to_be_visible()
            page.screenshot(path=str(OUT/'quick-390.png'));page.get_by_role('button',name='빠른 기록 닫기',exact=True).click()
            page.locator('[data-category-manage="nutrition"]').click();expect(page.locator('#category-dialog')).to_be_visible();page.screenshot(path=str(OUT/'category-390.png'));page.keyboard.press('Escape')
            RESULT['checks'].append('quick-and-category-dialog-small-screen')
            violations=[x for x in RESULT['contrast'] if x['ratio']+0.01<x['required']]
            RESULT['contrast_failures']=violations
            assert not violations, 'Text contrast failures'
            assert not any(x['overflow'] for x in RESULT['layouts']), 'Resize/spacing reflow failures'
            browser.close()
    except Exception as exc:
        RESULT['exceptions'].append(str(exc));RESULT['traceback']=traceback.format_exc()
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
        log.close();(OUT/'results.json').write_text(json.dumps(RESULT,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'checks':len(RESULT['checks']),'layouts':len(RESULT['layouts']),'contrast_samples':len(RESULT['contrast']),'exceptions':RESULT['exceptions']},ensure_ascii=False))
    return 1 if RESULT['exceptions'] else 0


if __name__=='__main__':
    raise SystemExit(main())
