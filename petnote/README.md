# 펫노트 (PetNote)

가족이 한 반려동물의 돌봄 기록과 일정을 공유하는 웹 서비스입니다. 급여·산책·체중 등 18개 기능의 기록, 루틴, 기간별 통계와 물품 관리를 제공합니다. 공간별 역할과 민감 기록의 공개 범위를 구분합니다.

## Docker로 시작하기

Docker Desktop의 Linux 컨테이너 엔진을 켜고 저장소에서 실행합니다.

```sh
cd petnote
docker compose up --build -d --wait
```

[펫노트 열기](http://127.0.0.1:8096/)에서 **관리자로 보기**로 강아지·고양이 시연 공간을 확인하거나, **빈 계정으로 시작**을 선택해 새 공간을 만듭니다. Caddy·Django·PostgreSQL이 실행되며 기본 구성은 해당 PC의 localhost에만 연결됩니다.

`docker compose ps`로 상태를 확인하고, `docker compose down`으로 종료합니다. 이 종료 명령은 저장된 기록의 Docker 볼륨을 보존합니다.

## 공개 서버 관리

기존 Windows PC에서는 바탕화면의 **펫노트 서버 관리**로 공개 서버를 켜고 끄거나 상태를 확인합니다. 저장된 Vercel 로그인을 재사용하며 재배포하지 않습니다. 바로가기 설치와 인증 복구 방법은 [배포와 운영](docs/21_배포와운영.md#바탕화면에서-서버-켜기끄기)을 참고합니다.

## 개발과 자동 검사

Python 3.13 개발 환경과 브라우저 설치 방법은 [개발·실행·백업](docs/development.md)에 있습니다.

```powershell
.\.venv\Scripts\python.exe scripts/run_local.py --demo
.\.venv\Scripts\python.exe scripts/check.py --browser --browsers chromium firefox
```

첫 명령은 별도 SQLite 시연을 실행합니다. 두 번째 명령은 서버·실행 도구·코드 구문·두 브라우저를 검사하고 `tmp/checks/`에 결과를 남깁니다. 이름을 지정한 독립 시연은 `scripts/run_local.py --demo --profile review --port 8036`으로 실행합니다.

## 구조와 문서

| 위치 | 내용 |
|---|---|
| `care/`, `config/` | 기록·권한·통계·루틴과 서버 설정·테스트 |
| `templates/`, `static/` | 화면과 브라우저 동작 |
| `deploy/`, `compose*.yaml` | Docker·운영 구성·백업·상태 검사 |
| `scripts/`, `scripts/qa/`, `scripts/tests/` | 실행·백업·자동 검사와 도구 회귀 테스트 |
| `docs/`, `docs/history/` | 현재 사용 안내와 과거 설계 기록 |
| `blog/` | 기존 개발 과정 초안과 블로그 연결 자료 |

[문서 안내](docs/README.md) · [기능과 코드 구조](docs/27_프로젝트_딕셔너리.md) · [자동 검사와 CI](docs/testing.md) · [정리·보완 결과](docs/maintenance.md) · [워크플로](../.github/workflows/petnote-ci.yml)

실제 사용자 대상 사용성 평가, 실물 모바일 기기와 실계정 Google OAuth 검증은 자동 검사 결과와 구분합니다. 기존 배포·성능 측정의 시점별 기록은 [실사용 점검](docs/28_실사용_점검.md)에 보존합니다.
