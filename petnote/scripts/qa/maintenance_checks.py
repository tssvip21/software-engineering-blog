"""Additional browser regressions for asynchronous UI, privacy and small screens."""
from __future__ import annotations

import json
import re
from pathlib import Path

from playwright.sync_api import expect


def member_panel_race(page, root, flows):
    flows.go(page, root + "members/?layout=desktop")
    links = page.locator(".member-list [data-member-open7]")
    assert links.count() >= 2
    first, second = links.nth(0), links.nth(1)
    first_url, second_url = first.get_attribute("href"), second.get_attribute("href")
    second_name = second.locator("strong").inner_text().strip().removesuffix("나").strip()
    page.evaluate("""first => {
      const original = window.fetch;
      window.__memberFetch = original;
      window.fetch = (url, options) => {
        if (new URL(url, location.href).pathname === first && options?.headers?.['X-Taily-Panel']) {
          return new Promise((resolve, reject) => {
            window.__releaseMember = () => original(url, options).then(resolve, reject);
          });
        }
        return original(url, options);
      };
    }""", first_url)
    try:
        first.click()
        expect(page.locator("#member-dialog7")).to_be_visible()
        page.wait_for_function("typeof window.__releaseMember === 'function'")
        page.locator("[data-member-close7]").click()
        second.click()
        expect(page.locator("#member-panel-title7")).to_contain_text(second_name)
        page.evaluate("async () => { await window.__releaseMember(); await new Promise(r => setTimeout(r, 100)); }")
        expect(page.locator("#member-panel-title7")).to_contain_text(second_name)
        assert page.locator("#member-dialog7").get_attribute("data-url").endswith(second_url)
    finally:
        page.evaluate("() => { window.fetch = window.__memberFetch; document.querySelector('#member-dialog7')?.close(); }")


def day_bar_refresh(browser, root, cookie, flows):
    context = browser.new_context(locale="ko-KR", timezone_id="Asia/Seoul", viewport={"width": 1440, "height": 950})
    context.add_cookies([cookie])
    page = context.new_page()
    try:
        page.clock.install()
        flows.go(page, root)
        page.evaluate("() => window.TailyUI.refresh()")
        before = page.locator(".day-bar-fill").evaluate("e => parseFloat(e.style.width) || 0")
        page.clock.fast_forward(31000)
        after = page.locator(".day-bar-fill").evaluate("e => parseFloat(e.style.width) || 0")
        assert after > before, {"before": before, "after": after}
    finally:
        context.close()


def exercise(browser, passed, failed, flows):
    page = browser.new_page(locale="ko-KR", timezone_id="Asia/Seoul", viewport={"width": 1440, "height": 950})
    page.set_default_timeout(6000)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("dialog", lambda dialog: dialog.accept())
    root, cookie = flows.open_account(page, passed)
    modules = flows.enable_modules(browser, root, cookie, passed)
    saved = flows.create_records(page, root, modules, passed)
    flows.check_history(page, root, saved, passed)
    flows.check_sharing(browser, page, root, saved, passed)
    for label, check in [
        ("폼 저장 응답과 이동 횟수", lambda: dirty_form_redirects(browser, passed)),
        ("구성원 창 재열기에서 늦은 응답 무시", lambda: member_panel_race(page, root, flows)),
        ("화면 갱신 뒤 오늘 시간 막대 계속 진행", lambda: day_bar_refresh(browser, root, cookie, flows)),
        ("입력창 오류와 저장 재시도", lambda: input_dialog_recovery(page, root, flows, passed)),
        ("브라우저 보안과 연속 입력", lambda: browser_security(page, root, flows, passed)),
        ("스크립트·저장소·다른 계정 경계", lambda: fallback_contexts(browser, root, cookie, flows, passed)),
        ("화면 크기와 키보드 탐색", lambda: responsive_layout(page, root, flows, passed)),
        ("산책 위치 측정과 종료", lambda: gps_controls(browser, root, cookie, flows, passed)),
    ]:
        try:
            check()
            if label in ("구성원 창 재열기에서 늦은 응답 무시", "화면 갱신 뒤 오늘 시간 막대 계속 진행"):
                passed(label)
        except Exception as error:  # noqa: BLE001 - Collect independent scenario failures.
            failed(label, error)
    from navigation_checks import exercise_navigation
    exercise_navigation(browser, root, cookie, flows, passed, failed)
    flows.check_summary(page, root, passed)
    assert not errors, errors
    page.close()


def input_dialog_recovery(page, root, flows, passed):
    flows.go(page, root + "?layout=desktop")
    trigger = page.locator('.quick-card[data-module="water"]').get_by_role("link", name="직접 입력", exact=True)
    target = trigger.get_attribute("href")
    page.route("**" + target, lambda route: route.abort(), times=1)
    trigger.click()
    expect(page.locator("[data-input-load-error]")).to_be_visible()
    passed("입력창 연결 실패에서 현재 화면 유지")
    page.locator("[data-input-retry]").click()
    form = page.locator("#input-dialog [data-input-form]")
    expect(form).to_be_visible()
    passed("입력창 다시 불러오기")
    note = form.locator('[name="note"]')
    note.fill("닫기 전에 보존할 입력")
    page.keyboard.press("Escape")
    expect(form.locator("[data-input-discard]")).to_be_visible()
    form.locator("[data-input-continue]").click()
    expect(note).to_have_value("닫기 전에 보존할 입력")
    passed("저장 전 닫기 확인에서 입력 보존")
    page.keyboard.press("Escape")
    form.locator("[data-input-discard-confirm]").click()
    expect(page.locator("#input-dialog")).not_to_be_visible()
    expect(trigger).to_be_focused()
    passed("입력 취소 뒤 원래 버튼으로 초점 복귀")
    trigger.click()
    expect(form).to_be_visible()
    note.fill("응답 유실 재시도 검사")
    for name, value in flows.SAMPLE_FIELDS["water"].items():
        flows.fill(form, name, value)
    post_url = form.get_attribute("action")
    consumed = []
    def lose_response(route):
        if route.request.method == "POST" and not consumed:
            response = route.fetch()
            assert response.status == 200
            consumed.append(True)
            route.abort()
        else:
            route.continue_()
    page.route("**" + post_url, lose_response)
    form.locator('button[type="submit"]').click()
    expect(form.locator("[data-input-status]")).to_contain_text("응답")
    expect(note).to_be_disabled()
    passed("저장 응답 유실 때 원래 입력과 요청 보존")
    page.unroute("**" + post_url, lose_response)
    form.get_by_role("button", name="저장 결과 다시 확인").click()
    expect(page.locator("#input-dialog")).not_to_be_visible()
    flows.go(page, root + "records/?q=응답 유실 재시도 검사")
    expect(page.locator("[data-record-link]")).to_have_count(1)
    passed("응답 유실 후 재시도에도 기록 한 건 유지")


def browser_security(page, root, flows, passed):
    flows.go(page, root)
    assert "care_demo_session=" not in page.evaluate("document.cookie")
    passed("브라우저 스크립트에서 세션 쿠키 접근 차단")
    status = page.evaluate("""async () => {
        const result = await fetch(document.body.dataset.actionEndpoint7, {
          method: 'POST', headers: {'Content-Type':'application/json'},
          body: JSON.stringify({operation:'quick', module:'water', token:crypto.randomUUID()})
        }); return result.status;
    }""")
    assert status == 403
    passed("브라우저의 CSRF 없는 저장 요청 거부")
    flows.go(page, root + "modules/water/new/")
    form = page.locator("#record-form")
    payload = '<img src=x onerror="window.petnoteInjected=true"> & <script>window.petnoteInjected=true</script>'
    flows.fill(form, "note", payload)
    for name, value in flows.SAMPLE_FIELDS["water"].items():
        flows.fill(form, name, value)
    form.get_by_role("button", name="기록 저장").click()
    expect(page.locator(".record-note p")).to_have_text(payload)
    expect(page.locator(".record-note img,.record-note script")).to_have_count(0)
    assert page.evaluate("window.petnoteInjected === undefined")
    passed("저장한 HTML 메모를 실행 없이 문자로 표시")
    flows.go(page, root + "records/?module=water")
    before = page.locator("[data-record-link]").count()
    flows.go(page, root)
    button = page.locator('.quick-card[data-module="water"] [data-action7]').first
    button.evaluate("button => { button.click(); button.click(); }")
    expect(page.locator("#flow-toast7")).to_contain_text("저장")
    flows.go(page, root + "records/?module=water")
    expect(page.locator("[data-record-link]")).to_have_count(before + 1)
    passed("빠른 기록 연속 클릭으로 중복 저장 방지")


def fallback_contexts(browser, root, cookie, flows, passed):
    plain = browser.new_context(java_script_enabled=False, locale="ko-KR")
    plain.add_cookies([cookie])
    page = plain.new_page()
    try:
        flows.go(page, root + "modules/water/new/")
        form = page.locator("#record-form")
        flows.fill(form, "note", "스크립트 없는 기록")
        for name, value in flows.SAMPLE_FIELDS["water"].items():
            flows.fill(form, name, value)
        form.get_by_role("button", name="기록 저장").click()
        expect(page.locator(".record-note")).to_contain_text("스크립트 없는 기록")
        passed("자바스크립트 없이 직접 기록 저장과 조회")
    finally:
        plain.close()
    restricted = browser.new_context(locale="ko-KR")
    restricted.add_cookies([cookie])
    restricted.add_init_script("""(() => {
      for (const name of ['getItem','setItem','removeItem']) {
        Storage.prototype[name] = function() { throw new DOMException('Storage disabled', 'SecurityError'); };
      }
    })()""")
    page = restricted.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        flows.go(page, root)
        page.locator('.quick-card[data-module="water"] [data-action7]').first.click()
        expect(page.locator("#flow-toast7")).to_contain_text("저장")
        assert not errors, errors
        passed("브라우저 저장소 차단 상태에서 빠른 기록")
    finally:
        restricted.close()
    stranger = browser.new_context(locale="ko-KR")
    stranger.add_cookies([flows.test_session()])
    page = stranger.new_page()
    try:
        response = page.goto(flows.BASE + root)
        assert response.status in (403, 404)
        expect(page.locator(".quick-card")).to_have_count(0)
        passed("다른 계정으로 돌봄 공간 직접 접근 거부")
    finally:
        stranger.close()


def responsive_layout(page, root, flows, passed):
    for width, mode in [(320, "mobile"), (390, "mobile"), (768, "mobile"), (1440, "desktop")]:
        page.set_viewport_size({"width": width, "height": 900})
        for suffix in ("", "modules/", "records/", "routines/", "overview/"):
            flows.go(page, root + suffix + "?layout=" + mode)
            overflow = page.evaluate("document.documentElement.scrollWidth - innerWidth")
            assert overflow <= 1, {"width": width, "page": suffix, "overflow": overflow}
        passed(f"{width}px 오늘·기능·기록·루틴·통계 화면 가로 넘침 없음")
    page.set_viewport_size({"width": 390, "height": 844})
    flows.go(page, root + "?layout=mobile")
    trigger = page.locator(".phone-menu-trigger")
    trigger.click()
    expect(page.locator("#phone-menu")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator("#phone-menu")).not_to_be_visible()
    expect(trigger).to_be_focused()
    passed("모바일 메뉴 Escape 닫기와 초점 복귀")
    # On phones a quick-record row opens the input sheet; its button records the default at once.
    page.locator('.pn-qrow[data-module="water"] .pn-qrow-open').click()
    dialog = page.locator("#input-dialog")
    expect(dialog.locator("[data-input-form]")).to_be_visible()
    # The sheet slides up when it opens; measure where it settles.
    dialog.evaluate("el => Promise.all(el.getAnimations().map(a => a.finished))")
    bounds = dialog.bounding_box()
    assert bounds["y"] >= 0 and bounds["y"] + bounds["height"] <= 845, bounds
    passed("모바일 입력창이 화면 높이 안에 표시")
    dialog.locator("[data-input-close]").focus()
    page.keyboard.press("Shift+Tab")
    expect(dialog.locator('button[type="submit"]')).to_be_focused()
    page.keyboard.press("Tab")
    expect(dialog.locator("[data-input-close]")).to_be_focused()
    passed("입력창 키보드 초점 순환")
    page.keyboard.press("Escape")
    expect(dialog).not_to_be_visible()
    trigger.click()
    page.locator("#phone-menu nav").get_by_role("link", name="기록", exact=True).click()
    # Phones open the record tab on today's timeline; the full list is one switch away.
    expect(page.locator("h1")).to_have_text(re.compile(r"^\d+월 \d+일 기록$"))
    page.locator(".pn-seg").get_by_role("link", name="전체 목록", exact=True).click()
    expect(page.locator("h1")).to_have_text("전체 기록")
    passed("모바일 메뉴에서 기록 이동과 전체 목록 전환")
    page.set_viewport_size({"width": 1440, "height": 950})
    flows.go(page, root + "?layout=desktop")
    separator = page.locator("#sidebar-resize")
    separator.focus()
    page.keyboard.press("End")
    expect(separator).to_have_attribute("aria-valuenow", "360")
    page.keyboard.press("Home")
    expect(separator).to_have_attribute("aria-valuenow", "200")
    passed("키보드로 사이드바 너비 조절")


def gps_controls(browser, root, cookie, flows, passed):
    context = browser.new_context(
        locale="ko-KR", permissions=["geolocation"],
        geolocation={"latitude": 35.1595, "longitude": 129.0756, "accuracy": 5},
        viewport={"width": 1440, "height": 950},
    )
    context.add_cookies([cookie])
    page = context.new_page()
    page.set_default_timeout(8000)
    try:
        response = page.goto(flows.BASE + root)
        assert response.headers.get("permissions-policy", "").find("geolocation=()") == -1
        page.locator('.quick-card[data-module="walk"] [data-action7="start"]').first.click()
        scope = page.locator('.flow-ongoing[data-module="walk"]')
        if not scope.count():
            scope = page.locator(".flow-ongoing").filter(has=page.locator("[data-gps-start7]"))
        expect(scope).to_be_visible()
        scope.locator("[data-gps-start7]").click()
        expect(scope.locator("[data-gps-status7]")).to_contain_text("위치 1개")
        passed("허용한 브라우저 위치로 산책 GPS 측정")
        scope.locator("[data-gps-start7]").click()
        expect(scope.locator("[data-gps-start7]")).to_contain_text("측정")
        scope.locator("[data-distance7]").fill("0.25")
        expect(scope.locator("[data-distance7]")).to_have_value("0.25")
        passed("GPS 중단 후 산책 거리 직접 입력")
        scope.locator('[data-action7="finish"]').click()
        expect(page.locator("#flow-toast7")).to_contain_text("저장")
        passed("측정 중단 뒤 산책 완료 저장")
    finally:
        context.close()


def dirty_form_redirects(browser, passed):
    """Verify the real form enhancer with isolated HTTP responses and no account."""
    app_js = (Path(__file__).resolve().parents[2] / "static/care/app.js").read_text(encoding="utf-8")
    origin = "http://petnote.test"
    initial = """<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>저장 확인</title>
      <script defer src="/app.js"></script></head><body><main id="main">
      <form method="post" action="/save" data-dirty-form>
        <label>메모<input name="note" value="처음"></label>
        <button type="submit">저장</button><p class="save-status" role="status"></p>
      </form></main></body></html>"""
    validation = initial.replace('value="처음"', 'value="입력 보존"').replace(
        '<form method="post"', '<p class="field-error">입력값을 확인해 주세요.</p><form method="post"'
    )
    cases = [
        ("success", 200, {"ok": True, "redirect_url": "/done"}),
        ("validation", 422, None),
        ("external", 200, {"ok": True, "redirect_url": "https://outside.invalid/"}),
        ("credentials", 200, {"ok": True, "redirect_url": "http://name:password@petnote.test/done"}),
        ("script", 200, {"ok": True, "redirect_url": "javascript:alert(1)"}),
        ("missing", 200, {"ok": True}),
        ("denied", 403, {"ok": True, "redirect_url": "/done"}),
    ]
    labels = {
        "success": "폼 저장 성공 후 목적지 한 번만 불러오기",
        "validation": "서버 검증 오류에서 입력 보존과 오류 초점",
        "external": "폼 응답의 외부 주소 이동 차단",
        "credentials": "폼 응답의 인증정보 포함 주소 차단",
        "script": "폼 응답의 스크립트 주소 차단",
        "missing": "불완전한 저장 응답에서 입력 보존",
        "denied": "저장 거절 응답에서 입력과 재시도 유지",
    }
    def response_handler(case, status, payload, requests):
        def respond(route):
            request = route.request
            requests.append((request.method, request.url))
            if not request.url.startswith(origin + "/"):
                route.abort()
            elif request.url == origin + "/app.js":
                route.fulfill(content_type="text/javascript", body=app_js)
            elif request.url == origin + "/form":
                route.fulfill(content_type="text/html; charset=utf-8", body=initial)
            elif request.url == origin + "/save":
                assert request.method == "POST"
                assert request.headers.get("x-requested-with") == "fetch"
                assert request.headers.get("x-petnote-response") == "redirect-json"
                if case == "validation":
                    route.fulfill(status=status, content_type="text/html; charset=utf-8", body=validation)
                else:
                    route.fulfill(status=status, content_type="application/json", body=json.dumps(payload))
            elif request.url == origin + "/done":
                route.fulfill(content_type="text/html; charset=utf-8", body='<main id="main">저장 완료</main>')
            else:
                route.abort()
        return respond

    for case, status, payload in cases:
        context = browser.new_context(locale="ko-KR")
        page = context.new_page()
        page.set_default_timeout(6000)
        errors, requests = [], []
        page.on("pageerror", lambda error, errors=errors: errors.append(str(error)))
        context.route("**/*", response_handler(case, status, payload, requests))
        try:
            page.goto(origin + "/form")
            page.locator('input[name="note"]').fill("입력 보존")
            page.get_by_role("button", name="저장", exact=True).click()
            if case == "success":
                page.wait_for_url(origin + "/done")
                expect(page.locator("#main")).to_have_text("저장 완료")
                assert requests.count(("GET", origin + "/done")) == 1, requests
            elif case == "validation":
                expect(page.locator(".field-error")).to_have_text("입력값을 확인해 주세요.")
                expect(page.locator(".field-error")).to_be_focused()
                expect(page.locator('input[name="note"]')).to_have_value("입력 보존")
                expect(page.get_by_role("button", name="저장", exact=True)).to_be_enabled()
                assert page.url == origin + "/form"
            else:
                expect(page.locator(".save-status")).to_contain_text("입력은 남아 있으니")
                expect(page.locator('input[name="note"]')).to_have_value("입력 보존")
                expect(page.get_by_role("button", name="저장", exact=True)).to_be_enabled()
                assert page.url == origin + "/form"
                assert not any(url == origin + "/done" or not url.startswith(origin + "/")
                               for _, url in requests), requests
            assert requests.count(("POST", origin + "/save")) == 1, requests
            assert not errors, errors
            passed(labels[case])
        finally:
            context.close()
