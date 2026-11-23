"""Local-only Phase 7 review. Never replaces an original DB or stops another server."""
from contextlib import closing
from pathlib import Path
import argparse
import json
import os
import socket
import sys

ROOT=Path(__file__).resolve().parents[1]
WORK=ROOT/"tmp/handoff/phase7"
DATA=WORK/"preview-data"

def main():
    parser=argparse.ArgumentParser(description="테일리 7차 통계·기록 검수 환경")
    parser.add_argument("--port",type=int,default=8036)
    args=parser.parse_args()
    if not 1024<=args.port<=65535:
        parser.error("1024~65535 사이의 포트를 선택하세요.")
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1",args.port))==0:
            parser.error("기존 프로세스가 사용하는 포트입니다. 다른 서버를 종료하지 않았습니다.")
    if not (DATA/"demo.sqlite3").is_file() or not (WORK/"fixture.json").is_file():
        parser.error("7차 독립 시연 데이터가 준비되지 않았습니다. 기존 DB를 복사/초기화하지 않습니다.")
    os.environ.update(
        DJANGO_SETTINGS_MODULE="config.settings",
        CARE_DATA_DIR=str(DATA),
        CARE_DEMO_MODE="1",
        DJANGO_DEBUG="1",
        APP_ORIGIN=f"http://127.0.0.1:{args.port}",
        DJANGO_ALLOWED_HOSTS="127.0.0.1,localhost",
    )
    sys.path.insert(0,str(ROOT))
    import django
    django.setup()
    from django.conf import settings
    from django.core.management import call_command
    settings.SESSION_COOKIE_NAME="taily_phase7_preview_session"
    settings.CSRF_COOKIE_NAME="taily_phase7_preview_csrf"
    settings.STATIC_ROOT=DATA/"staticfiles"
    call_command("check")
    call_command("migrate",interactive=False,verbosity=0)
    call_command("seed_demo",verbosity=0)
    call_command("seed_references",verbosity=0)
    call_command("collectstatic",interactive=False,verbosity=0)
    from config.wsgi import application
    from waitress import serve
    info={"pid":os.getpid(),"port":args.port,"data":str(DATA),
          "scope":"localhost-only, isolated phase7 demo database",
          "source_original_db_used_as_runtime":False}
    (WORK/"server-preview.json").write_text(json.dumps(info,ensure_ascii=False,indent=2),encoding="utf8")
    print(f"PHASE7_PREVIEW http://127.0.0.1:{args.port}",flush=True)
    serve(application,host="127.0.0.1",port=args.port,threads=4,max_request_body_size=262144,expose_tracebacks=False)

if __name__=="__main__":
    main()
