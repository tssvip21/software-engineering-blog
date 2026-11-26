# 펫노트 | 소프트웨어공학 학기 프로젝트

가족이 함께 반려동물을 돌볼 때 누가 언제 밥을 줬는지 확인할 수 있는 웹 서비스입니다. 사료와 급여량을 미리 저장해 두면 버튼 한 번으로 기록하고, 다른 보호자가 PC나 모바일에서 바로 확인할 수 있습니다.

[펫노트 실행 방법](./petnote/README.md#docker로-실행하기) · [개발 블로그](https://tssvip21.github.io/software-engineering-blog/)

## 구현 및 검증 현황

- 급여·산책·체중 등 18개 돌봄 기능을 선택해 기록하고, 가족과 일정·기록·통계를 공유할 수 있습니다.
- Docker Compose로 프런트엔드·백엔드·DB를 함께 실행합니다. SQLite와 Docker PostgreSQL에서 자동 테스트가 각각 486개 통과했습니다.
- 실제 사용자 테스트는 아직 진행하지 않았습니다. [브라우저 검사 및 성능 측정 결과](./petnote/docs/28_실사용_점검.md)

[이전 개발 이력](https://github.com/tssvip21/software-engineering-blog/commits/petnote/)
