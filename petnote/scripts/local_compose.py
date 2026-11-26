"""Portable, loopback-only Docker Compose launcher for Windows and macOS.

No Google credentials are copied. A persistent local env file and named volumes
belong to this project only. 'down' keeps volumes; there is no data-reset option.
"""
from __future__ import annotations
from pathlib import Path
import argparse
import os
import re
import secrets
import shutil
import socket
import subprocess

ROOT=next(parent for parent in Path(__file__).resolve().parents if (parent / "manage.py").is_file())


def main(argv=None):
    parser=argparse.ArgumentParser(description='펫노트 로컬 Docker 실행 · 운영 DB와 별도')
    parser.add_argument('action',choices=('up','stop','down','status','check','test'))
    parser.add_argument('--project')
    parser.add_argument('--port',type=int,default=8096)
    args=parser.parse_args(argv)
    if args.project is None:
        environments = ROOT / 'tmp/local-docker'
        legacy = environments / 'taily-local-review/runtime.env'
        current = environments / 'petnote-local-review/runtime.env'
        args.project = 'taily-local-review' if legacy.is_file() and not current.exists() else 'petnote-local-review'
    if not re.fullmatch(r'(?:petnote|taily)-local-[a-z0-9-]{1,32}',args.project):
        parser.error('다른 컨테이너를 건드리지 않도록 프로젝트 이름은 petnote-local-로 시작해야 해요.')
    if not 1024<=args.port<=64000:parser.error('1024~64000 범위의 로컬 포트를 사용해 주세요.')
    docker=shutil.which('docker')
    if not docker:parser.error('Docker Desktop을 설치·실행한 뒤 다시 실행해 주세요.')
    check=subprocess.run([docker,'info','--format','{{.OSType}}'],capture_output=True,text=True)
    if check.returncode or check.stdout.strip()!='linux':parser.error('Docker Desktop의 Linux 컨테이너 엔진에 연결할 수 없어요.')
    folder=ROOT/'tmp/local-docker'/args.project;envfile=folder/'runtime.env'
    if not envfile.exists():
        if args.action!='up':parser.error('먼저 up으로 이 프로젝트의 로컬 환경을 준비해 주세요.')
        for port in (args.port,args.port+1000):
            with socket.socket() as probe:
                if probe.connect_ex(('127.0.0.1',port))==0:parser.error(f'{port} 포트가 사용 중이에요. 다른 포트를 지정해 주세요.')
        folder.mkdir(parents=True,exist_ok=True)
        settings={'DJANGO_DEBUG':'1','DJANGO_SECRET_KEY':secrets.token_urlsafe(48),
            'DJANGO_ALLOWED_HOSTS':'localhost,127.0.0.1,testserver','APP_ORIGIN':f'http://127.0.0.1:{args.port}',
            'POSTGRES_DB':'petnote','POSTGRES_USER':'petnote','POSTGRES_PASSWORD':secrets.token_urlsafe(40),
            'SITE_ADDRESS':'http://:80','HTTP_BIND':'127.0.0.1','HTTP_PORT':str(args.port),
            'HTTPS_BIND':'127.0.0.1','HTTPS_PORT':str(args.port+1000),'GOOGLE_CLIENT_ID':'','GOOGLE_CLIENT_SECRET':''}
        with envfile.open('x',encoding='utf8',newline='\n') as out:out.write('\n'.join(k+'='+v for k,v in settings.items())+'\n')
        if os.name!='nt':envfile.chmod(0o600)
    settings=dict(line.split('=',1) for line in envfile.read_text(encoding='utf8').splitlines() if '=' in line and not line.startswith('#'))
    if settings.get('HTTP_BIND')!='127.0.0.1' or settings.get('DJANGO_DEBUG')!='1' or settings.get('HTTP_PORT')!=str(args.port):
        parser.error('기존 로컬 설정과 포트가 달라요. 이 환경을 덮어쓰지 않았어요.')
    if settings.get('GOOGLE_CLIENT_SECRET') or settings.get('GOOGLE_CLIENT_ID'):
        parser.error('이 분리된 검사 환경에는 외부 로그인 비밀값을 넣지 않아요.')
    command=[docker,'compose','--project-directory',str(ROOT),'-f',str(ROOT/'compose.production.yaml'),'--env-file',str(envfile),'-p',args.project]
    steps={'up':['up','-d','--build','--wait'],'stop':['stop'],'down':['down'],
           'status':['ps'],'check':['config','--quiet'],
           'test':['exec','-T','backend','python','manage.py','test','care.tests','--noinput','--verbosity','1']}
    result=subprocess.run(command+steps[args.action],cwd=ROOT)
    if result.returncode:return result.returncode
    if args.action=='up':print(f'LOCAL_DOCKER http://127.0.0.1:{args.port} · 운영/시연 DB와 별도 · Google 로그인 미연결')
    return 0


if __name__=='__main__':raise SystemExit(main())
