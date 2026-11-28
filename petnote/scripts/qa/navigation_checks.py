"""Behavioral checks for global and section-specific navigation."""
from __future__ import annotations

from pathlib import Path
import re

from playwright.sync_api import expect


GROUPS = {
    "care": ("돌봄", (("modules/", "기능"), ("routines/", "루틴"), ("supplies/", "물품"))),
    "history": ("기록", (("records/", "기록"), ("overview/", "통계"))),
    "manage": ("관리", (("members/", "구성원"), ("settings/", "반려동물"), ("preferences/", "설정"))),
}


def capture_failure(page, flows, name):
    """Keep failed navigation states beside the selected QA report."""
    try:
        engine = page.context.browser.browser_type.name
        path = Path(flows.OUT).parent / f"navigation-{engine}-{name}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(path), full_page=True, timeout=5000)
    except Exception:
        # The original assertion remains the reportable failure if a page has closed.
        pass


def assert_scope(page, scope, label=None):
    dock = page.locator(".mobile-dock")
    expect(dock).to_be_visible()
    expect(dock).to_have_count(1)
    expect(dock).to_have_attribute("data-nav-scope", scope)
    expect(dock).to_have_class(
        re.compile(r"\bis-global\b" if scope == "global" else r"\bis-context\b")
    )
    if label:
        expect(dock.locator('a.dock-link[aria-current="page"]')).to_have_count(1)
        expect(dock.locator('a.dock-link[aria-current="page"]')).to_have_text(label)
    return dock


def global_and_context(page, root, flows, passed):
    flows.go(page, root + "?layout=mobile")
    dock = assert_scope(page, "global", "오늘")
    expect(dock.locator(".dock-items a.dock-link")).to_have_text(["오늘", "돌봄", "기록", "관리", "전체"])
    passed("모바일 오늘 화면에 다섯 개 기본 메뉴 표시")
    for scope, (entry, destinations) in GROUPS.items():
        flows.go(page, root + "?layout=mobile")
        page.locator(".mobile-dock").get_by_role("link", name=entry, exact=True).click()
        dock = assert_scope(page, scope, destinations[0][1])
        expect(dock.locator(".dock-items a.dock-link")).to_have_text([label for _, label in destinations])
        for suffix, label in destinations:
            dock.get_by_role("link", name=label, exact=True).click()
            expect(page).to_have_url(flows.BASE + root + suffix)
            assert_scope(page, scope, label)
            expect(page.locator("#input-dialog")).not_to_be_visible()
        dock.get_by_role("link", name="기본 메뉴로 돌아가기", exact=True).click()
        expect(page).to_have_url(flows.BASE + root)
        assert_scope(page, "global", "오늘")
        passed(f"{entry} 진입·하위 메뉴 이동·기본 하단바 복귀")


def history_and_refresh(page, root, flows, passed):
    flows.go(page, root + "?layout=mobile")
    page.locator(".mobile-dock").get_by_role("link", name="돌봄", exact=True).click()
    page.locator(".mobile-dock").get_by_role("link", name="루틴", exact=True).click()
    expect(page).to_have_url(flows.BASE + root + "routines/")
    page.wait_for_function("() => !document.body.hasAttribute('data-navigation-pending')")
    page.go_back(wait_until="domcontentloaded")
    assert_scope(page, "care", "기능")
    page.go_back(wait_until="domcontentloaded")
    assert_scope(page, "global", "오늘")
    page.go_forward(wait_until="domcontentloaded")
    assert_scope(page, "care", "기능")
    page.reload(wait_until="domcontentloaded")
    assert_scope(page, "care", "기능")
    passed("브라우저 뒤로·앞으로·새로고침에서 메뉴 상태 복원")
    page.evaluate("() => window.TailyUI.refresh()")
    assert_scope(page, "care", "기능")
    passed("실시간 본문 갱신 후 현재 돌봄 메뉴 유지")


def deep_links(page, root, flows, passed):
    for suffix, scope, label in (
        ("modules/water/", "care", "기능"),
        ("modules/water/new/", "care", "기능"),
        ("modules/water/statistics/", "history", "통계"),
        ("routines/new/", "care", "루틴"),
        ("products/new/", "care", "물품"),
        ("space-management/", "manage", "설정"),
    ):
        flows.go(page, root + suffix + "?layout=mobile")
        assert_scope(page, scope, label)
    passed("입력·개별 통계·설정 상세 URL에서 올바른 상위 메뉴 선택")
    # Phones open the record tab on one day; the full list is the explicit list view.
    flows.go(page, root + "records/?view=list&layout=mobile")
    record = page.locator("[data-record-link]").first.get_attribute("href")
    assert record, "Expected a previously created sample record"
    flows.go(page, record)
    assert_scope(page, "history", "기록")
    passed("기록 상세 바로 접근에서 기록 하단바 유지")


def hub_and_dialog(page, root, flows, passed):
    flows.go(page, root + "?layout=mobile")
    page.locator(".mobile-dock").get_by_role("link", name="전체", exact=True).click()
    expect(page).to_have_url(flows.BASE + root + "navigation/")
    assert_scope(page, "global", "전체")
    for _, destinations in GROUPS.values():
        for suffix, _ in destinations:
            expect(page.locator(f'#main a[href="{root + suffix}"]').first).to_be_visible()
    for suffix, label in (("settings/", "반려동물"), ("preferences/", "설정")):
        flows.go(page, root + "navigation/?layout=mobile")
        page.locator(f'#main a[href="{root + suffix}"]').first.click()
        expect(page).to_have_url(flows.BASE + root + suffix)
        assert_scope(page, "manage", label)
        expect(page.locator("#input-dialog")).not_to_be_visible()
    passed("전체 메뉴에서 기존 사이드바의 모든 화면 접근")
    flows.go(page, root + "?layout=mobile")
    trigger = page.locator(".phone-menu-trigger")
    trigger.click()
    dialog = page.locator("#phone-menu")
    expect(dialog).to_be_visible()
    page.keyboard.press("Escape")
    expect(dialog).not_to_be_visible()
    expect(trigger).to_be_focused()
    trigger.click()
    dialog.get_by_role("link", name="기록", exact=True).click()
    assert_scope(page, "history", "기록")
    for suffix, name, label in (
        ("settings/", "반려동물 상태", "반려동물"), ("preferences/", "일반 설정", "설정")
    ):
        trigger.click()
        expect(dialog).to_be_visible()
        dialog.get_by_role("link", name=name, exact=True).click()
        expect(page).to_have_url(flows.BASE + root + suffix)
        assert_scope(page, "manage", label)
        expect(dialog).not_to_be_visible()
        expect(page.locator("#input-dialog")).not_to_be_visible()
    passed("상단 메뉴의 키보드 닫기·초점 복귀·섹션 이동")


def desktop_groups(page, root, flows, passed):
    page.set_viewport_size({"width": 1440, "height": 950})
    flows.go(page, root + "?layout=desktop")
    expect(page.locator(".mobile-dock")).not_to_be_visible()
    expect(page.locator("#sidebar")).to_be_visible()
    primary = page.locator('#sidebar nav[aria-label="돌봄 탐색"]')
    expect(primary.locator("a")).to_have_text(["오늘", "돌봄 기능", "전체 기록", "루틴", "물품 관리", "최근 통계"])
    expect(page.locator(".sidebar-members")).to_be_visible()
    expect(page.locator(".sidebar-settings")).to_be_visible()
    expect(page.locator(".sidebar-nav-group")).to_have_count(0)
    expect(page.locator('link[href*="/care/navigation.css"]')).to_have_count(0)
    expect(page.locator('script[src*="mobile-menu.js"]')).to_have_count(0)
    passed("PC 기존 사이드바 순서와 구성원·설정 영역 보존")
    for suffix, current in (("modules/", "돌봄 기능"), ("overview/", "최근 통계")):
        primary.locator(f'a[href="{root + suffix}"]').click()
        expect(page).to_have_url(flows.BASE + root + suffix)
        expect(primary.locator('a[aria-current="page"]')).to_have_text(current)
    for suffix in ("settings/", "preferences/"):
        current_url = page.url
        page.locator(f'#sidebar a[href="{root + suffix}"]').click()
        expect(page.locator("#input-dialog")).to_be_visible()
        expect(page).to_have_url(current_url)
        page.locator("#input-dialog [data-input-close]").click()
        expect(page.locator("#input-dialog")).not_to_be_visible()
    passed("PC 원래 탐색 및 반려동물·일반 설정 입력창 동작 유지")


def check_bounds(page, width, suffix):
    overflow = page.evaluate("document.documentElement.scrollWidth - innerWidth")
    assert overflow <= 1, {"width": width, "page": suffix, "overflow": overflow}
    dock = page.locator(".mobile-dock")
    expect(dock).to_be_visible()
    bounds = dock.bounding_box()
    height = page.viewport_size["height"]
    assert bounds and bounds["x"] >= -1 and bounds["x"] + bounds["width"] <= width + 1, bounds
    assert bounds["y"] >= 0 and bounds["y"] + bounds["height"] <= height + 1, bounds
    if dock.get_attribute("data-nav-scope") != "global":
        assert bounds["x"] > 0 and bounds["y"] + bounds["height"] < height, bounds
    for anchor in dock.locator("a").all():
        if not anchor.is_visible():
            continue
        target = anchor.bounding_box()
        assert target["width"] >= 43.5 and target["height"] >= 43.5, {
            "width": width, "page": suffix, "target": anchor.inner_text(), "bounds": target,
        }
    targets = page.locator('#main :is(a[href],button,input:not([type="hidden"]),select,textarea):visible')
    if targets.count():
        target = targets.last
        target.scroll_into_view_if_needed()
        page.evaluate("window.scrollTo(0, document.scrollingElement.scrollHeight)")
        box = target.bounding_box()
        assert box is None or box["y"] + box["height"] <= bounds["y"] + 1, {
            "width": width, "page": suffix, "target": box, "dock": bounds,
        }


def responsive_sizes(page, root, flows, passed):
    suffixes = ("", "modules/", "routines/", "supplies/", "records/", "overview/",
                "members/", "settings/", "preferences/", "navigation/")
    for width in (320, 390, 768):
        page.set_viewport_size({"width": width, "height": 900})
        for suffix in suffixes:
            flows.go(page, root + suffix + "?layout=mobile")
            check_bounds(page, width, suffix)
        passed(f"{width}px 모든 메뉴의 가로 넘침·터치 크기·본문 가림 검사")
    for width, height in ((390, 480), (844, 390)):
        page.set_viewport_size({"width": width, "height": height})
        for suffix in ("", "modules/water/new/", "records/", "preferences/"):
            flows.go(page, root + suffix + "?layout=mobile")
            check_bounds(page, width, suffix)
        passed(f"{width}×{height} 작은 높이에서 하단바와 마지막 입력 요소 접근")


def no_script_navigation(browser, root, cookie, flows, passed):
    context = browser.new_context(
        java_script_enabled=False, locale="ko-KR", viewport={"width": 390, "height": 844}
    )
    context.add_cookies([cookie])
    page = context.new_page()
    page.set_default_timeout(6000)
    try:
        flows.go(page, root + "?layout=mobile")
        trigger = page.locator("a.phone-menu-trigger")
        expect(trigger).to_have_attribute("href", root + "navigation/")
        trigger.click()
        expect(page).to_have_url(flows.BASE + root + "navigation/")
        for _, destinations in GROUPS.values():
            for suffix, _ in destinations:
                expect(page.locator(f'#main a[href="{root + suffix}"]').first).to_be_visible()
        page.locator(f'#main a[href="{root}supplies/"]').first.click()
        dock = assert_scope(page, "care", "물품")
        dock.get_by_role("link", name="기본 메뉴로 돌아가기", exact=True).click()
        assert_scope(page, "global", "오늘")
        passed("자바스크립트 없이 전체 메뉴·물품·기본 메뉴 이동")
    except Exception:
        capture_failure(page, flows, "no-script")
        raise
    finally:
        context.close()


def layout_preferences(browser, root, cookie, flows, passed):
    context = browser.new_context(
        locale="ko-KR", viewport={"width": 390, "height": 844},
        user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) Mobile",
    )
    context.add_cookies([cookie])
    page = context.new_page()
    page.set_default_timeout(6000)
    try:
        flows.go(page, root)
        expect(page.locator(".phone-header")).to_be_visible()
        flows.go(page, root + "?layout=desktop")
        expect(page.locator("#sidebar")).to_have_count(1)
        expect(page.locator(".phone-header")).to_have_count(0)
        expect(page.locator(".mobile-dock a")).to_have_text(["오늘", "기능", "기록", "루틴", "통계"])
        page.locator(".mobile-dock").get_by_role("link", name="기능", exact=True).click()
        expect(page.locator("#sidebar")).to_have_count(1)
        expect(page.locator(".mobile-dock a[aria-current=page]")).to_have_text("기능")
        flows.go(page, root + "?layout=auto")
        expect(page.locator(".phone-header")).to_be_visible()
        page.set_viewport_size({"width": 1440, "height": 950})
        flows.go(page, root + "?layout=mobile")
        expect(page.locator(".phone-header")).to_be_visible()
        assert_scope(page, "global", "오늘")
        passed("모바일 감지·PC 강제 선택·자동 복귀·넓은 화면 모바일 선택")
    except Exception:
        capture_failure(page, flows, "layout")
        raise
    finally:
        context.close()



def accelerated_tabs(page, root, flows, passed):
    for layout in ("mobile", "desktop"):
        page.set_viewport_size({"width": 390 if layout == "mobile" else 1440, "height": 950})
        flows.go(page, root + "?layout=" + layout)
        page.evaluate("""() => {
            window.navigationDocumentMarker = {};
            window.navigationMainMarker = document.getElementById('main');
            window.navigationShellMarker = document.querySelector('.phone-header, #sidebar');
        }""")
        nav = ".mobile-dock" if layout == "mobile" else "#sidebar"
        pending = page.evaluate("""({nav, path}) => {
            document.querySelector(nav + ' a[href="' + path + '"]').click();
            return {
                body: document.body.hasAttribute('data-navigation-pending'),
                main: document.getElementById('main').getAttribute('aria-busy'),
                inert: document.getElementById('main').inert
            };
        }""", {"nav": nav, "path": root + "modules/"})
        assert pending == {"body": True, "main": "true", "inert": True}, pending
        expect(page).to_have_url(flows.BASE + root + "modules/")
        page.wait_for_function("() => !document.body.hasAttribute('data-navigation-pending')")
        state = page.evaluate("""() => ({
            sameDocument: Boolean(window.navigationDocumentMarker),
            replacedMain: document.getElementById('main') !== window.navigationMainMarker,
            keptShell: window.navigationShellMarker?.isConnected,
            ready: !document.getElementById('main').inert &&
                   !document.getElementById('main').hasAttribute('aria-busy')
        })""")
        assert all(state.values()), state
        page.locator(nav + ' a[href="' + root + 'routines/"]').click()
        expect(page).to_have_url(flows.BASE + root + "routines/")
        page.wait_for_function("() => !document.body.hasAttribute('data-navigation-pending')")
        assert page.evaluate("Boolean(window.navigationDocumentMarker)")
        page.locator(nav + ' a[href="' + root + '"]').first.click()
        expect(page).to_have_url(flows.BASE + root)
        page.wait_for_function("() => !document.body.hasAttribute('data-navigation-pending')")
        active = '.mobile-dock a[aria-current="page"]' if layout == "mobile" else '#sidebar nav[aria-label="돌봄 탐색"] a[aria-current="page"]'
        expect(page.locator(active)).to_have_text("오늘")
        passed(f"{layout} 탭 클릭 즉시 대기 표시·공통 메뉴 유지·본문 교체")


def accelerated_history(page, root, flows, passed):
    flows.go(page, root + "modules/?layout=mobile")
    page.evaluate("window.navigationDocumentMarker = {}; window.scrollTo(0, 260)")
    old_scroll = page.evaluate("window.scrollY")
    page.locator('.mobile-dock a[href="' + root + 'routines/"]').click()
    expect(page).to_have_url(flows.BASE + root + "routines/")
    page.wait_for_function("() => !document.body.hasAttribute('data-navigation-pending')")
    assert page.evaluate("window.scrollY") == 0
    requests = []
    def requested(request):
        if request.resource_type in ("document", "fetch", "xhr") and request.url.startswith(flows.BASE + root):
            requests.append(request.url)
    page.on("request", requested)
    try:
        page.go_back(wait_until="domcontentloaded")
        expect(page).to_have_url(flows.BASE + root + "modules/?layout=mobile")
        page.wait_for_function("() => !document.body.hasAttribute('data-navigation-pending')")
        page.wait_for_function("(y) => Math.abs(window.scrollY - y) <= 2", arg=old_scroll)
        assert len(requests) == 1, requests
        requests.clear()
        page.go_forward(wait_until="domcontentloaded")
        expect(page).to_have_url(flows.BASE + root + "routines/")
        page.wait_for_function("() => !document.body.hasAttribute('data-navigation-pending')")
        assert len(requests) == 1, requests
        assert page.evaluate("Boolean(window.navigationDocumentMarker)")
    finally:
        page.remove_listener("request", requested)
    passed("부분 전환 뒤 뒤로·앞으로 이동의 단일 조회와 스크롤 복원")


def delayed_fetch(page, paths):
    page.evaluate("""(paths) => {
        window.navigationOriginalFetch = window.fetch;
        window.fetch = (...args) => {
            const request = args[0];
            const path = new URL(request instanceof Request ? request.url : String(request), location.href).pathname;
            const result = window.navigationOriginalFetch(...args);
            const delay = paths[path] || 0;
            return delay ? result.then(response => new Promise(resolve => setTimeout(() => resolve(response), delay))) : result;
        };
    }""", paths)


def restore_fetch(page):
    page.evaluate("""() => {
        if (window.navigationOriginalFetch) {
            window.fetch = window.navigationOriginalFetch;
            delete window.navigationOriginalFetch;
        }
    }""")


def accelerated_races(page, root, flows, passed):
    flows.go(page, root + "?layout=mobile")
    delayed_fetch(page, {root + "modules/": 450})
    try:
        page.evaluate("""(root) => {
            document.querySelector('.mobile-dock a[href="' + root + 'modules/"]').click();
            setTimeout(() => document.querySelector('.mobile-dock a[href="' + root + 'records/"]').click(), 20);
        }""", root)
        expect(page).to_have_url(flows.BASE + root + "records/")
        page.wait_for_function("() => !document.body.hasAttribute('data-navigation-pending')")
        page.wait_for_timeout(550)
        expect(page).to_have_url(flows.BASE + root + "records/")
        assert_scope(page, "history", "기록")
    finally:
        restore_fetch(page)
    passed("빠른 연속 탭 선택에서 늦게 도착한 이전 응답 제외")

    flows.go(page, root + "?layout=mobile")
    delayed_fetch(page, {root: 450})
    try:
        page.evaluate("""(root) => {
            window.navigationRefreshTask = window.TailyUI.refresh().catch(() => null);
            setTimeout(() => document.querySelector('.mobile-dock a[href="' + root + 'records/"]').click(), 20);
        }""", root)
        expect(page).to_have_url(flows.BASE + root + "records/")
        page.wait_for_function("() => !document.body.hasAttribute('data-navigation-pending')")
        page.evaluate("() => window.navigationRefreshTask")
        expect(page.locator("h1")).to_contain_text("기록")
        assert_scope(page, "history", "기록")
    finally:
        restore_fetch(page)
    passed("기존 화면 새로고침과 탭 이동의 응답 경합에서 현재 본문 유지")


    flows.go(page, root + "?layout=mobile")
    page.evaluate("window.navigationMainMarker = document.getElementById('main')")
    delayed_fetch(page, {root + "modules/": 450})
    try:
        page.evaluate("""(root) => {
            document.querySelector('.mobile-dock a[href="' + root + 'modules/"]').click();
            setTimeout(() => document.querySelector('.skip-link').click(), 20);
        }""", root)
        page.wait_for_function("() => location.hash === '#main'")
        page.wait_for_timeout(550)
        expect(page).to_have_url(flows.BASE + root + "?layout=mobile#main")
        assert page.evaluate("document.getElementById('main') === window.navigationMainMarker")
        assert_scope(page, "global", "오늘")
    finally:
        restore_fetch(page)
    passed("일반 본문 이동을 선택하면 이전 탭 요청의 늦은 응답 무시")


def accelerated_fallbacks(page, root, flows, passed):
    flows.go(page, root + "?layout=mobile")
    page.evaluate("""(target) => {
        window.navigationDocumentMarker = {};
        const original = window.fetch;
        window.fetch = (...args) => {
            const request = args[0];
            const url = request instanceof Request ? request.url : String(request);
            if (new URL(url, location.href).pathname === target) return Promise.reject(new TypeError('Network unavailable'));
            return original(...args);
        };
    }""", root + "modules/")
    page.locator('.mobile-dock a[href="' + root + 'modules/"]').click()
    expect(page).to_have_url(flows.BASE + root + "modules/")
    expect(page.locator("#main")).to_be_visible()
    assert page.evaluate("typeof window.navigationDocumentMarker === 'undefined'")
    assert_scope(page, "care", "기능")
    passed("부분 조회 실패 시 일반 페이지 이동으로 복구")

    flows.go(page, root + "settings/?layout=mobile")
    page.evaluate("window.navigationDocumentMarker = {}")
    name = page.locator('form[data-pet-profile] [name="name"]')
    name.fill(name.input_value() + " 확인")
    page.locator(".phone-menu-trigger").click()
    page.locator('#phone-menu form[method="dialog"] button').first.click()
    expect(page.locator("#phone-menu")).not_to_be_visible()
    before = page.url
    page.once("dialog", lambda dialog: dialog.dismiss())
    page.locator('.mobile-dock a[href="' + root + '"]').click(no_wait_after=True)
    page.wait_for_timeout(150)
    expect(page).to_have_url(before)
    assert page.evaluate("Boolean(window.navigationDocumentMarker)")
    expect(name).to_have_value(re.compile(" 확인$"))
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator('.mobile-dock a[href="' + root + '"]').click(no_wait_after=True)
    expect(page).to_have_url(flows.BASE + root)
    assert page.evaluate("typeof window.navigationDocumentMarker === 'undefined'")
    passed("수정 중 입력은 이동 확인을 거치며 취소하면 그대로 보존")



def accelerated_session_boundary(page, root, flows, passed):
    flows.go(page, root + "?layout=mobile")
    page.evaluate("window.navigationDocumentMarker = {}")
    cookie_name = page.locator("body").get_attribute("data-csrf-cookie")
    csrf = next(item for item in page.context.cookies() if item["name"] == cookie_name)
    page.context.add_cookies([{**csrf, "value": "n" * 32}])
    requests = []
    def requested(request):
        if request.url == flows.BASE + root + "modules/":
            requests.append(request.resource_type)
    page.on("request", requested)
    try:
        page.locator('.mobile-dock a[href="' + root + 'modules/"]').click()
        expect(page).to_have_url(flows.BASE + root + "modules/")
        assert page.evaluate("typeof window.navigationDocumentMarker === 'undefined'")
        assert requests == ["document"], requests
    finally:
        page.remove_listener("request", requested)
    passed("설정된 CSRF 쿠키가 바뀌면 새 문서로 이동해 세션 상태 재확인")

    flows.go(page, root + "?layout=mobile")
    page.evaluate("window.navigationDocumentMarker = {}")
    target = flows.BASE + root + "modules/"
    def changed_context(route):
        if route.request.resource_type == "fetch":
            response = route.fetch()
            body = re.sub(r'data-navigation-context="[^"]+"',
                          'data-navigation-context="changed-context"', response.text(), count=1)
            route.fulfill(response=response, body=body)
        else:
            route.continue_()
    page.route(target, changed_context)
    try:
        page.locator('.mobile-dock a[href="' + root + 'modules/"]').click()
        expect(page).to_have_url(target)
        assert page.evaluate("typeof window.navigationDocumentMarker === 'undefined'")
        assert_scope(page, "care", "기능")
    finally:
        page.unroute(target, changed_context)
    passed("탐색 권한 문맥이 달라진 응답은 기존 화면에 끼우지 않고 새 문서로 확인")


def exercise_navigation(browser, root, cookie, flows, passed, failed):
    """Run after sample records exist and before the owner account logs out."""
    context = browser.new_context(
        locale="ko-KR", timezone_id="Asia/Seoul", viewport={"width": 390, "height": 844}
    )
    context.add_cookies([cookie])
    page = context.new_page()
    page.set_default_timeout(6000)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    checks = [
        ("Accelerated tab navigation", lambda: accelerated_tabs(page, root, flows, passed)),
        ("Navigation history", lambda: accelerated_history(page, root, flows, passed)),
        ("Navigation races", lambda: accelerated_races(page, root, flows, passed)),
        ("Navigation fallbacks", lambda: accelerated_fallbacks(page, root, flows, passed)),
        ("Session boundary", lambda: accelerated_session_boundary(page, root, flows, passed)),
        ("기본·영역별 하단바", lambda: global_and_context(page, root, flows, passed)),
        ("탐색 이력과 화면 갱신", lambda: history_and_refresh(page, root, flows, passed)),
        ("상세 경로 메뉴", lambda: deep_links(page, root, flows, passed)),
        ("전체 메뉴와 메뉴 창", lambda: hub_and_dialog(page, root, flows, passed)),
        ("PC 기존 사이드바 보존", lambda: desktop_groups(page, root, flows, passed)),
        ("모바일 화면 크기", lambda: responsive_sizes(page, root, flows, passed)),
        ("스크립트 없는 탐색", lambda: no_script_navigation(browser, root, cookie, flows, passed)),
        ("화면 모드 선택", lambda: layout_preferences(browser, root, cookie, flows, passed)),
    ]
    try:
        for index, (label, check) in enumerate(checks):
            try:
                check()
            except Exception as error:
                capture_failure(page, flows, f"flow-{index}")
                failed("탐색: " + label, error)
        if errors:
            failed("탐색: 브라우저 스크립트 오류", AssertionError(errors))
        else:
            passed("새 탐색 흐름에서 브라우저 스크립트 오류 없음")
    finally:
        context.close()
