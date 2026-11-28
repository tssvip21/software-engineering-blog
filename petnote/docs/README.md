# 펫노트 문서 안내

현재 실행과 검증 방법은 아래 문서부터 확인합니다.

| 문서 | 내용 |
|---|---|
| [개발·실행·백업](development.md) | Docker·Python 실행, 시연 데이터 분리, 설치, 백업과 복원 |
| [자동 검사와 CI](testing.md) | 서버·브라우저·도구 검사, Docker 통합 검증, 결과 확인 |
| [모바일 탐색](navigation.md) | 메뉴 구분, 플로팅 하단바, 전체 메뉴와 접근성 |
| [프로젝트 딕셔너리](27_프로젝트_딕셔너리.md) | 화면·요청·권한·DB·코드의 연결 |
| [배포와 운영](21_배포와운영.md) | 기존 배포 과정, 운영 설정과 복구 참고 |
| [정리·보완 결과](maintenance.md) | 이번 유지보수의 변경 이유와 실제 검증 범위 |

## 폴더 구조

| 위치 | 역할 |
|---|---|
| `care/views/` | 주소에서 호출하는 요청 처리와 응답 |
| `care/services/` | 권한 확인, 공간·구성원·기록·물품·루틴·초대의 업무 처리 |
| `care/inventory/`, `care/reporting/`, `care/presentation/` | 물품·재고, 통계 계산, 화면 데이터 구성 |
| `care/models.py`, `care/migrations/`, `care/tests/` | DB 모델·변경 이력·서버 테스트 |
| `config/` | Django 설정·주소·로그 필터 |
| `templates/`, `static/` | 화면·스타일·브라우저 동작·사용 중인 이미지와 글꼴 |
| `deploy/docker/`, `compose*.yaml`, `Dockerfile` | Caddy·컨테이너 실행·상태 검사·서버 초기 설정 |
| `deploy/vercel/` | Vercel 소스 패키징·빌드 검사·단계 배포·공개 전환 |
| `deploy/backup/` | PostgreSQL 백업·복원 검사·정기 실행 설정 |
| `deploy/source_files.py` | 배포 방식이 함께 쓰는 애플리케이션 파일 선택 |
| `scripts/` | 설치, 로컬 실행, 통합 검사, 백업 등 현재 사용하는 명령 |
| `scripts/qa/` | 현재 브라우저 회귀 검사와 성능 측정 |
| `scripts/qa/legacy/` | 이전 개발 단계의 개별 화면·시각 검사 참고 코드 |
| `scripts/tests/` | 로컬 실행기·백업 도구의 안전 동작 검사 |
| `scripts/dev/` | 시연 자료 관리 보조 도구 |
| `docs/history/` | 초기 기획과 과거 설계·실행 기록 |
| `blog/` | 기존 블로그 초안·연결 문서·이미지 |
| `data/`, `backups/`, `tmp/`, `.venv/` | 로컬 데이터·백업·검사 결과·설치 환경. 커밋 대상에서 제외 |

## 과거 기록

[초기 기획](history/01_기획초안.md), [서비스 기획안](history/06_서비스기획안.md), [화면 구조](history/09_화면구조와사용흐름.md) 등 이전 기록은 `history/`에 모았습니다. 과거 문서의 날짜·테스트 수·배포 주소는 그 당시의 결과입니다.

기존 블로그가 직접 연결하는 개발 기록은 같은 경로를 유지합니다. [실사용 점검 기록](28_실사용_점검.md)도 이전 측정의 조건과 결과를 보존하며, 현재 재실행 방법은 [자동 검사와 CI](testing.md)를 따릅니다. 단계별 중복 실행기와 완료된 작업 지시서는 Git 이력에서 확인할 수 있습니다.
