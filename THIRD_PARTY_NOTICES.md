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

## 런타임 패키지

Python, Django, google-auth, requests, python-dotenv, WhiteNoise, Waitress, python-dateutil, tzdata 및 관련 의존성을 사용한다. 실제 검증 버전은 `requirements.lock`에서 확인한다. 각 패키지의 라이선스와 원저작권은 해당 배포물의 메타데이터 및 라이선스 파일을 따른다. 개발 검사에는 Playwright, Ruff, pip-audit를 사용한다.

## 참고 기준 데이터

WSAVA Global Nutrition Committee의 Body Condition Score 자료에서 적용 대상과 조건을 확인한 항목을 참고 기준으로 연결한다. 원본 차트 이미지나 전문을 사이트에 재배포하지 않으며, 관련 통계 화면에 원본 자료 링크와 적용 조건을 표시한다.

- 개 BCS: https://wsava.org/wp-content/uploads/2025/06/WSAVA_BCSCat_BCSDog_Nutrition_250612.pdf
- 고양이 BCS: https://wsava.org/wp-content/uploads/2025/06/WSAVA_BCSCat_BCSCat_Nutrition_250612.pdf

## 자체 제작 요소

화면에 사용하는 단순 선형 SVG 아이콘과 애플리케이션 HTML·CSS·JavaScript는 이 프로젝트에서 작성했다. 외부 폰트 파일이나 유료 이미지·동영상은 포함하지 않았다.
