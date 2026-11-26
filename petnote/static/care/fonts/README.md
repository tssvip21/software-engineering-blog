# 웹 서체 출처

- Pretendard Variable v1.3.9 — 길형진 (orioncactus). 본문·제목·조작부에 사용.
  https://github.com/orioncactus/pretendard/tree/v1.3.9
  공식 동적 서브셋 92개: `packages/pretendard/dist/web/variable/woff2-dynamic-subset/PretendardVariable.subset.0.woff2`부터 `PretendardVariable.subset.91.woff2`까지. 이 바이너리는 수정하지 않았다.
  CSS 원본: https://github.com/orioncactus/pretendard/blob/v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.css
  라이선스: Pretendard-OFL.txt (SIL Open Font License 1.1). Reserved Font Name: Pretendard.
- Petnote Supplemental — 위 버전에서 공식 동적 서브셋에 없는 글자 2,307개를 포함한 수정 배포본. 원본의 가변 굵기 축과 글자 윤곽을 유지하고 내부 글꼴·인스턴스 이름을 변경했다. 원저작권·제작자·상표·OFL 표기는 유지한다.
  원본: https://raw.githubusercontent.com/orioncactus/pretendard/v1.3.9/packages/pretendard/dist/web/variable/woff2/PretendardVariable.woff2
  원본 SHA256: `9599f12fd42fc0bce1cd50b47a0c022e108d7aa64dd0d1bb0ed44f3282d900b4`
  수정 배포본 SHA256: `44f2f3d5f69e99702b5d9a13e73f961ba2af9e25946bfa810d36cbf8e897ee97`
  라이선스: Pretendard-OFL.txt (SIL Open Font License 1.1).
- Manrope — Google Fonts에서 제공하는 OFL 배포본의 Latin 웹폰트. 큰 수치·기간·건수 등 숫자에 제한해 사용.
  https://github.com/google/fonts/tree/main/ofl/manrope
  https://fonts.gstatic.com/s/manrope/v23/xn7gYHE41ni1AdIRggexSg.woff2
  라이선스: Manrope-OFL.txt (SIL Open Font License 1.1).

2026-11-26 Pretendard의 단일 2,057,688바이트 다운로드를 화면의 글자에 해당하는 Unicode 서브셋 요청으로 바꿨다. `pretendard.css`는 공식 동적 서브셋의 범위를 유지하며 로컬 경로와 기존 CSS 별칭 `Pretendard`, 굵기 100–900을 사용한다. 공식 서브셋의 12,029개 코드 포인트와 보완 서체의 2,307개를 합쳐 기존 원본의 14,336개를 모두 유지한다. 보완 서체는 `Petnote Supplemental`이라는 별도 CSS·내부 이름으로 제공하고 해당 글자가 있을 때만 로드한다.

모든 폰트는 같은 출처에서 제공하며 외부 폰트 서버에 요청하지 않는다. `font-display: swap`과 시스템 글꼴 폴백을 사용한다. Manrope는 기존 공식 배포본을 유지했다. 원저작권과 OFL 전문은 위 라이선스 파일을 따른다.

## 보완 서체 재생성

런타임 의존성은 추가하지 않는다. 별도 빌드 환경에서 Python 3.13, `fonttools==4.69.1`, `brotli==1.2.1`을 사용한다. 위 원본 파일을 내려받은 뒤 프로젝트 루트에서 실행한다.

```sh
python scripts/build_font_supplement.py --source /path/to/PretendardVariable.woff2
```

스크립트는 원본 해시를 검증하고 공식 서브셋에 없는 범위를 계산한 뒤 `PetnoteSupplemental.woff2`와 CSS의 보완 범위를 재생성한다. 기존 글자 범위, 가변 굵기 축, 이름 변경도 검증한다. 해시가 다른 원본은 사용하지 않는다.
