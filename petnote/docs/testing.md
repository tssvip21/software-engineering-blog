# 자동 검사와 CI

현재 자동 검사는 실제 기록과 분리된 SQLite 또는 별도 Docker PostgreSQL에서 실행합니다. 기존 개발 단계의 검사 도구는 `scripts/qa/legacy/`에 보관하며, 전체 재검사의 시작점은 `scripts/check.py`입니다.

## 로컬 전체 검사

[개발 환경](development.md)을 설치한 뒤 `petnote` 폴더에서 실행합니다.

```powershell
.\.venv\Scripts\python.exe scripts/check.py
.\.venv\Scripts\python.exe scripts/check.py --browser --browsers chromium firefox
```

첫 명령은 의존성 충돌, Python 구문·이름 오류, Django 설정, 마이그레이션 누락, 서버 테스트, 실행·백업 도구 테스트, 모든 JavaScript 파일의 구문을 확인합니다. 두 번째 명령은 Chromium과 Firefox의 실제 화면 동작도 검사합니다.

요약은 `tmp/checks/results.json`, 개별 로그는 같은 폴더에 저장합니다. 브라우저 검사 결과는 `browser-results.json`입니다. 실패·실행 오류·시간 초과는 명령의 실패 종료 코드로 전달됩니다. `--output tmp/<검사이름>`으로 다른 결과 위치를 지정할 수 있습니다.

## 브라우저만 검사

```powershell
.\.venv\Scripts\python.exe scripts/qa/verify_maintenance.py --browsers chromium firefox
```

임시 SQLite DB와 비어 있는 로컬 포트에서 검사 서버를 만들고 끝나면 서버와 DB를 정리합니다. 로그·결과는 `tmp/qa/`에 남습니다. 검사 항목은 다음 범위를 포함합니다.

| 범위 | 실제 확인 동작 |
|---|---|
| 기록 | 18개 기능의 입력, 수정·취소·검색·기간 필터, HTML 메모의 문자 표시 |
| 공동 돌봄 | 초대·참여, 다른 공간 접근 거부, 공개 범위, 세션·CSRF |
| 일정·물품·통계 | 루틴 등록과 완료, 물품·재고, 통계 페이지와 날짜 오류 |
| 실패와 복구 | 연결 실패, 저장 후 응답 유실, 재시도해도 한 건 유지, 빠른 연속 클릭 |
| 화면 상태 | 늦은 구성원 응답 무시, 부분 갱신 후 시간 표시 계속 동작 |
| 탐색 구조 | 기본·영역별 하단바, PC 기존 메뉴 보존, 직접 주소·새로고침·뒤로 가기, 스크립트 없는 전체 메뉴 |
| 접근성·화면 크기 | 키보드 초점·Escape·모바일 메뉴, 320·390·768·1440px의 주요 화면 |
| 브라우저 기능 | JavaScript·저장소 제한 시 기본 사용, 위치 허용 후 GPS·중단·직접 입력 |

실제 Google 계정 OAuth, 실물 모바일 기기, 사람을 대상으로 한 사용성 평가는 이 자동 검사와 별개입니다.

## Docker 통합 검사

서로 다른 기록이 섞이지 않도록 전용 프로젝트 이름과 포트를 사용합니다.

```powershell
$env:PETNOTE_PORT = '18096'
docker compose -p petnote-maintenance-qa up --build -d --wait
.\.venv\Scripts\python.exe scripts/qa/verify_petnote_browser.py --base-url http://127.0.0.1:18096 --project petnote-maintenance-qa --browsers chromium firefox --output tmp/qa/compose-browser-results.json
```

브라우저 도구는 프로젝트 라벨, localhost 바인딩, 시연용 PostgreSQL DB를 확인한 뒤 가상 계정으로 실행합니다. 운영 주소나 임의의 기존 컨테이너를 대상으로 사용할 수 없습니다.

저장소 최상위에서는 구성과 실행 계약을 확인할 수 있습니다.

```powershell
petnote\.venv\Scripts\python.exe .github/scripts/check_compose.py
petnote\.venv\Scripts\python.exe .github/scripts/check_compose_runtime.py --project petnote-maintenance-qa --port 18096
```

구성 검사는 로컬·운영·선택형 공개 시연의 Compose 파일과 누락된 필수 설정을 검사합니다. 실행 검사는 HTTP·정적 파일·보안 헤더, 컨테이너 재시작 후 기록 유지, 별도 임시 DB로의 PostgreSQL 덤프 복원을 확인합니다. 이때 생성한 검사용 기록과 임시 복원 DB만 검사 대상으로 사용합니다.

## CI 구성

[워크플로](../../.github/workflows/petnote-ci.yml)는 애플리케이션·CI 변경에 대해 다음 두 작업을 실행합니다.

| 작업 | 검사 |
|---|---|
| 코드·브라우저 | 새 커밋 및 현재 소스의 공개 적합성, 검사기 자체 회귀 테스트, 고정된 의존성의 알려진 취약점, 전체 SQLite·도구·JavaScript·브라우저 검사 |
| Docker·PostgreSQL | Compose 구성, 셸 문법, 이미지 빌드·상태 검사, 전체 PostgreSQL 테스트, HTTP·재시작·백업 복원 |

워크플로 권한은 소스 읽기로 제한하고 사용 중인 Actions는 커밋 해시로 고정합니다. 이전 실행 취소, 작업 시간 제한, 결과 보관과 임시 환경 종료를 포함합니다. 서비스 배포나 저장소 push를 수행하는 단계는 없습니다.

소스 공개 검사기는 비밀 설정·DB·개인 식별 정보와 공개하지 않을 표현을 검사하며, 실패 로그에는 분류와 파일 위치만 표시합니다. 로컬에서 저장소 최상위의 `python .github/scripts/check_privacy.py --working-tree`로 같은 검사를 실행할 수 있습니다.

GitHub Actions 서비스에서의 실행 결과와 PC에서 같은 검사를 실행한 결과는 구분합니다. 로컬 커밋만 만든 상태에서는 GitHub에서 새 CI 실행이 발생하지 않습니다.
