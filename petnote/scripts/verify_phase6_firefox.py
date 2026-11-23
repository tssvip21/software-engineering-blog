"""Focused Firefox compatibility checks against a running, local-only review server."""
from demo_browser_session import select_fixture_role
from pathlib import Path
import argparse,json,traceback
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright,expect
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'tmp/handoff/phase6/firefox';OUT.mkdir(parents=True,exist_ok=True)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8025);args=parser.parse_args()
    base=f'http://127.0.0.1:{args.port}';fixture=json.loads((OUT.parent/'fixture.json').read_text(encoding='utf8'));path='/rooms/'+fixture['rooms']['rich']+'/'
    report={'pages':[],'checks':[],'errors':[],'requests':[]}
    try:
        with sync_playwright() as pw:
            browser=pw.firefox.launch(headless=True);report['version']=browser.version
            ctx=browser.new_context(viewport={'width':1440,'height':1000},locale='ko-KR',reduced_motion='reduce')
            page=ctx.new_page();page.set_default_timeout(10000);page.on('pageerror',lambda e:report['errors'].append(str(e)))
            page.on('request',lambda r:report['requests'].append({'method':r.method,'host':urlparse(r.url).hostname,'path':urlparse(r.url).path}) if r.method!='GET' else None)
            page.goto(base);select_fixture_role(page, 'owner');page.wait_for_url(base+'/')
            for width in [1440,390]:
                page.set_viewport_size({'width':width,'height':1000 if width>600 else 844})
                for suffix in ['', 'modules/', 'modules/settings/', 'records/', 'overview/', 'routines/', 'settings/', 'members/']:
                    response=page.goto(base+path+suffix,wait_until='networkidle');page.evaluate('document.fonts.ready')
                    assert response.status==200;assert not page.evaluate('document.documentElement.scrollWidth>innerWidth+1')
                    report['pages'].append({'width':width,'page':suffix or 'today','http':response.status})
                page.goto(base+path+'modules/settings/',wait_until='networkidle')
                opener=page.locator('[data-library-open]').first;opener.click();expect(page.locator('#module-library')).to_be_visible()
                for _ in range(14):page.keyboard.press('Tab');assert page.evaluate('document.querySelector("#module-library").contains(document.activeElement)')
                page.screenshot(path=str(OUT/f'library-{width}.png'));page.keyboard.press('Escape');expect(opener).to_be_focused()
                keys=page.locator('#chosen-modules .chosen-row').evaluate_all('es=>es.map(e=>e.dataset.key)')
                handle=page.locator('#chosen-modules .drag-handle').first;handle.focus();page.keyboard.press('Space');page.keyboard.press('ArrowRight');page.keyboard.press('Escape')
                assert keys==page.locator('#chosen-modules .chosen-row').evaluate_all('es=>es.map(e=>e.dataset.key)')
                page.goto(base+path+'modules/');page.locator('[data-module-key="meal"] a[data-quick]').click();expect(page.locator('#quick-content form')).to_be_visible();page.get_by_role('button',name='빠른 기록 닫기',exact=True).click()
                report['checks'].append({'width':width,'library_focus_and_restore':True,'keyboard_move_cancel':True,'quick_form_open_close':True})
            report['fonts']=page.evaluate('[...document.fonts].map(f=>({family:f.family,status:f.status}))')
            assert all(any(f['family']==name and f['status']=='loaded' for f in report['fonts']) for name in ['Pretendard','Manrope'])
            assert not report['errors'];assert all(r['path']=='/demo/login/' for r in report['requests']),report['requests']
            browser.close()
    except Exception as error:
        report['exception']=repr(error);report['traceback']=traceback.format_exc()
    (OUT/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'pages':len(report['pages']),'checks':len(report['checks']),'version':report.get('version'),'error':report.get('exception')},ensure_ascii=False))
    return 1 if report.get('exception') else 0


if __name__=='__main__':raise SystemExit(main())
