"""Run the existing local showcase with phase8 UI, without seeding or resetting data."""
from pathlib import Path
import argparse,json,os,socket,sys

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'tmp/handoff/phase7/preview-data'
WORK=ROOT/'tmp/handoff/phase8'


def main():
    parser=argparse.ArgumentParser(description='테일리 8차 로컬 확인 서버 · 기존 시연 자료 사용')
    parser.add_argument('--port',type=int,default=8036)
    args=parser.parse_args()
    if not 1024<=args.port<=65535:parser.error('1024~65535 사이의 포트를 지정하세요.')
    if not (DATA/'demo.sqlite3').is_file():parser.error('기존 로컬 시연 DB가 없어요. 새 예시를 생성하거나 기존 자료를 초기화하지 않았어요.')
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1',args.port))==0:parser.error('이미 사용 중인 포트예요. 다른 서버는 종료하지 않았어요.')
    os.environ.update(DJANGO_SETTINGS_MODULE='config.settings',CARE_DATA_DIR=str(DATA),CARE_DEMO_MODE='1',DJANGO_DEBUG='1',
        DJANGO_ALLOWED_HOSTS='127.0.0.1,localhost',APP_ORIGIN=f'http://127.0.0.1:{args.port}',
        POSTGRES_HOST='',GOOGLE_CLIENT_ID='',GOOGLE_CLIENT_SECRET='',PYTHONUTF8='1')
    sys.path.insert(0,str(ROOT))
    import django
    django.setup()
    from django.conf import settings
    from django.core.management import call_command
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor
    executor=MigrationExecutor(connection)
    if executor.migration_plan(executor.loader.graph.leaf_nodes()):
        raise SystemExit('적용되지 않은 데이터 구조 변경이 있어요. 백업과 복사본 검증 후 적용해야 해요. 서버가 자료를 임의 수정하지 않았어요.')
    settings.SESSION_COOKIE_NAME='taily_phase7_preview_session'
    settings.CSRF_COOKIE_NAME='taily_phase7_preview_csrf'
    settings.STATIC_ROOT=DATA/'staticfiles'
    call_command('check')
    call_command('collectstatic',interactive=False,verbosity=0)
    from config.wsgi import application
    from waitress import serve
    WORK.mkdir(parents=True,exist_ok=True)
    (WORK/'server-preview.json').write_text(json.dumps({'pid':os.getpid(),'port':args.port,'data':str(DATA),
        'host':'127.0.0.1','seeded':False,'migrated_on_start':False,'scope':'existing local showcase only'},ensure_ascii=False,indent=2),encoding='utf8')
    print(f'LOCAL_PREVIEW http://127.0.0.1:{args.port}',flush=True)
    serve(application,host='127.0.0.1',port=args.port,threads=4,max_request_body_size=262144,expose_tracebacks=False)


if __name__=='__main__':main()
