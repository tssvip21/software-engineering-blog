"""Run browser regressions against an isolated, disposable local SQLite server."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import urlopen

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "manage.py").is_file())
sys.path.insert(0, str(ROOT))


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def load_flows():
    source = Path(__file__).with_name("verify_petnote_browser.py")
    if not source.exists():
        source = ROOT / "scripts" / "verify_petnote_browser.py"
    spec = importlib.util.spec_from_file_location("petnote_browser_flows", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browsers", nargs="+", choices=("chromium", "firefox", "webkit"), default=["chromium", "firefox"])
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--output", type=Path, default=ROOT / "tmp" / "qa" / "browser-results.json")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    results = {"checks": [], "failures": [], "browsers": args.browsers}
    from playwright.sync_api import sync_playwright

    with tempfile.TemporaryDirectory(prefix="petnote-browser-qa-") as data_dir:
        port = args.port or free_port()
        base = f"http://127.0.0.1:{port}"
        os.environ.update({
            "DJANGO_SETTINGS_MODULE": "config.settings", "DJANGO_DEBUG": "1",
            "CARE_DEMO_MODE": "1", "CARE_PUBLIC_DEMO": "0", "CARE_PUBLIC_DEMO_ENTRY": "0",
            "CARE_DATA_DIR": data_dir, "DJANGO_SECRET_KEY": secrets.token_urlsafe(64),
            "DJANGO_ALLOWED_HOSTS": "127.0.0.1,localhost,testserver", "APP_ORIGIN": base,
            "POSTGRES_HOST": "", "VERCEL": "0", "GOOGLE_CLIENT_ID": "", "GOOGLE_CLIENT_SECRET": "",
        })
        import django
        django.setup()
        from django.contrib.auth import get_user_model
        from django.core.management import call_command
        from django.test import Client
        from django.conf import settings

        log_path = args.output.with_suffix(".server.log")
        with log_path.open("w", encoding="utf-8") as log:
            call_command("migrate", interactive=False, verbosity=0, stdout=log)
            call_command("seed_references", verbosity=0, stdout=log)
            server = subprocess.Popen(
                [sys.executable, "-X", "utf8", str(ROOT / "manage.py"), "runserver", f"127.0.0.1:{port}", "--noreload"],
                cwd=ROOT, env=os.environ.copy(), stdout=log, stderr=subprocess.STDOUT,
            )
            try:
                deadline = time.monotonic() + 30
                while True:
                    if server.poll() is not None:
                        raise RuntimeError(f"QA server exited: {log_path}")
                    try:
                        with urlopen(base + "/health/", timeout=1) as response:
                            if response.status == 200:
                                break
                    except (OSError, URLError):
                        pass
                    if time.monotonic() >= deadline:
                        raise RuntimeError(f"QA server did not become ready: {log_path}")
                    time.sleep(0.1)

                flows = load_flows()
                flows.BASE = base
                flows.OUT = args.output
                def make_session():
                    from django.db import connections
                    try:
                        account = get_user_model().objects.create_user("browser-qa-" + secrets.token_hex(10), first_name="검사 보호자")
                        client = Client()
                        client.force_login(account)
                        name = settings.SESSION_COOKIE_NAME
                        return {"name": name, "value": client.cookies[name].value, "url": base}
                    finally:
                        connections.close_all()
                def session():
                    # Playwright owns an event loop on this thread; isolate ORM setup.
                    from concurrent.futures import ThreadPoolExecutor
                    with ThreadPoolExecutor(max_workers=1) as worker:
                        return worker.submit(make_session).result()
                flows.test_session = session

                with sync_playwright() as playwright:
                    for browser_name in args.browsers:
                        browser = None
                        def passed(label):
                            entry = {"browser": browser_name, "label": label}
                            if entry in results["checks"]:
                                raise AssertionError(f"Repeated check: {entry}")
                            results["checks"].append(entry)
                            print(f"{browser_name}: {label}", flush=True)
                        try:
                            browser = getattr(playwright, browser_name).launch()
                            from maintenance_checks import exercise
                            def failed(label, error):
                                results["failures"].append({"browser": browser_name, "label": label, "error": str(error)})
                            exercise(browser, passed, failed, flows)
                        except Exception as error:
                            results["failures"].append({"browser": browser_name, "error": str(error)})
                            if browser:
                                for index, context in enumerate(browser.contexts):
                                    if context.pages:
                                        try:
                                            context.pages[-1].screenshot(path=str(args.output.parent / f"{browser_name}-failure-{index}.png"), full_page=True)
                                        except Exception:
                                            pass
                        finally:
                            if browser:
                                browser.close()
                            args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
            finally:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=10)
                from django.db import connections
                connections.close_all()
    print(json.dumps({"passed": len(results["checks"]), "failures": results["failures"], "output": str(args.output)}, ensure_ascii=False))
    return int(bool(results["failures"]))


if __name__ == "__main__":
    raise SystemExit(main())
