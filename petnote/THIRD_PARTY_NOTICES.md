# 사용 도구 및 출처

## Unslop — UI 검토와 정돈

- 도구: **Unslop**
- 제작자: **Damir Sokolovsky (@heydamir)**
- 원본 저장소: https://github.com/heydamir/unslop
- 확인·사용한 커밋: `a6f121eee5f29c2f74976b75456feae8b66afb3a`
- 라이선스: **Creative Commons Attribution 4.0 International (CC BY 4.0)**
- 저장소 라이선스: https://github.com/heydamir/unslop/blob/a6f121eee5f29c2f74976b75456feae8b66afb3a/LICENSE
- 라이선스 전문: https://creativecommons.org/licenses/by/4.0/

실제 스킬과 참고 규칙을 읽고 구현된 화면의 정보 우선순위, 반복되는 모듈 카드, 간격·색상·아이콘·입력 폼을 검토하는 데 활용했다. 이를 바탕으로 빠른 기록 영역의 노출량을 줄이고, 기록 목록의 수치·단위를 명확히 표시하며, 변경 이력을 항목별로 읽을 수 있게 정돈했다. 원본 스킬은 수정하지 않았고, 프로젝트의 화면 코드는 별도로 구현했다. 제작자의 보증이나 공식 제휴를 뜻하지 않는다.

### 2차 개선 적용 — 2026-11-04

정보 구조를 먼저 정한 다음 Unslop의 실제 스킬과 Signs/Tokens 기준으로 다시 검토했다. 일상 기록장과 공동 체크리스트라는 제품 성격을 유지하면서 중립색 바탕과 절제된 초록색 강조, 한 계열의 선형 아이콘, 읽기 폭과 간격을 정리했다. 오늘의 동작·루틴·이력·통합 요약을 각 목적에 맞게 배치하고, 통합 요약은 네 가지를 먼저 보여주되 나머지 근거와 상세 항목은 펼쳐 확인하도록 했다. 좁은 화면에서는 메뉴와 입력 팝업, 정렬 조작과 버튼 간격을 조정했다.

앞선 제작자·출처·커밋·라이선스 표기를 그대로 적용한다. 원본 스킬을 배포 코드에 복제하지 않았고, 화면 코드와 입력·정렬 로직은 프로젝트에서 구현했다.

### 3차 개선 적용 — 2026-11-05

Unslop의 방향·진단·간격·글자·대비·아이콘·동작 원칙을 다시 적용했다. 함께 쓰는 생활 기록장이라는 방향을 유지하고, 분류별 모듈 묶음의 빈 공간, 반복되는 관계 카드와 동일한 추세 그림, 작은 역할 문자의 가독성을 점검했다. 모듈을 열 단위로 흐르게 하고, 관계는 간결한 이유 목록으로, 현재 근거에서 먼저 볼 추세 하나는 크게, 나머지 추세는 계산 근거 안으로 정돈했다. 드래그 손잡이는 처음부터 보이게 하고 44px 수준의 조작 영역과 포커스를 유지했다. 원본 스킬을 수정·재배포하지 않았으며 앞의 제작자·커밋·라이선스 표기를 그대로 적용한다.

## 분석 방법과 품종 자료

중앙값·MAD와 이상 관측의 설명은 NIST/SEMATECH, Theil–Sen 방법은 SciPy 공식 설명과 원 논문 안내를 참고했다. 공개 문서의 수식과 서비스가 선택한 관측일·변화 문턱값은 구분한다. 품종 이름과 확인한 체중 범위는 AKC, CFA, TICA 자료에서 확인했으며 탐색용 체격 구간은 보조 자료의 적용 범위를 검토해 별도 설계값으로 표시했다. 수치·링크·조건과 방법의 한계는 [종합 상태 분석 방법](docs/12_종합상태_분석방법.md)에 정리했다. 해당 단체가 서비스를 보증하거나 검증했다는 뜻은 아니다. 외부 사진·품종 설명 전문을 복제하지 않았다.

## 런타임 패키지

Python, Django, google-auth, requests, python-dotenv, WhiteNoise, Waitress, python-dateutil, tzdata 및 관련 의존성을 사용한다. 실제 검증 버전은 `requirements.lock`에서 확인한다. 각 패키지의 라이선스와 원저작권은 해당 배포물의 메타데이터 및 라이선스 파일을 따른다. 개발 검사에는 Playwright, Ruff, pip-audit를 사용한다.

## 참고 기준 데이터

WSAVA Global Nutrition Committee의 Body Condition Score 자료에서 적용 대상과 조건을 확인한 항목을 참고 기준으로 연결한다. 원본 차트 이미지나 전문을 사이트에 재배포하지 않으며, 관련 통계 화면에 원본 자료 링크와 적용 조건을 표시한다.

- 개 BCS: https://wsava.org/wp-content/uploads/2025/06/WSAVA_BCSCat_BCSDog_Nutrition_250612.pdf
- 고양이 BCS: https://wsava.org/wp-content/uploads/2025/06/WSAVA_BCSCat_BCSCat_Nutrition_250612.pdf

## 자체 제작 요소

화면에 사용하는 단순 선형 SVG 아이콘과 애플리케이션 HTML·CSS·JavaScript는 이 프로젝트에서 작성했다. 외부 폰트 파일이나 유료 이미지·동영상은 포함하지 않았다.


### 4차 개선 적용 — 2026-11-05

앞서 명시한 Unslop 스킬과 Signs 기준을 실제 화면 정돈에 다시 사용했다. 사용자의 기능 요구와 결과 내용을 우선하고, 같은 곳으로 가는 중복 링크·반복 안내를 줄였다. 한글 어절 줄바꿈, 글자 위계·대비·선택 상태·조작 간격을 맞추고, 결과를 이해하는 데 꼭 필요하지 않은 반복 그림은 덜어냈다. 기존 브랜드의 절제된 강조색과 한글 시스템 글꼴은 유지했다. 원본 스킬은 수정하지 않았으며 기존 제작자·저장소·커밋·CC BY 4.0 표기를 그대로 적용한다.

4차에서 사용한 개인 기록 연구·설계 평가 지침·통계적 근거와 한국어 조판·접근성 기준은 [4차 설계 근거](docs/history/15_테일리_4차설계근거.md)에 출처와 적용 한계를 구분해 적었다. 외부 폰트·유료 에셋을 새로 포함하지 않았다.


### 5차 개선 적용 — 2026-11-09

앞서 표기한 Damir Sokolovsky (@heydamir)의 Unslop 스킬과 같은 커밋·CC BY 4.0 출처를 유지하며, 다색 역할과 정보 위계·반복 카드·입력 동선을 검토했다. 사용자 요구에 맞춰 단색 제한을 기계적으로 적용하지 않고, 중립 바탕과 분류별 색·이름·아이콘을 함께 사용했다. 불필요한 그림·가짜 수치·동급 설명의 반복을 줄이고 PC/모바일을 각각 확인했다. 원본 스킬·외부 앱의 코드·이미지·폰트 파일을 제품에 복제하지 않았다.

정보 시각화와 접근성의 근거·적용 한계는 [5차 개선 문서](docs/17_테일리_5차개선.md)에 정리했다. Shneiderman(1996), Heer & Bostock(2010) 및 W3C WCAG 2.2 자료는 설계·검증 참고이며 제작자의 보증이나 실제 사용자 연구 결과를 뜻하지 않는다.


### 5차 두 번째 UI 재설계 적용 — 2026-11-09

앞서 명시한 Unslop 스킬의 제품 방향·간격·색의 면적·서체 위계·상태 피드백 원칙을 카드 보드와 편집 화면에 적용했다. 사용자가 요청한 전체 UI 재설계를 우선하여 기존 행 목록의 형태는 보존하지 않았으며, 넓은 분류 색 면을 줄이고 데이터·아이콘과 실제 동작에 강조를 집중했다. 원본 스킬·이미지·코드를 새로 복제하지 않았고 기존 제작자·커밋·CC BY 4.0 표기를 유지한다.

## 자체 제공 웹 서체

- **Pretendard Variable v1.3.9** — Copyright (c) 2021, Kil Hyung-jin. Reserved Font Name: Pretendard. SIL Open Font License 1.1. https://github.com/orioncactus/pretendard/tree/v1.3.9
- **Manrope (Google Fonts OFL 배포본, Latin 웹폰트)** — Copyright 2018 The Manrope Project Authors. SIL Open Font License 1.1. https://github.com/google/fonts/tree/main/ofl/manrope

Pretendard의 공식 동적 서브셋 92개와 Manrope는 수정하지 않고 같은 서버에서 제공한다. 공식 동적 서브셋에서 빠지는 기존 글자는 원본 v1.3.9에서 추출한 **Petnote Supplemental**로 보완한다. 이 수정 배포본은 SIL OFL 1.1을 따르며 내부 글꼴·인스턴스 이름을 바꾸고 원저작권·상표·제작자·라이선스 표기를 유지했다. 각 저작권·OFL 원문, 파일별 출처·해시·재생성 방법은 [서체 출처](static/care/fonts/README.md), `Pretendard-OFL.txt`, `Manrope-OFL.txt`에서 확인할 수 있다. 본문은 Pretendard와 보완 서체, 숫자 강조는 Manrope, 로드 실패 시 시스템 산세리프를 사용한다.
