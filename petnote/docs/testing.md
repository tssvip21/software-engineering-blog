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

## 업로드 전 로컬 자동 검사

저장소 최상위의 `.githooks/pre-push`는 실제 전송 전에 현재 커밋의 전체 조상·현재 파일·비밀값·서버와 도구 테스트를 검사합니다. 검사할 커밋과 업로드할 커밋이 다르거나, 미커밋 변경·미추적 소스·축소된 Git 이력이 있으면 차단합니다. 검사 중 소스가 바뀌거나 도구가 없고 검사가 실패해도 전송하지 않습니다.

개발용 Python 환경과 공식 [Gitleaks 8.30.1](https://github.com/gitleaks/gitleaks/releases/tag/v8.30.1)을 준비한 뒤 설치합니다. 아래 도구 경로는 실제 설치 위치로 맞춥니다.

```powershell
$checkPython = (Resolve-Path .\petnote\.venv\Scripts\python.exe).Path
$secretScanner = (Resolve-Path C:\tools\gitleaks.exe).Path
& $checkPython .github/scripts/pre_push.py --install --python $checkPython --gitleaks $secretScanner
& $checkPython .github/scripts/pre_push.py --check
```

`--check`는 업로드 없이 같은 검사를 실행합니다. 설치 설정은 현재 worktree에만 적용되므로 다른 작업 폴더나 새 clone에서는 다시 설치합니다. 기존 hook은 덮어쓰지 않습니다. Linux에서도 가상 환경 Python과 Gitleaks의 절대 경로를 지정합니다.

검사기는 도구 버전을 확인하고 Windows 실행 파일은 검증된 배포 해시와도 비교합니다. Gitleaks는 저장소의 임의 예외 설정이나 허용 주석에 의존하지 않는 기본 규칙으로 실행하며, 탐지 원문을 출력하지 않습니다. 앱 검사의 세부 로그는 `petnote/tmp/pre-push/checks/`에 남습니다.

서버·도구·구문 검사는 로컬 전송 전에 실행하고, 브라우저와 Docker 전체 검사는 CI에서도 실행합니다. 현재 HEAD와 다른 브랜치 커밋·annotated tag를 함께 보내는 동작은 차단하므로 검사할 커밋을 먼저 checkout합니다. 이 hook은 설치된 작업 폴더의 일반 `git push` 경로를 보호하며 GitHub 서버의 병합 규칙을 대신하지 않습니다.

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
| 코드·브라우저 | 선택한 HEAD의 전체 조상 및 현재 소스의 공개 적합성, 가져온 Git 이력의 Gitleaks 검사, 검사기 회귀 테스트, 고정된 의존성의 알려진 취약점, 전체 SQLite·도구·JavaScript·브라우저 검사 |
| Docker·PostgreSQL | Compose 구성, 셸 문법, 이미지 빌드·상태 검사, 전체 PostgreSQL 테스트, HTTP·재시작·백업 복원 |

워크플로 권한은 소스 읽기로 제한하고 사용 중인 Actions는 커밋 해시로 고정합니다. 이전 실행 취소, 작업 시간 제한, 결과 보관과 임시 환경 종료를 포함합니다. 서비스 배포나 저장소 push를 수행하는 단계는 없습니다.

소스 공개 검사기는 비밀 설정·DB·개인 식별 정보와 공개하지 않을 표현을 검사하며, 실패 로그에는 분류와 파일 위치만 표시합니다. 저장소 최상위의 `python .github/scripts/check_privacy.py --working-tree`는 현재 파일, `--history <40자리 커밋 SHA>`는 그 커밋의 전체 조상, `--all`은 모든 로컬 refs를 검사합니다. 전체 이력 모드는 shallow clone을 거절합니다. 공개 저장소의 과거 PR refs·이미지·문서 내부 개인정보 점검은 현재 checkout의 자동 검사와 별도로 범위를 확인합니다.

CI의 Gitleaks 8.30.1 다운로드는 SHA-256으로 검증합니다. 탐지 값을 가리고 기본 규칙, 인코딩·압축 내부 탐색, 허용 주석 무시를 명시하며 저장소의 예외 파일이 검사를 축소하지 않게 합니다.

GitHub Actions 서비스에서의 실행 결과와 PC에서 같은 검사를 실행한 결과는 구분합니다. 로컬 커밋만 만든 상태에서는 GitHub에서 새 CI 실행이 발생하지 않습니다.
