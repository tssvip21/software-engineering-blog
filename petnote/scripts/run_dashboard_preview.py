"""Isolated local review. Never replaces original databases or stops another server."""
from contextlib import closing
from pathlib import Path
import argparse
import json
import os
import socket
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "tmp/handoff/phase5/preview-data"


def main():
    parser = argparse.ArgumentParser(description="테일리 5차 검수 화면 실행")
    parser.add_argument("--port", type=int, default=8017)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("1024~65535 사이 포트를 선택해 주세요.")
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", args.port)) == 0:
            parser.error("이미 사용 중인 포트예요. 기존 서버는 종료하지 않았어요.")
    DATA.mkdir(parents=True, exist_ok=True)
    target, source = DATA / "demo.sqlite3", ROOT / "data/demo.sqlite3"
    if source.is_file() and not target.exists():
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as src, closing(sqlite3.connect(target)) as dst:
            src.backup(dst)
    os.environ.update(DJANGO_SETTINGS_MODULE="config.settings", CARE_DATA_DIR=str(DATA), CARE_DEMO_MODE="1",
                      DJANGO_DEBUG="1", APP_ORIGIN=f"http://127.0.0.1:{args.port}", DJANGO_ALLOWED_HOSTS="localhost,127.0.0.1")
    sys.path.insert(0, str(ROOT))
    import django
    django.setup()
    from django.conf import settings
    from django.core.management import call_command
    settings.SESSION_COOKIE_NAME = "taily_phase5_preview_session"
    settings.CSRF_COOKIE_NAME = "taily_phase5_preview_csrf"
    settings.STATIC_ROOT = DATA / "staticfiles"
    call_command("check")
    call_command("migrate", interactive=False, verbosity=0)
    call_command("seed_demo")
    call_command("seed_references")
    call_command("collectstatic", interactive=False, verbosity=0)
    from config.wsgi import application
    from waitress import serve
    (DATA.parent / "server-preview.json").write_text(json.dumps({"pid": os.getpid(), "port": args.port,
        "data": str(DATA), "original_data_untouched": True}), encoding="utf-8")
    print(f"PREVIEW http://127.0.0.1:{args.port}", flush=True)
    serve(application, host="127.0.0.1", port=args.port, threads=4,
          max_request_body_size=262144, expose_tracebacks=False)


if __name__ == "__main__":
    main()
