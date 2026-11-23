"""Exercise the dedicated local Compose stack in Chromium, using synthetic accounts."""
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from care.demo_fixtures import SAMPLE_FIELDS

BASE = "http://127.0.0.1:8097"
CONTAINER = "taily-local-petnote-qa-backend-1"
OUT = ROOT / "tmp" / "petnote-browser-results.json"


def test_session():
    # Only test setup uses Django's test client. All subsequent actions use Chromium and Caddy.
    fixture = """
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client
import json, uuid
assert settings.DEBUG and settings.APP_ORIGIN == 'http://127.0.0.1:8097'
account = get_user_model().objects.create_user('qa-' + uuid.uuid4().hex, first_name='검사 보호자')
client = Client()
client.force_login(account)
key = settings.SESSION_COOKIE_NAME
print(json.dumps({'name': key, 'value': client.cookies[key].value, 'url': settings.APP_ORIGIN}))
"""
    result = subprocess.run(["docker", "exec", "-i", CONTAINER, "python", "manage.py", "shell",
                             "-c", "exec(__import__('sys').stdin.read())"], input=fixture.encode(),
                            capture_output=True, check=True)
    return json.loads(result.stdout.splitlines()[-1])


def go(page, path, status=200):
    response = page.goto(BASE + path)
    assert response and response.status == status, (path, response.status if response else None)


def fill(form, name, value):
    field = form.locator(f'[name="{name}"]')
    if field.get_attribute("type") == "hidden":
        expect(field).to_have_value(value)
    elif field.evaluate("e => e.tagName") == "SELECT":
        field.select_option(value)
    else:
        field.fill(value)


def open_account(page, passed):
    go(page, "/")
    expect(page.locator(".landing-hero")).to_be_visible()
    passed("비로그인 첫 화면")
    go(page, "/rooms/new/")
    assert page.url == BASE + "/"
    passed("로그인 전 등록 안내")
    cookie = test_session()
    page.context.add_cookies([cookie])
    go(page, "/")
    expect(page.get_by_role("link", name="첫 반려동물 등록")).to_be_visible()
    passed("새 계정의 빈 공간 목록")
    page.get_by_role("link", name="첫 반려동물 등록").click()
    pet = page.locator("form[data-pet-profile]")
    pet.locator('[name="name"]').fill("검사 보리")
    pet.locator('[name="species"]').select_option("dog")
    pet.locator('[type="submit"]').click()
    page.wait_for_url(re.compile(r"/rooms/[0-9a-f-]{36}/$"))
    root = page.url.removeprefix(BASE)
    passed("반려동물 등록 후 오늘 화면")
    go(page, "/")
    page.locator("a.room-card").filter(has_text="검사 보리").click()
    assert page.url == BASE + root
    passed("내 공간 목록에서 재접속")
    go(page, "/rooms/new/")
    other = page.locator("form[data-pet-profile]")
    fill(other, "name", "재시도 확인 고양이")
    fill(other, "species", "cat")
    answers = other.evaluate("""async form => {
        const send = async () => {
            const response = await fetch(form.action, {method: 'POST', body: new FormData(form)});
            return [response.status, response.url];
        };
        return Promise.all([send(), send()]);
    }""")
    assert answers[0] == answers[1] and answers[0][0] == 200
    go(page, "/")
    expect(page.locator(".room-card").filter(has_text="재시도 확인 고양이")).to_have_count(1)
    passed("동시 등록 요청 두 번에 공간 하나")
    go(page, root)
    page.locator('.quick-card[data-module="water"] [data-action7]').first.click()
    toast = page.locator("#flow-toast7")
    expect(toast).to_contain_text("저장")
    toast.locator("[data-toast-link7]").click()
    expect(page.locator("h1")).to_contain_text("물")
    passed("오늘 화면에서 물 교체 한 번에 기록")
    go(page, root + "routines/")
    page.get_by_role("link", name="루틴 추가").click()
    dialog = page.locator("#input-dialog [data-input-form]")
    fill(dialog, "name", "오전 물 확인")
    fill(dialog, "at", "08:30")
    dialog.locator('[name="modules"][value="water"]').check()
    dialog.locator('button[type="submit"]').click()
    expect(dialog).not_to_be_visible()
    go(page, root + "routines/")
    expect(page.locator(".preset-list7 h3").filter(has_text="오전 물 확인")).to_be_visible()
    passed("루틴 등록 후 목록에서 일정 확인")
    return root, cookie


def enable_modules(browser, root, cookie, passed):
    # Also exercise the HTML-only module editor, which exposes every selectable module.
    plain = browser.new_context(java_script_enabled=False)
    plain.add_cookies([cookie])
    editor = plain.new_page()
    go(editor, root + "modules/settings/")
    form = editor.locator("form").filter(has=editor.get_by_role("button", name="기능 저장"))
    options = form.locator('[name="enabled"]')
    modules = options.evaluate_all("items => items.map(e => e.value)")
    assert set(modules) == set(SAMPLE_FIELDS) - {"grooming", "reproduction"}
    for option in options.all():
        option.check()
    form.get_by_role("button", name="기능 저장").click()
    go(editor, root + "modules/settings/")
    assert editor.locator('form input[name=enabled]:checked').count() == len(modules)
    plain.close()
    passed("돌봄 기능 선택 저장과 재조회")
    return modules


def create_records(page, root, modules, passed):
    saved = {}
    for module in modules:
        go(page, root + f"modules/{module}/new/")
        form = page.locator("#record-form")
        fill(form, "status", "done")
        for name, value in SAMPLE_FIELDS[module].items():
            fill(form, name, value)
        note = f"확인 메모 {module}"
        fill(form, "note", note)
        if module == "sleep":
            form.get_by_role("button", name="기록 저장").click()
            expect(form.locator(".field-error")).to_contain_text("아직 끝나지 않은")
            passed("끝나지 않은 수면 기록 안내")
            start = datetime.fromisoformat(form.locator('[name="occurred_at"]').input_value())
            fill(form, "occurred_at", (start - timedelta(hours=2)).isoformat(timespec="minutes"))
        form.get_by_role("button", name="기록 저장").click()
        expect(page.locator(".record-note")).to_contain_text(note)
        assert "/records/" in page.url
        saved[module] = page.url.split("?")[0].removeprefix(BASE)
        passed(f"{module} 직접 입력 후 저장된 상세 확인")

    return saved


def check_history(page, root, saved, passed):
    for category, sample in [("nutrition", "water"), ("activity", "walk"), ("wellbeing", "toilet"), ("living", "expense")]:
        go(page, root + "records/")
        page.locator(f'.record-categories [data-category="{category}"]').click()
        expect(page.locator(".category-record-list")).to_contain_text("확인 메모 " + sample)
        passed(category + " 분류에서 기록 찾기")
    go(page, root + "records/")
    page.locator("#record-query").fill("확인 메모 walk")
    page.get_by_role("button", name="검색", exact=True).click()
    expect(page.locator("[data-record-link]")).to_have_count(1)
    page.locator("[data-record-link]").click()
    expect(page.locator(".record-note")).to_contain_text("확인 메모 walk")
    passed("메모로 검색 후 상세 열기")
    page.locator("[data-record-back]").click()
    expect(page.locator("#record-query")).to_have_value("확인 메모 walk")
    passed("상세에서 검색 조건 복원")
    go(page, root + "records/?q=없는기록123")
    expect(page.locator("[data-record-link]")).to_have_count(0)
    expect(page.locator(".empty-copy").first).to_be_visible()
    passed("검색 결과 없음")
    go(page, root + "records/?start=2025-02-02&end=2025-01-01")
    expect(page.locator(".form-errors")).to_contain_text("종료일")
    passed("기간 순서 오류 안내")
    for dates, message in [
        ("start=2025-02-02&end=2025-01-01", "종료일을 시작일"),
        ("start=2023-01-01&end=2025-01-01", "최대 366일"),
    ]:
        go(page, root + "modules/walk/statistics/?" + dates)
        expect(page.get_by_text(message, exact=False).first).to_be_visible()
        passed("통계 날짜 검사 · " + message)
    go(page, root + "records/?category=nutrition&module=weight")
    expect(page.locator("[data-record-link]")).to_have_count(1)
    expect(page.locator("[data-record-link]")).to_contain_text("확인 메모 weight")
    passed("분류 안에서 체중만 조회")
    go(page, saved["walk"] + "edit/")
    form = page.locator("#record-form")
    expect(form.locator('[name="minutes"]')).to_have_value("25")
    fill(form, "minutes", "35")
    fill(form, "edit_reason", "산책 시간 확인")
    form.get_by_role("button", name="기록 저장").click()
    expect(page.locator(".detail-list").first).to_contain_text("35")
    passed("산책 시간 수정 저장")
    expect(page.locator(".history-list")).to_contain_text("산책 시간 확인")
    passed("수정 이유와 이력 조회")
    go(page, saved["walk"])
    page.locator(".danger-zone summary").click()
    page.locator('[name="reason"]').fill("중복 입력 확인")
    page.get_by_role("button", name="기록 취소", exact=True).click()
    expect(page.locator(".notice").filter(has_text="중복 입력 확인")).to_be_visible()
    passed("기록 취소와 이유 확인")
    go(page, root + "records/?module=walk")
    expect(page.locator("[data-record-link]")).to_have_count(0)
    passed("기본 목록에서 취소 기록 제외")
    go(page, root + "records/?module=walk&state=cancelled")
    expect(page.locator(".is-void")).to_contain_text("확인 메모 walk")
    passed("취소한 기록만 다시 조회")


def check_sharing(browser, page, root, saved, passed):
    go(page, root + "members/")
    page.get_by_role("button", name="초대 링크 만들기").click()
    invite = page.locator("#created-invite").input_value()
    assert invite.startswith(BASE + "/join/#")
    passed("공동 보호자 초대 발급")
    guest = browser.new_page(locale="ko-KR", viewport={"width": 390, "height": 844})
    guest.goto(invite)
    expect(guest.locator('[name="display_name"]')).to_be_visible()
    passed("다른 브라우저에서 초대 미리보기")
    guest.locator('[name="display_name"]').fill("함께 돌보는 사람")
    guest.locator('[name="agree"]').check()
    guest.get_by_role("button", name="참여하기").click()
    guest.wait_for_url(BASE + root)
    passed("초대 동의 후 게스트 참여")
    go(guest, root + "members/")
    expect(guest.locator(".member-list")).to_contain_text("함께 돌보는 사람")
    passed("참여자 이름이 구성원 목록에 반영")
    go(guest, saved["water"])
    expect(guest.locator(".record-note")).to_contain_text("확인 메모 water")
    passed("공동 보호자가 일상 기록 조회")
    go(guest, root + "records/")
    assert not guest.locator('[data-record-link]').filter(has_text="확인 메모 medication").count()
    go(guest, saved["medication"], 403)
    passed("비공개 투약 기록 권한 확인")
    go(guest, root + "modules/settings/", 403)
    passed("게스트 기능 설정 제한")
    go(guest, root)
    expect(guest.locator(".mobile-dock")).to_be_visible()
    guest.locator(".mobile-dock").get_by_text("기록", exact=True).click()
    expect(guest.locator("h1")).to_have_text("전체 기록")
    passed("모바일 하단 탐색으로 전체 기록 보기")
    go(page, root + "members/")
    page.get_by_role("button", name="사용 중단").click()
    expect(page.locator(".invitation-list")).to_contain_text("사용 중단")
    passed("초대 링크 중단")
    go(guest, root)
    expect(guest.locator(".mobile-dock")).to_be_visible()
    passed("초대 중단 뒤 기존 구성원 계속 사용")
    guest.close()


def check_summary(page, root, passed):
    for days in (7, 30, 90):
        go(page, root + "overview/?days=" + str(days))
        expect(page.locator('.statistics-periods [aria-current="page"]')).to_contain_text(str(days))
        expect(page.locator("#measurements7")).to_be_visible()
        passed(f"최근 {days}일 통계 기간 선택")
    go(page, root + "supplies/?tab=stock")
    expect(page.locator(".stock-reading").first).to_have_text(re.compile(r"^\s*(2\s*kg|2,?000\s*g)\s*$"))
    passed("입고 기록으로 재고 탭 잔량 계산")
    go(page, root + "preferences/")
    expect(page.locator('[name="timezone"]')).to_have_value("Asia/Seoul")
    passed("공간 시간대 설정 불러오기")
    go(page, root + "settings/")
    expect(page.locator('[name="name"]')).to_have_value("검사 보리")
    passed("반려동물 프로필 다시 열기")
    go(page, "/")
    page.get_by_role("button", name="로그아웃").click()
    expect(page.locator(".landing-hero")).to_be_visible()
    passed("로그아웃 후 첫 화면")



def exercise(browser, passed):
    page = browser.new_page(locale="ko-KR", timezone_id="Asia/Seoul", viewport={"width": 1440, "height": 950})
    page.set_default_timeout(5500)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("dialog", lambda dialog: dialog.accept())
    root, cookie = open_account(page, passed)
    modules = enable_modules(browser, root, cookie, passed)
    saved = create_records(page, root, modules, passed)
    check_history(page, root, saved, passed)
    check_sharing(browser, page, root, saved, passed)
    check_summary(page, root, passed)
    assert not errors, errors
    page.close()


def main():
    results = []
    def passed(label):
        assert label not in results
        results.append(label)
        print(f"{len(results):02} {label}", flush=True)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            exercise(browser, passed)
            browser.close()
        assert len(results) >= 50
    finally:
        OUT.parent.mkdir(exist_ok=True)
        OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf8")


if __name__ == "__main__":
    main()
