# 경마 BMED 분석기 — 프로젝트 지침

- **프로젝트 경로**: `C:\Users\USER\Desktop\경마분석서버`
- **GitHub**: https://github.com/kwonceo/racing-analyzer.git (`origin/master`)
- **구성**: Flask 서버(`app.py`) + Chrome 확장(`chrome-extension/`) + 분석기 웹(`static/`)

---

## ⚠️ BMAD 원칙 (모든 작업에서 반드시 준수)

1. **기존 기능 절대 삭제 금지** — 새 기능은 "추가/보강"만. 삭제는 사용자가 명시적으로 지시한 항목(예: 일본 단승)만.
2. **작업 전 현재 파일 구조 확인 후 진행** — 관련 코드/데이터를 먼저 읽고 시작한다.
3. **완료 후 보고** — 무엇을·어떻게 바꿨는지 한국어로 명확히 요약.
4. **완료 후 보완점 자동 파악해서 함께 보고** — 남은 이슈·데이터 제약·후속 제안을 함께 제시.
5. **GitHub 백업 필수** — 작업 완료 시 커밋 + `git push origin master`.

> 커밋 메시지 끝에 `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

---

## 아키텍처

### 서버 (`app.py`, Flask, 127.0.0.1:8011, `debug=True` 자동 리로드)
- `static/`(`static_url_path=""`)에서 `index.html`·`js`·`css` 서빙.
- 한국경마: PDF 업로드 → PyMuPDF(fitz) 렌더 → Claude Vision 판독. 엔드포인트 `POST /api/korea/start`.
  - `import fitz`는 try/except로 방어(미설치여도 서버 기동, 405 대신 503 안내) — **405 재발 방지 핵심**.
- 배당 3종 저장: `triple_store.json`(raceKey→{quinella,exacta,trio,history}).
- 분석 핵심: `_triple_analyze(rk, rec)` → drops(급락)·reversals(쌍승역전)·signals·betRecommend·patternMatch 반환.
- 학습: `data/learning.json`(records+stats). 결과 입력 시 `_apply_result_learning` → `_recompute_learning_stats`.
- 현재 경주 조회: `GET /api/current_race`(확장이 마지막 수집한 raceKey).

### Chrome 확장 (`chrome-extension/`, MV3)
- `background.js`: 서비스워커. `chrome.alarms` 30초 하트비트 + fine 5초 루프로 **팝업/탭 무관 백그라운드 자동수집**(`BG_DRIVES=true`로 content.js 자체 루프는 OFF). 발주 임박 단계 알림, 결과 자동수집(resFetch), 분석기 창 열기(OPEN_ANALYZER, type:normal).
- `content.js`: keiba.go.jp·사설(asyukk) 배당판에서 수집. `collectTripleKeiba`(URL fetch)·`collectTripleByTabs`(탭 클릭). `extractRaceKey`로 raceKey 자동 감지(+30초 `watchRaceChange`로 경주 변경 추종).
- `timer.js`: 전 탭 상단 카운트다운 바 + 페이지↔확장 릴레이(FORCE_COLLECT/OPEN_ANALYZER) + 배당판에 "📊 분석기 열기" 버튼.
- `popup.js/html`: raceKey·종목·간격·자동전송·일본 중앙/지방 토글.

### 분석기 웹 (`static/js/app.js`, `static/index.html`)
- 탭: 한국경마 / 일본경마 / 결과기록 / 기수DB / 통계.
- 상단 바: 🔄 경주 새로고침(현재경주 자동표시·30초 폴링) + 🪟 별도 창으로 열기(+📌 항상 위=PowerToys Win+Ctrl+T 안내).

---

## 시장별 수집 규칙 (중요)
- **한국경마**: 복승만 수집(쌍승·삼복승·단승 제외). 전적=PDF Vision.
- **일본경마**: 복승·쌍승·삼복승 수집(단승 제거됨).
  - **지방(NAR, keiba.go.jp)**: 전적표 있음(출마표2/DebaTable). 전적+배당 통합. 마감 T-1분·T-30초.
  - **중앙(JRA)**: 전적표 없음 → 배당만. 마감 T-1분30초에 수집 중지, 이상감지 T-2분 강제.
  - 팝업 `[🏟 지방(NAR)] [🏇 중앙(JRA)]` 토글(`japanType`).
- **경기 마감 감지**: "발매마감/締切" DOM 또는 배당 무변동 5틱 → 자동수집 중단.

## 배당 패턴 학습 시스템 (구현됨, 배당 데이터 기반)
- 패턴 태그: 급락50+/급락30+/쌍승역전/배당압축/복승불일치 + 시점(T-N분).
- 통계 탭: 패턴별 적중률·시점별 급락 효과. 신뢰도(표본 5회+)로 베팅 비중 자동 조정.
- 현재 경주 패턴 매칭 카드(patternMatch) → 한국/일본 통합분석 화면 표시.

---

## ⚠️ 알려진 데이터 제약
- **전적(recent 착순) 데이터가 현재 비어 있음**(저장된 1702마리 중 0마리). 거리·코스·경주날짜·기수이력은 아예 미수집.
- 따라서 **전적 기반 패턴 학습**(당거리적중/거리변경/기수교체/오랜공백 등)은 데이터 수집을 먼저 살려야 작동함.
- 계산 가능한 것: 배당 급락 동반 여부(있음). 부진/최근연속입상은 recent가 채워져야 가능.

## 작업 관례
- 확장 코드 변경 시: `manifest.json` 버전 bump + `chrome-extension.zip` 재빌드(PowerShell `Compress-Archive`).
- 서버/프론트만 변경 시: ZIP 재빌드 불필요(서버 자동 리로드 + 브라우저 새로고침).
- 검증: `node --check`(JS 문법), `python -c "import ast..."`(app.py), 브라우저 콘솔 에러 확인, 가능하면 라이브 엔드포인트 테스트.
- 테스트: `node tests/run_stats.js`, `python tests/run_flow.py`.
