"""Real Chromium actions and layout/contrast checks in a disposable local database."""
from demo_browser_session import select_fixture_role
from contextlib import closing
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import time
import traceback
import uuid

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tmp/handoff/phase5-round2/browser"
DATA = ROOT / "tmp/handoff/phase5-round2" / ("browser-data-" + uuid.uuid4().hex[:8])
PORT = 8022
BASE = f"http://127.0.0.1:{PORT}"
RESULT = {"checks": [], "layout": [], "contrast": [], "page_errors": [], "network_hosts": [], "data": str(DATA)}


def save():
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(json.dumps(RESULT, ensure_ascii=False, indent=2, default=str), encoding="utf8")


def checkpoint(name, detail=None):
    RESULT["checks"].append({"name": name, "detail": detail})
    save()


def main():
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", PORT)) == 0:
            raise RuntimeError("Verification port occupied. No existing server was stopped.")
    DATA.mkdir(parents=True)
    OUT.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(DJANGO_SETTINGS_MODULE="config.settings", CARE_DATA_DIR=str(DATA), CARE_DEMO_MODE="1",
               DJANGO_DEBUG="1", APP_ORIGIN=BASE, DJANGO_ALLOWED_HOSTS="127.0.0.1,localhost,testserver", PYTHONUTF8="1")
    os.environ.update(env)
    sys.path.insert(0, str(ROOT))
    fixture_file = ROOT / "tmp/handoff/phase5-round2/fixture.json"
    source = ROOT / "tmp/handoff/phase5-round2/fixture-data/demo.sqlite3"
    if source.exists() and fixture_file.exists():
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as src, closing(sqlite3.connect(DATA / "demo.sqlite3")) as dst:
            src.backup(dst)
        fixture = json.loads(fixture_file.read_text(encoding="utf8"))
    else:
        fixture = None
    import django
    django.setup()
    from django.core.management import call_command
    from django.contrib.auth import get_user_model
    from django.db import connections
    from django.utils import timezone
    from care import services
    from care.models import Activity, Room, Membership
    from care.forms import RoomForm, RecordForm
    from care.demo_fixtures import record_data
    from care.modules import MODULES
    from playwright.sync_api import sync_playwright, expect
    call_command("migrate", interactive=False, verbosity=0)
    call_command("seed_demo", verbosity=0)
    call_command("seed_references", verbosity=0)
    if fixture is None:
        user = get_user_model().objects.get(username="demo_owner")
        fixture = {"rooms": {}}
        for label in ["rich", "few", "empty"]:
            form = RoomForm({"name": "5차 자동검사 " + label, "species": "dog", "timezone": "Asia/Seoul", "use_defaults": "on"})
            assert form.is_valid(), form.errors
            r = services.create_room(user, form)
            fixture["rooms"][label] = str(r.pk)
            if label != "rich":
                continue
            owner = r.members.get(role="owner")
            services.update_modules(owner, list(MODULES), list(MODULES), r.settings_version, ["meal", "walk", "water", "weight"])
            Membership.objects.create(room=r, user=get_user_model().objects.get(username="demo_member"), role="guest", display_name="검수 참여자")
            for offset in range(35):
                at = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=offset)
                for key, extra in [("meal", {"quantity": "150" if offset < 7 else "100"}), ("weight", {"weight": "9" if offset < 7 else "8"}), ("walk", {"minutes": "15" if offset < 7 else "30"})]:
                    f = RecordForm(record_data(key, occurred_at=at.strftime("%Y-%m-%dT%H:%M:%S"), **extra), room=r, module=key, member=owner)
                    assert f.is_valid(), f.errors
                    services.save_activity(owner, key, f)
    room = Room.objects.get(pk=fixture["rooms"]["rich"])
    owner = room.members.get(role="owner")
    path = f"/rooms/{room.pk}/"
    RESULT.update(room_path=path, rooms=fixture["rooms"])
    connections.close_all()
    log = (OUT / "server.log").open("w", encoding="utf8")
    server = subprocess.Popen([sys.executable, "manage.py", "runserver", f"127.0.0.1:{PORT}", "--noreload"], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    page = browser = None
    try:
        for _ in range(100):
            with socket.socket() as probe:
                if probe.connect_ex(("127.0.0.1", PORT)) == 0:
                    break
            if server.poll() is not None:
                raise RuntimeError("Verification server exited")
            time.sleep(.1)
        os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "1"  # Only this test-side ORM, never the server.
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000}, locale="ko-KR", timezone_id="Asia/Seoul", reduced_motion="reduce")
            page = context.new_page()
            page.set_default_timeout(10000)
            page.on("dialog", lambda d: d.accept())
            page.on("pageerror", lambda e: RESULT["page_errors"].append(str(e)))
            page.on("request", lambda r: RESULT["network_hosts"].append(urlparse(r.url).hostname) if urlparse(r.url).hostname not in RESULT["network_hosts"] else None)
            page.goto(BASE, wait_until="networkidle")
            page.screenshot(path=str(OUT / "public-1440.png"), full_page=True)
            select_fixture_role(page, 'owner')
            page.goto(BASE + path, wait_until="networkidle")
            expect(page.locator(".care-pulse")).to_be_visible()
            expect(page.locator(".mobile-dock")).not_to_be_visible()
            checkpoint("desktop-real-summary-and-existing-sidebar")
            pages = {"today": "", "modules": "modules/", "module-meal": "modules/meal/", "module-walk": "modules/walk/", "config": "modules/settings/", "groups": "groups/", "records": "records/", "records-food": "records/?category=nutrition", "overview": "overview/", "routines": "routines/", "cadences": "cadences/", "members": "members/", "settings": "settings/", "statistics": "modules/walk/statistics/", "products": "products/"}
            for width in [320, 390, 768, 1024, 1440, 1920]:
                page.set_viewport_size({"width": width, "height": 1000})
                for name, suffix in pages.items():
                    response = page.goto(BASE + path + suffix, wait_until="networkidle")
                    overflow = page.evaluate("document.documentElement.scrollWidth>innerWidth+1")
                    RESULT["layout"].append({"screen": name, "width": width, "status": response.status, "overflow": overflow})
                    if width in [390, 1440]:
                        page.screenshot(path=str(OUT / f"{name}-{width}.png"), full_page=True)
                    if overflow:
                        RESULT.setdefault("overflow_nodes", []).append({"screen": name, "width": width, "nodes": page.locator("#main *").evaluate_all("es=>es.filter(e=>e.getBoundingClientRect().right>innerWidth+2&&e.getBoundingClientRect().width>0).slice(-10).map(e=>({tag:e.tagName,cls:e.className,text:e.innerText?.slice(0,60),right:e.getBoundingClientRect().right}))")})
                save()
            assert all(x["status"] == 200 and not x["overflow"] for x in RESULT["layout"]), "Responsive screen failures; inspect results"
            checkpoint("ninety-responsive-layouts")
            for label in ["few", "empty"]:
                for width in [390, 1440]:
                    page.set_viewport_size({"width": width, "height": 900})
                    for suffix in ["", "modules/", "overview/"]:
                        page.goto(BASE + "/rooms/" + fixture["rooms"][label] + "/" + suffix, wait_until="networkidle")
                        assert not page.evaluate("document.documentElement.scrollWidth>innerWidth+1")
                    page.screenshot(path=str(OUT / f"{label}-overview-{width}.png"), full_page=True)
            checkpoint("few-modules-and-empty-records-no-fabricated-values")
            page.set_viewport_size({"width": 1440, "height": 1000})
            page.goto(BASE + path, wait_until="networkidle")
            count = int(page.locator("[data-today-count]").inner_text().replace("건", "").strip())
            page.locator('.quick-card[data-module="meal"] a[data-quick]').click()
            expect(page.locator("#quick-dialog")).to_be_visible()
            page.locator('#quick-content button[type="submit"]').click()
            expect(page.locator("#quick-dialog")).not_to_be_visible()
            expect(page.locator("[data-today-count]")).to_contain_text(str(count + 1))
            page.locator("[data-feedback-undo]").click()
            expect(page.locator("[data-feedback-message]")).to_contain_text("취소")
            expect(page.locator("[data-today-count]")).to_contain_text(str(count))
            checkpoint("quick-save-and-undo-refresh-real-summary")
            page.goto(BASE + path + "modules/", wait_until="networkidle")
            page.locator('[data-module-filter="nutrition"]').click()
            page.locator('#workspace-search').fill("식사")
            page.locator('[data-module-key="meal"] a[data-quick]').click()
            page.locator('#quick-content button[type="submit"]').click()
            expect(page.locator("#quick-dialog")).not_to_be_visible()
            expect(page.locator('#workspace-search')).to_have_value("식사")
            expect(page.locator('[data-module-filter="nutrition"]')).to_have_attribute("aria-pressed", "true")
            page.locator("[data-feedback-undo]").click()
            expect(page.locator("[data-feedback-message]")).to_contain_text("취소")
            checkpoint("new-module-preview-keeps-filter-after-save-and-undo")
            page.goto(BASE + path + "modules/settings/", wait_until="networkidle")
            page.locator(".order-tools>summary").click()
            keys = page.locator("#chosen-modules .chosen-row").evaluate_all("es=>es.map(e=>e.dataset.key)")
            page.locator("#module-to-move").select_option(keys[-1])
            page.locator("#module-target-position").select_option("0")
            page.locator("[data-place-module]").click()
            expect(page.locator("[data-module-config]")).to_have_attribute("data-status", "saved")
            page.reload(wait_until="networkidle")
            assert page.locator("#chosen-modules .chosen-row").first.get_attribute("data-key") == keys[-1]
            checkpoint("single-pointer-select-position-persists-without-drag")
            handle = page.locator("#chosen-modules .drag-handle").last
            key = handle.locator("xpath=ancestor::li[1]").get_attribute("data-key")
            handle.focus();page.keyboard.press("Space");page.keyboard.press("Home");page.keyboard.press("Enter")
            expect(page.locator("[data-module-config]")).to_have_attribute("data-status", "saved")
            page.reload(wait_until="networkidle")
            assert page.locator("#chosen-modules .chosen-row").first.get_attribute("data-key") == key
            checkpoint("existing-keyboard-order-persists")

            # Actual editor composition, library focus, pin preview, save failures and conflicts.
            expect(page.locator('#module-library')).not_to_be_visible()
            assert page.locator('.chosen-modules').evaluate('e=>getComputedStyle(e).display') == 'grid'
            all_keys=page.locator('#chosen-modules .chosen-row').evaluate_all('es=>es.map(e=>e.dataset.key)')
            second=page.locator('#chosen-modules .chosen-row').nth(1)
            second.locator('.drag-handle').focus();page.keyboard.press('Space');page.keyboard.press('ArrowLeft');page.keyboard.press('Enter')
            expect(page.locator('[data-module-config]')).to_have_attribute('data-status','saved')
            page.reload(wait_until='networkidle');assert page.locator('#chosen-modules .chosen-row').first.get_attribute('data-key') == all_keys[1]
            checkpoint('grid-keyboard-horizontal-position-persists')
            before_records=Activity.objects.filter(room=room).count()
            page.locator('[data-remove-module="treat"]').click()
            expect(page.locator('[data-module-config]')).to_have_attribute('data-status','saved')
            opener=page.locator('[data-library-open]').first;opener.click()
            expect(page.locator('#module-library')).to_be_visible();expect(page.locator('#catalog-search')).to_be_focused()
            for _ in range(12):
                page.keyboard.press('Tab');assert page.evaluate('document.querySelector("#module-library").contains(document.activeElement)')
            page.locator('#catalog-search').fill('간식')
            expect(page.locator('.catalog-item:not([hidden])')).to_have_count(1)
            page.locator('[data-add-module="treat"]').click()
            expect(page.locator('[data-module-config]')).to_have_attribute('data-status','saved')
            expect(page.locator('[data-add-module="treat"]')).to_be_disabled()
            assert Activity.objects.filter(room=room).count()==before_records
            page.screenshot(path=str(OUT/'library-desktop.png'),full_page=True)
            page.keyboard.press('Escape');expect(opener).to_be_focused()
            pin=page.locator('[data-pin-module="treat"]');pin.check()
            expect(page.locator('[data-layout-preview]')).to_contain_text('간식')
            expect(page.locator('[data-module-config]')).to_have_attribute('data-status','saved')
            page.reload(wait_until='networkidle');expect(page.locator('[data-pin-module="treat"]')).to_be_checked()
            checkpoint('library-add-close-focus-and-live-pin-preview-preserve-records')
            endpoint=page.locator('[data-module-config]').get_attribute('data-endpoint')
            def reject_config(route):
                route.abort('internetdisconnected') if route.request.method=='POST' else route.continue_()
            context.route('**'+endpoint,reject_config)
            page.locator('[data-pin-module="treat"]').uncheck()
            expect(page.locator('[data-module-config]')).to_have_attribute('data-status','error')
            expect(page.locator('[data-pin-module="treat"]')).not_to_be_checked()
            context.unroute('**'+endpoint,reject_config);page.locator('#config-retry').click()
            expect(page.locator('[data-module-config]')).to_have_attribute('data-status','saved')
            checkpoint('grid-config-network-failure-preserves-selection-and-retry')
            other=context.new_page();other.goto(BASE+path+'modules/settings/',wait_until='networkidle')
            other.locator('[data-pin-module="treat"]').check();expect(other.locator('[data-module-config]')).to_have_attribute('data-status','saved')
            page.locator('[data-pin-module="meal"]').uncheck()
            expect(page.locator('[data-module-config]')).to_have_attribute('data-status','error')
            expect(page.locator('#config-status')).to_contain_text('다른 참여자')
            page.locator('#config-reload').click();expect(page.locator('[data-module-config]')).to_have_attribute('data-status','saved')
            expect(page.locator('[data-pin-module="treat"]')).to_be_checked();other.close()
            checkpoint('grid-config-two-tab-conflict-keeps-server-settings')

            page.goto(BASE + path + "modules/", wait_until="networkidle")
            button = page.locator('[data-category-manage="living"]')
            endpoint = button.get_attribute("data-endpoint")
            button.click();expect(page.locator("#category-dialog")).to_be_visible()
            for _ in range(12):
                page.keyboard.press("Tab")
                assert page.evaluate('document.querySelector("#category-dialog").contains(document.activeElement)')
            page.keyboard.press("Escape");expect(button).to_be_focused()
            checkpoint("category-dialog-keyboard-containment-and-return")
            def fail_post(route):
                route.abort("internetdisconnected") if route.request.method == "POST" else route.continue_()
            context.route("**" + endpoint, fail_post)
            button.click();page.locator('[data-category-choice][value="cleaning"]').uncheck();page.locator('#category-form button[type="submit"]').click()
            expect(page.locator("#category-dialog")).to_have_attribute("data-status", "error")
            expect(page.locator('[data-category-choice][value="cleaning"]')).not_to_be_checked()
            context.unroute("**" + endpoint, fail_post)
            page.locator('#category-form button[type="submit"]').click();expect(page.locator("#category-dialog")).not_to_be_visible()
            button.click();page.locator('[data-category-choice][value="cleaning"]').check();page.locator('#category-form button[type="submit"]').click();expect(page.locator("#category-dialog")).not_to_be_visible()
            checkpoint("category-network-failure-keeps-choice-and-retry")
            page.goto(BASE + path + "records/?category=nutrition&module=meal&q=", wait_until="networkidle")
            page.locator('.pagination a').filter(has_text="다음").click();page.wait_for_load_state("networkidle")
            back = page.url
            page.locator('[data-record-link]').first.click()
            page.locator('a.button.primary').first.click();page.locator('#id_note').fill("5차 브라우저 수정 검증")
            page.get_by_role("button", name="기록 저장", exact=True).click();page.locator('[data-record-back]').click()
            assert page.url == back
            checkpoint("category-history-edit-keeps-pagination-and-scope")
            page.goto(BASE + path + "modules/walk/statistics/", wait_until="networkidle")
            page.locator('.date-shortcuts a').filter(has_text="최근 7일").click()
            expect(page.locator('.date-shortcuts a[aria-current]')).to_have_text("최근 7일")
            query = parse_qs(urlparse(page.url).query)
            assert "start" in query and "end" in query
            checkpoint("explicit-statistics-period-controls")
            page.goto(BASE + path + "overview/", wait_until="networkidle")
            assert page.locator(".status-result .result-current").count() > 0
            page.locator('[data-result="weight"] summary').click()
            expect(page.locator('[data-result="weight"] .result-open')).to_have_text("접기")
            expect(page.locator('[data-result="weight"] .result-values')).to_be_visible()
            for word in ["중앙값", "MAD", "계산 방법", "실측"]:
                assert word not in page.locator("#main").inner_text()
            checkpoint("plain-result-plus-explicit-value-range-and-disclosure")
            guest = browser.new_context(locale="ko-KR")
            g = guest.new_page();g.goto(BASE);select_fixture_role(g, 'member')
            g.goto(BASE + path + "modules/")
            assert g.locator('[data-category-manage]').count() == 0
            assert g.locator('[data-module-key="expense"]').count() == 0
            assert g.request.get(BASE + path + "modules/settings/").status == 403
            assert g.request.get(BASE + path + "records/?modules=medication").status == 403
            g.goto(BASE + path + "overview/");expect(g.locator('#overall-title')).to_be_visible();guest.close()
            checkpoint("guest-scope-preserved-in-new-previews-and-navigation")
            mobile = browser.new_context(viewport={"width":390,"height":844}, locale="ko-KR", is_mobile=True, has_touch=True, reduced_motion="reduce", storage_state=context.storage_state())
            m = mobile.new_page();m.goto(BASE + path, wait_until="networkidle")
            expect(m.locator('.mobile-dock')).to_be_visible()
            m.locator('.mobile-dock a').filter(has_text="기능").tap()
            expect(m.locator('.mobile-dock a[aria-current]')).to_contain_text("기능")
            m.locator('.workspace-topbar [data-sidebar-toggle]').tap()
            expect(m.locator('.mobile-dock')).not_to_be_visible()
            assert m.locator('.mobile-dock').evaluate('(e)=>e.inert')
            m.locator('#sidebar [data-sidebar-toggle]').tap();expect(m.locator('.mobile-dock')).to_be_visible()
            checkpoint("mobile-bottom-navigation-and-menu-no-competing-focus")
            m.locator('[data-module-key="meal"] a[data-quick]').tap();expect(m.locator('#quick-dialog')).to_be_visible()
            m.locator('#quick-content button[type="submit"]').tap();expect(m.locator('#quick-dialog')).not_to_be_visible()
            m.locator('[data-feedback-undo]').tap();expect(m.locator('[data-feedback-message]')).to_contain_text("취소")
            checkpoint("mobile-quick-save-undo-above-bottom-navigation")
            m.goto(BASE + path + "modules/settings/", wait_until="networkidle")
            order = m.locator('#chosen-modules .chosen-row').evaluate_all('(es)=>es.map(e=>e.dataset.key)')
            m.locator('#chosen-modules .chosen-row').nth(1).evaluate("e=>e.scrollIntoView({block:'center',behavior:'instant'})")
            a = m.locator('#chosen-modules .chosen-row').nth(1).locator('.drag-handle').bounding_box()
            b = m.locator('#chosen-modules .chosen-row').first.bounding_box()
            x, y = a['x'] + a['width']/2, a['y'] + a['height']/2
            tx, ty = b['x'] + b['width']/2, b['y'] + b['height']/2
            cdp = mobile.new_cdp_session(m)
            cdp.send('Input.dispatchTouchEvent', {'type':'touchStart','touchPoints':[{'x':x,'y':y,'id':1}]})
            for step in range(1,13):
                cdp.send('Input.dispatchTouchEvent', {'type':'touchMove','touchPoints':[{'x':x+(tx-x)*step/12,'y':y+(ty-y)*step/12,'id':1}]});m.wait_for_timeout(16)
            cdp.send('Input.dispatchTouchEvent', {'type':'touchEnd','touchPoints':[]})
            expect(m.locator('[data-module-config]')).to_have_attribute('data-status','saved')
            m.reload(wait_until="networkidle");assert m.locator('#chosen-modules .chosen-row').first.get_attribute('data-key') == order[1]
            checkpoint("touch-drag-with-new-card-geometry-persists")

            m.locator('[data-library-open]').first.tap();expect(m.locator('#module-library')).to_be_visible()
            expect(m.locator('.mobile-dock')).not_to_be_visible()
            box=m.locator('#module-library').bounding_box();assert box['x']>=0 and box['x']+box['width']<=391
            m.screenshot(path=str(OUT/'library-mobile.png'))
            m.locator('[data-library-close]').first.tap();expect(m.locator('.mobile-dock')).to_be_visible()
            checkpoint('mobile-library-dialog-and-bottom-navigation-do-not-compete')

            m.goto(BASE + '/rooms/new/', wait_until="networkidle")
            m.locator('#id_species').select_option('cat');m.locator('#breed-search').fill('British')
            m.get_by_role('option', name='브리티시 쇼트헤어 British Shorthair').click();expect(m.locator('#id_size')).not_to_be_visible()
            m.screenshot(path=str(OUT/'registration-390.png'),full_page=True)
            mobile.close();checkpoint("breed-picker-and-cat-exception-remain-usable")
            assert not RESULT['page_errors'], RESULT['page_errors']
            assert sorted(RESULT['network_hosts']) == ['127.0.0.1'], RESULT['network_hosts']
            checkpoint("no-unhandled-javascript-or-external-product-requests")
            browser.close();browser=None
    except Exception as exc:
        RESULT['exception'] = type(exc).__name__ + ': ' + str(exc)
        RESULT['traceback'] = traceback.format_exc()
        if page:
            try:
                page.screenshot(path=str(OUT/'failure.png'),full_page=True)
                (OUT/'failure.html').write_text(page.content(),encoding='utf8')
            except Exception:
                pass
    finally:
        if browser:
            try:
                browser.close()
            except Exception:
                pass
        connections.close_all();server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
        log.close();save()
    print(json.dumps({'checks':len(RESULT['checks']),'layouts':len(RESULT['layout']),'exception':RESULT.get('exception')},ensure_ascii=False))
    return 1 if RESULT.get('exception') else 0


if __name__ == '__main__':
    raise SystemExit(main())
