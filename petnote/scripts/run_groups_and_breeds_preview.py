"""Run a private review copy without touching existing servers or source databases."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import socket
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'tmp/handoff/phase3/preview-data'


def main():
    parser=argparse.ArgumentParser(description='테일리 3차 로컬 검수 화면')
    parser.add_argument('--port',type=int,default=8013)
    args=parser.parse_args()
    if not 1024<=args.port<=65535:
        parser.error('1024–65535 포트를 선택해 주세요.')
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1',args.port))==0:
            parser.error(f'{args.port}번 포트가 사용 중이에요. 기존 프로세스는 종료하지 않았어요. 다른 --port를 지정해 주세요.')
    DATA.mkdir(parents=True,exist_ok=True)
    source=ROOT/'data/demo.sqlite3';target=DATA/'demo.sqlite3'
    if not target.exists() and source.is_file():
        with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(target)) as dst:
            src.backup(dst)
    os.environ.update(DJANGO_SETTINGS_MODULE='config.settings',CARE_DATA_DIR=str(DATA),CARE_DEMO_MODE='1',DJANGO_DEBUG='1',APP_ORIGIN=f'http://127.0.0.1:{args.port}',DJANGO_ALLOWED_HOSTS='127.0.0.1,localhost')
    sys.path.insert(0,str(ROOT))
    import django
    django.setup()
    from django.conf import settings
    # Different cookie names avoid replacing an existing local demo login session.
    settings.SESSION_COOKIE_NAME='taily_phase3_preview_session'
    settings.CSRF_COOKIE_NAME='taily_phase3_preview_csrf'
    settings.STATIC_ROOT=DATA/'staticfiles'
    from django.core.management import call_command
    call_command('check')
    call_command('migrate',interactive=False,verbosity=0)
    call_command('seed_demo')
    call_command('seed_references')
    call_command('collectstatic',interactive=False,verbosity=0)
    from config.wsgi import application
    from waitress import serve
    marker=ROOT/'tmp/handoff/phase3/server-preview.json'
    marker.write_text(json.dumps({'pid':os.getpid(),'port':args.port,'data':str(DATA),'source_untouched':True},ensure_ascii=False),encoding='utf8')
    print(f'PREVIEW http://127.0.0.1:{args.port} — 별도 검수용 DB',flush=True)
    print('Original databases, existing servers and their login cookies are unchanged.',flush=True)
    serve(application,host='127.0.0.1',port=args.port,threads=4,max_request_body_size=262144,expose_tracebacks=False)


if __name__=='__main__':
    main()
