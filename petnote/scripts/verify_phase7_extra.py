"""Cross-browser, 375px accessibility, 200% text and keyboard-only forms."""
from demo_browser_session import select_fixture_role
from pathlib import Path
from contextlib import closing
from datetime import datetime,timezone
import json,os,socket,sqlite3,subprocess,sys,time,traceback,uuid

from playwright.sync_api import sync_playwright,expect

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"tmp/handoff/phase7/extra"
OUT.mkdir(parents=True,exist_ok=True)
SOURCE=ROOT/"tmp/handoff/phase7/preview-data/demo.sqlite3"
FIXTURE=json.loads((ROOT/"tmp/handoff/phase7/fixture.json").read_text(encoding="utf8"))
PORT=8035;BASE=f"http://127.0.0.1:{PORT}";ROOM=FIXTURE["rooms"]["rich"];DATA=ROOT/("tmp/handoff/phase7/extra-data-"+uuid.uuid4().hex[:7])
RESULT={"at":datetime.now(timezone.utc).isoformat(),"cases":[],"failures":[],"data_dir":str(DATA)}
def save(): (OUT/"results.json").write_text(json.dumps(RESULT,ensure_ascii=False,indent=2),encoding="utf8")
def case(name,browser,info=None):
    RESULT["cases"].append({"browser":browser,"case":name,"info":info});save()
def dbcount(module):
    with closing(sqlite3.connect(DATA/"demo.sqlite3")) as con:
        return con.execute("SELECT COUNT(*) FROM care_activity WHERE room_id=? AND module=? AND voided=0",
            (ROOM.replace("-",""),module)).fetchone()[0]
def audit(page,label,browser):
    status=page.evaluate("""() => ({width:innerWidth,sw:document.documentElement.scrollWidth,
      overflow:document.documentElement.scrollWidth > innerWidth + 1,
      offenders:[...document.querySelectorAll('#main *')].filter(e=>e.getBoundingClientRect().right>innerWidth+2 && e.getBoundingClientRect().width>0).slice(0,7).map(e=>({tag:e.tagName,cls:typeof e.className==='string'?e.className.slice(0,60):'',w:e.getBoundingClientRect().width}))})""")
    RESULT.setdefault("layout_data",[]).append({"browser":browser,"label":label,**status})
    if status["overflow"]:raise AssertionError(f"{browser} {label} 375px overflow: {status}")
def main():
    DATA.mkdir(parents=True)
    with closing(sqlite3.connect(SOURCE.as_uri()+"?mode=ro",uri=True)) as src,closing(sqlite3.connect(DATA/"demo.sqlite3")) as dst:src.backup(dst)
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1",PORT))==0:raise RuntimeError("Verification port already used; did not kill other process")
    env=os.environ.copy();env.update(CARE_DATA_DIR=str(DATA),CARE_DEMO_MODE="1",DJANGO_DEBUG="1",
        DJANGO_ALLOWED_HOSTS="127.0.0.1,localhost,testserver",APP_ORIGIN=BASE)
    with (OUT/"server.log").open("w",encoding="utf8") as log:
        server=subprocess.Popen([sys.executable,"manage.py","runserver",f"127.0.0.1:{PORT}","--noreload"],
                                cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
    try:
        for i in range(100):
            with socket.socket() as probe:
                if probe.connect_ex(("127.0.0.1",PORT))==0:break
            time.sleep(.1)
        with sync_playwright() as pw:
            for name in ["chromium","firefox"]:
                browser=None
                try:
                    browser=getattr(pw,name).launch(headless=True)
                    context=browser.new_context(viewport={"width":375,"height":980},locale="ko-KR",timezone_id="Asia/Seoul",reduced_motion="reduce")
                    page=context.new_page()
                    page.set_default_timeout(12000)
                    problems=[]
                    page.on("pageerror",lambda e:problems.append(str(e)))
                    page.goto(BASE,wait_until="networkidle")
                    select_fixture_role(page, 'owner')
                    expected={"오늘":"","돌봄 기능":"modules/","최근 통계":"overview/","루틴":"routines/","설정":"settings/",
                              "메모 입력":"modules/observation/entry/","체중 통계":"modules/weight/statistics/",
                              "프리셋":"modules/meal/presets/","구성원":"members/"}
                    for title,suffix in expected.items():
                        response=page.goto(BASE+f"/rooms/{ROOM}/"+suffix,wait_until="networkidle")
                        assert response.status==200,f"{name} {title} HTTP {response.status}"
                        audit(page,title,name)
                        if title in ("최근 통계","돌봄 기능","오늘"):
                            page.screenshot(path=str(OUT/f"{name}-{title}-375.png"),full_page=True)
                    case("nine-375px-pages-no-horizontal-overflow",name)
                    page.goto(BASE+f"/rooms/{ROOM}/overview/",wait_until="networkidle")
                    # Text scaling is an accessibility contract; not a device-pixel-ratio mock.
                    page.evaluate("document.documentElement.style.fontSize='200%'")
                    audit(page,"200%-text-overview",name)
                    page.screenshot(path=str(OUT/f"{name}-200pct-overview.png"),full_page=True)
                    case("200%-root-font-size-overview",name)
                    page.goto(BASE+f"/rooms/{ROOM}/modules/",wait_until="networkidle")
                    page.evaluate("document.documentElement.style.fontSize='200%'")
                    audit(page,"200%-text-modules",name)
                    case("200%-root-font-size-modules",name)
                    page.goto(BASE+f"/rooms/{ROOM}/",wait_until="networkidle")
                    opener=page.locator("[data-member-open7]").first
                    if not opener.is_visible():
                        sidebar=page.locator(".workspace-topbar [data-sidebar-toggle]").first
                        sidebar.focus();page.keyboard.press("Enter")
                        expect(opener).to_be_visible()
                    opener.focus();page.keyboard.press("Enter")
                    expect(page.locator("#member-panel-title7")).to_be_visible()
                    for i in range(16):
                        page.keyboard.press("Tab")
                        assert page.evaluate("document.querySelector('#member-dialog7').contains(document.activeElement)"),f"{name} tab escaped dialog step{i}"
                    page.keyboard.press("Shift+Tab")
                    assert page.evaluate("document.querySelector('#member-dialog7').contains(document.activeElement)")
                    checkbox=page.locator('#member-dialog7 input[name="notices"]')
                    if checkbox.count():
                        before=checkbox.is_checked();checkbox.focus();page.keyboard.press("Space")
                        assert checkbox.is_checked()!=before
                        page.keyboard.press("Space")
                    page.once("dialog",lambda dialog:dialog.accept())
                    page.keyboard.press("Escape")
                    expect(page.locator("#member-dialog7")).not_to_be_visible()
                    expect(opener).to_be_focused()
                    case("member-modal-tab-shift-tab-escape-space-focus-return",name)
                    # Native form submits under keyboard only, once, in an isolated DB.
                    before=dbcount("observation")
                    page.goto(BASE+f"/rooms/{ROOM}/modules/observation/entry/",wait_until="networkidle")
                    field=page.get_by_label("특이사항 메모")
                    field.fill("검증용 가상 기록 · 키보드 제출")
                    submit=page.locator('#main form[data-safe-submit7] button[type="submit"]').first
                    submit.focus()
                    page.keyboard.press("Enter")
                    page.wait_for_url("**/records/*/",timeout=12000)
                    assert dbcount("observation")==before+1
                    case("keyboard-form-enters-once-and-record-created",name)
                    # Confirm that the live JS has no uncaught browser failures.
                    assert not problems,(name,problems)
                    case("no-uncaught-browser-javascript-errors",name)
                    browser.close()
                except Exception as e:
                    RESULT["failures"].append({"browser":name,"exception":repr(e),"trace":traceback.format_exc()[-6500:]})
                    try:
                        page.screenshot(path=str(OUT/f"{name}-failure.png"),full_page=True)
                    except Exception:pass
                    if browser:browser.close()
                save()
    finally:
        server.terminate()
        try:server.wait(timeout=7)
        except subprocess.TimeoutExpired:server.kill()
        save()
    print(json.dumps({"cases":len(RESULT["cases"]),"failures":[{"browser":x["browser"],"exception":x["exception"]} for x in RESULT["failures"]]},ensure_ascii=False))
    return int(bool(RESULT["failures"]))
if __name__=="__main__":raise SystemExit(main())
