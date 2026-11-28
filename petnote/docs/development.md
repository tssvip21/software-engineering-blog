# 개발·실행·백업

모든 명령은 저장소의 `petnote` 폴더에서 실행합니다. 블로그 사이트는 저장소 최상위에서 별도로 관리합니다.

## Docker로 로컬 시연

Docker Desktop의 Linux 컨테이너 엔진을 켠 뒤 실행합니다.

```sh
docker compose up --build -d --wait
docker compose ps
```

접속 주소는 `http://127.0.0.1:8096/`입니다. 관리자로 들어가 준비된 강아지·고양이 공간을 확인하거나 빈 계정에서 새 공간을 만들 수 있습니다. Caddy, Django, PostgreSQL의 세 서비스가 실행되며 DB 포트는 외부에 공개하지 않습니다. 비밀번호는 최초 실행 때 별도 Docker 볼륨에 생성됩니다.

종료할 때는 `docker compose down`을 사용합니다. 기본 종료 명령은 DB 볼륨을 보존합니다. `down --volumes`는 해당 환경의 기록을 삭제하므로 일상적인 종료 명령으로 사용하지 않습니다.

다른 포트와 별도 환경 이름을 사용하려면 PowerShell에서 다음처럼 지정합니다.

```powershell
$env:PETNOTE_PORT = '18096'
docker compose -p petnote-local-review up --build -d --wait
docker compose -p petnote-local-review ps
```

같은 환경을 종료할 때도 같은 `-p` 이름을 사용합니다. 기본 Compose는 localhost에만 연결되며, 운영 HTTPS 구성은 `compose.production.yaml`로 분리되어 있습니다. 운영 예시는 `deploy/production.env.example`에 있고 실제 비밀값을 담은 파일은 커밋하지 않습니다.

운영 Compose 형식 자체를 별도 로컬 환경에서 검사하려면 다음 실행기를 사용합니다. 이 환경은 위 시연 프로젝트와 다른 이름·포트를 사용하며 외부 로그인에 연결하지 않습니다.

```powershell
.\.venv\Scripts\python.exe scripts/local_compose.py up --project petnote-local-production-check --port 18098
.\.venv\Scripts\python.exe scripts/local_compose.py check --project petnote-local-production-check --port 18098
```

실행기는 `tmp/local-docker/<프로젝트>/runtime.env`의 기존 값을 보존합니다. 셸에 남은 운영 DB·OAuth·공개 바인딩·Compose 설정은 전달하지 않고, 검증한 로컬 설정으로 실행합니다. HTTP·HTTPS 바인딩, 포트, DB 이름, 허용 호스트 등이 로컬 조건과 다르면 컨테이너를 변경하기 전에 중단합니다. 자동 생성된 비밀값도 그대로 재사용하며 인용부호·변수 치환이 필요한 수동 변경값을 임의로 해석하지 않습니다.

Docker의 실제 연결 대상을 확인해 로컬 Unix socket·Windows named pipe에서만 실행합니다. 원격 TCP·SSH context에서는 중단합니다. 환경 파일이 없는데 같은 프로젝트의 컨테이너나 볼륨이 남아 있으면 비밀번호를 새로 만들지 않습니다. 원래 `runtime.env`를 복구하거나 별도 프로젝트 이름을 선택해야 합니다. `stop`·`down`도 같은 프로젝트와 포트를 지정하며, 볼륨을 삭제하는 옵션은 제공하지 않습니다.

## Docker 운영 구성과 공개 시연

현재 공개 펫노트는 Vercel과 Supabase에서 실행하며 Docker 컨테이너를 Vercel에 올리는 방식은 아닙니다. `compose.production.yaml`은 별도 서버에 설치할 때 사용하는 HTTPS·PostgreSQL 구성입니다.

운영 환경 파일은 `deploy/production.env.example`을 복사해 저장소 밖이나 Git 제외 위치에 보관합니다. 공개 도메인·HTTPS 원본·허용 호스트를 같은 주소로 맞추고, Django 비밀키와 DB 비밀번호는 각각 새 무작위 값으로 설정합니다. 기존 서버를 갱신할 때는 원래 Compose 프로젝트명과 DB 볼륨·비밀값을 그대로 사용합니다.

```sh
docker compose -f compose.production.yaml --env-file /secure/path/petnote.env config --quiet
docker compose -f compose.production.yaml --env-file /secure/path/petnote.env up --build -d --wait
```

외부에 여는 포트는 Caddy의 80·443뿐입니다. DB는 TCP 접속 준비가 끝난 뒤 backend를 시작하며 backend의 상태 검사에 통과해야 frontend가 시작됩니다. 단일 backend 구성으로, 시작 시 마이그레이션·기준 자료 등록·정적 파일 수집을 수행합니다. 업데이트 전 백업 방법과 기존 서버의 복구 기록은 [배포와 운영](21_배포와운영.md)을 따릅니다.

공개 시연은 선택 사항입니다. 사용할 때만 `compose.demo.yaml`을 함께 지정하고, 별도 시연 비밀키 `DEMO_DJANGO_SECRET_KEY`와 실제 개인정보가 없는 합성 SQLite 자료 `PUBLIC_DEMO_SEED_PATH`를 준비합니다.

```sh
docker compose -f compose.production.yaml -f compose.demo.yaml --env-file /secure/path/petnote.env up --build -d --wait
```

이 추가 구성은 시연 진입 표시와 `/try/` 프록시를 함께 켜고, 시연 컨테이너가 준비될 때까지 frontend 시작을 기다립니다. 추가 구성을 사용하지 않으면 `/try/`는 404로 응답합니다. `CARE_PUBLIC_DEMO_ENTRY`만 수동으로 켜지 않습니다. 시연은 별도 SQLite 볼륨과 쿠키 경로를 사용하며 운영 PostgreSQL에 연결하지 않습니다. 운영 DB나 실제 사용자 백업을 시연 자료로 지정하지 않습니다.

## Python 개발 환경

Python 3.13과 Node.js가 필요합니다. Node.js는 브라우저 코드의 문법 검사에 사용합니다. PowerShell에서:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m playwright install chromium firefox
.\.venv\Scripts\python.exe scripts/run_local.py --demo
```

macOS·Linux에서는 `python3.13 -m venv .venv`와 `.venv/bin/python`을 사용합니다. Linux 브라우저 설치에는 `python -m playwright install --with-deps chromium firefox`가 필요할 수 있습니다.

기본 시연 주소는 `http://127.0.0.1:8001/`입니다. `START_DEMO.cmd`도 같은 시연 실행기를 사용합니다. 시연 DB는 `tmp/local-demo/demo.sqlite3`에 저장됩니다.

별도 시연을 여러 개 띄울 때는 이름과 포트를 지정합니다.

```powershell
.\.venv\Scripts\python.exe scripts/run_local.py --demo --profile review --port 8036
.\.venv\Scripts\python.exe scripts/run_local.py --demo --profile mobile --port 8037
```

각 시연은 `tmp/previews/<이름>/`의 DB·정적 파일과 별도 로그인 쿠키를 사용합니다. 이름에는 영문·숫자·밑줄·하이픈을 사용할 수 있습니다. 기존 데이터나 다른 서버를 복사·중지하지 않습니다. 사용 중인 포트는 시작 전에 거부합니다.

`--demo` 실행에서는 `.env`나 상위 프로세스의 운영 DB·공개 시연·OAuth 설정을 사용하지 않습니다. 기본 SQLite 실사용 모드는 `scripts/run_local.py` 또는 `START_LOCAL.cmd`이며, 기본 데이터 위치는 `data/care.sqlite3`입니다. PostgreSQL 실행은 Compose를 사용합니다.

## SQLite 백업과 복원

실행 중인 SQLite도 온라인 백업으로 일관된 복사본을 만들 수 있습니다. DB 파일과 같은 이름의 JSON 검증 파일이 `backups/`에 함께 저장됩니다.

```powershell
.\.venv\Scripts\python.exe scripts/backup.py
.\.venv\Scripts\python.exe scripts/backup.py --demo
.\.venv\Scripts\python.exe scripts/backup.py --demo --profile review
```

첫 명령은 실사용 SQLite, 두 번째는 기본 시연, 세 번째는 이름을 지정한 시연을 백업합니다. Docker PostgreSQL에는 이 도구를 사용하지 않습니다.

복원할 때는 대상 서버를 먼저 종료하고 DB와 JSON 파일을 함께 보관한 상태에서 실행합니다.

```powershell
.\.venv\Scripts\python.exe scripts/backup.py --demo --profile review --restore backups/<백업파일>.sqlite3 --confirm-stopped
```

복원 전에 파일 종류·SHA-256·SQLite 무결성을 확인합니다. 현재 DB가 있으면 복원 직전의 안전 백업과 검증 JSON도 만들고, 검증된 임시 복사본으로 교체합니다. 잘못된 백업은 현재 DB를 바꾸지 않습니다.

PostgreSQL의 백업·복원 참고 도구는 `deploy/backup-postgres.sh`, `deploy/verify-backup.sh`에 있습니다. 자동 CI의 복원 검사는 실제 운영 DB와 분리된 검사 DB만 사용합니다.

## 정리와 업데이트

의존성은 `requirements.lock`을 기준으로 설치하고 개발 도구는 `requirements-dev.txt`에서 고정합니다. 검사 결과는 `tmp/`, 캐시와 수집된 정적 파일은 제외 규칙에 따라 관리합니다. 기존 데이터가 있는 `data/`·`backups/`나 Docker 볼륨을 코드 정리 대상으로 삭제하지 않습니다.
