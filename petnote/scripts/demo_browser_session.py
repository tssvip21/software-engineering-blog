"""Select a fixture role for older local browser scenarios."""
from urllib.parse import urlsplit


def select_fixture_role(page, role):
    if urlsplit(page.url).hostname not in ("127.0.0.1", "localhost") or role not in ("owner", "member"):
        raise ValueError("로컬 시연 검사 전용 계정이에요.")
    form = page.locator(".demo-picker form")
    response = page.request.post(
        page.url.rstrip("/") + "/demo/login/",
        form={"csrfmiddlewaretoken": form.locator('[name="csrfmiddlewaretoken"]').input_value(), "identity": role},
        max_redirects=0,
    )
    assert response.status == 302
    page.goto(page.url)
