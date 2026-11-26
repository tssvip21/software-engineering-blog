"""Check public PetNote routes without creating users, spaces, or care records.

Default checks use GET/HEAD with new anonymous sessions. --oauth-flow additionally
starts and cancels one login attempt; this creates temporary session/rate-limit
metadata, but never visits Google or exchanges an authorization code.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
from pathlib import Path
import time
from urllib.parse import parse_qs, urljoin, urlsplit

import requests

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "manage.py").is_file())
VIEWPORTS = ((320, 740), (375, 812), (390, 844), (768, 1024), (1280, 900))
PRIVATE_ROOM_PATH = "/rooms/00000000-0000-0000-0000-000000000000/"


class PublicHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.assets = []
        self.forms = []
        self.form = None
        self.layout = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "body":
            self.layout = attrs.get("data-layout", "desktop")
        elif tag == "script" and attrs.get("src"):
            self.assets.append(attrs["src"])
        elif tag == "link" and "stylesheet" in attrs.get("rel", "").split() and attrs.get("href"):
            self.assets.append(attrs["href"])
        elif tag == "form":
            self.form = {"action": attrs.get("action", ""), "csrf": "", "disabled": False}
            self.forms.append(self.form)
        elif self.form is not None and tag == "input" and attrs.get("name") == "csrfmiddlewaretoken":
            self.form["csrf"] = attrs.get("value", "")
        elif self.form is not None and tag == "button" and "disabled" in attrs:
            self.form["disabled"] = True

    def handle_endtag(self, tag):
        if tag == "form":
            self.form = None


def valid_origin(value):
    parsed = urlsplit(value)
    local = parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost", "::1")
    if (not (parsed.scheme == "https" or local) or not parsed.hostname
            or parsed.username or parsed.password or parsed.path not in ("", "/")
            or parsed.query or parsed.fragment):
        raise argparse.ArgumentTypeError("Use an HTTPS origin, or localhost HTTP for a disposable check.")
    return value.rstrip("/")


def same_origin(url, origin):
    return urlsplit(url)._replace(path="", query="", fragment="") == urlsplit(origin)._replace(path="", query="", fragment="")


def add(results, label, passed, **details):
    results["checks"].append({"label": label, "passed": bool(passed), **details})


def request(session, origin, method, path, **kwargs):
    # Redirects are inspected before another request; no third-party requests here.
    return session.request(method, origin + path, timeout=(5, 20), allow_redirects=False, **kwargs)


def check_http(results, args):
    with requests.Session() as session:
        session.headers["User-Agent"] = "PetNote-Public-Check/1.0"
        start = time.monotonic()
        home = request(session, args.origin, "GET", "/")
        add(results, "home_get", home.status_code == 200, status=home.status_code,
            seconds=round(time.monotonic() - start, 3))
        if home.status_code != 200:
            return
        add(results, "home_security_headers",
            "no-store" in home.headers.get("Cache-Control", "")
            and home.headers.get("X-Content-Type-Options") == "nosniff"
            and home.headers.get("X-Frame-Options") == "DENY"
            and "frame-ancestors 'none'" in home.headers.get("Content-Security-Policy", ""))
        head = request(session, args.origin, "HEAD", "/")
        results["observations"]["home_head_status"] = head.status_code
        add(results, "home_head_has_expected_contract", head.status_code in ((200, 405) if args.baseline else (200,)),
            status=head.status_code, note="Legacy HEAD 405 is accepted only in baseline mode.")

        health = request(session, args.origin, "GET", "/health/")
        try:
            healthy = health.json() == {"status": "ok"}
        except ValueError:
            healthy = False
        add(results, "health_get", health.status_code == 200 and healthy, status=health.status_code)
        head = request(session, args.origin, "HEAD", "/health/")
        allowed = (200, 405) if args.baseline else (200,)
        add(results, "health_head", head.status_code in allowed, status=head.status_code)
        if head.status_code == 405 and args.baseline:
            results["notes"].append("Legacy health HEAD 405 accepted only for the requested baseline.")

        join = request(session, args.origin, "GET", "/join/")
        add(results, "invitation_entry_get", join.status_code == 200, status=join.status_code)
        protected = request(session, args.origin, "GET", "/rooms/new/")
        target = urljoin(args.origin, protected.headers.get("Location", ""))
        add(results, "registration_requires_login",
            protected.status_code == 302 and same_origin(target, args.origin) and urlsplit(target).path == "/",
            status=protected.status_code)
        inaccessible = request(session, args.origin, "GET", PRIVATE_ROOM_PATH)
        add(results, "anonymous_room_is_not_exposed", inaccessible.status_code == 404,
            status=inaccessible.status_code)

        document = PublicHTML()
        document.feed(home.text)
        assets = []
        for asset in document.assets:
            url = urljoin(args.origin + "/", asset)
            if same_origin(url, args.origin) and urlsplit(url).path.startswith("/static/") and url not in assets:
                assets.append(url)
        add(results, "same_origin_assets_discovered", bool(assets), count=len(assets))
        for url in assets:
            parsed = urlsplit(url)
            # The referenced static query may contain a cache version, never a credential.
            asset = request(session, args.origin, "GET", parsed.path + ("?" + parsed.query if parsed.query else ""))
            content_type = asset.headers.get("Content-Type", "").split(";", 1)[0].strip()
            expected = ("text/css",) if parsed.path.endswith(".css") else ("text/javascript", "application/javascript")
            add(results, "static_asset", asset.status_code == 200 and content_type in expected,
                path=parsed.path, status=asset.status_code, content_type=content_type)

        if not args.baseline:
            check_mobile_user_agents(results, args, session)

        oauth_forms = [form for form in document.forms
                       if same_origin(urljoin(args.origin, form["action"]), args.origin)
                       and urlsplit(urljoin(args.origin, form["action"])).path == "/auth/google/start/"]
        add(results, "google_login_is_configured",
            bool(oauth_forms) and any(form["csrf"] and not form["disabled"] for form in oauth_forms))
        if args.oauth_flow and oauth_forms:
            check_oauth(results, args, session, next((form for form in oauth_forms if not form["disabled"]), oauth_forms[0]))
        elif not args.oauth_flow:
            results["notes"].append("OAuth start/cancel was not requested; no login attempt was submitted.")


def check_mobile_user_agents(results, args, session):
    # Representative browser headers exercise the fallback used without client hints.
    agents = (
        ("iPhone Safari", "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
         "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"),
        ("Android Chrome", "Mozilla/5.0 (Linux; Android 15; Pixel 9) "
         "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"),
        ("Android Firefox", "Mozilla/5.0 (Android 15; Mobile; rv:132.0) Gecko/132.0 Firefox/132.0"),
    )
    for kind, agent in agents:
        response = request(session, args.origin, "GET", "/",
                           headers={"User-Agent": agent, "Sec-CH-UA-Mobile": None})
        document = PublicHTML()
        document.feed(response.text)
        add(results, "mobile_user_agent_without_client_hint",
            response.status_code == 200 and document.layout == "mobile",
            browser_type=kind, observed_layout=document.layout, status=response.status_code)


def check_oauth(results, args, session, form):
    if form["disabled"] or not form["csrf"]:
        add(results, "oauth_start", False, reason="Login form is disabled or lacks CSRF.")
        return
    response = request(session, args.origin, "POST", "/auth/google/start/",
                       data={"csrfmiddlewaretoken": form["csrf"]},
                       headers={"Origin": args.origin, "Referer": args.origin + "/"})
    target = urlsplit(response.headers.get("Location", ""))
    query = parse_qs(target.query)
    state = query.get("state", [""])[0]
    callback = query.get("redirect_uri", [""])[0]
    good = (response.status_code == 302 and target.scheme == "https"
            and target.hostname == "accounts.google.com"
            and callback == args.origin + "/auth/google/callback/"
            and query.get("response_type") == ["code"]
            and query.get("code_challenge_method") == ["S256"]
            and bool(query.get("code_challenge")) and bool(query.get("nonce"))
            and len(state) >= 32)
    add(results, "oauth_start", good, status=response.status_code)
    if not good:
        return
    # Only our own generated state is used, with cancellation instead of an auth code.
    cancelled = request(session, args.origin, "GET", "/auth/google/callback/",
                        params={"error": "access_denied", "state": state})
    destination = urljoin(args.origin, cancelled.headers.get("Location", ""))
    add(results, "oauth_cancel_returns_home",
        cancelled.status_code == 302 and same_origin(destination, args.origin)
        and urlsplit(destination).path == "/", status=cancelled.status_code)
    if cancelled.status_code == 302 and same_origin(destination, args.origin):
        home = request(session, args.origin, "GET", "/")
        add(results, "oauth_cancel_notice", home.status_code == 200 and "로그인을 취소" in home.text)
    results["notes"].append("One login attempt was started and cancelled. Google was not visited; no auth code or user was created.")


def check_browser_context(results, args, browser, name, mode):
    mobile = mode == "mobile"
    context = browser.new_context(
        viewport={"width": 390 if mobile else 1280, "height": 844 if mobile else 900},
        has_touch=mobile,
        extra_http_headers={"Sec-CH-UA-Mobile": "?1" if mobile else "?0"},
    )
    # Match the server's mobile client-hint selection without any saved login state.
    blocked = []
    def guard(route):
        url = route.request.url
        if route.request.method not in ("GET", "HEAD") or not same_origin(url, args.origin):
            blocked.append({"method": route.request.method, "external": not same_origin(url, args.origin)})
            route.abort()
        else:
            route.continue_()
    context.route("**/*", guard)
    page = context.new_page()
    console_errors, page_errors = [], []
    page.on("console", lambda message: console_errors.append(True) if message.type == "error" else None)
    page.on("pageerror", lambda error: page_errors.append(True))
    page.set_default_timeout(15000)
    page.set_default_navigation_timeout(30000)
    try:
        response = page.goto(args.origin + "/", wait_until="networkidle")
        add(results, "browser_home_get", response is not None and response.status == 200, browser=name, mode=mode)
        page.evaluate("document.fonts.ready.then(() => true)")
        observed_mode = page.locator("body").get_attribute("data-layout") or "desktop"
        add(results, "browser_layout_selection", observed_mode == mode or args.baseline,
            browser=name, mode=mode, observed=observed_mode)
        if mobile and observed_mode != "mobile" and args.baseline:
            results["notes"].append(f"{name}: baseline serves the legacy template for mobile hints.")
        for width, height in (VIEWPORTS[:-1] if mobile else VIEWPORTS[-1:]):
            page.set_viewport_size({"width": width, "height": height})
            page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
            layout = page.evaluate("""() => ({
                width: window.innerWidth,
                documentWidth: document.documentElement.scrollWidth,
                bodyWidth: document.body.scrollWidth,
                headingVisible: [...document.querySelectorAll('h1')].some(el => {
                    const rect = el.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                })
            })""")
            add(results, "anonymous_responsive_layout",
                max(layout["documentWidth"], layout["bodyWidth"]) <= layout["width"] + 1
                and layout["headingVisible"], browser=name, mode=mode, viewport=width, **layout)
            if args.screenshots:
                screenshot = args.output.parent / f"public-{name}-{width}.png"
                page.screenshot(path=str(screenshot), full_page=True)
        response = page.goto(args.origin + "/join/", wait_until="networkidle")
        add(results, "browser_invitation_get", response is not None and response.status == 200, browser=name, mode=mode)
        add(results, "browser_javascript", not console_errors and not page_errors,
            browser=name, mode=mode, console_errors=len(console_errors), page_errors=len(page_errors))
        add(results, "browser_read_only_same_origin", not blocked, browser=name, mode=mode, blocked_requests=len(blocked))
    finally:
        context.close()


def check_browsers(results, args):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        for name in args.browsers:
            browser = None
            try:
                browser = getattr(playwright, name).launch()
                for mode in ("mobile", "desktop"):
                    try:
                        check_browser_context(results, args, browser, name, mode)
                    except Exception as error:
                        add(results, "browser_context", False, browser=name, mode=mode,
                            error_type=type(error).__name__)
            except Exception as error:
                add(results, "browser_run", False, browser=name, error_type=type(error).__name__)
            finally:
                if browser:
                    browser.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", type=valid_origin, default="https://petnote.duckdns.org")
    parser.add_argument("--output", type=Path, default=ROOT / "tmp" / "qa" / "public-results.json")
    parser.add_argument("--browsers", nargs="+", choices=("chromium", "firefox", "webkit"), default=["chromium", "firefox"])
    parser.add_argument("--http-only", action="store_true")
    parser.add_argument("--oauth-flow", action="store_true", help="Start/cancel one OAuth attempt; creates session/rate-limit metadata only.")
    parser.add_argument("--baseline", "--allow-legacy-health-head", dest="baseline", action="store_true",
                        help="Record legacy HEAD 405 and absent mobile template for a pre-deployment baseline only.")
    parser.add_argument("--screenshots", action="store_true")
    args = parser.parse_args(argv)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    results = {"origin": args.origin, "started_utc": datetime.now(timezone.utc).isoformat(),
               "checks": [], "observations": {}, "notes": [], "oauth_requested": args.oauth_flow,
               "scope": "Anonymous public pages only; no user, space, or care-record creation."}
    try:
        check_http(results, args)
    except Exception as error:
        add(results, "http_run", False, error_type=type(error).__name__)
    if not args.http_only and any(item["label"] == "home_get" and item["passed"] for item in results["checks"]):
        check_browsers(results, args)
    results["passed"] = bool(results["checks"]) and all(item["passed"] for item in results["checks"])
    results["finished_utc"] = datetime.now(timezone.utc).isoformat()
    args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": results["passed"], "checks": len(results["checks"]),
                      "failures": [item for item in results["checks"] if not item["passed"]],
                      "output": str(args.output)}, ensure_ascii=False))
    return int(not results["passed"])


if __name__ == "__main__":
    raise SystemExit(main())
