# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# 경마 BMED 분석기 - 프로젝트 지침

## 기본 원칙
1. **기존 기능 절대 삭제 금지** (추가/수정만)
2. **작업 전 현재 파일 구조 확인** 후 진행
3. **한국어로 답변**
4. **각 단계 완료 후 보고**
5. **GitHub 백업 필수** (`git push origin master`)
6. **작업 완료 후 보완점 자동 파악해서 함께 보고**
7. **CHANGELOG 자동 갱신** — 새 기능 추가·버그 수정 시 `CHANGELOG.md` 최신 버전 섹션에 반영(아래 규칙)
8. **CLAUDE.md 자동 갱신 (권대표 지시 2026-07-29)** — **모든 작업 후 반드시 이 파일을 갱신·저장**한다.
   코드 수정뿐 아니라 **파악·조사만 한 경우도 포함** — 문서와 실제 코드가 다르면 그 자리에서 "정정"으로 명시해 남긴다.
   (이 파일이 다음 세션의 유일한 인수인계 문서다. 갱신이 밀리면 다음 세션이 stale한 전제 위에서 잘못 출발한다.)

> 커밋 메시지 끝에 `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

## 정보
- **경로**: `C:\Users\USER\Desktop\경마분석서버`
- **GitHub**: https://github.com/kwonceo/racing-analyzer.git (`origin/master`)
- **서버**: Flask, port 8011 (`python app.py`, `debug=True` 자동 리로드)
- **Chrome 확장**: v2.1.11 (`chrome-extension/`, MV3)
- **안정 체크포인트**: 태그 `v2.3.0-stable` (AI 준비 인프라 완성). 복구는 `RECOVERY.md`.

## 📘 완전 가이드 (한눈 요약)
### 시스템 개요
- Flask 서버 port 8011 + Chrome 확장 v2.1.x + 분석기 웹(5개 탭).
- 일본(복승·쌍승·전적)·한국(복승·PDF전적) 경마 이상감지 분석.
- **AI 학습 데이터 수집 중**(`data/ai_training/`, 목표 500경주 → Phase 2/3).

### 핵심 분석 공식
1. **초과급락** = 말N 평균급락 − 전체평균급락. 절대 **10%+ 급락 → 집중신호(노이즈 아님)** (`_excess_drop_analysis` `ABS_STRONG=-10`).
2. **역전비율** = 쌍승(B→A) / 쌍승(A→B). **<0.95 역전신호** / <0.80 강한 / <0.60 압도적 (`_win_exacta_reversal`).
3. **불일치점수** = 예상최저복승 / 실제최저복승. **1.2+ 주의** / 1.5+ 강한 / 2.0+ 압도적 (`_quinella_mismatch`).
4. **종합 신뢰도** = 초과급락40% + 쌍승역전35% + 복승불일치25% (70+ 🔴 / 40~69 🟡) (`_signal_confidence`).
5. **BMED 전략 5가지**(`_bmed_strategy`): 보험형(이상감지 없음+유력마 명확)·압축형(2두 강한신호)·역배열형(쌍승역전)·분산형(대규모급락)·고배당도전형(강한신호+고배당) + 원금보전 배분·기대환수율 + 보험용 매트릭스(정상/보험 2종).
6. **실시간 고도화**(`_advanced_anomaly`): 급락속도(분당%·간격하한0.25분+절대폭)·연속하락(+20)/단발반등(−15)·페이크베팅·복승 환급률(역수합=`refundRate`, 상위3조합 90%+ `🟠 자금집중`)·**말별 연속하락 `horseStreaks`(1회 후보⚪/2회 약한🟡/3회+ 확정🔴/반등 페이크🟠)**. 단승 급락 = 최우선 신호(`_sh_order` 최상단). 수집 간격 마감임박 T-3분 10초·T-1분 5초.

### 데이터 구조 (`data/`)
```
data/
├── ai_training/     ← AI 학습 핵심(완전 데이터 + 품질점수)  [추적]
├── analysis_log/    ← 분석 로그(패턴학습 코퍼스)        [**미추적**·gitignore]
├── race_results/    ← 경주 결과 완전 저장                   [추적]
├── race_report/     ← 고배당 적중 재현 리포트(추천근거·타임라인) [추적]
├── daily_summary/   ← 일별 자동 요약(YYYY-MM-DD.json)       [추적]
├── pattern_learning.json / discovered_patterns.json ← 패턴 통계 [추적]
├── prerace/ · korea_history/ · korea_session.json ← 한국 PDF/결과 [추적]
└── odds_history/ · triple_store.json · learning.json ← 임시·고빈도 [gitignore]
```

### 알려진 버그 (전부 수정 완료 ✅)
- **노이즈 판정 오류**(초과급락 미반영) → ✅ 절대 10%+ 집중신호 승격(`ABS_STRONG`).
- **한국경마 raceKey 매칭 불일치**(`서울 5`↔`2026-.. 서울 5경주`) → ✅ `_resolve_race_key` 유연 매칭.
- **자동전송 OFF 시 탭 클릭 버그** → ✅ 수집 게이트에서 autoSend/한국모드 우선 체크.
- **신호말 전체 조합 미표시**(147배 놓침) → ✅ `_signal_combo_bets` 신호말 전 조합 추천.
- **마감 오판/첫수집 가짜급락/opening 배당 정착** → ✅ `pageRemainingMs`·워밍업·`_is_opening_settle`.

### AI 개발 로드맵
- **Phase 1 (지금)**: 데이터 수집 — `ai_training/` 완전 구조 + 품질점수(80+ AI학습용).
- **Phase 2 (100경주)**: 데이터 정제 — 패턴 자동 발견 강화.
- **Phase 3 (500경주)**: 모델 학습 — `tools/export_ai_data.py`로 CSV/JSON 내보내 학습.
- **Phase 4 (검증 후)**: AI 통합 — 예측 모델을 분석 파이프라인에 병합.
- 현황·마일스톤은 통계 탭 `🤖 AI 학습 준비 현황` + `GET /api/ai-training/status`.

### 홈서버 운영
- **자동 시작**: `경마서버_자동시작.bat`(서버+배당판 열기) · 시작프로그램 등록 `scripts/register_startup.bat`.
- **자동 백업**: `scripts/backup_checkpoint.bat`(수동) · 매일 자정 자동 `scripts/register_daily_backup.bat`(작업 스케줄러 등록).

## 분석 원칙
- **통합 점수 = 이상감지(배당) 60% + 전적 40%** (`_integrated_grades` 기본값). **50경주+ 누적 시 비교학습으로 자동 조정**(`_learned_integrated_weights`: 이상감지·전적 적중률 우세 쪽으로 ±15%p, 이상감지 0.45~0.75).
- **한국경마**: 복승만 수집 / **일본경마**: 복승+쌍승+전적 (삼복승 **수집만** 제거 v2.1.9 — 추천은 추정배당 보험으로 유지, 삼복승 로직 코드 보존)
- **이상감지**: T-2분 강제 실행 (중앙 JRA)
- **배당 급락**: 30%↑ 경고(🟠) / 50%↑ 강력(🔴)
- **A/B/C/D 등급**: 상위 비율 45:28:17:10
- **배당 급락말 → 삼복승 보험픽 자동 추가**

## 현재 보완 필요 항목
1. ✅ **KRA 실제 기수 데이터 연동 완료** — 현직기수 104명 실데이터(합성기수 0). `static/data/jockeys.json`.
2. **일본 NAR 전적 수집** — `parseDebaTable` try/catch+2회 재시도로 코드 안정화 완료. 실제 keiba 라이브 경주 검증만 잔여.
3. **결과 페이지 파싱** — `_parseResultDoc`(확장)·`_parse_result_rows`(서버) 전각숫자·着 컬럼·완화 헤더 매칭 보강 완료. 실경주 HTML 라이브 검증만 잔여.
4. ✅ **모바일 화면 최적화 완료** (반응형 CSS).
5. ✅ **실제 투자금액 입력 필드 완료(일괄 포함)** — 단건 결과입력에 `실수령 배당금(원)` 란 추가. 입력 시 서버가 `실수령−투자금`으로 정확 손익 계산(`payout` 파라미터·`record.payout_actual`), 공란이면 확정배당 추정 유지. **일괄 등록도 완료** — `renderBulkSummary`가 경주별 `투자/실수령` 편집 표를 렌더하고 `POST /api/results/adjust`(`_recompute_pnl`)로 저장된 학습 레코드를 in-place 갱신(공란이면 확정배당 추정). 정액 가정은 초기값일 뿐, 경주별 실측 조정으로 학습 통계에 반영됨.
6. **패턴 학습 데이터 축적 중** (결과 입력 쌓여야 통계 산출) — 진행 중.
7. **📌 보류(권대표 지시 2026-07-28 — 나중에 다시 논의) · 교체 보조 조합 자금 배분 UI**
   - **무엇**: `_apply_rec_hysteresis`가 복승 메인 교체 시 이전 조합을 `switchBackup=True`로 2순위 보존하도록 적용됨(커밋 `0aaea857`). 그런데 **화면에 "얼마씩 걸어야 하는지"가 안 나온다** — `budget_allocation`에 `switchBackup` 몫이 반영돼 있지 않음.
   - **왜 중요**: 시뮬 수치는 **총투자 동일·조합당 절반(500/500)** 가정에서 나온 것. 지금처럼 각 1,000원씩 걸면 **투자가 2배**가 된다(회수율은 214.1%로 같지만 원금 노출이 커짐).
   - **근거 데이터**: 막판 복승 메인 교체 210건 중 확정배당 보유 119건 시뮬 —
     현행(교체후만) 투자 119,000 / 손익 **+48,700** / 적중 26 / 회수율 **140.9%**
     안A(교체후·전 분산) 투자 119,000 / 손익 **+135,750** / 적중 47 / 회수율 **214.1%**
     · 놓친 적중 배당 합 341.8(최대 155.6배) vs 잡은 적중 167.7(최대 21.6배) → "하나만 남기는 것"이 손해였음.
   - **주의**: 표본 119건(배당 미확보 91건 제외) · 155.6배 1건의 영향이 큼 · 둘 다 미적중 72건(61%)은 개선 없음.
   - **남은 일**: ⓐ`budget_allocation`에 보조 몫 배분 ⓑ프론트에 보조 조합 금액 표시 ⓒ**삼복승에도 동일 적용 검토**(현재 삼복승 히스테리시스는 '보류'만 하고 교체 시 이전 조합 보존은 미적용).

## CHANGELOG 자동 관리
**새 기능 추가 / 버그 수정 / 보완 작업을 할 때마다 `CHANGELOG.md`를 함께 갱신한다(잊지 말 것).**
- **위치**: 파일 최상단의 **현재 최신 버전 섹션**(`## vX.Y.Z (날짜) — 제목 · 현재 최신`) 안의 해당 소제목에 한 줄 추가.
  - 새 기능 → `### 추가된 기능` / 버그 수정 → `### 수정된 버그` / 남은 과제 → `### 보완점`
- **새 버전 승격 기준**: 큰 기능 묶음/마일스톤이면 새 `## vX.Y.Z` 섹션을 최상단에 추가(이전 "현재 최신" 표기는 제거)하고, 그 커밋에 `git tag -a vX.Y.Z -m "..."` + `git push origin --tags`.
  - 버전 규칙: 호환 깨짐=MAJOR / 기능 추가=MINOR / 버그·문서=PATCH.
- **커밋 관례**: 코드 변경 커밋에 `CHANGELOG.md` 갱신을 **같은 커밋**에 포함. 문구는 커밋 메시지와 일치시킨다.
- 되돌리기 안내는 `README.md` "🔄 버전 관리 & 되돌리기" 섹션 유지.

## 단축 명령어
- `#보완` → 현재 보완점 파악 후 우선순위 작업
- `#백업` → `git add . && git commit && git push`
- `#상태` → 파일 구조 + 현재 상태 보고
- `#기능` → 완성 기능 목록 보고
- `#변경` → 최근 작업을 `CHANGELOG.md` 최신 섹션에 반영(+ 필요 시 새 버전 태그)

## 세션 시작 자동 실행
1. `git pull`로 최신 코드 확인
2. 현재 보완 필요 항목 파악
3. 파악 후 대기

---

## 아키텍처

### 서버 (`app.py`, Flask, 127.0.0.1:8011)
- `static/`(`static_url_path=""`)에서 `index.html`·`js`·`css` 서빙.
- 한국경마: PDF 업로드 → PyMuPDF(fitz) 렌더 → Claude Vision 판독. `POST /api/korea/start`.
  - `import fitz`는 try/except로 방어(미설치여도 서버 기동, 405 대신 503) — **405 재발 방지 핵심**.
- 분석 핵심: `_triple_analyze(rk, rec)` → drops·reversals·signals·betRecommend·patternMatch·form·elimination·integrated 반환. **모든 분석/학습이 이 dict를 소비**.
- 결과 학습: `_apply_result_learning` → `_recompute_learning_stats` + `_learn_upset` + `_discover_patterns` 연쇄.
- **이상감지 누적**(v2.3.0): `_history_append`가 매 수집 스냅샷에 단승/복승 급락 + **쌍승 역전**(최저 쌍승 조합 방향 반전)을 영구 기록(스냅샷 삭제 없음). `GET/POST /api/odds/anomaly-feed`가 스냅샷에서 시간순·중복제거 누적 피드 파생(마감 후에도 유지).
- **마감 오판·첫수집 가짜급락 방어**(v2.3.0, 확장 v2.1.10): 확장 `detectRaceClosed`가 `pageRemainingMs`(배당판 "남은시간" 직접 파싱)로 진짜 마감 임박(≤90초)일 때만 무변동 마감 적용(발주시각 미검출 시 `!deadline`→무조건 마감 버그 제거). 서버 `_triple_analyze`/`_history_append`는 첫 비교(수집 2건뿐=`market_forming`)를 1틱 워밍업으로 급락 계산·기록 보류 → 첫 수집 못 가져와 뜨던 -90%대 가짜급락 제거(2번째 수집 기준, 3번째부터 계산).
- **실시간 분석 유지(초반 되돌이 방지)**(버그수정): `_baseline_reset_needed`가 변동성 큰 배당 단발 블립에 오발동해 history를 비워 분석이 초반으로 회귀하던 문제 → **확립(4+스냅샷) 후에는 배당 휴리스틱 초기화 금지**(`triple_ingest` `_established` 가드, `_triple_analyze`도 `len(hist)>4`면 재설정 미표시). 확립 후 경주 전환은 **raceKey 변경**으로 처리. 프론트 `onJapanOddsUpdate`는 rk 변경 시에만 경고 초기화 + 기준값 상태에선 경고 생략(중복 제거). 검증 `tests/run_report.py`.
- **경주 전환 배당 잔존 방어**(v2.3.0): `_baseline_reset_needed`(직전 대비 공통 복승 60%+가 90%+ 급락=시장 전반 붕괴→다른 경주 잔존)가 `triple_ingest`에서 history 초기화(`baselineReset`, **단 확립 전에만**)·`_history_append`에서 이상감지 생략·스냅샷 `baseline_reset` 표기. `_triple_analyze`가 첫 수집=`baselineSet`·전환 감지=`baselineReset`(변동 계산 생략) + 개별 95%+ 급락은 복승/단승 drops에서 제외 + 🟡 기준재설정 신호. 프론트 renderTripleAnalyze 헤더에 "🎯 기준값 설정됨"/"⚠️ 기준값 재설정" 배너.
- **끝난 경주 활성 캐시 정리(직전 배당 잔존 방어)**(v2.3.0, `_triple_prune_stale`·`STALE_ACTIVE_SEC=1800`): `triple_store.json`이 경주를 무한 누적하고 `max-t` 폴백이 끝난 직전 경주를 계속 표시하던 문제. `triple_ingest`가 매 수집마다 30분+ 미갱신 경주를 활성 캐시에서 제거(방금 수집·최근 30분내는 유지=한/일 동시 안전, `data/odds_history` 히스토리 파일은 영구 보존→학습·복기·`_rec_from_history` 영향 없음). `current_race`/`triple_latest`가 `stale`(최신 경주도 30분+ 미갱신) 플래그 반환. 프론트: `pollJapanOdds`가 `raceKey` 불일치·`stale` 응답 무시, `refreshCurrentRace` 30초 자동감지가 경주 변경 시 **자동 초기화+전환**(`hardResetRaceState`), 상단 `🆕 새 경주 시작` 버튼(`newRaceStart`→`/api/odds/triple/reset`+상태 초기화). `_established`(4+스냅샷) baseline 확립 가드와 상호 보완.
- **결과 4착 + 삼복승 near-miss 학습**(v2.3.0): 결과 입력 폼(recordResult·saveJapanResult·saveResult) 4착 필드 추가. `_apply_result_learning`이 추천 삼복승 2두 top3 + 1두 4착이면 `near_miss`/`near_miss_horse`/`trio_near_miss` 기록 → `_record_near_miss`가 `data/near_miss.json`(gitignore) 누적. `_near_miss_frequent`(2회+) 말이 출전 시 `_triple_analyze`가 `삼복승 보험(4착빈번)` 픽 자동 추가(마감 전만). `GET /api/learning/near-miss`, 통계 `renderNearMissStats` 카드. 적중 판정 기준(복승 1+2·삼복승 1+2+3)은 불변.
- **이상감지 vs 추천 비교 학습**(v2.3.0, `_triple_analyze` 반환 `compareRecommend`/`integratedWeights`): `_compare_recommend`가 이상감지 기반(집중급락→급락조합→배당인기)·전적 기반(전적 총점)·최종(betRecommend) 추천 조합 3종 산출. `_apply_result_learning`이 결과와 각 조합 비교 → 레코드 `cmp_anomaly_hit`/`cmp_form_hit`/`cmp_final_hit` → `_recompute_learning_stats`의 `compare_stats`(적중률) + `integrated_weights`(50경주+ 자동 조정). `_integrated_grades(weights)`가 `_learned_integrated_weights()`로 가중치 자동 반영(기본 40/60). 프론트 `renderCompareStats` 카드(통계 탭). 분석 로그 `compare_recommendation` 저장.
- **마감 후 신호 처리**(v2.3.0, 확장 v2.1.8): `_history_append`가 스냅샷에 `mb_signed`(부호 포함 발주전분)·`after_close` 기록(마감 후=음수). `_triple_analyze`가 현재 스냅샷 `after_close` 시 급락을 삼복승 보험(`anomaly_horse`)·대규모급락 전략에서 제외(추천 미반영)하고 모든 신호에 `phase`("마감 N분전"/"마감 후")·`afterClose`·`note`("참고만") 태깅, 반환 `afterClose`/`minutesBefore`. 프론트: 마감 후 배너 + 신호 회색·소리/플래시 생략(`updateOddsAlert`). 확장 수집 간격 단계 단축(T-3분 15초/T-1분 10초/T-30초 5초, `background.js autoTick`). `_record_after_close_case`가 `data/after_close_cases.json`에 케이스 저장(`GET /api/after-close/cases`, gitignore).
- **📋 경주별 결과 입력 시스템**(신규, `_missing_results` 보강 + `GET /api/race-results/missing`, 결과기록 탭): 분석·배당 수집된(analysis_log 有·결과 無) 경주를 결과기록 탭 상단 `📋 결과 입력 대기` 목록에 자동 표시(추천 요약·이상감지·갱신시각). [결과 입력] 팝업(1~4착·복승/삼복승 배당·투자/실수령)은 **기존 `/api/history/record-result` 재사용**(적중판정·수익·학습 그대로) → 즉시 목록/통계/리포트 갱신·제거. 발주 근접 갱신 30분 경과 미입력 → `🔔` 알림(60초 전역 폴링·localStorage 중복방지). 미입력 N경주 배너. **기존 입력경로(일괄·세션폼·일본복기) 보존, UI만 추가**. 검증 `tests/run_report.py`.
- **🏆 고배당 적중 상세 분석 리포트**(신규, `_build_race_report`/`_signal_win_tags`/`_combo_timeline`): 결과 입력 시 `_apply_result_learning`이 `data/race_report/<날짜>_<경마장>_<경주>.json` 자동 생성 — `why_recommended`(입상마·유력마별 초과급락·대표조합 배당 타임라인·쌍승역전 비율·전적점수·신뢰도), `recommendation_process`(스토리 단계), `confidence_breakdown`(초과40+역전35+불일치25 가중 + 상/중/하), `win_tags`. 기존 `an`(분석 반환)·스냅샷만 소비(재계산 없음). 명예의 전당은 기존 `_highlight_save`(복승30배+/삼복승100배+) 확장(리치 필드). 학습은 레코드 `win_tags` + `_recompute_learning_stats.win_tag_stats`(신호·동시조합별 적중률·고배당 적중률). 엔드포인트 `GET /api/race-report/list·get`, `GET /api/highlights`. 프론트 결과기록 탭 `🏆 명예의 전당` 카드 + `📄 경주 재현 리포트`(4탭). 검증 `tests/run_report.py`. **삭제 없이 확장만**.
- **신호 품질 필터링**(v2.3.0, `_triple_analyze` 반환 `signalQuality`): `_excess_drop_analysis`(초과급락=말평균-전체평균, 5%p+ 🔴/0~5%p 🟡/노이즈 제거) → `_signal_situation`(상황별 가중치 일반50:50/이상감지다수40:60/대규모30:70/대규모+집중20:80, 대규모 시 신호소스=집중도) → `_integrated_adaptive`(상황 가중 통합등급, 기존 `_integrated_grades` 40/60은 유지) + `_combo_signal_quality`(추천 조합 상/중/하+근거). 대규모 급락 시 개별 급락 신호 `lowConfidence`↓ + 집중급락 말 `🔴 집중급락` 신호 승격. 프론트 `renderSignalQuality` 카드 + 베팅표 신호품질 컬럼.
- **타임라인 정제 + 신호 안정화**(v2.3.0, `_triple_analyze` 반환 `signalTimeline`·`nextRaceBlocked`): `_history_append`가 매 스냅샷에 `signal_horse`(집중급락 1순위=`_excess_drop_analysis` 재사용)·`signal_reason`·`next_race_blocked`를 기록. **[1번]** `_combo_timeline`이 마감 후(`after_close`)·다음경주(`next_race_blocked`) 스냅샷을 `excluded`+제외사유로 표기(데이터 보존·변동계산 제외). **[2번]** `_next_race_surge`(직전 대비 공통 복승 60%+가 200%+ 급등=다음 경주 유입)가 `_history_append`에서 스냅샷 차단(이상감지·신호말 계산 생략). `_as_qmap`이 리스트/딕셔너리 배당 형식 모두 정규화. **[3·4·5번]** `_signal_timeline_from_doc`가 signal_horse 시퀀스에서 변경 이력(`changes`{previous/new_signal·reason·prev_was_candidate})·안정화(1회=`candidates`/2연속=`confirmed`)·유효시점(`events`{first,confirmed,vanished,count})·`finalSignal`/`finalConfirmed` 도출(마감후/다음경주/기준재설정 제외). 엔드포인트 `GET /api/odds/signal-timeline`. 리포트 `signal_change_history`(5번째 탭). 프론트 `renderSignalTimeline` 카드 + 리포트 신호이력 탭. 검증 `tests/run_report.py`.
- **고배당 경고 신호 완전 기록**(v2.3.0, `_triple_analyze` 반환 `alertSignal`): 배당 급변(복승 30%+ 급락=`ALERT_DROP_THRESH`)을 `_record_alert`가 `data/alerts/<경주>.json`에 영구 기록(`odds_snapshot` 조합별 before→after·경고말·당시 복승 메인 추천·마감전분, 같은 조합쌍 1회만·마감후/기준값 상태 제외). 결과 입력 시 `_match_alerts_to_result`가 경고말 입상(`alert_correct`)·**경고 무시 후 놓침**(`ignored_miss`=경고말 입상했으나 당시 추천 미포함) 판정 → 학습 레코드 `alert_fired`/`alert_hit`/`alert_ignored` + `highlight_wins.json` 강화. `_recompute_learning_stats` `alert_stats`(발생·입상률·무시후미적중·조언). 프론트 `renderAlertSignal` 상단 `⚠️ 경고 신호 감지!` 배너 + 통계 `renderAlertStats` 카드(40%+ 시 "경고말 추천 포함 권장"). `GET /api/alerts/list`·`/get`. `data/alerts/` gitignore.

- **실패 복기 학습 시스템**(v2.3.0, `data/failure_review.json`[gitignore·런타임 누적], `_classify_failure`/`_failure_record`/`_failure_report`/`_failure_stats`): `_apply_result_learning`이 **미적중 경주**만 `_classify_failure`로 실제 입상마(추천 제외 최상위) 배당 타임라인(`_horse_repr_timeline`=단승 우선·없으면 최저 복승 조합)을 역추적해 5유형 판정(우선순위: 타이밍→전적오판→페이크→노이즈→신호미반영). 유형별 카운트·놓친 신호 패턴 누적 + **같은 패턴 3회+**(`FAIL_RULE_THRESHOLD=3`) 반복 시 규칙 자동 생성(생성 시점 추천 적중률을 `before_rate`로 스냅샷). 히스토리 `review.failure`에 분류 저장. `GET /api/failure/report`(정답말 1·2·3착 역추적 + 텍스트 리포트)·`/api/failure/stats`(유형 분포·1착 신호보유율·개선 전/후·규칙)·`/api/failure/rules`. ⚠ 기존 학습(learning.json)과 독립 저장소.
- **일괄등록 유연화 + 명예의 전당**(v2.3.0): `_TRACK_GROUPS`/`_TRACK_REVERSE`+`_track_norm`이 경마장명 한/일/영 별칭 통일(帯広=obihiro=OBI=오비히로 등 25개), `_area_num`이 영문 토큰(obihiro·OBI 5R)도 인식 → `_resolve_race_key` 유연 매칭 강화(라이브 raceKey가 `佐賀` 한자여도 매칭). `GET /api/races/list`(시간순 경주 목록·미입력 필터)로 [순서대로 빠른입력]. `_highlight_story`가 고배당 적중(복승30+/삼복승100+)에 스토리+정답말 타임라인 첨부, `GET /api/hall-of-fame`.

### Chrome 확장 (`chrome-extension/`, MV3)
- `background.js`: 서비스워커. `chrome.alarms` 30초 하트비트 + fine 5초 루프로 **백그라운드 자동수집**(`BG_DRIVES=true`). 발주 임박 알림, 결과 자동수집(resFetch 7/9/11분·최대 3회). fetch 릴레이: `FETCH_URL`(omit, DebaTable) / `FETCH_RESULT_HTML`(include, 로그인 세션 결과 페이지).
- `content.js`: keiba.go.jp·사설(asyukk) 수집. `collectTripleKeiba`·`collectTripleByTabs`. `extractRaceKey`(+30초 `watchRaceChange`). `detectPostTime`/`autoDetectPostTime`(발주시각 자동감지). `collectResultsByFetch`(video_iframe→/bet/result?id=N). `dedupeStarters`(출마표2 파서 오탐 방어).
- `timer.js`: 전 탭 카운트다운 바 + 페이지↔확장 릴레이(FORCE_COLLECT/OPEN_ANALYZER/**FETCH_RESULT_HTML 왕복**).
- `popup.js/html`: raceKey·종목·간격·자동전송·일본 중앙/지방 토글 + 발주시각 실시간 표시.

### 분석기 웹 (`static/js/app.js`, `static/index.html`)
- 탭: 한국경마 / 일본경마 / 결과기록 / 기수DB / 통계.
- **마감 전 3단계 알림 + 이상감지 누적 패널**(v2.3.0, `initClosingWatch`): `/api/auto/status`의 `deadline`으로 남은시간 계산 → T-1분30초/1분/30초에 소리(2/3/4회)+화면강조 오버레이(`#closingAlert`)에 누적 이상감지 요약 + 메인 복승/삼복승(`/api/odds/triple/analyze`) 표시. 좌하단 `#anomalyFeedPanel`이 `anomaly-feed`를 3초마다 누적 표시.
  - **경주별 분리·한국 포함·히스토리**: 패널은 `_closing.panelRk`(현재 경주)만 `[raceKey]` 헤더 블록으로 표시(경주 안 섞임). 한국·일본 흐름 모두 `setAnomalyPanelRace(rk)`로 현재 경주 지정(한국=`pollKoreaOdds` 링크 시, 일본=분석 렌더 시). `📜 히스토리` 토글=`renderAnomalyHistory`가 `/api/history/list`(`anomalyCount`>0)의 과거 경주를 raceKey별 블록으로 표시, `◀ 현재`로 복귀. 이전 경주는 서버 스냅샷(odds_history)에 영구 보존.
- **일본경마 복기**(v2.3.0, 일본경마 탭 `📒 일본경마 분석 내역 · 결과 복기`, `loadJapanReviewList`/`openJapanReview`/`renderJapanReview`/`saveJapanResult`): `/api/analysis-log/list`에서 일본경마(서울/부산/부경/제주/과천·TEST 제외) 필터·날짜별 목록(기본 오늘). 클릭 시 `/api/analysis-log/get`으로 유력마/제거마·이상감지·추천조합 표시 + 결과 입력 폼(1~3착·투자금액·실수령배당) → `/api/history/record-result`로 저장·자동판정 → `renderJapanReviewReport`(복승/삼복승 적중·이상감지 정확도·손익) 즉시 표시 + `loadLearningStats`/`renderStats` 통계 갱신. 분석 로그에 `raceKey` 저장 + `review_doc`에 `pnl`/`stake` 추가(재조회 손익 유지).
- 결과기록 탭: **📋 일괄 결과 등록**(URL→확장 경유 fetch 또는 HTML 붙여넣기 → `/api/results/bulk`).
- 통계 탭: 학습 통계 + 부진마 이변 조건별 적중률 + 🔎 자동 발견 패턴(충분도 진행바).

---

## 명령어 (Commands)
```bash
python app.py                             # 서버 실행 (보통 이미 떠 있음)
pip install -r requirements.txt           # flask, anthropic, PyMuPDF(fitz)
# 문법 검증 (커밋 전 필수)
python -c "import ast; ast.parse(open('app.py',encoding='utf-8').read())"
node --check chrome-extension/content.js  # 확장 JS 각각
node -e "new Function(require('fs').readFileSync('static/js/app.js','utf8'))"  # app.js
# 테스트
node tests/run_stats.js
python tests/run_flow.py
python tests/run_formula.py          # 유력마/제거마 공식 정합성
python tests/run_reversal.py         # 쌍승 역전 다중순위·flip 다중조합
python tests/run_report.py           # 고배당 적중 재현 리포트·신호조합 태깅
python tests/run_prerace.py          # 한국 PDF 전경주 사전분석
# 확장 ZIP 재빌드 (확장 코드 변경 시에만, manifest 버전 bump 후)
cd chrome-extension && python -c "import zipfile,os; zf=zipfile.ZipFile('../chrome-extension.zip','w',zipfile.ZIP_DEFLATED); [zf.write(os.path.join(r,f),os.path.relpath(os.path.join(r,f),'.')) for r,_,fs in os.walk('.') for f in fs]; zf.close()"
```
- **단일 함수 검증**: `importlib.util`로 `app.py` 로드 → 저장소 상수(`UPSET_FILE` 등)를 임시 경로로 monkeypatch → 함수 직접 호출(프로덕션 데이터 오염 방지).
- 한글 본문 POST는 shell curl 대신 python urllib로 테스트(인코딩).

## 시장별 수집 규칙
- **한국**: 복승만. 전적=PDF Vision. **일본**: 단승+복승+쌍승 수집(삼복승 수집 제거 v2.1.9 · **단승 수집 재도입 v2.1.17** — 단승 급락=최강 신호. keiba는 単勝複勝 표 fetch, 사설은 탭 수집). 삼복승 배당 미수집 시 `_triple_analyze`가 삼복승을 `_trio_est` 추정배당 '보험(추정)' 소액(≤18%)으로 유지(`estimated`)·복승 메인에 잔여 배분. 실배당 수집 시 기존 로직.
  - **한국 판정 강화(확장 v2.1.6)**: `isKoreaMode(raceKey, market)` = 종목=한국(팝업) OR raceKey에 KRA 경마장명(`isKoreaByRaceKey`) OR **페이지 본문/URL에 KRA 경마장명+경마맥락**(`pageLooksKorean`, raceKey 추출 실패 대비). true면 복승만·쌍승/삼복승 탭 클릭 완전 스킵(`collectTripleKeiba`·`collectTripleByTabs` 양 경로). 로그 `[한국모드] 복승만 수집 - 쌍승/삼복승 생략`. ⚠ 확장 코드 변경이므로 **브라우저에서 확장 새로고침 필수**(안 하면 구코드가 계속 실행).
  - **지방(NAR, keiba.go.jp)**: 전적표(출마표2/DebaTable) 있음. 마감 T-1분·T-30초.
  - **중앙(JRA)**: 전적표 없음→배당만. 마감 T-1분30초 수집중지, 이상감지 T-2분 강제. 팝업 `japanType` 토글.
- **마감 감지**: "발매마감/締切" DOM 또는 배당 무변동 5틱 → 자동수집 중단.

## 학습 시스템 (결과 입력 시 `_apply_result_learning`에서 갱신)
- **배당 패턴**(`data/learning.json`): 급락50+/급락30+/쌍승역전/배당압축/복승불일치 + 시점. 표본 5회+ 신뢰도로 베팅 비중 조정.
- **부진마 역전**(`data/pattern_learning.json`, `_learn_upset`): 부진마=최근5평균착순≥4.0. 입상 시 급락30%+·복승이상감지 동반 태깅 → condition_stats. 전적 있는 경주만(한국 PDF 즉시). `GET /api/learning/upset`.
- **대규모 급락**(`data/pattern_learning.json`의 `patterns`, `_learn_mass_drop`): 전체 복승 조합 50%+ 또는 30개+ 동시 30%급락(`_mass_drop_detect`, `an.massDrop`) 감지 시 결과 입력마다 사례 축적(고배당율·적중률 `condition_stats["대규모급락"]`). 전략(`_apply_mass_drop_strategy`)=삼복승 보험 8→15%·중배당 복승 보험·최저배당 신뢰도 하락. 히스토리상 반복 패턴(66~84% 동시급락 11경주).
- **패턴 자동 발견**(`data/discovered_patterns.json`, `_discover_patterns`): `data/analysis_log/` 스캔 → 적중 경주 공통점(기준 +12%p·표본 5↑만). 충분도 목표 50경주. `GET/POST /api/patterns/discovered`.
- **원시 데이터**(`data/analysis_log/`): `_analysis_log_save`가 매 분석(30초)마다 배당 타임라인·전적점수·이상감지·결과 완전 저장. 별도 raw 저장소 불필요(재사용).

## 결과 자동수집 (2경로)
- **자동**(발주 후 7/9/11분): `doResultFetch`→`collectResultsByFetch`(video_iframe→/bet/result?id=N, 로그인 세션)→`POST /api/results/auto`. 성공/실패 Chrome 알림.
- **일괄**(마감 후): [일괄 등록]→확장 `FETCH_RESULT_HTML`→`POST /api/results/bulk`(`_parse_result_rows`→`_match_row_to_key` 지역+라운드 매칭→학습). URL 실패 시 HTML 붙여넣기 폴백.

## 데이터 저장소
- 루트: `triple_store.json`(3종 배당+히스토리)·`starters_store.json`(전적)·`results_store.json`(착순).
- `data/`: `learning.json`·`pattern_learning.json`·`discovered_patterns.json`·`analysis_log/`·`odds_history/`·`korea_history/`·`korea_session.json`(PDF 사전분석 세션)·`prerace/`.

### 데이터 커밋 정책 (churn 운영 규칙)
- **워킹트리 churn은 정상**: 라이브 분석 중 서버가 데이터 파일(`analysis_log/`·`korea_session.json`·`discovered_patterns.json`·`prerace/` 등)을 30초 주기로 갱신 → `git status`가 상시 dirty. 이는 **의도된 동작**이며 매 변경마다 커밋하지 않는다.
- **커밋 시점 = 명시적 백업 + 결과 입력 자동 백업**: 서버 백업 함수(`_analysis_log_git_backup`·`_korea_git_backup`, 버튼/엔드포인트) 또는 `#백업`/마일스톤 커밋 외에, **결과 입력마다 `_data_git_backup`(5초 디바운스·pathspec 커밋)이 코퍼스만 자동 add+commit+push**(데몬 스레드·비블로킹). 30초 churn은 여전히 자동 커밋 안 함(결과 입력이라는 명시적 이벤트에만 트리거). 수동 즉시: `POST /api/data/backup` / 통계 탭 `🛡️ 데이터 보호`. ⚠ 위험한 `git reset --hard`는 `scripts/safe_reset.bat`로 실행(실행 전 `backups/data_<ts>/`에 data\ 물리 스냅샷 자동 생성, `backups/`는 gitignore).
- **추적 유지(백업 대상)**: `race_results/`(경주별 완전 저장)·`race_report/`(고배당 적중 재현 리포트)·`ai_training/`(AI 학습 완전 데이터·품질점수)·`korea_session.json`·`korea_history/`·`prerace/`·`discovered_patterns.json`·`pattern_learning.json`·`simulation_db/`(적중왕전개 프로파일). **`dist/`(내보내기 출력)·`highlight_wins.json`은 gitignore.**
- ⚠️ **`data/analysis_log/` 는 추적 제외다(2026-07-20 [C안] 권대표 승인 · 2026-07-29 문서 정정).**
  하루 수십~수백 파일이라 **양쪽 PC 자동커밋 충돌의 주범**이었다(당일 3회 실측). 로컬 파일은 그대로 유지되고
  보존은 **6시간 주기 백업 zip(+구글 드라이브)** 이 담당한다.
  · 종전 이 문서와 `.gitignore` 5행 주석에 "analysis_log 추적 유지"가 남아 실제 상태와 상충했다 → 정정 완료.
  · 🔴 **운영 주의 — 분석 로그를 고치는 작업은 PC마다 따로 돌려야 한다.**
    `analysis_log/`가 git으로 오가지 않으므로, 이 디렉터리를 수정하는 스크립트의 결과는 **그 PC에만 남는다**.
    예: 경륜장 `sport` 오분류 정정 213건(`tools/fix_keirin_sport_tag.py --apply`)은 **서버 PC에서만 적용**됐다.
    → **다른 PC(랩탑 등)에서도 같은 스크립트를 각각 재실행**해야 종목별 통계가 일치한다.
    (스크립트는 멱등이다 — 이미 정정된 파일은 `sport=cycle`이라 대상에서 자동 제외된다.)
- **gitignore(고빈도 임시)**: `triple_store.json`·`starters_store.json`·`results_store.json`·`odds_store.json`·`learning.json`·`odds_history/`·`kra_history.json`·`.claude/`.

## PDF 전경주 사전분석 (한국)
- **아침 1회 업로드 → 전경주 백그라운드 순차 분석 → 경주별 즉시 사용.** `_korea_run_job`(데몬스레드)이 PDF 전 페이지 감지→기수표→경주 그룹핑→경주별 추출+`_do_analyze`. 진행상황 `"분석 중... N/M 경주 완료"`를 `korea_session.json`에 실시간 저장 → 탭 전환/새로고침/서버 재시작에도 지속·재개.
- **경주별 영구 저장**: 완료 즉시 `_prerace_save_race` → `data/prerace/<날짜>_<경마장>_<라운드>.json` + `index.json`. `GET /api/korea/prerace`(목록·경량) / `GET /api/korea/prerace/<key>`(1건 전체·즉시 로드). `/api/korea/reset` 시 `_prerace_clear`로 초기화. 경로조작 방어·index 유실 시 디렉터리 스캔 복구. 검증: `tests/run_prerace.py`.

## ⚠️ 알려진 데이터 제약
- **KRA 실데이터 연동됨**(data.go.kr, `tools/fetch_kra.py`): 현직기수 104명(실 복승률, `static/data/jockeys.json`) + 경주성적 647경주(20260403~0704, `data/kra_history.json`)로 **전적 3건+ 보유마 1,120두** 확보. `kra_horse_summary`로 한국 분석 프롬프트에 실제 전적 주입됨.
- **한국 PDF 전적 정상 작동**(formScore·recentPlacings). 출마표2 파서가 오즈표를 긁어 334행 쓰레기로 한국 전적을 덮어쓰던 버그 수정(`_sanitize_starters` 마번 1~18 중복제거 + 전적 0두가 기존 전적 덮어쓰기 방지).
- **일본 NAR DebaTable recent 파싱**: 코드 안정화 완료(`parseDebaTable` try/catch + `fetchDebaStarters` 2회 재시도로 [] 폴백). 실제 keiba 라이브 경주 최종 검증만 잔여.
- **중앙 JRA 결과 파싱**: 전각숫자(０-９)·전각콜론·1着/2着/3着·複勝·三連複 컬럼 + 완화 헤더 매칭 대응 완료(`_parseResultDoc`·`_parse_result_rows`). 착순 컬럼 부재 시 [] 조기 반환.
- **거리·코스·기수이력 세부는 미수집** → 부진마 학습의 "거리 변경/기수 교체" 조건은 이력 수집 선행 필요(현재 배당 급락·이상감지 동반만 계산). KRA전적의 착순은 확보됨.
  - **제거 공식 거리경험 -15 훅은 배선 완료**(`_elim_score(no_dist_exp)` ← `_elimination`이 `fh.noDistExp` 전달). 거리 이력 수집 시 전적표에 `noDistExp` 플래그만 채우면 자동 활성(현재는 데이터 미수집→감점 미적용). 공식 정합성은 `tests/run_formula.py`가 검증.
  - **거리 수집 준비 완료(`fetch_kra.py`)**: 응답에 거리가 있으면 `rcDist`를 race 레코드+`byHorse`에 담도록 추가(다중 필드명 방어). **단, 현 구독 엔드포인트 `racedetailresult`는 거리 미반환**(필드에 dist 없음), `API299_1`은 500 → **거리 보유 엔드포인트 확정이 선행 조건**. 활성화 잔여: ①거리 엔드포인트 배선(`--dist-url` 패턴) ②현재 경주 거리를 triple/starters 레코드에 저장 ③`byHorse.rcDist`↔현재거리 매칭으로 `noDistExp` 계산·주입. 3계층 모두 갖춰지면 자동 활성.
- KRA 기수통산성적비교 API는 EndPoint 미확정(500) — `--comp-url`로 정확 주소 지정 필요. 통산 핵심 지표는 현직기수정보에 포함.

## 작업 관례
- 확장 변경 시: `manifest.json` 버전 bump + ZIP 재빌드. 서버/프론트만 변경 시: ZIP 불필요(자동 리로드 + 브라우저 새로고침).
- 커밋 전 검증: `node --check`·`import ast`·app.js `new Function(...)` + 가능하면 라이브/합성 단위 테스트.
- 페이지↔서버 통신은 확장(timer.js 릴레이) 경유 — 분석기 웹은 `chrome.runtime` 직접 접근 불가.

---

# 🔧 빠른 참조 (2026-07-28 추가)

## 핵심 함수 위치 (`app.py`)
> 라인 번호는 수정 시 밀림. **정확한 위치는 항상 `grep -n "^def <함수명>" app.py`로 재확인**할 것.

| 함수 | 라인(2026-07-28 기준) | 범위 | 역할 |
|---|---|---|---|
| `_confidence_picks` | **6955** | 6955~7075 | 신뢰도 기반 축/후보마 선정 (왕축·strongAxis 판정) |
| `_final_picks` | **7195** | 7195~8279 | **최종 복승/삼복승 확정**. 왕축 강제 로직 ~7870, B라인 그물망 ~7872 |
| `_triple_analyze` | **9230** | 9230~11813 | 분석 총괄 진입점. Gemini 검수 호출 11406/11423 |
| `_history_save_analysis` | **13064** | 13064~13105 | 분석 스냅샷 히스토리 저장 |
| `_build_analysis_log` | **13194** | 13194~13488 | 분석 로그(패턴학습 코퍼스) 문서 생성 |
| `_apply_result_learning` | **16212** | — | 결과 입력 시 학습 연쇄 진입점 |
| `_ev_band_p` / `_ev_bands_update` | 8415 / 8436 | — | EV 추정적중률(학습 표본 50+ 자동 교체) · ⚠ 학습 적용 시 선형보간 우회 |
| `_EV_RESCUE_*` + 복원 블록 | 8395 / EV필터 직후 | — | **12~30배 EV강등분 1개 메인 복원**(2026-07-29, OOS 검증) |

### 🔁 리플레이(회수율 사전검증) — **규칙 변경 전 필수**
- 엔진: **`review_engine.py`**(48KB) · `replay_day(date, stake, keirin_re)`(478행) · 정책 20여종을 나란히 측정.
- 엔드포인트 `GET/POST /api/review/replay` · 리포트 `/review-report?date=YYYY-MM-DD` · 원장 `data/review_replay/<날짜>.json`.
- 데이터 소스: `data/analysis_log/`(추천·후보·타임라인) + `data/race_results/`(착순·확정배당) **동일 파일명 조인**.
- 회수율 비교는 **총투자 동일**(경주당 stake 고정 → 조합 수로 분할) 가정으로 계산해야 원금 노출이 공정하게 비교된다.
  조합당 정액으로 계산하면 조합을 늘리는 안이 자동으로 유리해 보이는 착시가 생긴다.
- ⚠ **in-sample 금지** — 같은 데이터로 곡선을 뽑아 같은 데이터로 검증하면 부풀려진다(이번 실측: in-sample +5.5%p ↔ OOS +0.9%p).
  **날짜로 전·후반 분할**해 후반부(OOS)에서 개선되는지, **종목별로도 모두 개선**되는지까지 확인할 것.
- 🔴 **리플레이는 '실제 코드 경로를 재현했는지'를 먼저 검증한다.**
  면제·예외 로직(`_lowodds_exempt`·`_mkt2`·EV 면제 토큰 등)을 빠뜨리면 **숫자가 아무리 정교해도 무의미하다.**
  · **실제 사례(2026-07-30 v1→v2 정정)**: 경계 1.8→1.5 시뮬에서 정액 컷에 면제가 없다고 가정해
    "1순위가 mid 로 가면 잘린다"고 계산 → **137.9%** 를 보고했다. 그러나 `_lowodds_exempt` ⓐ가
    **시장 최저배당 조합을 면제**하므로 1순위는 그대로 남고, EV 필터까지 반영하면 실제는 **85.6%**였다.
    전제 하나로 결론이 뒤집혔다(그 사이 "적중 중앙값 1.6→6.6배 개선"이라는 부수 근거도 함께 무효화됐다).
  · **체크리스트**: 시뮬 대상 함수의 ⓐ면제/예외 분기 ⓑ전멸 시 복원(관망 금지) ⓒ상한 캡(`_quinella_target`)
    ⓓ정렬 기준을 코드에서 **직접 읽어** 재현했는지 확인하고, 재현하지 못한 항목은 **결과에 명시**한다.
  · 재현 불가 항목이 있으면 그 시뮬은 **낙관/비관 어느 쪽 상한인지**를 함께 적는다.

## 데이터 경로
| 경로 | 추적 | 용도 |
|---|---|---|
| `data/analysis_log/` | ✅ git | 분석 로그 = 패턴학습 코퍼스 (30초 주기 갱신) |
| `data/ai_training/` | ✅ git | AI 학습 완전 데이터 + 품질점수(80+ 학습용) |
| `data/race_results/` · `data/race_report/` | ✅ git | 경주 결과 / 고배당 재현 리포트 |
| `data/pattern_learning.json` · `data/discovered_patterns.json` | ✅ git | 부진마 이변·자동 발견 패턴 통계 |
| `data/korea_history/` · `data/prerace/` · `data/korea_session.json` | ✅ git | 한국 PDF 사전분석 |
| `logs/gemini_review/` | ❌ 미추적 | **Gemini 자동진단 결과 JSON** (`gemini_reviewer._LOG_DIR`) |
| `triple_store.json` · `starters_store.json` · `results_store.json` | ❌ gitignore | 배당/전적/착순 고빈도 캐시 |
| `data/odds_history/` · `data/learning.json` | ❌ gitignore | 스냅샷·학습 원장 (임시·고빈도) |
| `backups/` | ❌ gitignore | 위험 작업 전 `data/` 물리 스냅샷 |

## Git 워크플로우 (서버PC ↔ 랩탑 2대 운영)
```bash
# 1) 항상 pull 먼저 (서버가 결과 입력마다 자동 커밋+푸시 → 원격이 수시로 앞서감)
git pull --no-rebase origin master

# 2) 코드 수정

# 3) 문법 검증 (커밋 전 필수)
python -c "import ast; ast.parse(open('app.py',encoding='utf-8').read())"
node -e "new Function(require('fs').readFileSync('static/js/app.js','utf8'))"
node --check chrome-extension/content.js

# 4) 커밋 + 푸시
git add -A && git commit -m "..." && git push origin master

# 5) 마일스톤이면 태그
git tag -a vX.Y.Z -m "..." && git push origin --tags
```

### ⚠️ 2대 동시 운영 충돌 대응
- 서버 PC의 `_data_git_backup`이 **결과 입력마다 자동 커밋+푸시** → 랩탑 작업 중 원격이 계속 전진.
- `git pull` 충돌 시: **`data/` 는 최신(=경주 더 진행된) 쪽 채택**이 원칙.
  ```bash
  cp -r data "backups/data_premerge_$(date +%Y%m%d_%H%M%S)/"   # 먼저 물리 백업
  git checkout --theirs -- data/ && git add data/              # 원격(랩탑) 채택 예시
  git commit && git push origin master
  ```
- 채택 전 **반드시 양쪽 내용 비교**(`git show :2:<파일>` = ours / `:3:<파일>` = theirs).
  - 판단 기준: `discovered_patterns.json`의 `races_with_result`가 큰 쪽 = 더 많이 학습된 쪽.
- `app.py`는 보통 자동머지됨 — 충돌 시에만 수동 병합(**절대 한쪽 통째 채택 금지**).

## 📌 2026-07-29 작업 기록 (커밋 24건 · 태그 `v-safe-2026-07-29a/b/c`)

### 확정된 성과
- **회수율 정정 67.6%** (경마 65.2 / 경륜 68.4). ⚠ 세션 중 보고했던 「전체 98.7% · 경마 141.7%」는 **오류**였다 —
  추천 조합 0개(투자 0)인데 `hit=True`로 잡힌 33건의 배당이 회수에만 더해져 637,300원이 부풀려졌다.
  집계는 `nq>0`/`nt>0`인 종류만 회수 인정하도록 정정.
- **손실 전액이 삼복승** — 삼복승 −698,700원 ≈ 전체 손익 −696,600원. **복승만 걸었다면 전체 99.7%**(경륜 단독 105.7%).
- **확정배당 869건 백필**(`/api/audit/results` 23일치) + 삼복승 배당 폴백 배선 + `payouts_raw` 원본 보존.
- **베팅 규칙 v2**(`BET_RULES`·`_apply_bet_rules`) — 시장별 분리 · **단방 금지**(`minQ=2`) · **관망 금지**(`alwaysRecommend`) ·
  추천도 등급(`_bet_grade`) · 배당 되돌림 필터 · **재급락 면제**.
- **단승 수집 복구** — oddspark `betType=1`(単勝・複勝 표). 문서상 v2.1.17 재도입은 확장 경로만이었고
  주력 oddspark 경로엔 매핑 자체가 없어 **전수 0건**이었다. 실전 검증 완료(소노다 6R 12두).
- **저배당 강축 전환** — `profitTier=low`의 "복승 0개+삼복승 집중"을 반전. 실측 1.5배 미만 구간 **적중 70.6%·회수 142.2%**(전 구간 최고).
- **관측 개통** — `signal_quality_full`·`betGrade`·`shadowWouldBet` 저장. *동작은 하는데 기록이 없어 검증 불가*가 반복 병목이었다.
- **성능** — `/api/day/races` **6.9초 → 0.23초**(`_snapshot_index` TTL 60초 + mtime 캐시 3종).

### 리플레이로 검증된 배당 유형별 실측 (395경주·11,010조합·전체 평균 적중률 3.6%)
| 유형 | 조합수 | 적중률 | 중앙배당 | 판정 |
|---|---|---|---|---|
| 진성급락(반등 없음) | 1,321 | **8.6%** | 4.7배 | 최강(평균의 2.4배) |
| 횡보 | 2,272 | 5.4% | 6.4배 | — |
| 급등 20~50% | 2,457 | 1.7% | 25.8배 | ⚠ 평균배당 88.5배·기대값 1.57은 **2,375배 1건의 착시**(중앙값 기대 0.44) |
| **페이크급락**(급락 후 30%+ 반등) | 560 | **1.2%** | 5.7배 | 역신호(진성의 1/7) |
| **급등 50%+** | 2,792 | **0.6%** | 24.9배 | 최악(1/14) |
- ⚠ **진성급락을 추천 순위 1순위로 올리는 것은 기각**(리플레이: 회수율 100.2% → **91.4%** 악화).
  진성급락은 *후보를 찾는* 데 유용하고 *이미 뽑힌 것의 순서*엔 새 정보가 없다.
- **재급락**(급락→반등→재급락, `strong_signals` type 4)은 별개 신호로 **실측 68~69%(표본 204~206)**.
  페이크급락 필터가 이걸 죽일 뻔해 면제 규칙 추가(소노다 7R 2+5 → 실제 적중).

## 🔴 2026-07-29 발견 · 미해결 (다음 세션 최우선)

> **공통 뿌리: "받은 데이터를 믿을 수 있는가."** 아래 넷이 해결되지 않으면 그 위의 모든 분석·회수율이 흔들린다.
>
> **2026-07-29 코드 재확인 결과(정정 포함)** — 아래 4건 모두 **미해결 확정**:
> · **1번 南関東**: 배당(odds)만 미수집. **결과 백필은 이미 지원**됨(`_JP_BABA_CODE`에 浦和18·船橋19·大井20·川崎21, app.py:23184).
> · **2번 수집 경합**: `_multi_bg_loop`(30376) `max_workers=6` **그대로**(30430). 루프 30초 주기·경주당 timeout 40초
>   → 창 안 60경주면 한 바퀴 최대 400초. 밀림 구조 확정.
> · **3번 조합 딥**: `_arity_guard`(1932)는 **스냅샷 단독 검증**(N두↔nC2 비율 40~150%)이라 7두→21조합이면 ratio 100%로 **통과**.
>   **직전 스냅샷 대비 시계열 게이트는 미구현**.
> · **3-B번 열 밀림**: `sanity`/`off_by_one` 계열 **검색 0건 — 완전 미구현**.

### 1. 南関東 4장 서버 수집 불가 — **가장 큰 데이터 손실** 🔴
- **카와사키·오이·후나바시·우라와**가 서버(oddspark) 수집 대상에서 통째로 빠진다.
- ⚠ **원인 정정**: 세션 중 "야간 경주 스케줄 누락 / 갱신 주기 문제"로 두 번 오진했으나 **둘 다 아니다**.
  발주 2분 전(18:28)에도 `개최 목록에 없음`이고, oddspark 홈의 `RaceList.do` 링크에 애초에 없다.
  **南関東 4장은 oddspark가 커버하지 않는다**(별도 소스 `nankankeiba.com` 필요) — 버그가 아니라 **소스 한계**.
- 현재는 확장(사설·`src=private`) 경로에만 의존 → 30초 주기라 마감 직전 변화를 놓친다.
- 영향: 전체경주 화면 "오늘 40경주"·회수율 집계에서 이 4장이 제외돼 실제 분석(오늘 68경주)과 어긋난다.
- ✅ **원인 확정(2026-07-29 · 직접 fetch 대조)** — **소스 커버리지 한계가 맞다. 파서 문제가 아니다.**
  개최일(오늘 카와사키 6경주 실제 진행·전부 `src=private`로만 수집됨)에 oddspark를 직접 받아 대조한 결과:
  | 확인 대상 | 결과 |
  |---|---|
  | oddspark 홈의 `RaceList.do?raceDy=&opTrackCd=&sponsorCd=` 링크 | **나고야(43/33)·소노다(51/26) 2건뿐** |
  | 南関東 4장 표기 | `<div class="place_area">南関東` 아래 **경마장 소개 링크만**(`/keiba/racetrack/31~34/`) |
  | 카와사키 소개페이지(`/keiba/racetrack/34/`)의 `RaceList.do?raceDy=` 링크 | **0개** |
  → 즉 oddspark에 南関東은 **경마장 소개 페이지로만 존재하고 발매(배당·출주표) 정보가 없다**.
  ⛔ **"oddspark 내 南関東만 DOM 구조가 달라 파서 분기가 필요하다"는 가설은 기각** — 파싱 이전에
    **개최 데이터 자체가 없다**. (이 항목은 이미 두 번 오진된 이력이 있으니 추측으로 판단하지 말 것.)
  ⚠ 실측 부작용: 확장 단독(30초 주기) 의존이라 오늘 카와사키 스냅샷이 **1·2·8·9개, 6경주는 0개**로
    마감 직전 변화를 구조적으로 놓친다.
  ⚠ 인코딩 함정: oddspark는 **UTF-8**이다(`Content-Type: text/html;charset=utf-8`). euc-jp로 디코드하면
    한자가 깨져 "해당 경마장 없음"이라는 **가짜 결론**이 난다 — 검증 중 실제로 한 번 오판했다.
- ✅ **해결 완료(2026-07-29)** — **nankankeiba가 아니라 `keiba.go.jp`(NAR 공식)로 배선**했다.
  이미 결과 백필·전적에 쓰던 사이트라 **새 소스를 붙인 게 아니라 쓰던 소스의 범위를 넓힌 것**이고,
  `_JP_BABA_CODE`와 같은 `k_babaCode` 체계를 그대로 쓴다(浦和18·船橋19·大井20·川崎21).
  | 항목 | 내용 |
  |---|---|
  | 복승(馬連複) | `OddsUmLenFuku` + `odds_flg=4`(馬番順) |
  | 쌍승(馬連単) | `OddsUmLenTan` (방향 보존) |
  | 단승 | `OddsTanFuku` |
  | **삼복승(三連複)** | `Odds3LenFuku` — `_nar_parse_trio`(앵커 기반 별도 파서) |
  | **전적** | `DebaTable` — `_nar_parse_deba` + `_nar_autocollect_form` |
  | 스케줄 | `RaceList?k_raceDate=&k_babaCode=` — 경주번호·**발주시각**·두수 제공 |
  - 신규 함수: `_nar_fetch`·`_nar_odds_url`·`_nar_parse_pair_odds`·`_nar_parse_win`·`_nar_race_list`·`_nar_schedule`
    + `_multi_schedule_fetch` 병합 + `_multi_collect_one`에 `narBaba` 분기(경륜·oddspark 분기보다 앞).
  - ⭐ **구조적 이점**: 배당표가 `축마 1두 = 1 table, 행 = (상대마번, 배당)` 형태라 **열(colspan) 추적이 없다**
    → oddspark 그리드 파서에서 반복된 **열 밀림(3-B)·조합 누락(4번)이 원리적으로 발생하지 않는다.**
  - 검증: 실제 응답 단위검증 **12/12 통과**(카와사키 9R — 복승 28=8C2 · 쌍승 56=8P2 · 단승 8두 일치 ·
    `_keiba_odds_live` 게이트 통과 · 빈/깨진 입력 방어) + 라이브 스케줄 편입 확인(트랙 9→10곳·카와사키 12경주)
    + 전 트랙 분기 라우팅 검증(경마 2·南関東 1·경륜 7 전부 필수필드 보유).
  - ✅ **삼복승 배선 완료** — `_nar_parse_trio`. ⚠ 표 구조가 복승/쌍승과 **다르다**: 1축(a)이
    `<a id="Na">` **앵커로 섹션을 나누고** 그 안의 `<table>` 하나가 2축(b), 행이 (3축 c, 배당)이다.
    즉 a 는 표 안에 없어 **앵커 위치로만** 알 수 있어 표 단위 파서(`_nar_parse_pair_odds`)로는 a 를 잃는다.
    실배당을 얻으므로 `_trio_est` 추정배당보다 우선(추정 로직은 폴백으로 보존). 검증 **56=8C3·누락 0**.
  - ✅ **전적 배선 완료** — `_nar_parse_deba`(DebaTable) + `_nar_autocollect_form`.
    점수 계산은 **기존 `_keiba_build_form` 을 그대로 재사용**해 다른 수집 경로와 동일 스키마·동일 공식
    (`_keiba_starter_store_row` 키 일치 검증). `starters_store` 에 `source="keiba_nar"` 로 저장·경주당 1회.
    추출: 마번·마명·기수·**최근5착순·두수·코너통과·상3F·과거거리·마체중** + 이번 경주 거리.
    E2E 검증(카와사키 9R): 8두 전원 총점 산출 · 각질(선행형/추격형/평지형) · 등급 A~D · 최근착순 보유.
  - ⏳ **라이브 수집 검증 잔여**: 배선 완료 시점에 카와사키 최종 경주(20:50)가 끝나 수집 창(발주 10분전~2분후)
    밖이었다. **다음 南関東 개최일에 `data/odds_history/`의 카와사키·오이·후나바시·우라와 스냅샷 수와
    `src` 값**(`oddspark`로 기록됨)을 확인할 것 — 종전 확장 단독 1·2개에서 20개 내외로 늘어야 정상.

### 2. 수집 공백 — 스케줄에 있는데도 0~1개 🟠 (원인 가설 교체 · 경보 개통)
> **⚠ 2026-07-29 정정: "수집 경합(병렬 부족)" 진단은 실측으로 기각됐다.**
> `today_schedule` 실측 — 오늘 96경주(경마24·경륜72)인데 **수집 창(발주 10분전~2분후) 안 동시 경주는
> 최대 4개**(경마2·경륜2)다. `max_workers=6`으로 이미 충분하며 병렬 부족은 원인이 **아니다**.
> **동시성을 올리지 말 것** — 밀림은 그대로인 채 oddspark WAF 차단 위험만 커진다(경주당 fetch 3~4회라
> 사이클당 요청이 배로 는다). Gemini도 같은 리스크를 지적했고 실측이 이를 뒷받침한다.
> · **피해 규모 실측**: 오늘 85경주 중 **26경주가 스냅샷 3개 미만 · 그중 15경주는 0개**
>   (나고야 2·4·5·7·8R · 소노다 3·5·8·10·11R · 히로시마 3·4R · 카와사키 6R · 와카야마 5R · 히라츠카 7R).
> · **유력 가설(다음 세션 검증)**: **[사설 우선] 게이트 오작동**. `_multi_collect_one`이 triple_store의
>   최근 src가 'oddspark 아님' + 200초 내면 oddspark 저장을 생략하는데(확장과의 배당 진동 방지),
>   확장이 실제로 그 경주를 수집하지 않으면 **양쪽 다 저장 안 돼 스냅샷 0**이 된다.
>   실제 `/api/ingest/rejects`에 `사유=사설 우선(oddspark 백업 생략)` + `mappingSuspect=true`가 남아 있고,
>   그 `prevSrc`는 정규화되지 않은 **원시 URL**(`https://ks1.dke-d11diw.site/...`)이라
>   `"oddspark" not in src` 판정을 그대로 통과한다.
>   → ✅ **조치 완료(2026-07-29)**: `_multi_collect_one`의 이력 게이트에 **'그 경주 이력이 최근(≤200초)
>     갱신 중인가'** 조건 추가 — 사설이 활성이어도 **이력이 비어 있으면 oddspark가 공백을 메운다**(`🩹 이력 공백 보충`).
>     사설이 정상 기록 중이면 판정 불변이라 기존 '사설 우선' 원칙·이중 기록 방지는 그대로다.
>   → ✅ **관측 개통(핵심)**: 이 생략은 **지금까지 아무 로그도 남기지 않았다**(`/api/ingest/rejects`는
>     `triple_ingest` 거부만 커버). 공백 15경주가 거부 로그에 하나도 없던 이유가 이것이다.
>     이제 생략·공백보충·**이력 기록 예외**(app.py:2393, 종전엔 콘솔 print로만 남아 서버 창을 놓치면 소실)
>     세 경로 모두 거부 로그에 기록된다.
> · ✅ **경보 작동 확인(2026-07-29 22:40)**: `data/collect_gaps/2026-07-29.json`에 **2건 기록**
>   (나고야 11경주·12경주 — 스냅샷 0). `_snapshot_shortage_check`가 실제로 '조용한 실패'를 잡아냈다.
>   단 `/api/ingest/rejects`의 신규 사유(`사설 우선(이력 기록 생략)`·`사설 우선 해제(이력 공백)`)는
>   **0건** — 조치 후 수집 창에 든 경주가 없어 해당 경로가 아직 미발동이다(다음 개최일 재확인).
> · ⏳ **라이브 검증 잔여**: 조치 시점에 당일 경주가 대부분 종료돼 게이트 발동을 실측하지 못했다.
>   **다음 개최일에 `/api/ingest/rejects`에서 `사설 우선(이력 기록 생략)`·`사설 우선 해제(이력 공백)`
>   발생 건수와 `data/collect_gaps/` 누적을 대조**해 실제로 공백이 줄었는지 확인할 것.
>   ⚠ 참고: 검증 중 본 `나고야 12경주`(스냅샷 0·analysis만 누적)는 **이미 종료된 경주**라 원인 확정 불가였다
>     — 종료 후에도 analysis 경로가 파일 mtime을 계속 갱신하므로 "갱신 중=수집 중"으로 오해하지 말 것.
>   (참고: src 원시 URL은 전체 16,931틱 중 9건뿐이고 연속 틱 src 변경도 0.9%라 **전면적 문제는 아니다** —
>    이 게이트 경로에서만 문제가 된다.)
> · ✅ **개통**: `_snapshot_shortage_check` — 발주 3~15분 경과 경주의 스냅샷이 3개 미만이면 콘솔 경보 +
>   `data/collect_gaps/<날짜>.json` 누적(경주당 1회). **완전 읽기 전용**(수집·추천·학습 무개입).
>   *"동작은 하는데 기록이 없어 검증 불가"를 반복하지 않기 위한 가시화가 먼저다.*

### 2-old. (기각된 원 진단 · 기록 보존) 수집 경합 — 스케줄에 있는데도 0~1개
- 오늘 나고야: 1R 1개 · 2R 0개 · 3R 1개 · 4R 1개 · 5R 0개 · **6R만 19개**.
- 스케줄은 정상(나고야 12경주 `postEpoch` 전부 보유) → **알고도 못 받았다**.
- 추정: 경마 2 + 경륜 6~7 트랙 동시 개최 시 `_multi_bg_loop` 동시 6 병렬로는 한 바퀴가 길어져 밀림
  (코드 주석에 "경륜 67경주 동시 개최 시 누락" 전례 기록 있음).
- **직접 피해**: 나고야 4R이 **마감 3분 전 단 1회** 스냅샷으로 추천 확정 → 9번(1착) 탈락.
- **할 일**: 병렬 수 상향 or 경마 우선순위 부여 + **스냅샷 3개 미만 경주 자동 경보**.

### 3. 조합 수 딥(dip) — 오염 스냅샷이 추천을 뒤집는다 🔴
- 실측: 스냅샷 6,730건 중 **30회**(0.4%) · **9경주**. `src` 무관(private 13 · oddspark 17).
- 패턴: 66/55/78조합 → **21조합**으로 급감 후 **다음 틱에 정상 복귀**("생겼다 사라진다" — 대표 체감과 일치).
- 딥 틱에서는 남은 조합이 폭락·폭등으로 계산돼 추천이 통째로 갈아엎어진다.
  실사고: **나고야 4R** — `3+6` 227.7→4.2배(−98.6%)·`3+9` 23.3→195.7배(+740%)로 **9번(5+9·3+9) 탈락, 결과 9-4-8**.
  실제 3+6은 **252배**(대표 확인) → 4.2배가 오염값이었다.
- 기존 방어(`_baseline_reset_needed`·`_next_race_surge`·95%+급락 제외)가 **전부 통과**.
  이유: 이들은 "직전 대비 **공통** 조합의 60%+"를 보는데, 공통 조합 자체가 21개로 줄면 판정이 무력화된다.
- ⚠ **21조합을 무조건 오류로 보면 안 된다** — 취소마(`取消`) 발생 시 8두→7두면 21조합이 **정상값**이다
  (카와사키 8R 실화면 확인). 정상 축소와 파싱 실패를 **구분**해야 한다.
- ✅ **완료(2026-07-29)**: `_combo_count_dip`(직전 대비 조합 수 <70% · 직전 10조합+ 일 때만) 단독 게이트 추가.
  **거부가 아니라 1틱 보류** — 배당은 그대로 저장하고 그 틱의 급락 계산만 생략한다.
  취소마로 인한 정상 축소면 딥이 2틱 이상 지속돼 다음 틱부터 자연히 계산이 재개되므로,
  "정상 축소 vs 파싱 실패" 판별을 사전에 하지 않아도 안전하다(실측: 딥 112건 중 43건만 다음 틱 복귀).
  실측 112건/23,230틱(0.48%). 스냅샷에 `odds_suspect` 사유 기록 + 타임라인·신호시퀀스에서 제외 표기.

### 3-B. 배당 열 밀림(off-by-one) — 조합 수는 정상인데 **값이 인접 조합으로 매핑** 🔴
- **실시간 포착(7/29 18:37 나고야 9R, 발주 8분 전, `src=private`)**:
  ```
  18:36:51  55조합  3+5=12.9  5+11=69.7  5+7=16.5
  18:37:15  55조합  3+5=12.6  5+11= 7.3  5+7=35.0   ← 24초 뒤
  실제 배당판:      3+5=12.4  5+11=55.9  5+7=15.7
  ```
- **조합 수는 55로 동일** → 3번(조합 딥) 게이트로는 **못 잡는다**. 판정 축이 다르다.
- 대조 결과 **한 칸 밀림**이 확인된다: 시스템 `5+7=35.0` = 화면의 **`5+8`** 값.
  대표 관찰도 동일(**`5+11`을 `5+12`로 착각**). 값이 뒤섞인 게 아니라 **열 인덱스가 어긋난 것**.
- 결과: 가짜 `-89.5%` 급락 2건(5번·11번)이 만들어져 그대로 추천 근거가 됐다.
  같은 틱에 `-89.5%`와 `+112%`가 공존 — 정상 시장에서 24초 만에 나올 수 없는 조합이다.
- **3-A(조합 수 딥)와 뿌리가 같을 가능성**: 둘 다 열 추적(`x`) 문제.
  단 이번은 `src=private`(확장) → **확장 파서(`content.js`)에도 같은 결함이 있는지** 확인 필요.
- ⚠ 사설 배당판 화면 하단에 *"메인배당 오류시 영상속 배당버튼 및 2차배당을 이용해 주십시오"* 안내가 있다.
  **소스 자체가 간헐 오류를 인정**하므로, 파서 수정과 별개로 **수신값 검증(sanity check)** 이 필요하다.
- ✅ **완료(2026-07-29·방어 계층)**: `_column_shift_suspect` — 현재 값이 **직전의 인접 조합 값**과 오차 6% 내로
  일치하는 조합이 5개 이상이고 그것이 '정상 일치'의 절반을 넘으면 밀림으로 판정 → 그 틱 급락 계산 보류.
  조합 수가 같아도 잡히므로 3-A(조합 딥) 게이트와 **판정 축이 다르다**.
  실측 **33건/22,257틱(0.15%)** — src 분포는 `oddspark` 16 · 구데이터(src 미기록) 17 · **`private` 0건**.
  ⚠ 즉 확장 파서(`content.js`)의 결함이라는 가설은 **이 데이터로는 지지되지 않는다**(단 7/29 18:37
  나고야 9R 실시간 관찰은 `src=private`이었으므로 표본 밖 케이스일 수 있다 — 확장 파서 대조는 여전히 잔여).
- **잔여**: ⓐ서버·확장 양쪽 파서의 열 정합 검증(같은 경주를 두 경로로 받아 대조)
  ⓑ임시 방어 — **동일 틱에 −80%↓와 +100%↑가 동시 다수 발생하면 그 스냅샷을 기준값에서 제외**.

### 4. 복승 파서 조합 누락 3.2% 🟠
- oddspark 5,001스냅샷 중 159건(3.2%) · private 2.9%. **특정 마번 1개의 전체 행이 통째로 누락**.
  (12두 66→55 = 10번 전체 / 11두 55→45 = 2번 전체 — 수치가 정확히 일치)
- 대상: `_oddspark_grid_combos`(`app.py:21244`) — colspan 기반 열(`x`) 추적.
  의심: `colmap.get(x-1)`과 `pend[0]==x-1`의 열 정합. colspan 2/1 혼재 시 `heads=None`이 되는 경로.
  코드 주석의 *"첫 배당은 전용 행이 없는 최소 차번(=implicit)"* 복원 실패로 보인다.
- **할 일**: 개최 중 경주 `betType=6` HTML의 `<th colspan>` 배치를 덤프 → 나고야(12두) 기준 **66조합** 복원 확인.

### 그 밖에 남은 것
- **재급락 신호 단독 리플레이** — 표본 204~206건. 순위 반영 여부를 데이터로 판정(진성급락은 이미 기각).
- **마감 직전 초대형 급락(90%+) 신뢰도 재검증** — 나고야 4R·소노다 7R 연속으로 함정이었다.
- **막판 추천 교체 리플레이** — 히스테리시스가 있는데도 두 경주 다 마지막 틱에 뒤집혔다.
- 시점별 × 유형별 교차 리플레이(T-10분 급락 vs T-1분 급락) · tier 경계 1.5배 재조정 ·
  섀도우 삼복승 50경주 도달 시 재평가(`GET /api/shadow/trifecta`) · `odds_history` JSON 손상 1건.

### ⚠ 이 세션에서 배운 작업 원칙
- **리플레이가 세 번 판단을 뒤집었다** — 급등 20~50% 흑자(착시) · 페이크급락 일괄 제외(재급락 죽임) ·
  진성급락 순위 우대(회수율 9%p 악화). **규칙 변경 전 반드시 리플레이를 통과시킬 것.**
- **관측 없이 판단 없다** — `signalQuality`·`betGrade`·섀도우 성적 모두 "동작은 하는데 기록이 없어" 검증 불가였다.
  새 로직은 **판단 근거를 함께 저장**할 것.
- `an` 최상위 필드는 `analysis_log`에 저장되지 않는다 → **`corePicks`에 넣어야 남는다.**
- 편집 중 `except` 누락으로 서버가 한 번 내려갔다. **`python -c "import ast; ast.parse(...)"`를 먼저 돌릴 것.**

## 📏 저배당 성적 산출 — **방법론 확정(2026-07-29)** · 혼동 재발 방지

> 같은 "1.5배 미만" 수치가 **142.2%** 와 **26.2%** 로 정반대로 나온 적이 있다. 계산 오류가 아니라
> **서로 다른 지표를 같은 이름으로 부른 것**이었다. 앞으로는 반드시 아래 A/B를 명시해서 쓴다.

| | **A안 (실전 기준·기본값)** | B안 (참고용) |
|---|---|---|
| 구간 분류 | **복승 1순위 추천 조합의 배당** | `profitTier.minOdds`(시장 최저복승) |
| 투자 | **1조합에 stake 전액** | 총투자 동일(추천 N조합 분할) |
| 적중 | **1순위 조합이 top2와 일치** | 추천 전체 중 하나라도 적중 |
| 모집단 | 결과 보유 전체(1,287경주) | `profitTier` 보유분(677경주) |

- ✅ **A안이 실제 베팅과 일치한다** — 현행 `tier=low` 정책이 **복승 1순위만 메인 유지**(2순위 이하 참고 강등)
  이므로 실제로 **1조합에 베팅**한다. 따라서 성적 판단의 기본 지표는 A안이다.
- ✅ **어제 산출(커밋 `db876d36`)은 재현됐다**: A안 `~1.5배` **57경주·66.7%·132.1%**
  ↔ 어제 기록 51경주·70.6%·142.2%(그 사이 경주가 더 쌓여 n 증가). **어제 수치는 정확했다.**
- ⚠ **B안(26.2%)은 어제와 비교할 대상이 아니었다** — 대상 집합이 애초에 다르다
  (A안 `~1.5배` 57경주 ↔ B안 8경주 · **교집합 5경주**). 지표를 혼동한 대조였다.
  · 차이의 근원: A안의 1순위 57건 중 **52건이 `quinellaRef`(강등분)에서 복원**된 값이다.
    당시 `tier=low`가 복승을 전부 강등(`fq=[]`)했기 때문 — 즉 **"1순위를 남겼다면" 반사실 시뮬레이션**이고,
    그것이 바로 정책 반전의 근거였다(정책을 바꾼 지금은 실제로 표시된다).

### 🔴 그 과정에서 드러난 진짜 문제 — **근거 구간과 적용 구간이 어긋난다**
| 경륜류 (A안·1조합) | n | 적중 | 회수율 |
|---|---|---|---|
| `~1.5배` **(근거 구간)** | 54 | 70.4% | **139.4%** |
| `1.5~1.8배` **(적용되나 근거 밖)** | 54 | 31.5% | **49.4%** |
| `~1.8배` = **tier=low 전체(실제 적용)** | 108 | 50.9% | **94.4%** ← 손익분기 미달 |
| `1.8~2.0배`(tier=mid) | 25 | 36.0% | 95.6% |

| 일본경마 (경계 2.0) | n | 적중 | 회수율 |
|---|---|---|---|
| `~1.5배` | **1** | 0% | 0% |
| `1.5~2.0배` | **11** | 81.8% | 150.0% |
| `~2.0배` = tier=low 전체 | **12** | 75.0% | **137.5%** |

- **경륜은 139.4% 구간(<1.5) 근거로 <1.8 전체에 적용** → 사이 구간(1.5~1.8·n=54)이 **49.4%**라
  합산 **94.4%로 손익분기 미달**이다. 어제 커밋도 *"진짜 강축 경계는 2배가 아니라 1.5배 · 경계 재조정은
  별도 검증 후"* 라고 **위험을 이미 명시**했고, 이번에 데이터로 확증됐다.
- **반전 이후 실측이 이를 뒷받침한다**: 7/29 `tier=low` 경륜 **21경주 적중 28.6%·회수 43.3%**
  (1순위 배당 **중앙 1.60** = 근거 밖 구간). ⚠ n=21로 얇다.
- ⚠ **종목별로 방향이 정반대다** — 일본경마는 `~2.0배` 전체가 **137.5%(n=12)** 로 흑자다.
  경계 조정은 **반드시 종목별로** 해야 한다. 단 n=12는 매우 얇아 단독 근거로 쓰기 어렵다.
- **결론**: 반전 자체(<1.5배 강축 유지)는 **옳다 — 되돌리지 않는다.** 문제는 **경계**다.

### 🧪 1.5~1.8배 구간 대안 4종 리플레이 (2026-07-30 · 경륜 55경주 · 일본경마 무수정)
| 안 | n | 적중률 | 회수율 | 판정 |
|---|---|---|---|---|
| 현행 · 복승 1순위 1조합 | 55 | 32.7% | **51.5%** | 기준선 |
| B · 복승0 + 삼복승 상위2 | 55 | 14.5% | **31.6%** | ⛔ −19.9%p **기각** |
| C · 최소 1조합 보장(등급 최하) | 55 | 32.7% | **51.5%** | 베팅 대상 동일 — **표기 차이뿐** |
| D · 진성급락 경주만 | **21** | 33.3% | **51.9%** | 개선 아님 |
- **D안 기각**: 대조군(비진성급락 22경주)이 **57.3%로 오히려 더 높다**. ⚠ 표본 21건(<30) 명시.
  어제 "진성급락 9.1%"는 **11,010 조합 전체** 기준의 *후보 탐색* 지표라, 이미 저배당 1순위로 좁혀진
  구간에서는 추가 정보가 없다(진성급락 정렬 우대가 기각된 것과 같은 패턴).
- **4안 모두 손익분기 미달**(최고 51.9%) → 문제는 "무엇을 거느냐"가 아니라 **이 구간 자체의 수익성**.

### 🚫 경계 1.8→1.5 변경 — **보류 확정(2026-07-30)**
> ⚠ **v1 시뮬은 전제가 틀렸다(정정)**: 정액 컷(2.5배 미만 제외)에 면제가 없다고 가정했으나,
> `_lowodds_exempt`(app.py:8682) ⓐ가 **시장 최저배당 조합을 면제**한다. 1.5~1.8배 1순위는 곧 시장 최저
> 조합이라 **mid 로 가도 메인에 남는다.** 즉 경계 변경은 "1순위를 교체"가 아니라 **"1순위 + 2.5배+ 추가"**다.
> v1이 보고한 **137.9%는 실전에서 나올 수 없는 수치**다.

| 안 | n | 적중률 | 회수율 | 1건제외 | 3건제외 |
|---|---|---|---|---|---|
| 현행(경계1.8·low) | 56 | 33.9% | **53.6%** | 50.4% | 44.3% |
| 변경(경계1.5·mid) EV 미적용 | 56 | 57.1% | 100.5% | 70.6% | 51.9% |
| **변경(경계1.5·mid) EV 필터 적용(v2)** | 56 | 42.9% | **85.6%** | 55.7% | **33.6%** |

- 🔴 **EV 필터가 고배당 후보를 대부분 막는다**: 2.5배+ 후보 **105조합 → 강등 71(67.6%)**
  (강등 배당대: 5~10배 39 · 15~20배 11 · 20~25배 9 · 10~15배 6). EV≥1.0 통과는 25조합뿐.
- **보류 사유**: 이득이 +84.4%p → **+32.0%p** 로 축소되고, **상위 3건 제외 시 33.6%로 현행(44.3%)보다 나쁘다**
  (극단값 의존이 현행보다 심하다). 여전히 손익분기 미달이다.
  ⚠ 이 v2도 낙관적 상한이다 — 강급락 3배+ 타겟과 `_lowodds_exempt` ⓑⓒⓓ는 미적용(적용 시 메인이 더 준다).
- ⚠ **적중 배당 중앙값 정정**: v1의 "1.6→6.6배 개선"은 전제 오류 산물이다. v2에서는 **1.6배로 현행과 동일** —
  1순위가 메인에 남아 저배당 적중이 그대로 잡히기 때문이다.
- 💡 **진짜 병목은 경계가 아니라 면제 구조로 보인다** — 시장최저 조합이 정액 컷(`_lowodds_exempt` ⓐ)과
  EV 필터(`_mkt2`) **양쪽에서 무조건 면제**되는 한, 저배당 1순위는 어떤 tier 에서도 생존한다.

### 🔬 EV 강등 조합 실측 감사 (2026-07-30 · 조합 단위 · 경륜 56경주의 2.5배+ 후보 105조합)
| 분류 | n | 적중 | 적중률 | 회수율 | 1건제외 | 3건제외 |
|---|---|---|---|---|---|---|
| **EV 강등** | **71** | 10 | 14.1% | **108.6%** | **80.0%** | **51.7%** |
| EV 통과 | 25 | 1 | 4.0% | 134.0% | 0.0% | 0.0% |
| 면제토큰 생존 | 9 | 2 | 22.2% | 115.6% | 42.2% | 0.0% |

**배당대별(강등분)** — ⚠ **30건 이상은 `5~10배` 하나뿐이며, 나머지는 전부 판정 불가(n<30)**
| 배당대 | n | 적중 | 회수율 | 판정 |
|---|---|---|---|---|
| 2.5~5배 | 4 | 2 | 182.5% | ⚠판정불가 |
| **5~10배** | **39** | 5 | **89.2%** | **유일한 유효 표본 — 100% 미만** |
| 10~15배 | 6 | 1 | 186.7% | ⚠판정불가 |
| 15~20배 | 11 | 1 | 31.8% | ⚠판정불가 |
| 20~25배 | 9 | 1 | 225.6% | ⚠판정불가 |
| 25~30배 | 2 | 0 | 0.0% | ⚠판정불가 |

- **표면상 강등분 회수율 108.6%로 100% 초과** — 그러나 **극단값 의존이 결정적**이다:
  **1건 제외 시 80.0% · 3건 제외 시 51.7%**. 적중 10건의 배당이 20.3·11.2·8.9·7.9·6.7…로
  상위 소수가 회수를 지배한다. **이 프로젝트에서 네 번째로 반복된 극단값 착시 패턴이다.**
- **유효 표본(n≥30)은 `5~10배` 39조합뿐이고 89.2%로 100% 미만** → 그 구간에서는 **EV 필터가 옳다.**
- **판정: EV 필터 오차 구간을 특정하지 못했다.** 100% 초과 구간(2.5~5·10~15·20~25배)은 전부
  n=4~9로 **적중 1~2건이 만든 수치**라 근거로 쓸 수 없다. → **이 방향은 현 표본에서 종결.**
  ⚠ 단 "EV 필터가 완전히 옳다"는 결론도 아니다 — **판정 불가 구간이 남아 있다**는 뜻이다.
- 참고: EV **통과** 25조합도 134.0%지만 **1건 제외 시 0.0%**(적중 1건·33.5배)로, 통과·강등 양쪽 모두
  이 표본에서는 신뢰구간이 극단적으로 넓다.

### 🔍 이중 면제 구조 실태 (2026-07-30 파악 · 수정 없음)
| 면제 | 위치 | 도입 | 막으려던 문제 |
|---|---|---|---|
| `_lowodds_exempt` ⓐ | app.py:8682 (정액 컷) | 2026-07-24 · 커밋 `fc24ecab` | **카사마츠 4R `2+5`(2.1배·시장1위+유력마)** 가 "수익성" 명목으로 통째 강등되던 모순. EV 필터엔 면제가 있는데 정액 컷엔 없어 생긴 비대칭을 해소 |
| `_mkt2` | app.py:8718 (EV 필터) | 2026-07-20 · `③-1 EV 면제 확대` | **모리오카 5R `1+2`** — keyHorses 재정렬(전적 통합)로 *시장 최저 조합 ≠ keyHorses 1·2위* 가 되면 면제를 놓쳐 **메인이 전멸→⚠복원**되던 구멍 |
- **면제 없이 돌리면 깨지는 것**: ⓐ**메인 전멸 빈발** → `⚠기대값 미달(참고)` 복원 경로로 떨어져
  회원 화면에 "확신 없는 1개"만 남는다(관망 금지 원칙과 충돌). ⓑ**시장 1위·유력마 조합이 흔적 없이 사라져**
  회원이 보는 배당판과 추천이 어긋난다(= 사설 우선 원칙과 같은 취지의 신뢰 문제).
  ⓒ`fc24ecab` 리플레이 근거: 7/21·22·23 **악화 0 · 최대 +3적중 · ROI 동급~+27%p** — 면제 도입이 개선이었다.
- ⚠ **따라서 면제를 단순 제거하는 방향은 위험하다.** 종전 리플레이가 이미 "제거 상태(=면제 없음)"보다
  "면제 있음"이 낫다고 판정했다. 손댄다면 **면제 자체가 아니라 그 뒤의 배분(1순위 비중)** 이 대상이어야 한다.
  ⏸ 수정안은 다음 단계 · **승인 전 코드 변경 금지**.

### 🔁 재검토 트리거 (충족 전에는 경계·전략 재변경 금지)
- **1.5~1.8배 구간 표본 120경주 이상**(현재 **56**) **또는 적중 30건 이상**(현재 **24**)
- 도달 시 **out-of-sample 기간분할 검증**을 실행한다.
- ⛔ 지금 하지 말 것: ⓐ병행안(1순위+2.5배+ 1개) — 계산상 기대수익이 낮아진다 ·
  ⓑ기간분할 — 56경주를 반으로 쪼개면 표본이 더 나빠진다.
- 재현 스크립트 기준: A안(1순위 1조합 = 실전 일치) · 총투자 동일 · `_lowodds_exempt`/EV 면제 토큰 반영.

⏸ 모든 조치안은 **권대표 승인 대기**. ⚠ 추천 경로(`_profit_tier_of`·`_apply_profit_strategy`) 수정 금지.

## 💰 저배당 편중 — 회수율 정체의 근본 원인 (2026-07-29 실측·다음 세션 최우선)

> **회원 요구**: "저배당만 잡는 건 어떤 AI든 한다. **10~50배를 균등하게**, 추천할 땐 과감하게."
> 실측상 회원 요구 구간이 **기대값도 가장 높다**(20~50배 = 0.96, 전 구간 1위).

### 실태 (589경주 · 확정배당 기준)
- **추천 1순위의 71.6%가 시장 최저배당 조합**(시장 2위까지 합치면 80.3%). 구조적으로 저배당만 나온다.
- **추천의 72.8%가 10배 미만**. 회원이 원하는 10~50배는 **25.4%**뿐.

| 배당대 | 추천수 | 비율 | 적중률 | 기대값 |
|---|---|---|---|---|
| ~3배 | 220 | 15.3% | 30.0% | 0.75 |
| 3~5배 | 361 | 25.2% | 14.4% | 0.58 |
| 5~10배 | 463 | **32.3%** | 9.1% | 0.64 |
| 10~20배 | 301 | 21.0% | 3.7% | 0.56 |
| **20~50배** | 63 | **4.4%** | 3.2% | **0.96** ← 최고 |

- **적중 vs 놓친 배당 격차(중앙값)**: 일본 4.6배↔16.8배 · 경륜 3.5배↔10.4배 · 한국 6.9배↔24.7배(**약 3배**).
  경륜 적중의 41.1%가 3배 미만 / 일본 미적중의 45.1%가 20배 이상. **안전한 저배당만 잡고 고배당은 놓친다.**
  경륜은 적중률 40%로 나쁘지 않은데 **맞춰도 3.5배**라 2조합 손익분기 5.0배에 못 미쳐 구조적 적자.

### 원인 — ⓐ EV 필터의 고배당 암살 → ✅ **해결 완료 (2026-07-29 · 리플레이 검증 통과)**
- 강등 사유 1위: `기대값 N 미달(배당 N배 × 추정적중률 N%)` — **526건**(2위 37건과 격차 큼).
- ⚠ **원인 정정** — "추정적중률 1% = 3배 과소평가"는 **틀린 진단**이었다. `_ev_bands_update` 학습이 이미
  돌고 있어 15배+ 밴드는 실측 3.5%로 자동 교체돼 있었다(실측 3.2%와 일치). 진짜 원인은 둘이다:
  1. **EV≥1.0 절대 임계의 구조적 귀결** — 시장 환급률 75%에서 EV 1.0을 넘는 구간은 11.4~15배와
     28.6배 이상**뿐**이다. 사실상 전 구간이 강등되고 **면제 토큰**(시장 최저복승·유력마 1·2위·시장유력)
     보유 조합만 생존 → 그 토큰이 대부분 저배당 계열이라 1순위의 71.6%가 시장 최저배당이 됐다.
     (문서의 「면제 1045건 vs 강등 526건」이 정확히 이 구조다.)
  2. **선형보간이 학습 적용 시 무력화되는 버그** — `_ev_band_p` 주석은 "경계 절벽을 보간으로 연속화"라
     명시하나, 표본 50+ 밴드는 보간을 건너뛰고 **계단값을 즉시 return**(app.py:8426)해 절벽이 오히려
     커졌다(14.9배 EV 1.32 ↔ 15.0배 0.53 · **2.5배 낙차**). **회원이 원하는 20배가 절벽 바닥**이었다.
  - 요컨대 필터가 **거꾸로** 작동했다 — 실측 0적중인 30배+(0/43)는 열어두고, 실측 흑자인 15~30배는 잘랐다.
- **실측 재측정**(500경주·조합 2,068 · `data/analysis_log` + `race_results` 조인):
  | 배당대 | 강등분 n | 적중률 | 기대값 | 판정 |
  |---|---|---|---|---|
  | 8~12배 | 114 | 3.5% | 0.34 | 강등이 옳음 |
  | 12~15배 | 12 | 8.3% | 1.04 | 개방 |
  | 15~20배 | 44 | 6.8% | **1.21** | 개방 |
  | 20~30배 | 35 | 5.7% | **1.35** | 개방 |
  | 30~50배 | 5 | 0% | 0 | 유지(닫음) |
- ✅ **조치**: `_EV_RESCUE_LO/HI/MAX = 12.0/30.0/1` — EV 강등분 중 **12~30배 최저배당 1개를 메인 복원**
  (`_final_picks` EV 필터 직후, `evRescue=True` 표기·참고목록 중복 제거). **삭제 없이 되살리기만**.
- ✅ **리플레이 검증**(총투자 동일·경주당 stake 고정 분할):
  전체 82.6%→**88.2%** · 전반부 72.3%→72.9% · **후반부 OOS 92.9%→103.5%(흑자 전환)** ·
  종목별 전부 개선(일본 69.6→73.2 · 경륜 91.0→97.1 · 한국 73.1→79.2) · 10~50배 비율 23.8%→28.5%.
  경계 민감도 낮음(15~30배 87.5%로 고원). ⚠ **8~30배(83.8%)·12~50배(86.0%)는 열수록 악화 — 넓히지 말 것.**
- 검증 스크립트 재현: `data/analysis_log`의 `corePicks.quinellaRef`(refReason에 `기대값` 포함=EV강등분) +
  `corePicks.finalQuinellas` + `data/race_results`의 `payouts.quinella` 를 조인해 재생.

### 원인 — ⓑ 축을 '시장 최저배당'에서 잡는다
- 추천 근거 문구 상위: `신호 근거로 기대값 면제`(1045) · **`시장 저배당 보완`(342)** · **`시장 최저복승`(244)** ·
  `시장 저배당+신호`(116). 배당판을 그대로 따라가는 구조.

### 다음 단계 — 전개 기반 고배당 포착 (Gemini 설계 · ⚠ 전제 미충족)
> "배당이 이상해서 잡는다"가 아니라 **"이 판은 선행 경합으로 앞이 무너져 추입이 온다"** 를 먼저 예측하는 방향.
> 방향은 타당하나 **지금 당장 구현 불가** — 아래 전제부터 확인할 것.

- ✅ **페이스 예측은 있다**: `corePicks.paceAnalysis.pace` — 400건 중 **242건(60%)** 보유(빠른109/느린68/보통65).
- ⚠ **정정(2026-07-29 확인)** — "말별 각질 데이터 전무(0건)"는 **틀렸다**. 실제로는 각질 수집·학습이 이미 가동 중:
  `_gait_of`(app.py:26494 · `gait`/`styleType`/`runningStyle` 순 조회)·`_running_style`(26465)·
  `paceAnalysis`·`jpFlowSim`(일본지방)·`kraFlowSim`(한국) + **`data/pace_analysis/` 318경주 축적**.
  `data/pace_stats.json`에 페이스별 입상 각질 분포 산출 완료 — **빠른**: 선행56.7·추입37.7 / **느린**: 추입18.2·자유75.4 /
  **보통**: 추입61.6·선행37.7 (`_pace_stats_recompute`, app.py:8808).
  → **할 일 2(각질 수집)는 이미 충족**. 다음은 **할 일 3(전개 리플레이 검증)을 바로 착수**하면 된다.

### 📌 [적중왕전개] Phase 0 — 각질 데이터 누적 (2026-07-29)
- ✅ **일본경마 페이스 보유율 23.6% — 버그 아님(원인 규명 완료).**
  `corePicks`/`paceAnalysis` **저장이 2026-07-18에 도입**됐고, 그 이전 로그(7/06~7/17 · 약 380건)에는
  필드 자체가 없다. 이들이 분모에 남아 누적 평균을 끌어내린 것이다.
  **날짜별 실측**: 7/20 **1.9%** → 7/26 70.4% → 7/27 **100%** → 7/28 87.5% → 7/29 90.3%.
  **최근 4일 85.4%(140/164) — 목표 70% 이미 초과 달성.** 수집 파이프라인 수정 불필요.
  - 잔여 9건(최근 4일 corePicks有·페이스無)의 내역: ⓐ**중앙(JRA)** 나카교·삿포로 — **전적표가 없어
    구조적으로 각질 산출 불가**(설계상 정상, 통계에서 분리 필요) ⓑ**南関東·몬베츠** 카와사키 6·8R·
    몬베츠 8·11R — 전적 0두 → **오늘 `_nar_autocollect_form` 배선으로 해소 예정**(다음 개최일 확인).
  - ✅ **별건 — 경륜장 '일본경마' 오분류 소급 정정 완료(2026-07-29 · `tools/fix_keirin_sport_tag.py`)**
    ⚠ **실제 규모는 213건**이었다(앞서 보고한 110건은 4개 지명만 표본 확인한 값 — **정정**).
    전수: 기후32·사세보30·기시와다24·마쓰도24·오다와라16·구마모토15·우쓰노미야13·다마노12·마쓰사카11·
    세이부엔10·시즈오카9·히로시마5·야히코5·静岡2·도야마2·도요하시1·케이오카쿠1·와카야마1 (7/07~7/24).
    · 지명 목록은 **`app.py`의 `_KEIRIN_ONLY_RE`를 런타임 파싱해 재사용**한다(두 곳에 두지 않음).
      이 정규식은 설계상 **경륜 전용 지명만** 담고 이중소속(고치·나고야·카와사키)은 제외돼 있어 오탐이 없다.
    · **`sport`·`category` 2개 필드만** 변경하고 `sport_fixed`에 정정 이력을 남긴다. 다른 필드 무수정
      (샘플 검증: 키 28개 유지·`result`/`hit`/`corePicks` 보존). `--dry` 기본·`--apply` 필요·원자적 저장.
    · **효과**: 종목 분포 경마 843→**630** · 경륜 501→**715**. 경마 페이스 보유율 **23.6% → 31.4%**로 정정
      (누적 기준. 최근 4일 실측 85.4%는 불변 — 오분류가 과거 통계를 끌어내리고 있었다).
    · ✅ **성적 수치 재계산 완료 — 결론 불변(되돌릴 것 없음).** 재분류 213건 중 성적 판정 대상
      (`corePicks.profitTier` 보유)은 **단 1경주**뿐이다. 213건이 전부 7/07~7/24인데 `corePicks` 저장이
      7/18 도입이라 대부분 집계에 애초에 들어가지 않았다.
      | 지표(정정 전 → 후) | 경륜류 | 일본경마 |
      |---|---|---|
      | 복승 전체 | n 314→315 · 적중 38.5→38.4% · 회수 88.8→88.5% | n 167→166 · 30.5→30.7% · 67.5→67.9% |
      | 삼복승 전체 | n 446→447 · 46.0→45.9% · 51.3→51.1% | n 185→184 · 24.9→25.0% · 30.6→30.7% |
      | **tier=low 복승** | n 21→21 · **33.3% · 40.5% (Δ0)** | n 1→1 · **0% (Δ0)** |
      | 시장최저 <2.0배 복승 | n 63→63 · **36.5% · 51.4% (Δ0)** | n 1→1 (Δ0) |
      | 시장최저 <1.5배 복승 | n 8→8 · **25.0% · 26.2% (Δ0)** | — |
      **전 지표 Δ적중 ≤ ±0.2%p · Δ회수 ≤ ±0.4%p.** 저배당 구간(<2.0·<1.5·tier=low) **이동분 0경주** →
      **실전 반영한 `tier=low` 복승 1순위 복구의 근거는 무영향이다. 되돌릴 필요 없음.**
      ⚠ 표본 주의: 재계산의 저배당 셀은 경륜 8~63경주 · **일본경마 1경주**로 매우 얇다. 절대값은
      산출 방법(여기서는 `profitTier.minOdds` + 총투자 동일 분할)에 따라 이전 세션 수치와 다를 수 있으며,
      이번 산출물의 핵심은 **재분류에 따른 이동폭(before→after)** 이다.
      ⚠ 배당 유형별 수치(진성급락 8.6% 등)는 **종목 무관**이라 재계산 대상이 아니다(확인 완료).
    · ✅ **tier 경계 오적용 확인(작업3)** — `profitTier.sportLabel`(판정 시점에 적용된 경계)로 직접 검증.
      경계는 종목별로 다르다(경륜 **1.8/5.0** ↔ 일본경마 **2.0/8.0** ↔ 한국 3.0/10.0).
      · 재분류 이동분 중 잘못된 경계(일본경마)로 판정된 건 **1경주**(와카야마 1R·minOdds 3.80),
        그러나 **경계 차이로 tier가 실제로 뒤바뀐 건 0경주**(3.80은 양쪽 다 mid) → **실피해 없음.**
      · 🟠 **다만 실시간 재발이 완전히 멎지는 않았다** — 최근 7일 562경주 중 종목↔라벨 불일치 **3경주**
        (와카야마 7/24 · **코치 6R 7/25 · 코치 10R 7/26**). 셋 다 minOdds 3.6~4.6으로 **tier 결과는 동일**해
        피해는 없었다.
      · **원인은 `_KEIRIN_ONLY_RE`가 아니다.** 코치는 **이중소속**(경마장+경륜장)이라 설계상 그 정규식에
        일부러 넣지 않는다. 실제 원인은 **분석 시점에 `sport`가 아직 확정되지 않아**(뒤늦게 `boat`로 확정)
        `_profit_tier_of`가 경마 경계를 쓴 것이다. 즉 지명 패턴이 아니라 **종목 확정 타이밍** 문제다.
      · **잔여 과제**: minOdds가 1.8~2.0 구간이면 tier가 low↔mid로 실제 뒤바뀐다. 지금은 우연히 피했을 뿐이므로
        `_profit_tier_of` 호출 전 종목 확정 보장(또는 확정 후 재판정)이 필요하다. ⚠ 추천 경로 수정이라 별도 승인 필요.
- ✅ **소실 방지 배선 완료** — 각질·페이스는 **가공 결과만** 저장되고 그 **원본 입력**은 어디에도 남지
  않아, 임계값을 바꿔도 과거를 재계산할 수 없었다. 출마표는 경주 종료 시 내려가 **영구 소실**된다.
  | 보존 대상 | 위치 | 종전 |
  |---|---|---|
  | `corners`·`fieldSizes`·`pastDistances`·`last3fList`·`pastPlacings` | `_keiba_starter_store_row` | 각질 역산 후 **폐기** |
  | `kimarite`(결정수 **원본 시행수**) | 경륜 전적 저장행 | **비율만** 저장(3전 100% ↔ 30전 83% 구분 불가) |
  | `distance`·`surface`·`trackCond` | `starters_store[rk]` 3경로 + `_nar_parse_deba` | **파싱은 되나 미저장**(로그 1,392건 전부 거리 0건) |
  | 위 전부 + 경륜 `line`·`tendency` | `_raw_profile_snapshot` → 분석 로그 `raw_profile` | 없음 |
  ⚠ `_raw_profile_snapshot`은 **순수 복사**다 — 새 fetch 없이 `starters_store`만 읽으며 추천·판정·학습에 무개입.
- ✅ **경륜 누적 개통** — `tools/build_keirin_profiles.py` → `data/simulation_db/keirin_profiles.jsonl`
  (`schema_version: 1` · append-only · `race_id` 중복 스킵 · 백필→전진누적 **승격 허용**).
  현재 **500행**(백필 499 · 전진 1) · 페이스 475 · 결과 455 · `line_pairs` 470 · 페이스×두수 **30건+ 2셀**.
  ⚠ **백필 한계(권대표 지시로 명시)**: 과거 로그엔 `raw_profile`이 없어 `corners`·`field_sizes`·
  `kimarite_n`·`lines`가 **복원 불가 → null** 이며 **`"backfilled": true`** 로 구분한다.
  전진 누적분부터 원본이 채워진다. 현황: `python tools/build_keirin_profiles.py --stats`
- ⛔ **경마용 `horse_profiles.jsonl` 보류(2026-07-29 22:40 검증 결과)** — 조건부 진행이었고 **조건 미충족**.
  - **배선 자체는 정상**(함수 단위 16/16 통과): `_nar_parse_deba` distance 1400·surface 더트·trackCond 良 /
    `_keiba_starter_store_row` corners·fieldSizes·pastDistances·last3fList·pastPlacings 전부 산출 /
    `_raw_profile_snapshot` 8두 전원 전달. **코드 문제 아님.**
  - **그런데 실데이터는 0%**: 분석 로그 1,398건 중 `raw_profile` 보유 **3건**, 그마저 원본 필드는
    `line`·`tendency`(기존부터 저장되던 값)만 채워지고 corners·kimarite·distance는 **0/26**.
  - **원인 = 타이밍**: `starters_store` 109경주가 **전부 패치 이전 저장분**이고, 전적은 **경주당 1회만
    수집**(`source` 일치 시 재수집 스킵)이라 새 필드가 채워질 기회가 없다. 오늘 경주는 이미 종료(마지막
    20:50)돼 수집 창 밖이었다.
  - ✅ **내일은 자연히 해소된다** — 새 경주는 새 raceKey라 `starters_store`에 항목이 없어 신스키마로 수집된다.
  - **판단**: 지금 만들면 경륜과 똑같이 **원본 전부 null인 백필 행**만 쌓인다 → **다음 개최일 확인 후 생성.**
- ⚠ **표본 현실(직시)**: 셀당 30건이 확보되는 한계선은 **경륜 A차원(페이스×두수) 일부(2~5셀) · 경마 A0(페이스 단독)**
  까지다. **B(구장)·C(거리) 차원은 양쪽 다 불가.** 경마 B차원 판정에는 결과 보유 **약 2,600경주**가 필요한데
  현재 780경주다. → Phase 0의 실질 가치는 "차원 확대"가 아니라 **지금 버려지는 원본의 보존**이다.
- ⚠ **함수명 이중 정정 (2026-07-29 재확인)** — 이 항목은 **두 번 틀렸다**:
  1. Gemini가 인용한 `_simulate_race_flow` 는 **존재하지 않는다**(인용한 `app.py:25341~` 도 무관).
  2. 그 정정으로 적어둔 `_scenario_combos` **역시 존재하지 않는 이름**이었다(당시 확인 없이 옮겨 적음).
  - ✅ **실제 함수는 `_scenario_plan(cp, curQ, pace_analysis, sig_meta, valid_nos, axis_plan)`**
    — 정의 **app.py:7287**, 문제의 로직은 **app.py:7311**:
    `target = "추입" if pace == "빠른" else ("선행" if pace == "느린" else None)`
    시나리오A(축 기반 3조합) + 시나리오B(편성 유리 각질마 상위 2) + 삼복승 1조합을 반환한다.
  - ⛔ **이 로직(7311)은 리플레이로 기각됐다** — 빠른 페이스에서 추입/선입 **포함 3.37% vs 미포함 5.01%
    · z=−2.80(95% 유의)**. 살리면 **유의하게 나쁜 쪽으로** 추천이 기운다. 코드는 무삭제 보존하되
    **활성화하지 말 것**(검증 근거: `tools/replay_pace_gait.py`).
  - ⚠ **교훈**: 함수명·줄번호는 인용하지 말고 **`grep -n "^def <이름>" app.py` 로 매번 확인**할 것.
    라인 번호는 수정 시 밀리고, 이름은 기억으로 적으면 이렇게 연쇄 오류가 난다.
- ❌ **할 일 3 완료 — 가설 기각(2026-07-29 · `tools/replay_pace_gait.py`)**
  검증: 425경주·**11,921 조합**(추천 선택 편향 제거를 위해 **시장 전판**을 모집단으로 삼음).
  지표는 `엣지 = 실측적중률 ÷ 시장암시확률(0.75/배당)` — 1.0 초과라야 시장이 과소평가한 것이다.
  | 빠른 페이스 | n | 적중률 | 엣지 |
  |---|---|---|---|
  | 추입/선입 0두 | 1,297 | **5.01%** | 0.96 |
  | 추입/선입 1두 | 3,419 | 4.24% | 1.05 |
  | 추입/선입 2두 | 1,691 | **1.60%** | **0.83** |
  - **2비율 검정: 포함 3.37% vs 미포함 5.01% · z=−2.80 → 유의(95%)하게 "포함이 더 나쁘다".** 가설의 정반대다.
  - 같은 경주군에서 **선행 2두 포함이 5.25%로 최고** — "빠른 페이스면 선행 불리"라는 **전제 자체가 뒤집혔다**.
  - 각질 구성 어느 쪽도 엣지가 0.83~1.05로 **1.0 근처** = **각질 정보는 이미 배당에 반영돼 있어 추가 우위가 없다.**
- ⛔ **할 일 4(`_scenario_plan` 부스팅 활성화)는 기각한다.** `target = "추입" if pace == "빠른"`
  (**app.py:7311** · 함수 `_scenario_plan` 정의 app.py:7287)을 살리면 **유의하게 나쁜 쪽으로** 추천이 기운다.
  데이터가 채워지길 기다리던 로직이었으나 막상 채워 보니 방향이 반대였다.
  ⚠ 코드는 **삭제하지 않고 비활성 그대로 보존**(다른 시장·조건 재검증 여지).
- 🔍 **부차 발견(표본 얇음·추가 검증 대상)**: 빠른 페이스 **12~30배** 구간에서만 추입/선입 포함이 엣지를 보인다
  — 1두 **1.16**(n=562·hit 26) · 2두 **1.25**(n=203·hit 10) ↔ 0두 0.59(n=297·hit 7).
  1단계에서 EV 복원을 연 구간(12~30배)과 **정확히 겹친다**. 30배+ 는 전 구성 엣지 0.5~0.8로 최악이라
  1단계에서 30배+ 를 닫은 판단과도 일관된다. → 표본이 쌓이면 **"12~30배 복원 대상 선정 시 추입 포함 우대"** 재검증.
- **재현**: `python tools/replay_pace_gait.py` (완전 읽기 전용 · 운영 데이터 무수정).
  ⚠ 이로써 리플레이가 뒤집은 가설은 **네 번째**다(급등 20~50% 착시 · 페이크급락 일괄제외 · 진성급락 정렬 · 전개 부스팅).
  **"그럴듯함"은 근거가 아니다 — 규칙 변경 전 리플레이는 예외 없이 통과시킬 것.**

### 우선순위 요약
1. ✅ **EV 필터 재보정 완료**(할 일 1) — 12~30배 복원, OOS 회수율 92.9%→103.5%
2. ✅ 각질 수집(할 일 2·완료) → ✅ 전개 리플레이(할 일 3·**가설 기각**) → ⛔ 부스팅(할 일 4·**기각·미적용**)
3. ⚠ 단, **데이터 신뢰성(수집 공백·열 밀림·南関東)이 선행**되지 않으면 위 전부가 오염된 입력 위에서 돈다.
   오늘 놓친 20배+ 15건 중 다치카와 1R(82.6배)·소노다 7R(53.8배)은 **타임라인 1~5행**으로 애초에 판단 불가였다.

## 🐛 미해결 버그 목록 (2026-07-28 기준)
| # | 증상 | 추정 위치 | 상태 |
|---|---|---|---|
| 1 | **복승/삼복승 배당 오표시** — 추천 표의 배당값이 실제와 불일치 | `_final_picks`(7195~8279) 배당 주입부 / 프론트 렌더 | 미해결 |
| 2 | **2+5 중복 콤보** — 동일 조합이 복승 추천에 2회 이상 등장 | `_final_picks` 조합 dedupe 누락 (`_ft_bl_set` 계열 집합 처리) | 미해결 |
| 3 | **B라인 미발동** — B라인 그물망 삼복승 보험이 조건 충족에도 미추가 | `_final_picks` ~7872 `[B라인 그물망]` 블록 (`seen_t` 선점 경합) | 미해결 |
| 4 | **14번 마번 오표시** — 마번 14 이상에서 번호가 어긋나게 표시 | 마번 파싱/`valid_nos` 범위 (`_sanitize_starters` 1~18 제한 관련 의심) | 미해결 |

> 수정 시 원칙: **기존 로직 삭제 금지, 추가/보정만**. 수정 후 `tests/run_formula.py`·`tests/run_report.py`로 정합성 검증.

## 🧾 [2026-07-30] 스키마 드리프트 전수 감사 · 총평 검증 · 마감 후 재분석 실태

### 1) 파서 출력 ↔ 저장행 키 드리프트 — **아직 안 잡힌 탈락 후보 전수**
> 같은 유형의 소실이 오늘까지 **4번**(distance·surface·trackCond / corners 계열 / kimarite / declaredStyle).
> 전부 **파서는 뽑는데 저장행에서 빠지고, 예외가 안 나서 아무도 모른다.** AST 로 전수 대조했다.

| 경로 | 탈락 후보 |
|---|---|
| `_keiba_parse_shutsuba` → `_keiba_starter_store_row` | `venue` · `raceNo` · **`surface`** · **`trackCond`** · `sexAge` · `weight` · `winOdds` · `pop` · `lineageNb` · `detailUrl` |
| `_keiba_build_form` → 저장행 | `baseScore` · `rank` · `pastRaces` · `weight` · `winOdds` · `pop` · **보너스 분해 8종**(`distanceBonus`·`weightBonus`·`gradeBonus`·`distExpBonus`·`distAptitudeBonus`·`jockeyBonus`·`jockeyChangeBonus`·`styleBonus`) · `distExperienced` · `jockeyVenueRate` |
| `_nar_parse_deba` → 저장행 | `venue` · **`surface`** · **`trackCond`** · `lineageNb` |
| `_keirin_parse_card` → 저장 | `age` · `area` · `ki` · `recent` · `prev1` · `prev2` · `race_name` · `dist` · `post` · `race_no` |

- ⓐ **값의 성격 / ⓑ 복원 가능 여부**
  · 🔴 **`surface`·`trackCond`** — 경주 조건. **경주 종료 시 소실**(출마표가 내려감). 어제 `starters_store[rk]`
    레코드에는 담았으나 **말/선수 행 기준 대조에서는 여전히 누락으로 잡힌다** → 실제 저장 여부 재확인 필요.
  · 🔴 **`weight`(부담중량)·`winOdds`·`pop`(인기)** — 발주 시점 값. **종료 후 소실**. 인기·부담중량은
    전개·이변 분석의 1차 변수인데 지금 전혀 남지 않는다. **우선순위 높음.**
  · 🟠 **`sexAge`·`age`·`area`·`ki`(기수 期別)** — 정적 속성이라 **나중에 재수집 가능**(소실 아님).
  · 🟠 **`recent`·`prev1`·`prev2`(경륜 금·전·전전 개최 성적)** — 출마표에만 있어 **종료 시 소실**.
  · 🟢 **보너스 분해 8종·`baseScore`·`rank`** — 저장된 입력으로 **재계산 가능**(공식이 코드에 있음).
    단 공식이 바뀌면 과거 재현이 불가하므로 "그때 무엇을 근거로 그 점수를 줬나"는 소실된다.
  · 🟢 `lineageNb`·`detailUrl`·`race_name`·`venue`·`raceNo` — 키/링크·중복 정보. 우선순위 낮음.
- ✅ **상시 점검 편입 가능** — 이 대조는 **AST 정적 분석**이라 서버 실행 없이 돌릴 수 있다.
  **schema contract test** 로 만드는 것이 표준 해법이다(아래 설계 참조).

#### 📌 탈락 필드 우선순위 — 재정리(2026-07-30 · 🟢 판단 근거 재검토 반영)
| 순위 | 항목 | 성격 | 근거 |
|---|---|---|---|
| 🔴 1 | **`winOdds` · `pop`** | **종료 후 소실** | 발주 시점 **시장 인기**. 이변·전개 분석의 1차 변수인데 전혀 안 남는다. 오늘 총평 검증조차 "배당이 store 에 없어" 우회해야 했다 |
| 🔴 2 | **`weight`(부담중량)** | **종료 후 소실** | 거리·부담 변화가 이미 공식(`weight_change_bonus`)에 쓰이는데 **입력값이 안 남아 재계산 불가** |
| 🔴 3 | **`surface` · `trackCond`** | **종료 후 소실** | 코드는 배선됐으나 **실데이터 0%**(1-B 참조). 경로가 아직 안 탔을 뿐이라 **1계층 재수집이 선행 조건** |
| 🟠 4 | **`recent` · `prev1` · `prev2`** (경륜 금·전·전전 개최 성적) | **종료 후 소실** | 출마표에만 존재. 컨디션 추세의 1차 자료 |
| 🟠 5 | **보너스 분해 8종 · `baseScore` · `rank`** | ⚠ **🟢→🟠 상향** | 어제 "재계산 가능"으로 낮췄으나 **공식이 바뀌면 과거 재현이 불가**하다. 실제로 EV 곡선·tier 경계·페이크급락 필터가 **최근 2주에만 여러 번 바뀌었다** → "그때 왜 그 점수였나"가 이미 복원 불가 상태다. **점수 자체보다 분해값이 더 중요하다** |
| 🟡 6 | `sexAge` · `age` · `area` · `ki` | 재수집 가능 | 정적 속성이라 나중에 채워도 된다. **우선순위 낮춰도 무방** |
| 🟢 7 | `lineageNb` · `detailUrl` · `venue` · `raceNo` · `race_name` | 키·중복 | 보존 가치 낮음 → 계약에서 **`폐기: True` + 사유**로 명시 |
- ⚠ **5번 상향이 핵심 정정이다** — "재계산 가능"은 **공식이 불변일 때만** 성립한다. 이 프로젝트는 공식이
  자주 바뀌므로 **분해값(무엇을 근거로 몇 점 줬는지)은 그 시점에만 존재하는 정보**로 취급해야 한다.

### 1-B) `surface`·`trackCond` 실데이터 판정 (2026-07-30) — **대조 도구가 옳았다**
- **실측**: `starters_store` **111경주 전부** `distance`/`surface`/`trackCond` **없음**(oddspark 23·경륜 70·한국 13).
  → **오탐이 아니라 실제 미저장.**
- **원인 = 코드가 아니라 경로**: 저장 지점 2곳(app.py **22741 · 22790**)에 세 필드가 **정상 배선돼 있다.**
  그런데 **배선(7/29 22:24) 이후 저장된 경주가 2건뿐이고 둘 다 경륜**(경륜 저장행에는 거리 개념이 없어 미포함).
  **경마(oddspark) 전적은 배선 이후 단 1건도 저장되지 않았다** — 그 시각 이미 경주가 끝나 수집 창 밖이었다.
- ⚠️ **어제 `raw_profile` 0%와 완전히 같은 유형이다** — *"함수 단위 테스트는 통과하는데 실데이터는 0%"*.
  **함수 검증만으로 '배선 완료'라고 보고하면 안 된다.** 반드시 실데이터 확인까지 해야 한다.
- 🔴 **더 큰 문제**: `_KEIBA_FORM_DONE` + `source` 체크 때문에 **이미 저장된 23개 경주는 영원히 갱신되지 않는다.**
  → **1계층(schema_version 기반 재수집)이 없으면 스키마 확장은 매번 이렇게 무효화된다.**

### 2) 총평 코멘트 — **새 정보인지 규칙 기반 선검증** (Gemini 호출 0회)
| 측정 | 결과 |
|---|---|
| 총평 보유 | **70경주 (100%)** |
| 명단 역매칭으로 선수 언급 검출 | **70경주 (100%)** · 경주당 언급 **2~4명이 최빈**(3명 20 · 4명 20) |
| 「중심」 마커로 축 추출 성공 | **5경주뿐** (`が中心`·`の力を信頼`·`を軸`·`◎`) |
| **축 vs 전적 1위 겹침** | **2/5 = 40%** |
| **첫 언급 선수 vs 전적 1위 겹침** | **21/70 = 30%** |
| 라인 표현(`連れ`·`番手`·`分戦`) 포함 | 14/70 (20%) |
#### ✅ 배당 기준 재측정 (2026-07-30) — **판정이 뒤집혔다**
`odds_history` 마지막 스냅샷의 **복승 최저 조합 = 시장 1·2위**로 재측정(배당 이력 없음 10경주 제외).

| 기준 | 겹침 | 비율 |
|---|---|---|
| ① 축 vs **시장 1·2위** | 4/5 | **80%** |
| ① 축 vs 전적 1위 | 2/5 | 40% |
| ② 첫 언급 vs **시장 1·2위** | 39/60 | **65%** |
| ② 첫 언급 vs 전적 1위 | 21/70 | 30% |

- 🔴 **축 기준 80%로 판정선에 도달했다 → "새 정보 아님" 쪽에 가깝다.**
  총평은 **전적 총점보다 시장 배당과 훨씬 강하게 일치**한다(80% vs 40% · 65% vs 30%).
  예상기자가 배당을 보고 쓰는지, 같은 정보를 보는지는 알 수 없으나 **결과적으로 시장과 겹친다.**
- ⚠ **단정하지 못하는 이유**: 축 기준 표본이 **5경주뿐**이다(규칙 추출이 5/70에서만 성공).
  첫 언급 기준(n=60)은 65%로 80%에 못 미친다. **표본이 얇아 80%가 안정적인 값이라 보기 어렵다.**
- **결론(잠정)**: **총평 구조화 우선순위를 하향한다.** 시장 대비 새 정보가 20~35%뿐이라면
  LLM 투입 비용 대비 기대가 낮다. ⏸ 재검토 조건 — **축 추출 표본 30경주 이상**(현재 5).
  그때까지는 `comment` 를 **저장만 하고 활용하지 않는다**(이미 100% 저장 중이라 추가 비용 없음).
- ⚠ **규칙 기반만으로는 축 추출이 5/70(7%)** 에 그친다 — 문장 표현이 다양해 정규식으로 안 된다.
  **이 지점이 LLM 이 실제로 필요한 부분**이다(구조화 설계는 배당 기준 재측정 후 진행).

### 3) `_triple_analyze` 마감 후 동작 — **원인 확정**
- ⚠️ **`afterClose` 가드는 적용하지 않았다**(승인 대기로 보고했고 구현하지 않았다). 오늘 Gemini 호출 0건은
  가드 때문이 아니라 **아직 경주가 시작되지 않아서**다. 7/29는 670건 그대로다.
- **트리거 경로 3개**:
  · `/api/odds/triple/analyze` 엔드포인트(**app.py:13390 `triple_analyze()`** → 13426 `_analysis_log_save`)
    — **프론트가 폴링할 때마다 재분석**한다. **마감 후 가드 없음.** ← **8시간 반복의 주범**
  · 백그라운드 루프(30972) — `30 <= 남은시간 <= 600` 조건이라 **마감 후엔 안 돈다**(문제 없음)
  · 결과 입력(17958)·수동 재생성(26506) — 이벤트성(문제 없음)
- **부작용 실측**: 어제 로그 103개가 **7~14시간 전까지 계속 갱신**됐다(경주 종료 후에도 mtime 갱신).
  → ⓐ`day/races` 캐시(mtime 기반) 무효화 반복 ⓑ`readonly` 잠금이 매번 재평가 ⓒGemini 670건.
- **마감 후 재분석이 필요한 이유(있다)**: 결과 입력 시 `hit`/`profit`/`win_tags` 반영, 확정배당 백필,
  `readonly` 잠금 설정. 다만 이는 **이벤트 트리거로 충분**하고 **폴링마다 할 이유는 없다.**
- ⛔ **1차 조치안(`afterClose && readonly`)에는 구멍이 있다(권대표 지적 · 타당)** —
  `readonly` 는 **결과 확정 후에만** 걸리므로, **결과 수집이 실패한 경주는 영원히 안 걸려 계속 저장된다**
  (7/29 나고야 11·12R 같은 스냅샷 0건 경주가 정확히 그 케이스).

#### ✅ 보완안 — 시간 기반 가드 병행 (실측으로 임계값 확인)
**마감 → 결과 저장 소요 실측**(476경주 · `deadline_epoch` ↔ 결과파일 mtime):
| 구간 | 건수 | 누적 |
|---|---|---|
| 0~30분 | 148 | **54%** |
| 30~60분 | 45 | 71% |
| 60~120분 | 13 | 75% |
| 120~300분 | 16 | 81% |
| **300~330분** | **52** | 100% |
- 중앙값 **28분** · 90퍼센타일 **470분** · 최대 700분.
- ⚠ **30분은 부적절하다** — 30분 내 완료는 **54%뿐**이고, **300~330분 구간에 52건(11%)이 몰려 있다**
  (일괄 등록·백필로 뒤늦게 채워지는 경주군). 30분 컷은 이 절반 가까이를 폴링 저장에서 잘라낸다.
- ✅ **권고 임계 = 마감 후 120분**(누적 75% 커버). 그 뒤에 오는 25%는 **폴링이 아니라 이벤트**
  (결과 입력·일괄 등록·수동 재생성)로 채워지므로 폴링 저장을 막아도 손실이 없다.
- **확정 보완안(승인 대기·미구현)**:
  `triple_analyze()` 엔드포인트에서 **`afterClose` 이고 (`readonly` **또는** 마감 후 120분 경과)** 면
  `_analysis_log_save` 를 건너뛰고 **기존 로그를 그대로 반환**한다(분석 자체는 하되 저장만 스킵).
  · **이벤트 트리거는 계속 허용** — 결과 입력(17958)·수동 재생성(26506)·일괄 등록은 무영향.
  · 예상 효과: 8시간 반복 → **최대 2시간**으로 축소. Gemini 호출도 같이 줄어든다(하루 670건 → 100건대).
  · ⚠ 추천 경로라 **임의 수정 금지** — 승인 후 구현.

### 4) ②④ 결정론 검수 이관 — **구현 완료(병행 운영)**
- `_deterministic_review`(집합 비교 · 상수 하드코딩 없음) + `_dr_record` → `logs/det_review/`
  · ② `¬∃t∈finalTrifectas : linePairs[1] ⊆ t` · ④ `(급락 마번 ≤ -30%) − ∪(finalQuinellas) ≠ ∅`
  · **데이터가 없으면 판정하지 않고 `skipped` 에 사유를 남긴다**(status `NO_DATA`) —
    Gemini 가 WARNING 99.5% 로 변별력을 잃은 문제를 반복하지 않기 위함.
  · 같은 경주에서 **결과가 바뀔 때만 기록**(Gemini 처럼 수백 건 쌓이지 않음). 단위검증 **9/9**.
- ⚠ `gemini_reviewer.py` **삭제·비활성화 없음** — 같은 경주에 두 판정을 나란히 남겨 대조 검증한다.

### 📐 [설계안 · 승인 대기] schema contract test (안전장치 1계층 편입)
> 업계 표준(schema contract testing)을 이 프로젝트에 맞춘 형태.
- **계약 명시**: `tools/schema_contract.py` 에 `CONTRACT = {저장행함수: {필수키, 선택키, 폐기키}}` 를 둔다.
- **검증 2종**: ⓐ**정적** — AST 로 파서 생성 키 ↔ 저장행 키 차집합을 계산해 **계약에 없는 신규 탈락**이
  생기면 실패 ⓑ**동적** — `starters_store` 실데이터 샘플의 키가 계약 필수키를 만족하는지 검사.
- **실행 지점**: `tests/` 에 추가해 **커밋 전 검증 목록**에 포함(기존 `run_formula`·`run_report` 옆).
  추가로 **4계층 일일 감사 리포트**에서도 호출해 매일 자동 점검.
- **효과**: 파서에 필드를 추가하고 저장행에 안 넣으면 **테스트가 즉시 실패** → 오늘 같은 소실이 원천 차단된다.
- **리스크**: 낮음(읽기 전용·테스트). 다만 계약 초기값을 **현재 상태로 고정**하면 기존 탈락분이 묻히므로,
  위 표의 탈락 후보를 **`폐기키`로 명시적으로 분류**해 "알고도 안 담는 것"과 "모르고 빠진 것"을 구분해야 한다.
- 🔴 **`폐기` 분류에는 `사유`를 필수로 넣는다(권대표 지시 2026-07-30).**
  사유 없이 폐기로 적으면 나중에 **"실수인지 의도인지" 구분이 안 된다** — 오늘 4번 반복된 소실이 정확히
  그 상태에서 생겼다(아무도 왜 빠졌는지 몰랐다). 사유가 비면 **계약 파일 자체가 테스트 실패**여야 한다.
  ```python
  CONTRACT = {
    "_keiba_starter_store_row": {
      "필수": ["no", "name", "jockey", "totalScore", "recentPlacings", "styleType",
               "corners", "fieldSizes", "pastDistances", "last3fList", "pastPlacings"],
      "선택": ["bodyWeight", "distAptitude", "jockeyRate"],
      "폐기": {
        "lineageNb":   {"폐기": True, "사유": "경주 내 임시 키 — detailUrl/마번으로 재구성 가능"},
        "detailUrl":   {"폐기": True, "사유": "재조회용 링크 · 경주 종료 후 무효라 보존 가치 없음"},
        "venue":       {"폐기": True, "사유": "raceKey·파일명에 이미 포함(중복)"},
        "raceNo":      {"폐기": True, "사유": "raceKey에 이미 포함(중복)"},
        # ⚠ 아래는 '의도적 폐기'가 아니라 **미배선**이다 — 사유에 그렇게 적어 구분한다
        "winOdds":     {"폐기": False, "사유": "미배선 · 발주 시점 인기 지표라 종료 후 소실 — 배선 대상"},
        "pop":         {"폐기": False, "사유": "미배선 · 동상 — 배선 대상"},
        "weight":      {"폐기": False, "사유": "미배선 · 부담중량, 종료 후 소실 — 배선 대상"},
      }}}
  ```
  · `폐기: False` + 사유 = **"알고 있는 미배선"** → 테스트는 통과시키되 **일일 감사에 목록으로 노출**한다.
    `폐기: True` = 의도적 제외. **키가 계약에 아예 없으면 = 신규 탈락 → 즉시 실패.**

## 📐 [설계안 · 승인 대기] Gemini 재배치 + 진단 4항목 코드 이관 (2026-07-30)

### 설계 1 — 총평 코멘트 구조화 (Gemini 재배치처)
- **자산 확인**: `starters_store[rk].comment` 에 **예상기자 총평이 100% 저장돼 있다(70/70)** — 활용 0.
  예: *"こちらも小川と友永の２分戦だ。前走弥彦は優参叶わずの小川だが、節間２勝で力は見せた。高橋連れて決める。"*
  · ⚠ **선수 본인 코멘트는 아니다.** oddspark 출마표에 `コメント`/`選手コメント`/`短評` **전부 미검출** →
    선수 코멘트가 필요하면 **별도 소스(keirin.jp 등)** 가 있어야 한다.
- **총평에 반복 등장하는 정보(샘플 관찰)**: ⓐ**라인 구도**("２分戦"=2분전·"連れて") ⓑ**중심 선수 지목**("〜が中心")
  ⓒ**기동력/전개 평가**("機動力優勢"·"ロングの仕掛け") ⓓ**전주 성적 근거**("前走弥彦"·"節間２勝")
  ⓔ**대항马 언급**("〜の差しも"·"〜の一撃も互角")
- **구조화 대상 필드(안)**: `line_structure`(분전 수) · `axis_rider`(중심) · `counter_riders`[] ·
  `pace_hint`(선행 경합/기동력) · `confidence`(단정/유보 어조) · `raw`(원문 보존·필수)
- 🔴 **정확도 검증 방법(필수 · 오염 방지)**:
  ⓐ **결정론적 대조** — `axis_rider`/`counter_riders` 로 추출된 이름이 **출마표 선수 명단에 실재하는지** 검사.
    실재하지 않으면 **환각으로 판정하고 그 경주 결과 전체를 폐기**한다(부분 채택 금지).
  ⓑ **교차 일치율** — `line_structure` 를 이미 파싱된 `starters_store[rk].line`(並び 원본)과 대조.
    불일치율이 20%+ 면 구조화 자체를 신뢰하지 않는다.
  ⓒ **원문 병기 필수** — `raw` 를 항상 함께 저장해 사후 재구조화가 가능하게 한다(재현 불가 데이터를 만들지 않는다).
  ⓓ **섀도우 기간** — 최소 50경주는 **추천에 반영하지 않고 저장만** 하고, 리플레이로 기여를 확인한 뒤 배선한다.
- **호출 시점**: 출마표 공개 시 **경주당 1회**(현재 경주당 평균 7회 → **1회**). 하루 약 100건.
- ⚠ **선행 조건**: 총평은 이미 있으므로 **Gemini 없이 규칙 기반 추출부터 시도**할 가치가 있다.
  LLM은 규칙으로 안 되는 부분에만 쓰는 것이 비용·오염 양쪽에서 유리하다.

### 설계 2 — 진단 4항목 코드 이관 (결정론적 판정)
| 항목 | 데이터 | 판정식(안) | 난이도 | 선행조건 |
|---|---|---|---|---|
| ② **B라인 누락** | `keirinLinePairs` 61% · `finalTrifectas` 100% | `set(linePairs[1].combo) ⊄ ∪(finalTrifectas)` | **매우 낮음** | 없음 — **1순위** |
| ④ **급락 미반영** | `signals_detected`/`anomaly_history` 90% · `finalQuinellas` 80% | 급락 마번 − `∪(finalQuinellas)` ≠ ∅ | **매우 낮음** | 없음 — **1순위** |
| ③ **라인 교차** | `keirinLinePairs`(lead/mark) + `starters_store[rk].line` | 조합 2두의 라인 소속이 다른가 | 낮음 | **`line` 원본 보존**(1계층 스키마 승격) |
| ① **맹목적 왕축** | `strongAxis` — ⚠ **389건 중 22건(6%)** 만 세팅 | 왕축인데 신호 0 | 낮음 | **모집단 문제 별도 확인 필요** |
- **①의 6%가 정상인지 먼저 확인할 것** — `tier=low` 경주에서만 세팅되므로 낮은 게 정상일 수 있다.
  비정상이면 판정 이전에 세팅 로직부터 봐야 한다.
- ⚠ **`gemini_reviewer.py` 는 삭제·비활성화하지 않는다** — 코드 이관이 검증될 때까지 병행 운영하고,
  **같은 경주에 대해 두 판정을 대조**해 코드 판정의 정확도를 먼저 확인한다.

## 🤝 Gemini ↔ Claude Code 협업 분업
| 역할 | 담당 | 산출물 |
|---|---|---|
| **진단·설계** | Gemini | 로직 결함 지적, 수정 방향 제안 (`logs/gemini_review/*.json`) |
| **검증·패치·커밋** | Claude Code | 제안 검증 → app.py 패치 → 문법체크 → 커밋 → 푸시 |

- Claude Code는 Gemini 제안을 **그대로 적용하지 않는다.** 반드시 실제 코드/데이터로 재검증 후 반영.
- 코드 내 Gemini 유래 변경은 `# Gemini 제안 — <내용>` 주석으로 표시(예: app.py 7872).

### 자동진단 파이프라인 (`gemini_reviewer.py`, 110줄)
- 호출: `_triple_analyze` 내 `gemini_reviewer.review_async(...)` (app.py 11406 / 11423) — finalQ/finalT 확정 직후 **백그라운드 스레드**.
- 검사 4종: ①맹목적 왕축 ②B라인 누락 ③라인 교차 ④급락 미반영.
- 모델 `gemini-2.0-flash`, 경주당 **5분 1회**(`_CALL_INTERVAL=300`), timeout 5초.
- 결과 → `logs/gemini_review/YYYYMMDD_<경주>_HHMMSS.json`. `status=="WARNING"`이면 카카오 알림 발송.
- ✅ **가동 확인 완료(2026-07-28 12:20)** — 라이브 서버가 실제 경주 검수 로그 생성(`logs/gemini_review/`). 복구 이력:
  1. ✅ `requests` 설치(2.34.2) + `requirements.txt` 명시. 미설치 시 `import gemini_reviewer` 자체가 실패하고 app.py의 `except`가 삼켜 로그조차 안 남았음.
  2. ✅ `.env`에 `GEMINI_API_KEY` 추가(사용자 직접 입력).
  3. ✅ **모델 교체** — `gemini-2.0-flash`는 **서비스 종료**(404 `"no longer available"`). `_GEMINI_MODELS = [2.5-flash → 2.5-flash-lite → 2.0-flash]` 순차 폴백으로 변경(구모델도 삭제 없이 최후 폴백 유지).
  4. ✅ **출력 설정** — `maxOutputTokens 300→800`(2.5 계열은 300에서 `MAX_TOKENS`로 잘려 JSON 파손) + `thinkingConfig.thinkingBudget=0`(thinking이 출력예산 잠식 방지) + `responseMimeType="application/json"`. 실측 2.6초/486토큰.
  5. ✅ **카카오 함수명 배선** — `_send_kakao`가 찾던 3개 이름이 app.py에 전부 없었음 → 실제 함수 `_kakao_send_to_me(text, url=None)`(반환 `{ok, error?}`)를 1순위로 추가(기존 3개는 폴백 보존). 발송 성공/실패를 콘솔에 출력.
  6. ✅ **모듈 조회 방식** — `importlib.import_module("app")`는 `python app.py`(=`__main__`) 실행 시 app.py를 **두 번째 모듈로 재실행**하므로, `sys.modules["__main__"] → ["app"]` 순 조회로 변경(import_module은 최후 폴백).
- 🔒 **키 유출 방지**: 인증을 `?key=` 쿼리 → **`x-goog-api-key` 헤더**로 변경. 쿼리 방식은 requests 예외 메시지에 전체 URL이 실려 **API 키가 콘솔·로그로 그대로 노출**된다(실제 발생 확인). 모든 오류 출력은 `_mask()`로 키를 `<KEY>`로 치환.
### 📊 Gemini 진단 실적 감사 (2026-07-30 · 로그 740건 전수)
> ⚠ **전제 정정**: "4항목(맹목적왕축·B라인누락·라인교차·급락미반영)을 되풀이해 판별 실패"는 **틀린 전제**였다.
> 그 4종 세트는 **18건(2.4%)** 뿐이고, `_logic_source` 업그레이드(CHANGELOG #68) 이후 Gemini는
> **자유 형식 코드 리뷰** 모드로 바뀌어 **고유 issues 조합이 718종/740건**이다.
> 초기 5건(업그레이드 이전 로그)으로 일반화한 것이 원인이다.

| 지표 | 실측 |
|---|---|
| status | **WARNING 736 (99.5%)** · SAFE **4 (0.5%)** |
| 고유 issues 조합 | **718종 / 740건** (최빈 반복률 2.4%) |
| 지적 대상 함수 | `_final_picks` **625(84%)** · `_apply_profit_strategy` 139 · `_signal_confidence` 132 |
| `q_suggest`/`t_suggest` | 733 / 728건 보유 — 그러나 **베팅 조합이 아니라 코드 수정 지시문** |

- **진단 역할 종료 근거(반복률이 아니다)**: ①**WARNING 99.5%** — "이상 없음"을 못 내 선별 도구로 무의미
  ②제안이 코드 수정 지시문이라 **결과와 대조할 대상 자체가 없다**(적중 검증 불가)
  ③지적의 **84%가 `_final_picks`** 인데 **코드는 경주마다 바뀌지 않는다** → 같은 코드를 하루 625번 리뷰.
  **호출 단위가 경주가 아니라 코드 버전이어야 한다.**
- 🔴 **호출 과다의 진짜 원인 = 락 누수 아님, "마감 후 무한 호출"**
  · `_GEMINI_CALLED` 락은 **정상 작동**한다(연속 호출 간격 **중앙값 323초**, 300초 미만은 22.3%로 경계 근처).
  · `raceKey` 분열도 **없다**(고유 키 104개 = 실제 경주 수와 일치).
  · 진짜 원인: **호출이 발주 창을 훨씬 넘겨 하루 종일 계속된다** —
    히로시마 3경주 **09:02~17:18(8시간 16분) 58회** · 히로시마 4R 57회 · 소노다 5R 47회.
    수집 창은 12분인데 `_triple_analyze` 가 마감 후에도 계속 돌고 그때마다 `review_async` 가 호출된다
    (어제 확인한 "종료 경주도 analysis 가 계속 mtime 갱신"과 같은 뿌리).
  · **호출부(app.py:11817)에 `afterClose` 가드가 없다.** 5분 락만으로는 8시간 = 최대 96회를 못 막는다.
  · 경주당 호출 분포: 1~3회가 73경주로 정상이나, **15회 이상이 15경주**로 꼬리가 길다.
  · → **조치안(승인 대기)**: 호출부에 `afterClose` 가드 1줄 추가 시 하루 670건 → **약 100건 이하** 예상.

- ⚠️ **운영 주의**: `status=="WARNING"`이면 **카카오 알림이 실제 발송**된다(`data/kakao_token.json` 연동 시). Gemini는 WARNING을 후하게 내는 경향이 있어, 경주당 최대 5분 1회(`_CALL_INTERVAL=300`) 알림이 쌓일 수 있음 → 알림 과다 시 `_CALL_INTERVAL` 상향 또는 발송 조건(예: `issues` 2건 이상)을 조여야 한다.
- ⚠️ **콘솔 인코딩**: app.py는 기동 로그에 em-dash(`—`) 등 비-cp949 문자를 출력하므로, **stdout을 파일로 리다이렉트하면 `UnicodeEncodeError`로 기동 실패**한다. `경마서버_자동시작.bat`처럼 `chcp 65001`(UTF-8 콘솔)로 띄우거나 `PYTHONIOENCODING=utf-8`을 설정할 것.
- **진단 명령**
  ```bash
  python -c "import requests; print('requests OK')"
  python -c "import os; print('KEY:', bool(os.environ.get('GEMINI_API_KEY')))"
  python -c "import glob; print('Gemini 로그수:', len(glob.glob('logs/gemini_review/*.json')))"
  ```
  로그 0건 + `logs/gemini_review/` 디렉터리 자체가 없음 = **모듈 import 실패**(디렉터리는 import 시 `os.makedirs`로 생성되므로).
