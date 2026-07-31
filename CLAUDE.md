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
- 🔴 **판정식은 발동률을 먼저 재고, 발동 사례 3건을 눈으로 확인한다(2026-07-30 · 3회 반복 후 신설).**
  판정식이 **의도한 것과 다른 것을 재는** 실패가 하루에 세 번 났다. 발동률이 적정해도 다른 걸 재고 있을 수 있다.
  | 항목 | 의도 | 실제로 잰 것 |
  |---|---|---|
  | ④ 급락 미반영 | "급락말을 놓쳤다" | **"급락말이 많았다"**(발동 77% · z=+0.18) |
  | D 중복·상한 | "2+5 중복 버그" | **"같은 리스트 내 중복"**(0.2%) — 실제 버그는 **리스트 간** 중복(54.8%) |
  | ② B라인 누락 | "2번째 라인이 빠졌다" | ⚠ **정정** — 삼복승 4개+ 조건을 걸어도 54.7%라 **"2번째 라인을 실제로 안 담는 것"이 맞았다**(가설이 틀린 게 아니라 기준이 과했다) |
  **절차**: ①발동률 측정(적정 **5~30%**) → ②발동 사례 **3건을 직접 열어** 의도한 상황인지 확인 →
  ③그 다음에 성적(적중률·회수율·z) 측정. 순서를 바꾸면 엉뚱한 지표를 채택하게 된다.
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
   → ✅ **확률 테이블(Phase 1·2·2026-07-30 완료) — 생존 셀 0개**. 각질쌍 단독 밸류 없음이 확정됐다.
   다음 축은 **차원 확대가 아니라 다른 정보원**(라인 구도 `line_pairs` 94% 미사용 · `declaredStyle` 표기 각질).
   상세는 「[적중왕전개] Phase 1·2 완료」 절.
   → ⚠ **후속 조사에서 라인 축도 엣지 하한 미달(1.068 · CI 0.893~1.248)** — 「line_pairs 축 예비 조사」 절.
   남은 실익은 **`並び` 원본으로 라인미상 57% 제거** 후 재측정뿐이다.
2-c. ⏸ **paceBonus 3안 리플레이는 표본 9경주로 판정 보류**(내일 30경주 도달 시 재측정) — 역산은
   호출 순서(`_apply_pace_analysis` 9789 ↔ `_integrated_grades` 10873) 때문에 정확도 86.2%로 실패했다.
2-b. ✅ **사설 확정배당 건 종결(2026-07-30 권대표 확정)** — 복승 49/49 완전일치로 **회수율 67.6% 유효**.
   삼복승 오염은 섀도우 전환돼 실전 영향 없음. **`payouts` 우선순위 변경은 낮은 우선순위로 남긴다.**
3. ⚠ 단, **데이터 신뢰성(수집 공백·열 밀림·南関東)이 선행**되지 않으면 위 전부가 오염된 입력 위에서 돈다.
   오늘 놓친 20배+ 15건 중 다치카와 1R(82.6배)·소노다 7R(53.8배)은 **타임라인 1~5행**으로 애초에 판단 불가였다.

## 🔴 [2026-07-30] 미해결 버그 #1 원인 확정 — **`÷2` 보정이 한 리스트에만 적용됨**

> 어제 *"복승/삼복승 배당 오표시"* 로 남긴 버그의 원인이다. "명백한 오배선을 못 찾았다"고 했는데,
> **오배선이 아니라 같은 조합이 리스트마다 다른 값으로 저장되는 것**이 원인이었다.

### 실측 (히로시마 1R · 2026-07-29 · 확정 삼복승 **57.6배**)
| 리스트 | `3+5+7` | `1+3+5` | `oddsEst` |
|---|---|---|---|
| `earlyDropTrifectas` | **24.7** | — | None |
| `closingDropTrifectas` | — | **11.6** | None |
| **`finalTrifectas`** | **12.3** | **5.8** | **True** |
| `final_recommendation.trifecta_main` | **24.7** | — | — |

**정확히 2배 차이**다(24.7↔12.3 · 11.6↔5.8).

### 근본 원인 (app.py:11755 `_EST_CAL = 2.0`)
```python
for _t_e in (core_picks.get("finalTrifectas") or []):     # ← finalTrifectas 만 순회
    _t_e["odds"] = round(float(_t_e["odds"]) / _EST_CAL, 1)
    _t_e["oddsEst"] = True
```
- `_trio_est` 는 **구성 복승 3쌍 기하평균 × 2** 로 추정한다(두 정의 모두 동일·문제 없음).
- 2026-07-23 사세보 6R 실측으로 *"추정식이 중앙값 1.96배 과대"* → **표시 직전 `÷2` 보정**을 넣었는데,
  그 보정이 **`finalTrifectas`·`trifecta`·`confTrifecta` 세 곳에만** 적용된다.
- **`earlyDropTrifectas`·`closingDropTrifectas`·`darkTrifectas`·`denseBoxTrifectas`·
  `final_recommendation.*` 는 보정을 받지 않아 원래 값(×2)이 그대로 남는다.**
- → **같은 조합이 화면 위치에 따라 2배 다르게 표시된다.**

### 규모·영향
- **발동률 54.8%**(836경주 중 458건) — **절반 이상의 경주에서 회원이 보는 배당이 화면 위치마다 다르다.**
- 어제 목격한 "삼복승 배당이 복승 자리에 표시" 도 같은 뿌리다 — 리스트가 다르면 값이 다르므로
  어느 리스트를 읽느냐에 따라 엉뚱한 숫자가 나온다.
- ⚠ **어느 쪽도 정답이 아니다**: 히로시마 1R 확정 **57.6배** ↔ 24.7(미보정) ↔ 12.3(보정).
  보정 후가 **오히려 실제에서 더 멀다**. `÷2` 보정 자체가 이 경주에선 과교정이었다.
- 프론트는 `renderCorePicks(a)`(static/js/app.js:7123)를 **세 곳(4541·7358·7460)에서 호출**하므로
  같은 함수라도 소비하는 리스트가 다르면 화면마다 값이 갈린다.

### ⏸ 조치안(승인 대기 · 미구현)
1. **보정을 한 곳으로 모은다** — `_trio_est` 반환 시점에 `÷2` 를 적용하면 **모든 리스트가 같은 값**이 된다.
   (표시 직전 보정은 리스트를 하나씩 열거해야 해 누락이 구조적으로 반복된다.)
2. 또는 **보정 대상 리스트를 전수 열거**한다(임시방편 — 새 리스트가 생기면 또 누락).
3. ⚠ **`_EST_CAL=2.0` 값 자체도 재검증이 필요하다** — 히로시마 1R에서는 보정이 오차를 키웠다.
   확정배당 표본으로 계수를 다시 재야 한다.
- ⚠ **판정·학습에는 영향이 없다**(적중 판정은 조합으로 하고 배당은 확정배당을 쓴다). **표시 계층 문제**다.
  다만 회원이 보는 숫자가 위치마다 다르면 **신뢰 문제**이므로 우선순위가 높다.

### 🟠 [2026-07-30 09:22] 아오모리 4R — 마번 라벨 어긋남(1회 목격 · **재현 미확정**)
| 항목 | 값 |
|---|---|
| 서버 4R 추천 | `[1,5]=3.9배` · `[2,4]=7.7배` |
| 화면 오버레이 | **`2+3 (3.9배)`** · `2+4 (7.7배)` |
| 배당판 실값(4R) | **`1+5 = 3.8`** · `2+3 = 4.8` · `2+4 = 7.7` |
| 배당판 실값(3R) | `2+4 = 1.7`(최저) · `1+5 = 180.1` |
- ✅ **경주 혼입(3R↔4R)이 아니다** — `3.9배`는 4R `1+5`의 값이고, 3R에서 `1+5`는 **180.1배**다.
  `triple_store` 에도 3R·4R 이 정상 분리돼 있었다. **배당은 전부 4R 값으로 정확했다.**
- 🔴 **첫 조합의 마번만 `1+5` → `2+3` 으로 어긋났다.** 두 번째 `2+4=7.7`은 완전히 정확했다.
- → **미해결 #4 「경륜 14번 마번 오표시」와 같은 계열**로 본다. `raceKey` 오염이 아니라
  **마번 인덱스 문제**다(배당은 맞는데 라벨이 틀림 — 두 사건의 증상이 동일).
- ⚠ **1회 목격이라 재현 여부는 미확정이다.** 화면이 곧 정상으로 돌아왔고, 저장 로그(09:26:46)에는
  올바른 `[1,5]` 가 들어 있어 사후 재현이 불가능하다.
- **재현 시 대응**: 그 순간 **화면을 그대로 두고** `triple_store`·분석 로그·프론트 상태를 동시에 떠서 대조할 것.
- ⚠ **D(리스트 간 배당 불일치)와 별개 건이다** — D는 같은 경주 안에서 리스트마다 값이 다른 것이고,
  이건 배당은 맞는데 마번 라벨이 어긋난 것이다.

## 🔴 [2026-07-30 오후] 데이터 파이프라인 4대 결함 — 전부 조치 완료 (세션 끊김 복구 중 발견)

> 공통 성격: **넷 다 "조용히" 진행됐다.** 로그는 있었지만 아무도 세지 않았고, 그래서 몇 주간 누적됐다.
> 발견 경로가 전부 **육안**이었다는 점이 문제다 → 17항목 자동 체크리스트가 필요한 근거.

| # | 결함 | 규모 | 조치 |
|---|---|---|---|
| 1 | **oddspark `Accept` 헤더 누락 → 요청당 9초 tarpit** | 사이클 31~41초(주기 30초 초과·구조적 포화) | `_ODDSPARK_HEADERS` · **2.28초로 회복(slow 0/20)** |
| 2 | **저장 손상 → 빈 문서 폴백이 전량 삭제** | 와카야마 8R **19틱 + archive 13건 영구 소실** | `_json_load_guard` 격리 + 저장 건너뜀 |
| 3 | **스레드 간 tmp 충돌** | 단일 프로세스 9분에 WinError 26건 | tmp 스레드ID · 경로별 RLock · 재시도 → **−85%** |
| 4 | **oddspark 스냅샷 2중 기록** | 전체의 **41.0%** · 통계 **1.69배 과대** | `_bridge_wrote` 게이트 + 과거 4,507건 dedupe |

- **1번이 3·4번을 가렸다** — 사이클이 31초라 틱이 애초에 적어서 중복·손상이 눈에 덜 띄었다.
  1번을 고치자 틱이 2배가 되면서 쓰기 빈도가 올라 3번이 선명해졌다. **순서가 중요했다**(저장 보호 → 속도).
- **2번은 3번의 결과다** — 스레드 tmp 충돌로 원본이 깨지고, 그 깨진 파일을 빈 문서가 덮었다.
- **4번은 측정 기준 자체를 흔들었다** — "스냅샷 N틱"이 1.69배 부풀려져 있었으므로,
  🔴 **체크리스트 ④ 완료선은 반드시 dedupe 후 `distinct` 기준으로 잡아야 한다.**
- ⚠ **잔여**: `[분석로그]`·`multi_race_store` 등 **17곳은 아직 `path + ".tmp"`**(PID조차 없음).
  현재 저장 실패의 약 79%가 여기다(분당 1.0건). `_json_atomic` 흡수 우선 검토 → `[분석로그]` 부터.

### ✅ 南関東 라이브 수집 검증 완료 (7/29 「⏳ 검증 잔여」 항목 종결)
- **카와사키 1경주 `src=oddspark` 로 수집 확인**(7/30 14:50~ · 35틱·distinct 22).
- 7/29 문서의 *"다음 南関東 개최일에 `data/odds_history/` 의 카와사키·오이·후나바시·우라와 스냅샷 수와
  `src` 값을 확인할 것 — 종전 확장 단독 1·2개에서 20개 내외로 늘어야 정상"* 조건을 **충족**했다.
- 실제로는 `_multi_collect_one` 의 `narBaba` 분기(keiba.go.jp)로 받아 `src` 는 `oddspark` 로 기록된다
  (브리지가 `source="oddspark_bg"` 를 넘기기 때문) — **소스 표기와 실제 취득처가 다르니 혼동하지 말 것.**

### ✏️ 정정 — B라인 v2(`key_horses`)에 대한 종전 기술
- ❌ 종전: *"`key_horses` 를 고치면 B라인 삼복승이 처음 실전에 들어간다"*
- ✅ **정정**: **v1(`9df9bbd6`)은 살아 있고 지금도 동작한다** — `_triple_analyze` 후처리(app.py 약 11800행)라
  `key_horses` 가 스코프 안에 있다. 7/30 하루에만 **5경주에서 발동**(기후 5·6·8R · 와카야마 7·8R · 로그 197행).
  죽은 것은 **v2(`2530c191`)뿐**이고, v2 는 `_final_picks` 내부로 옮겨지면서 `key_horses` 가 스코프 밖이 됐다.
  따라서 정확한 표현은 **"v2 를 고치면 B라인이 더 많이 들어간다"** 이며,
  **성적 검증 데이터는 v1 발동분으로 이미 쌓이는 중**이라 검증이 더 빠르다.
- 수정안 2가지(⏸ 성적 검증 후 결정):
  · **A안** — 호출부에 `core_picks["keyHorses"] = key_horses` 1줄 추가(+ v2 는 `cp.get("keyHorses")`).
    `keirinLinePairs` 를 넘기는 바로 그 자리·그 방식이라 관례가 일치하고 **v1 과 의미가 완전히 같다.**
  · **B안** — v2 에서 `key_horses` → `_mrank`(app.py 약 8100행) 1줄 교체. 호출부 무변경이지만
    `_mrank` 는 **순수 시장 배당 기반**이라 전적 통합을 거친 `key_horses` 와 **다른 조합을 만든다.**
  · ⚠ **A안과 B안이 서로 다른 결과를 낸다는 점을 성적 검증 설계에 반드시 반영할 것.**

## 🧪 자체 검증 원칙 (2026-07-30 정리 · 세션 리셋 대비)
> 하루 동안 같은 유형의 실수가 반복돼 원칙으로 고정한다. **측정 전에 이 목록을 먼저 읽을 것.**

1. **`n<30` 은 판정 불가** — 명시하고 **결론에 쓰지 않는다.** 방향 참고까지만.
2. **상위 1건·3건 제외 수치를 항상 병기** — 극단값 착시가 오늘만 4번(급등 20~50% · 155.6배 1건 ·
   도야마 33.5배 · EV 강등분 108.6%→3건제외 51.7%).
3. **리플레이 전 '실제 코드 경로를 재현했는지' 먼저 확인** — 면제·예외 로직을 빠뜨리면 숫자가 정교해도 무의미.
   실제 사고: 경계 시뮬 **v1 137.9% → v2 85.6%**(`_lowodds_exempt` 미재현).
4. **판정식은 발동률 먼저, 성적 나중.** ①발동률(적정 **5~30%**) → ②**발동 사례 3건을 직접 열어**
   의도한 상황인지 육안 확인 → ③성적(적중률·회수율·z). 순서를 바꾸면 엉뚱한 지표를 채택한다.
5. **함수 단위 통과 ≠ 실데이터 반영** — 반드시 **저장 건수로 확인 후** 보고한다.
   오늘 3회 반복(`raw_profile` 0% · `surface/trackCond` 0% · 4필드 0%). 전부 "배선 시각이 경주 시간대 밖"이었다.
6. **감으로 박힌 상수는 실측으로 재검증** — 오늘 3건 발견:
   `_EST_CAL=2.0`(실측 2.35·배당대별 2.70↔1.40) · `paceBonus` 매핑 방향 반대 · EV 곡선 1%↔실측 3.2%.
7. **"이미 고쳐졌다"를 전제하지 말 것** — `fix_trio_coherence` 가 강제 정합을 하는데도 위반이 11.6% 남았다.
8. 🔴 **두 값을 비교하기 전에 '비교 가능한 값인지' 먼저 확인한다** — **같은 시각·같은 저장소·같은 정의**인가.
   서로 다른 저장소(`triple_store` ↔ `odds_history`)나 서로 다른 시점의 값을 비교하면 **없는 차이가 만들어진다.**
   · **실패 사례 A (2026-07-30)**: 아오모리 5R "oddspark 105.0 ↔ private 17.9 **= 6배 차이**"로 보고했으나,
     105.0은 `triple_store`(활성 캐시), 17.9는 `odds_history`(private 스냅샷)였다. 실제로 그 경주의
     **oddspark 유효 스냅샷은 0개**여서 **두 소스가 같은 조합을 동시에 들고 있던 적이 없다.**
     → 그 위에 "열 밀림(복연승 오독)" 가설과 "회원 실수령이 더 낮을 것"이라는 사업 우려까지 쌓였다.
     13경주 재대조 결과 **비율 중앙 1.000 · private가 체계적으로 낮은 경주 0건**으로 전부 기각됐다.
   · **실패 사례 B (2026-07-30)**: "oddspark 마감 직전 None **4틱 연속**"으로 보고했으나,
     그 스냅샷들은 `minutes_before=None`(**발주시각 미확정**)이지 마감 직전이 아니었다.
     전 기간 재측정: 마감 10분 내 oddspark 스냅샷 **9,609개 중 빈 값 0건(0.0%)**.
   · **두 건 모두 원칙 4(발동 사례를 직접 열어 확인)를 건너뛴 결과다.** 이상해 보이는 수치일수록
     **먼저 원자료를 열어야 한다** — 놀라운 발견일수록 측정 오류일 확률이 높다.
   · ✅ **차단 성공 사례 C (2026-07-30)**: 사설↔공식 확정배당 대조에서 첫 대조쌍이
     `finalOdds` ↔ 같은 레코드 `payouts` 였고 **비율 23/23 전부 1.000**이었다. 그런데 `payouts` 는
     **`finalOdds` 에서 파생된다**(app.py:17796 — finalOdds 1순위, 공식은 폴백) → **자기비교**였다.
     **1.000 이라는 "완벽한 일치"도 의심 신호다** — 값이 너무 안 맞을 때만 의심하지 말고,
     **너무 잘 맞을 때도 파생 관계를 의심**할 것. 독립 대조쌍(작성 시각·작성 경로가 다른 저장소)을
     따로 찾아야 비교가 성립한다. 상세는 「사설 ↔ 공식 확정배당 대조」 절.
   · 🔴 **배당을 비교할 때는 '같은 조합인지' 게이트를 먼저 통과시킬 것** — 정합 스윕은 **착순도 정정**하므로
     착순이 다르면 두 배당은 애초에 **다른 조합의 값**이다(위 대조에서는 착순 동일 52·불일치 0 확인 후 측정).
8-B. 🔴 **한 목록 안의 백분율은 분모를 통일한다(2026-07-30 신설).**
   같은 표·같은 목록에서 항목마다 분모가 다르면 **합이 100%가 안 되고 독자가 비율을 오독한다.**
   · **실패 사례**: 막판 편입 측정 보고에서 "복승 표시에 그 말 포함 127(45.0%)"은 **/282**,
     "그 말조차 없음 80(38.6%)"은 **/207** 이었다. 두 값이 같은 목록에 나란히 있었지만 분모가 달랐다.
     207 로 통일하면 **61.4% / 38.6%** (합 100%)가 맞다.
   · **규칙**: 목록 머리에 `⚠ 분모 통일: 아래 %는 전부 /N` 을 명시하고, 분모가 바뀌면 **표를 분리**한다.

8-C. 🔴 **모든 비율에 분모를 명시한다(2026-07-31 신설).**
   두 숫자가 안 맞으면 **"틀렸다"고 판정하기 전에 분모부터 확인한다.** 같은 목록 안의 백분율은 분모를 통일한다.
   · 2026-07-30~31 하루에 **다섯 번** 이 문제가 났다: 0틱 경주 제외(81.6%↔69.4%) ·
     45.0%(/282) ↔ 38.6%(/207) · 마 단위 ↔ 조합 단위 · **조합 단위(n=722) ↔ 경주 단위(n=35)** ·
     `displayedCombos` 없는 610경주가 B안 분모에서 이탈.
   · **"엣지 0.507 vs 적중 0" 은 모순이 아니었다** — 분모가 달랐을 뿐이다.

8-D. 🔴 **검증 코드도 검증한다(2026-07-31 신설).**
   **"테스트 통과"가 곧 "정상"이 아니다.** 테스트가 **의도한 것을 재는지** 발동 사례를 눈으로 확인한다.
   · 실패 사례: Phase A 단위 검증 22건 중 2건 실패 → 코드 결함이 아니라 **검사식이
     `_excluded` 필드의 문자열을 오탐**한 것이었다. 원자료를 직접 열어 실제 누출 0을 확인했다.
   · 회귀 테스트에도 같이 적용한다 — **통과·실패 둘 다** 사례를 열어본다(원칙 4와 같은 취지).

9. 🔴 **`except` 안에서 빈 기본값을 세우고 그것을 다시 저장하는 경로는 데이터를 지운다(2026-07-30 신설).**
   `except: pass` 는 '못 보게' 할 뿐이지만 `except: doc = {"snapshots": []}` 뒤의 저장은 **지운다** — 질이 다르다.
   · **실사고**: 7/30 14:09 와카야마 8경주 — 스냅샷 **20틱 + `archive_snapshots` 23건이 1틱/10건으로 초기화**.
     `archive_snapshots` 는 설계상 **영구 보존**인데도 잘렸다(복구 불가).
   · **연쇄 구조**: 두 스레드가 같은 tmp 를 공유 → 섞인 내용이 rename → **원본이 손상 JSON** →
     다음 reader 가 파싱 실패 → **빈 문서 폴백 → 전량 덮어쓰기**. 저장 실패 하나가 전량 소실이 된다.
   · **원칙**: 손상을 만나면 **덮어쓰지 말고 격리하고 그 사이클 저장을 건너뛴다**(`_json_load_guard`).
     격리는 **이동이 아니라 사본**(옮기면 그것도 소실). 앞부분 유효 JSON 복구를 먼저 시도한다
     (7/28 전수 복구에서 19건 중 13건이 'Extra data' 유형이었다).
   · **점검법**: `json.load` 의 `except` 에서 빈 dict/list 를 세우는 지점을 AST 로 훑고,
     그중 **로드 → 수정 → 같은 경로 저장**인 것만 고른다(읽기 전용 접근자는 위험하지 않다).
     7/30 기준 app.py 안에 **62곳**이 있고 그중 핫 경로 **5곳**을 처리했다.
10. 🔴 **외부 사이트가 느리면 네트워크가 아니라 헤더를 의심한다(2026-07-30 신설).**
   · **DNS·TCP·TLS 를 분리 측정하면 즉시 갈린다.** 실측: DNS 0.009초 · TCP 0.005초 · TLS 0.031초인데
     `urlopen` 은 **9.32초**였다 → 네트워크가 아니다.
   · 헤더를 하나씩 넣어 분리: UA만 **10.70초** / +`Accept-Encoding` 8.23초 / **+`Accept: */*` 0.23초**.
     oddspark(Akamai)는 `Accept` 가 없으면 **약 9초 tarpit**을 건다(브라우저는 항상 보낸다 = 봇 판별).
   · ⚠ 이런 상황에서 **동시성을 올리는 것은 오답**이다 — 요청당 비용이 문제인데 요청 수를 늘리면
     WAF 위험만 커진다. 실제로 7/29 "수집 경합" 진단이 이 함정이었다(실측으로 기각).
   · ⚠ **사이트마다 다르다.** 같은 조치를 일괄 적용하지 말 것 — `keiba.go.jp` 는 0.16~0.20초로 문제가 없어
     `_nar_fetch` 는 건드리지 않았다.

## ✅ [2026-07-30] 사설 ↔ 공식 확정배당 대조 — **복승은 동일(건 종결) · 삼복승은 판정 불가**

> **원래 우려**: 회원은 사설에서 베팅하므로 실수령이 공식 기준 회수율 67.6%와 다를 수 있다.
> **결론**: **복승은 사설=공식으로 완전히 같다(n=49·100%). 회수율 재산출 불필요 — 이 건은 닫는다.**

### 🔴 작업1에서 걸러낸 함정 — 처음 잡은 대조쌍은 **자기비교**였다
- 첫 시도: 학습 레코드 안의 `finalOdds`(사설) ↔ 같은 레코드 `payouts`(공식으로 알았음) → **비율 23/23 전부 1.000**.
- 그러나 `payouts` 는 독립 공식값이 **아니다**. **app.py:17796 `q_odds = _odds_val(fo.get("quinella"))`** —
  `payouts` 는 **`finalOdds` 를 1순위로 삼아 파생된다**(공식 `_official_payouts_for_rk` 는 비어 있을 때만 폴백).
  → 1.000 은 일치가 아니라 **같은 값을 두 번 읽은 것**이다. 원칙 8이 실제로 한 번 더 작동했다.
- ✅ **성립하는 독립 대조쌍을 따로 찾았다**: `_official_result_audit`(정합 스윕)은 과거 날짜를
  **`_rk_dated`(날짜접두)** 로 재학습하므로 **별도 레코드**가 생기고, 원래 사설 레코드는 그대로 남는다.
  · **사설측** = `learning.json` 중 combo형 `finalOdds` 보유 + `race` 에 날짜접두 **없는** 레코드(86건)
  · **공식측** = `data/race_results/*.json` 중 **`정합 스윕` 메모 보유분**(989건)의 `payouts`
    (스윕이 `inputs.quinella_odds`=공식으로 덮어쓴 값 — 사설 레코드와 **작성 시각·작성 경로가 다르다**)

### 작업1 확인 4항목
| # | 항목 | 결과 |
|---|---|---|
| ① | 보유율 | 사설 확정배당 **86경주**(고유 83) · 날짜 07-07·08·13·15 4일치. **복승 대조 n=49 → 판정 가능** |
| ② | 마권 종류 | **복승·삼복승 각 1조합씩만**. `finalOdds.quinella`/`finalOdds.trio` = `{combo, odds}` |
| ③ | 전수 여부 | ❌ **전수 아님**. 조합은 **`top3[:2]`·`top3[:3]` 로 합성**(파싱값 아님)이라 **적중 조합 1개뿐** |
| ④ | 정말 확정배당인가 | ✅ 확정배당. 단 **100배 표시 상한**(정확히 100.0 이 복승3·삼복승10건, 100 초과 0건) → **검열값이라 대조 제외** |

- 🔴 **④의 출처는 `extractResultOdds()` 가 아니다(전제 정정)**. 그 함수는 `払戻金`(원) 표를 읽고 `raw[]` 를
  채우는데, 저장된 86건은 **`raw` 가 전부 비어 있고 combo 가 착순 순서**다. 실제 작성자는
  **`_register_result_rows`(app.py:20921~20925)** — 일괄등록/OCR 경로가 `_parse_result_rows` 의
  `복승`/`삼복승` 컬럼(`qOdds`/`tOdds`)을 받아 조합을 합성해 넣는다.
  `extractResultOdds` 경로(`results_store.json`)는 **총 6건뿐이고 그중 5건은 source 가
  `keiba.go.jp/.../RaceMarkTable`(=공식)** 이라 사설 표본이 아니다.
- ⚠ **사설이라는 확증은 없다** — `results_bulk` 는 **HTML 출처(url)를 저장하지 않는다**(app.py:20954~20974).
  사설로 보는 근거는 정황뿐: ⓐ`경주지역`+`라운드` 컬럼의 **한국어 통합 결과표**(공식 사이트는 경마장별) ⓑ**경마+경륜 혼재**
  ⓒ**100배 표시 상한**. → **일괄등록 시 출처 URL 저장이 필요**(아래 미활용 항목과 함께).

### 작업2 대조 결과 (착순 동일 게이트 통과 52경주 · 100.0 검열 제외)
> 🔴 **선행 게이트(원칙 8)**: 스윕이 착순도 정정하므로 착순이 다르면 **다른 조합의 배당**이라 비교 불가.
> 실측 **착순 동일 52 · 불일치 0** → 같은 조합끼리 비교됨을 확인한 뒤에 아래를 측정했다.

| 마권 | n | 중앙 | 평균 | Q1/Q3 | 완전일치(±2%) | 사설<공식 | 사설>공식 |
|---|---|---|---|---|---|---|---|
| **복승** | **49** | **1.000** | 1.000 | 1.000/1.000 | **49/49 (100%)** | 0 | 0 |
| 삼복승 | 46 | 1.000 | 1.507 | 1.000/1.000 | 40/46 (87%) | 0 | **6** |

- ✅ **복승 = 완전 동일**. 범위조차 1.000~1.000이고 상위 1건·3건 제외에도 1.000 불변.
  → **사설 실수령이 공식보다 낮을 것이라는 우려는 복승에서 기각**. **67.6% 를 사설 기준으로 다시 낼 필요 없다.**
  (CLAUDE.md 기존 결론 *"손실 전액이 삼복승 · 복승만 걸면 99.7%"* 와 상충하지 않는다.)
- ⚠ **삼복승 불일치 6건은 판정 불가(원칙 1: n=6 < 30)** — 그러나 **전부 카와사키 한 곳**이고 **전부 사설이 높다**:
  | 날짜 | 경주 | 사설 | 공식 | 비율 |
  |---|---|---|---|---|
  | 07-07 | 카와사키 7R | 59.4 | 5.2 | **11.42** |
  | 07-07 | 카와사키 4R | 40.5 | 6.2 | 6.53 |
  | 07-07 | 카와사키 2R | 92.8 | 18.8 | 4.94 |
  | 07-08 | 카와사키 7R | 23.3 | 8.1 | 2.88 |
  | 07-08 | 카와사키 2R | 16.3 | 8.2 | 1.99 |
  | 07-07 | 카와사키 5R | 31.9 | 20.3 | 1.57 |
- ⛔ **"사설이 더 준다"고 해석하지 말 것.** 11.4배 격차는 환급률 차이로 설명되지 않는다. 유력 가설 2개:
  ⓐ**마권 종류 오식별** — `_parse_result_rows` 의 `iT = idx(r"삼복승|삼복|三連複|3連複")` 가 **3連単(순서적중)**
    컬럼을 잡았을 가능성(같은 착순에서 3連単 > 3連複 는 항상 성립하고 배율대도 들어맞는다)
  ⓑ**열 밀림**(미해결 3-B 계열). 근거: **카와사키만 재등록 3건 전부 값이 바뀌었다** —
    `07-08 5R: Q=1.4/T=7.5` → 재등록 `Q=7.5/T=100.0` (**2차 Q = 1차 T** = 한 칸 밀림 형태).
  🔴 **어느 쪽인지 확정 불가** — 원본 HTML이 저장되지 않아 사후 재현이 안 된다(위 출처 미저장 문제와 같은 뿌리).

### 🔴 실제 피해 — 오염된 사설 삼복승이 **장부에 이미 들어갔다**
- `payouts` 가 `finalOdds` 우선이므로(17796) 사설 값이 **그대로 손익 계산 입력**이 된다.
- **07-07 카와사키 7R: 삼복승 적중 · 사설 59.4배 → `pnl` +58,400원.** 공식 5.2배면 **+4,200원**이어야 한다
  → **약 54,200원 과대**. 사설 86 레코드 `pnl` 합계 150,300원의 **36%가 이 1건**이다(원칙 2·극단값).
- 🟠 **별건 동시 발견 — 같은 경주 2행 이중계상 가능**: 스윕이 날짜접두 키로 **별도 레코드**를 만들어
  `learning.json` 1,453행 중 **989행이 날짜접두**이고, **사설 레코드 54건은 같은 경주가 두 행으로 존재**한다.
  적중률·회수율 집계가 경주 단위 dedupe 없이 행을 세면 왜곡된다. ⏸ **미확인·수정 전 별도 조사 필요.**

### 📌 "소실 아닌 미활용" 항목 (racing-data-safety 계열)
| 항목 | 상태 |
|---|---|
| 사설 `finalOdds` | ⚠ **미활용이 아니다(전제 정정)** — `payouts` 1순위 입력이라 **공식보다 우선 적용 중**. 위 오염이 그 결과 |
| 일괄등록 **HTML 출처(url/사설·공식 구분)** | 🔴 **미저장** — 사설/공식 판별과 파싱 사고 재현이 모두 불가. 배선 대상 |
| 일괄등록 **원본 HTML(또는 파싱 전 rows)** | 🔴 **미저장** — 카와사키 6건의 원인(3連単 vs 열밀림) 확정 실패의 직접 원인 |
| `finalOdds.raw[]`(조합 전수) | 🟠 `extractResultOdds` 는 채우는데 bulk 경로엔 없음 → 적중 조합 1개만 남아 **"샀다면 얼마였나"** 계산 불가 |

### 남은 일 (⏸ 승인 전 코드 변경 금지)
1. 🔴 `_parse_result_rows` 의 삼복승 컬럼이 **3連複인지 3連単인지** 확정 — 카와사키 개최일에 사설 결과표 HTML 확보 후 대조.
2. 🔴 **일괄등록 출처(url)·원본 rows 저장** 배선(위 미활용 3항목).
3. 🟠 `payouts` 우선순위 재검토 — **공식이 있으면 공식이 이겨야** 오염이 재발하지 않는다(현재는 사설 우선).
   ⚠ `_apply_result_learning` 은 판정·학습 핵심 경로 → **별도 승인 필요**.
4. 🟠 `learning.json` 날짜접두 이중행 dedupe 실태 조사(집계 왜곡 여부).
- 재현 스크립트 기준: 사설측=날짜접두 없는 combo형 `finalOdds` / 공식측=`정합 스윕` 메모 보유 `race_results.payouts` /
  **착순 동일 게이트 필수** / 100.0 검열 제외 / 상위1·3건 제외 병기.

## 🧪 [적중왕전개] Phase 1·2 완료 — 전개 확률 테이블 · **생존 셀 0개(정직한 음성 결과)**

> **방법론 변경(권대표 지시 2026-07-30)**: 몬테카를로/KNN 기각 → **조건부 확률 테이블(정확 매칭 룩업)**.
> 근거: 표본이 얇아 몬테카를로는 파라미터가 늘어 검증이 어렵고, KNN 가중치는 저차원에서 불필요한 자유도다.
> **산출물**: `tools/build_flow_table.py`(신규·**완전 읽기 전용**) → `data/simulation_db/flow_table.json`.
> 재현: `python tools/build_flow_table.py --split 2026-07-26`

### 🔴 결론 먼저 — **엣지 신뢰구간 하한 > 1.0 인 셀은 0개다(전기간·전반기·후반기 모두)**
- **억지로 셀을 쪼개 찾아내지 않았다.** 지금 데이터로 **경륜 각질쌍에는 밸류가 없다**는 것이 결과다.
- ⚠ 이로써 리플레이·통계가 뒤집은 전개 관련 가설은 **다섯 번째**다(급등 착시 · 페이크급락 일괄제외 ·
  진성급락 정렬 · `_scenario_plan` 부스팅 · **각질쌍 확률 테이블**).

### 작업1 — 입력 데이터 실측 (`keirin_profiles.jsonl` 500행 · 07-21~07-29)
| 필드 | 보유율 | 판정 |
|---|---|---|
| `pace` · `pace_counts` · `gait_lists` · `lead_count` | **95.0%** | ✅ 사용 |
| `line_pairs` | 94.0% | (미사용 — 셀 최소화 원칙②) |
| `result.top3` / `payout_quinella` | 91.0% / 90.8% | ✅ |
| `market` | 68.8% | ❌ **사용 불가** — `{rec_min_quinella, rec_top_combo}` 뿐, **전체 배당판이 아니다** |
| `field_size` | 40.4% | ❌ → `gait_lists` 합으로 대체(188/191 일치·불일치 3) |
| `riders[].style_type` | **1419명 중 7명(0.5%)** | ❌ 사실상 없음 |
| `lines` · `venue_tendency` | 0.2% | ❌ 백필 null(문서대로) |

- 🔴 **`declaredStyle` vs `styleType` 판정 — 표기 우선이 맞지만 지금은 쓸 수 없다.**
  · `declared_style` 은 **keirin_profiles.jsonl 에 아예 없다**. `declaredStyle` 배선이 **오늘(2026-07-30, app.py:21941)**
    들어갔고 프로파일은 **7/29 22:26 생성**이라 시간순으로 존재할 수 없다.
  · 실데이터 확인(원칙 5): `starters_store` 983행 중 **`declaredStyle` 42행(4.3%) · `styleType` 784행(79.8%)**.
    → **표기는 아직 4.3%**라 차원 축으로 쓰면 표본이 붕괴한다.
  · **판정: 이번 Phase 는 `gait_lists`(= `styleType` 추정 계열)로 진행.** ⚠ **한계를 명시** — 각질이 추정값이므로
    표기 기준으로 다시 재면 결과가 달라질 수 있다. **`declaredStyle` 보유율 70%+ 도달 시 재측정**할 것.
- ✅ **원칙① 준수 검증** — `pace` 가 `paceBonus` 오염 없이 **원본 선행 마릿수의 함수**임을 실측으로 확인:
  `선행≥3→빠른 · ≤1→느린 · 2→보통` **475/475 재현 · 불일치 0**. `paceBonus`·`record_score`·`comp_score` 미사용.

#### 6셀 조합 수 — **실질 4셀뿐이다**
| 각질쌍 | 빠른\|7두 | 빠른\|9두 | 보통\|7두 | 느린\|7두 | 보통\|9두 | 느린\|9두 |
|---|---|---|---|---|---|---|
| 선행+추입 | **1,847** | **660** | **752** | **112** | — | — |
| 선행+선행 | **778** | **182** | 76 | 0 | — | — |
| 추입+추입 | **695** | **418** | **744** | **402** | — | — |
| 선행+자유 / 자유+추입 / 자유+자유 | 23/15/2 | 0 | 8/16/0 | 2/8/1 | — | — |
| **경주 수** | **160** | **35** | **76** | **25** | **0** | **0** |
- 🔴 **보통\|9두·느린\|9두는 경주가 0건** → 6셀 설계가 실제로는 **4셀**이다.
- 🔴 **`선입` 은 전 셀에서 0건, `자유` 도 거의 0** → 각질 분류가 실질 **선행/추입 이진**이다. 각질쌍은 3종뿐.
- **n≥30 칸: 11개 / 18개(61%)**. 판정 불가 7개.

### 작업2 — 시장암시확률 분모 **확정: 정규화 `(1/배당)/Σ(1/배당)`**
- ⚠ **앞선 "빠른+선행2두 시장암시 11.1%"는 고정상수 방식이었다** — `tools/replay_pace_gait.py:21` `TAKEOUT=0.75`,
  즉 `0.75/배당`. 정규화가 아니다.
- ✅ **실측으로 둘을 비교했다**(완전 배당판 **330경주** = 7두 294 + 9두 36):
  | 두수 | Σ(1/배당) 중앙 | 함축 환급률 | 고정0.75 대비 비율 | 5% 초과 오차 |
  |---|---|---|---|---|
  | 7두 | **1.3484** (범위 1.094~1.415) | **0.7416** | 1.0113 | 7/294경주 |
  | 9두 | **1.3427** (범위 1.337~1.391) | **0.7447** | 1.0071 | 0/36경주 |
  → **두 방식 차이는 중앙 약 1%**. `TAKEOUT=0.75` 라는 감으로 박힌 상수는 **실측 0.742~0.745로 타당**(원칙 6 통과).
- **정규화를 채택한 이유**: ⓐ경주별 환급률 편차를 자기교정 ⓑ`Σ(암시)=1` 이 보장돼 엣지가 **제로섬**이 된다
  ⓒΣ가 비정상인 경주(실측 최저 1.094)를 게이트로 자동 배제. `SUM_INV ∈ [1.25,1.45]` 게이트로 **3경주 제외**.
- 🔴 **`opening>=100` 제외는 하지 않는다(대표님 지시에 대한 실측 기반 정정)**:
  · 처음 지시대로 100배+를 제외하니 **완전 배당판이 330 → 7경주로 붕괴**했다.
  · 재측정 결과 **경륜 7두의 100배+ 는 껍데기가 아니라 진짜 고배당**이다 — 100배+ 를 포함해도
    Σ가 1.348(환급률 0.742)을 유지하고, **100배+ 가 Σ에서 차지하는 비중은 중앙 2.66%** 다.
    껍데기(실자금 없음)라면 Σ가 1.34에 크게 못 미쳤을 것이다.
  · 제외하면 **분모 Σ가 작아져 시장암시확률이 과대추정**되고 엣지가 인위적으로 낮아진다.
  · 진짜 미수집 경주는 **Σ 건전성 게이트**가 잡는다(Σ=1.09·1.15 3경주 제외) — 이쪽이 옳은 방어선이다.
- **배당 시점**: 채택 스냅샷 **중앙 마감 0.0분 전 · 3분 이내 325/330**(오염 스냅샷 `odds_suspect`·
  `baseline_reset`·`next_race_blocked`·`after_close` 는 건너뜀). 시장 효율이 가장 높은 시점이라 엣지 측정에 적절.

### 작업3 — `tools/build_flow_table.py` (신규 · 읽기 전용)
- **모집단 = 시장 전판**(추천 선택 편향 제거) · 적중 = 조합 2두 ⊆ top2(복승).
- **신뢰구간 방식 명시**: 적중률 = **Wilson score interval(z=1.96)** / 엣지 = **부트스트랩 2,000회·시드 20260730·
  2.5~97.5 백분위**(비율의 비율이라 해석적 분산식이 없음 — 권대표 권장안 채택).
- 채택 규칙을 코드에 고정: `survivors()` 가 **`edge_ci[0] > 1.0`** 만 반환(점추정 1.0 아님).
- `--split YYYY-MM-DD` 로 ⑤ OOS 분할. 설계 6원칙을 파일 상단 docstring 에 명문화.
- 모집단 필터 실측: 채택 **296경주** / 제외 — 완전배당판없음 109 · 결과없음 36 · 각질없음 23 ·
  두수(6·8·5) 29 · 비경륜 4 · 배당판건전성 3.

### 작업4 — 셀 성적 + OOS 검증
| 셀 | 각질쌍 | n | 적중 | 실측 | 시장 | 엣지 | 엣지 CI | 상위1제외 | 상위3제외 |
|---|---|---|---|---|---|---|---|---|---|
| 빠른\|7두 | 선행+추입 | 1,847 | 98 | 5.31% | 4.99% | 1.063 | 0.895~1.230 | **1.052** | **1.031** |
| 빠른\|7두 | 선행+선행 | 778 | 54 | 6.94% | 6.57% | 1.056 | 0.823~1.297 | 1.036 | 0.998 |
| 빠른\|7두 | **추입+추입** | 695 | 8 | 1.15% | 2.27% | **0.507** | **0.192~0.868** | 0.445 | 0.319 |
| 빠른\|9두 | 추입+추입 | 418 | 7 | 1.67% | 1.50% | 1.117 | 0.364~1.930 | — | — |
| 보통\|7두 | 추입+추입 | 744 | 22 | 2.96% | 2.82% | 1.050 | 0.651~1.477 | 1.003 | 0.908 |
| 보통\|7두 | 선행+추입 | 752 | 47 | 6.25% | 6.58% | 0.950 | 0.715~1.204 | 0.930 | 0.891 |
| 느린\|7두 | 선행+추입 | 112 | 9 | 8.04% | 7.19% | 1.118 | 0.484~1.857 | 0.996 | 0.752 |
| 느린\|7두 | 추입+추입 | 402 | 15 | 3.73% | 4.15% | 0.899 | 0.495~1.322 | 0.840 | 0.723 |

- **양(+) 생존 0개** — 엣지 1.0 을 넘는 칸이 6개나 있지만 **전부 CI 하한이 1.0 미만**이다.
  대부분 상위 1·3건 제외에서 1.0 아래로 무너진다(원칙 2). **유일한 예외가 `빠른|7두 선행+추입`**:
  1.063 → 1.052 → 1.031 로 **극단값에 거의 무의존**(n=1,847). 그런데 CI 하한 0.895 로 유의성 미달.
- **⑤ OOS**: 전반기(<07-26) 69경주 생존 **0** · 후반기(≥07-26) 227경주 생존 **0** · **양쪽 생존 0**.

#### 🟠 유일하게 유의했던 신호는 **음(-) 방향**이다 — `빠른|7두 추입+추입`
- 전기간 **엣지 0.507 · CI[0.192~0.868](상한도 1.0 미만) · 이항검정 p=0.0238** → 시장이 **과대평가**.
- **발동률(배당판 비중) 20.9%** — 적정 구간(5~30%) 안. ✅ **원칙 4 순서 준수**(발동률 → 육안 → 성적).
- ✅ **발동 사례 3건 육안 확인 — 전부 의도한 상황이었다**:
  | 경주 | 선행 | 추입 | 결과 top3 | 추입+추입 조합 | 적중 |
  |---|---|---|---|---|---|
  | 07-24 いわき平 4R | 3,4,6 | 1,2,5,7 | 3-2-1 | 6개(7.9~520.8배) | 0 |
  | 07-24 いわき平 7R | 2,1,3,5,4 | 6,7 | 2-3-4 | 1개(245.9배) | 0 |
  | 07-24 伊東 5R | 5,6,7 | 1,2,3,4 | 6-7-2 | 6개(5.6~75.7배) | 0 |
- 🔴 **그런데 OOS 에서 유의성을 잃는다**: 전반기 n=154 **적중 0**(엣지 0.000·p=0.055) ↔
  후반기 n=541 엣지 0.620 **CI[0.242~1.053] — 상한이 1.0을 넘는다.**
  → 원칙 ④를 엄격히 적용하면 **생존 실패**. **"강한 후보"까지이고 확정 신호가 아니다.**
- ⚠ 다만 **독립 측정 4건이 같은 방향**이다: ⓐ이 테이블 ⓑ`replay_pace_gait` 빠른×추입포함 **z=−2.80**
  ⓒ`pace_stats.json`(빠른: 선행 56.7 ↔ 추입 37.7) ⓓ`_apply_pace_analysis` 매핑 방향 오류(빠른→추입 +15).
  **`paceBonus` 가 지금 정확히 이 반대 방향으로 통합등급에 가산되고 있다**(app.py:26943)는 점에서 실무 함의가 있다.

### 작업5 — 해석
- **시장이 이미 아는 셀(엣지 ≈ 1.0)**: `빠른|9두 선행+추입`(1.002) · `보통|7두 추입+추입`(1.050) ·
  `보통|7두 선행+추입`(0.950). 각질 정보가 **배당에 이미 반영**돼 있다. 어제 결론(엣지 0.83~1.05)과 일치.
- **시장이 모르는 셀**: **없다.** 이 프로젝트의 "첫 실체"는 아직 나오지 않았다.
- 🔴 **표본을 더 쌓으면 되는가 — 계산해 봤다.** `빠른|7두 선행+추입`(가장 유망·극단값 무의존)이
  CI 하한 1.0 을 넘으려면 반폭 0.168 → 0.063 이 필요하고, 반폭은 1/√n 이므로 **약 7.1배 = 조합 13,000개
  ≈ 빠른|7두 경주 1,100여 경주**가 필요하다(현재 **160경주**). 하루 20~30경주면 **40~55일**.
  · 즉 **"표본을 더 쌓으면 된다"는 낙관은 근거가 얇다** — 엣지가 1.063 그대로 유지된다는 가정에서만 성립한다.
- **판단(잠정)**: 각질쌍 단독으로는 밸류가 없다. 다음에 붙일 축은 **차원 확대(구장·거리)가 아니라**
  (원칙② 위반이고 표본이 더 얇아진다) **다른 정보원**이어야 한다. 후보: 라인 구도(`line_pairs` 94% 보유·이번엔 미사용) ·
  `declaredStyle`(표기 각질, 70%+ 도달 시) · 12~30배 구간과의 교차(어제 부차 발견과 겹침).

### ⏸ Phase 3(섀도우 기록)·4(추천 개입) — **착수 안 함(설계대로 별도 승인 대상)**
- 생존 셀이 0이므로 **지금 섀도우로 넘길 대상 자체가 없다.** 넘긴다면 대상은 양 신호가 아니라
  **음 신호(`빠른|7두 추입+추입` 회피)** 이고, 그건 추천 경로 개입이라 반드시 승인이 필요하다.
- ⚠ 이번 세션에서 `_final_picks`·EV필터·`_apply_pace_analysis`·`_scenario_plan` **일절 수정 없음**(지시 준수).

### 🔁 재검토 트리거
- ⓐ`빠른|7두` 경주 **500경주 이상**(현재 160) 또는 ⓑ`declaredStyle` 보유율 **70% 이상**(현재 4.3%) ·
  ⓒ`빠른|7두 추입+추입` 후반기 표본 **1,000조합 이상**(현재 541)에서 CI 상한이 1.0 미만 유지되는지 재확인.
- 재현 시 반드시 `--split` OOS 를 함께 돌리고, **상위 1·3건 제외 수치를 병기**할 것.

## ⏸ [2026-07-30 오후] paceBonus 3안 리플레이 — **판정 보류(표본 9경주)** · 역산은 원칙 3에서 실패

### 결론: 오늘은 못 정한다. **내일 재는 것이 맞다.**
- **완전 기록 표본 = 9경주**(`paceBonus`+`paceBonusBase`+결과 모두 보유 · 경륜 8·경마 1). **n<30 → 원칙 1로 판정 보류.**
- 배선이 **오늘 들어갔다**: `horses[]` 의 `gait`·`paceBonus`·`paceBonusBase`·`paceDetail` 은
  **7/28·7/29 로그에 0경주**, 7/30 만 101행. (⚠ 원칙 5의 다섯 번째 사례 — 배선 시각이 경주 시간대와 어긋남.)

### 🔴 표본을 늘리려 역산을 시도했고 **실패했다(원칙 3)**
- `paceBonus` 는 5개 입력의 결정론적 함수라 역산이 가능해 보였다(`pace`·`gait`·`grade`·`no`·`nH` — 전부 로그 보유).
  역산 가능 후보 **242경주**(경륜 170·경마 72)로 표본이 27배 늘어날 수 있었다.
- **그런데 오늘 저장된 실제 `paceBonus` 를 정답지로 대조하니 정확도 86.2%(일치 56 / 불일치 9 / 65)** 였다.
- ✅ **원인 확정 — 호출 순서다.** `_apply_pace_analysis`(**app.py:9789**)가 `_integrated_grades`(**app.py:10873**)보다
  **1,084줄 앞서 실행된다.** 보너스식의 `is_a = grade == "A"` 는 그 시점의 **전적 등급**을 보는데,
  로그에 저장되는 `grade` 는 **최종 통합등급**이다(`grade_reason` 이 "배당 14.3배 · 급락 30%+ +30" 처럼
  이상감지 근거인 것이 증거). **보너스 시점의 전적 등급은 어디에도 저장되지 않는다.**
  · 실제 불일치 예: `소노다 1R no=7 추입·빠른·grade=C → 저장 25 / 역산 15`(보너스 시점엔 A였다는 뜻) ·
    `no=2 추입·빠른·grade=A → 저장 10 / 역산 20`(보너스 시점엔 A가 아니었다).
- ⛔ **따라서 과거 로그 기반 3안 리플레이는 하지 않았다.** 86% 정확도로 회수율을 내면 오늘 CLAUDE.md에
  적어둔 **경계 시뮬 v1(137.9%) → v2(85.6%)** 사고와 같은 유형이 된다. **숫자가 정교해도 무의미하다.**
- ✅ **내일부터는 역산이 필요 없다** — `paceBonusBase` 가 저장되므로 `base + 안별 보너스`로 **정확 재현**된다.

### ⚠ 리플레이가 재현하지 못한 코드 경로 (명시 요구사항)
요구된 체인은 `paceBonus → totalScore → _integrated_grades → keyHorses → _final_picks` 다.
| 단계 | 재현 | 비고 |
|---|---|---|
| `paceBonus` → `totalScore` | ✅ 정확 | `paceBonusBase + 보너스` (9경주) |
| `_integrated_grades` → `keyHorses` | 🟠 **근사** | 점수 상위 3두로 대체. 실제는 이상감지 60%+전적 40% 가중 |
| `_final_picks` | ❌ **미재현** | 1,084줄 · EV필터 · `_lowodds_exempt` · tier 경계 · 히스테리시스 |
→ **그래서 회수율·적중률은 산출하지 않았다.** 아래는 **결정론적 기계 효과만**이다.

### 📊 3안의 기계 효과 (n=9 · 성적 판정 아님 · 상위3두 = keyHorses 근사)
| 지표 | ①현행 | ②반전 | ③제거 |
|---|---|---|---|
| **추입+추입 조합 생성** | **13** | **2** | **9** |
| 선행+선행 조합 생성 | 2 | **15** | 2 |
| 선행+추입 조합 생성 | 12 | 10 | 16 |
| 상위2 적중 | 2/9 | 2/9 | 1/9 |
| 상위3에 정답 1착 포함 | 6/9 | 7/9 | 6/9 |

- ✅ **엔진 음 신호와 방향이 일치한다** — 현행이 **추입+추입을 가장 많이 만든다(13)**. 그 칸이 바로 엔진에서
  **엣지 0.507(CI 0.192~0.868·p=0.024)** 로 유일하게 유의하게 나빴던 셀이다. 반전은 2로 줄이고
  **선행+선행(엣지 1.056)을 15로 늘린다.** 즉 **반전은 나쁜 칸 → 나은 칸으로 생성을 이동시킨다.**
- ⚠ **적중 지표는 n=9로 전혀 변별력이 없다**(2/2/1 · 6/7/6). **결론에 쓰지 않는다.**
- 육안으로 보면 빠른 페이스에서 현행은 상위3을 **추입·추입·추입**으로 채우는데(아오모리 2R·4R·와카야마 1R)
  실제 결과는 선행 우세였다. 반전은 **선행·선행·선행**으로 채운다. 방향 참고까지만.

### 🔁 내일 재측정 조건
- `paceBonus`+`paceBonusBase`+결과 보유 경주 **30경주 이상**(현재 9). 경륜 하루 20~70경주라 **1~2일**이면 도달.
- 그때는 ⓐ종목별 분해 ⓑ기간분할 OOS ⓒ상위 1·3건 제외를 **전부** 낼 수 있다.
- ⛔ `_apply_pace_analysis` **수정 금지 유지**(권대표가 3안 결과 확인 후 결정).

## 🔍 [2026-07-30] line_pairs 축 예비 조사 — **라인도 시장이 이미 안다(엣지 하한 미달)**

> 각질쌍이 죽어 다음 후보로 조사했다. **라인은 주최측 표기 원본이고 각질은 추정값**이라는 점이 근거였다.
> ⚠ **테이블 생성은 다음 세션**(지시대로 설계·표본 계산까지). 다만 엣지는 미리 재 두었다 —
> **죽은 축에 테이블을 세우는 낭비를 막기 위해서**다.

### 구조 · 보유율
- `line_pairs` **경륜 496행 중 470(94.8%)** · 엔진 모집단 296경주는 **100% 보유**.
- 구조: `{combo:[lead,mark], lead, mark, lead_style, mark_style, label}`.
  🔴 **`lead_style`/`mark_style` 은 1,069쌍 중 6개만 채워져 사실상 비어 있다**(각질과 교차하려 해도 못 한다 —
  결과적으로 "각질과 교차하지 말라"는 지시와 일치).
- 라인 수 분포(엔진 모집단 296경주): **1라인 26 · 2라인 153 · 3라인 110 · 4라인 7**.

### 셀 정의 후보 판정
| 후보 | 판정 |
|---|---|
| ⓐ **라인 수**(1/2/3/4) | ✅ 축으로 쓸 수 있다. 2라인·3라인이 263경주로 주력 |
| ⓑ **최상위 라인 길이** | ⛔ **불가** — `combo` 가 (선두, 2번수) **2두 고정**이라 296경주 전부 길이 2다. 분산 0 |
| ⓒ **같은 라인 / 다른 라인** | ✅ 분리도가 가장 크다(아래) |

### 🔴 그런데 엣지로 보면 밸류가 없다
| 구분 | n | 적중 | 실측 | 시장암시 | 엣지 | 엣지 CI | 중앙배당 | 상위1제외 | 상위3제외 |
|---|---|---|---|---|---|---|---|---|---|
| **같은라인** | 690 | 105 | **15.22%** | 14.25% | **1.068** | **0.893~1.248** | 11.8 | 1.058 | 1.038 |
| 다른라인 | 2,100 | 67 | 3.19% | 3.56% | 0.897 | 0.698~1.103 | 39.7 | 0.884 | 0.858 |
| 라인미상 | 3,951 | 124 | 3.14% | 3.11% | 1.008 | 0.854~1.163 | 72.5 | 1.000 | 0.984 |

- **적중률은 같은라인이 다른라인의 4.8배(15.22% vs 3.19%)** — 각질쌍의 분리도(1.15~8%)보다 훨씬 크다.
- 🔴 **그러나 그 차이가 전부 배당에 들어 있다**(중앙배당 11.8 vs 39.7). 엣지 1.068 · **CI 하한 0.893 → 미달.**
  **"같은 라인이 잘 온다"는 것을 시장은 이미 안다.** 각질과 결론이 같다.
- ⚠ 단 **극단값 무의존**(1.068 → 1.058 → 1.038)이라 `빠른|7두 선행+추입`(1.063→1.031)과 **같은 패턴**이다.
  유의해지려면 역시 표본 약 7배가 필요하다.

### ✅ 다음 세션에 실익이 있는 지점 — **`line_pairs` 가 아니라 `並び` 원본을 써야 한다**
- 🔴 **커버리지 구멍**: `line_pairs` 는 라인마다 (선두, 2번수) **2명만** 담는다 →
  **7두에서 중앙 4/7(57%) · 9두에서 6/9(67%)** 만 라인에 잡힌다.
  그래서 **`라인미상` 버킷이 3,951개로 최대(57%)** 이고, 이 상태로는 축이 반쪽이다.
- ✅ **완전 라인 구도의 원본은 이미 저장 중이다** — `starters_store[rk].line`(並び):
  **7/28 26/37 · 7/29 44/68 · 7/30 9/9 경주**. (`keirin_profiles.jsonl` 의 `lines` 0.2% 는 **백필 탓**이고,
  원본이 없는 게 아니다 — 어제 "백필분은 null" 로 적어둔 항목이 실제로는 살아 있었다.)
- **다음 세션 설계**: `並び` 를 파싱해 **전체 라인 소속**을 복원 → `라인미상 57%` 제거 후 ⓐ·ⓒ 재측정.
  그래도 엣지 하한이 1.0을 못 넘으면 **라인 축도 종결**한다.

## ✅ [2026-07-30] declaredStyle 전진 누적 — **정상 작동 확인(오늘 저장분 100%)**
| 저장일 | 행 | `declaredStyle` | `styleType` |
|---|---|---|---|
| 2026-07-27 | 63 | 0 (0.0%) | 0 (0.0%) |
| 2026-07-28 | 312 | 0 (0.0%) | 269 (86.2%) |
| 2026-07-29 | 566 | 0 (0.0%) | 473 (83.6%) |
| **2026-07-30** | **56** | **56 (100.0%)** | 56 (100.0%) |
- **누적은 5.6%(56/997)지만 오늘 저장분은 100%** → 배선 정상. 원칙 5 통과(실데이터 건수로 확인).
- 누적 비율이 낮은 이유는 과거 스키마 행이 남아 있어서다. `_KEIBA_FORM_DONE`+`source` 게이트 때문에
  **기존 경주는 갱신되지 않으므로**, 비율은 **새 경주가 쌓이는 속도로만** 오른다(하루 60~90행).
  → **70% 도달까지 대략 1~2주.** 그 시점에 각질 축을 **표기 기반으로 재측정**할 가치가 있다
  (현 엔진은 `gait_lists` = `styleType` 추정 계열을 썼다).

## 🔴 [2026-07-30 3차] 두 축 겹침률 · gradeAtBonus 배선 · 라인 복원 — **결합 금지 확정**

### 작업1 — 겹침률 93.7% → **두 축은 사실상 하나. 결합 금지.**
> 권대표 가설: *"경륜 라인은 자력형(선행)이 앞에 서고 마크가 뒤에 붙는 구조이므로
> 같은 라인 조합은 선행+추입인 경우가 많을 것"* → **정확히 맞았다.**

| 측정 | 값 |
|---|---|
| 같은라인 715조합의 각질쌍 분포 | **선행+추입 670(93.7%)** · 추입+추입 38(5.3%) · 선행+자유 6 · 자유+추입 1 |
| 선행+추입 3,493조합 중 라인관계 | 같은라인 670(**19.2%**) · 다른라인 1,076(30.8%) · 라인미상 1,747(50.0%) |
| ⓐ 같은라인 기준 겹침률 | **93.7%** |
| ⓑ 선행+추입 기준 겹침률 | 19.2% |
| ⓒ 자카드 | 18.9% |

- 🔴 **겹침이 비대칭이다 — 같은라인 ⊂ 선행+추입(포함관계)**. 같은라인의 93.7%가 선행+추입이고,
  선행+추입의 19.2%만 같은라인이다. 즉 **같은라인은 선행+추입의 더 좁고 강한 부분집합**이다.
- **판정: 기준 최대 93.7% ≥ 70% → 결합 금지.** 결합 셀(같은라인 ∩ 선행+추입)은 n=670 으로
  같은라인 축(715)의 **93.7% 그대로**다. 표본만 45개 줄고 **새 정보는 없다.**
  (참고로 결합 성적은 엣지 1.107 · CI[0.934~1.298] — 역시 하한 미달이라 결합해도 유의해지지 않는다.)
- ✅ **통합 방향: 라인 축(같은라인)을 채택하고 각질쌍은 버린다.**
  근거 — 선행+추입 전체 엣지 1.063 ↔ 그중 **같은라인만 1.107 · 다른라인 0.81 · 라인미상 1.03**.
  같은라인이 선행+추입 안의 **유효 부분**이다. 각질쌍은 라인의 대리변수(proxy)였을 뿐이다.
- 🔍 교차표에서 눈에 띈 것(⚠ **탐색적 · 다중비교라 결론 아님**):
  `다른라인 × 추입+추입` e=1.11(n=554) · `라인미상 × 선행+선행` e=1.11(n=549) ·
  `같은라인 × 추입+추입` e=0.39(n=38·경계) — 8칸을 동시에 보면 일부가 1.1을 넘는 건 우연으로도 생긴다.

### 작업2 — `gradeAtBonus` 배선 완료 · **역산 정확도 88.3% → 100%**
- ⚠ **표현 정정(권대표 지적 · 타당)**: 어제 적은 *"gradeAtBonus 를 저장하면 과거 로그 역산이 영구 가능"* 은
  **부정확했다.** 과거 로그엔 이 필드가 없어 **소급 복원은 불가**하다.
  **실제 가치는 "오늘부터의 재현성 확보"** — 앞으로 paceBonus 공식이 바뀌어도 **저장된 값으로 과거를
  재계산할 수 있다**는 뜻이다. 1필드이므로 넣을 값어치는 충분하다는 판단으로 배선했다.
- ✅ **`_apply_pace_analysis` 무수정으로 배선했다**(수정 금지 지시 준수). 배선 지점은
  **`_build_analysis_log`(app.py:14322 근처) 1필드 추가**뿐이다 — `"gradeAtBonus": f.get("grade")`.
  · 성립 근거(코드에서 직접 확인): form 의 `grade` 는 `_keiba_build_form`(22816)·`_jra_build_form`(26603)이
    **빌드 시점에** 넣고, `_integrated_grades`(3979)·`_integrated_adaptive`(4247)는 **새 리스트(out)에만**
    등급을 부여해 **form 을 변형하지 않는다.** 그리고 `an["form"]` 은 `_apply_pace_analysis` 가
    변형한 바로 그 객체다(`f.get("gait")`·`f.get("paceBonus")` 가 이미 동작하는 이유).
    → 로그 작성 시점의 `f["grade"]` 가 곧 **보너스 계산 시점의 전적 등급**이다.
- ✅ **실데이터 확인(원칙 5)**: `gradeAtBonus` **26행 저장**(패치 후 갱신된 경주분).
- ✅ **효과 검증** — 같은 공식·grade 출처만 바꿔 대조:
  | grade 출처 | 일치 | 불일치 | 정확도 |
  |---|---|---|---|
  | 기존 `grade`(통합등급) | 113 | 15 | **88.3%** |
  | 신규 `gradeAtBonus` | 26 | **0** | **100.0%** |
- ⚠ 저장만이다. 추천 경로 무개입(app.py **+12줄 · 삭제 0줄**).

### 작업3 — 並び 라인 복원: **예측이 절반 맞았다**
- 🔴 **먼저 `line`(並び)의 실체를 정정한다** — 어제 "완전 라인 구도가 있다"고 적었으나,
  실제 저장 형태는 **평탄한 마번 순서 리스트**(`[2,4,7,5,1,3,6]`)로 **라인 경계 구분자가 없다.**
  라인은 `_simulate_race_flow_keirin` 휴리스틱(**자력형 선수마다 새 그룹 시작**)으로 **추정**해야 한다.
- 🔴 **조인 가능 표본이 매우 적다**: `starters_store[rk].line` 80경주는 **키에 날짜가 없어**(0/80)
  과거 경주와 조인할 수 없다(라이브 캐시라 같은 경마장·경주번호가 날짜마다 덮어써진다).
  조인 가능한 것은 **분석 로그의 `raw_profile.line` 22경주**뿐이고, 엔진 게이트까지 통과한 것은 **11경주**다.
- **증가분 먼저(지시 순서 준수) · n=11경주 · 231조합**:
  | 라인관계 | 기존(line_pairs) | 복원(그룹 전체) | 증감 |
  |---|---|---|---|
  | 같은라인 | 25 | **78** | **+53 (+212%)** |
  | 다른라인 | 68 | 153 | +85 |
  | 라인미상 | 138 | **0** | −138 |
  · 복원 그룹 크기: 1두 6 · 2두 12 · **3두 8 · 4두 2 · 5두 3** → `line_pairs` 가 놓치던 3번째+ 멤버가 실재한다.
- **예측 검증 — 둘로 나눠 기록한다**:
  · ❌ **"복원해도 같은라인 표본이 많이 늘지 않을 것"은 틀렸다** — +212% 늘었다(라인미상 138 중 53개가 같은라인).
  · ✅ **그 예측의 취지("신호가 강해지지 않는다")는 맞았다** — 같은라인 **적중률 20.0% → 7.69%로 떨어졌다**
    (새로 편입된 53조합 중 적중 1개뿐). 엣지 1.776 → **1.330** · CI[0.517~2.195]로 **하한 여전히 미달**.
    권대표가 근거로 든 *"라인미상 3.14% ≈ 다른라인 3.19%"* 논리가 결과적으로 옳았다 —
    **라인미상에 섞여 있던 같은라인은 '약한' 같은라인이었다.**
- ⚠ **n=11경주(231조합)로 판정 불가**(원칙 1). 위 수치는 **증가분·방향 참고까지만**이다.
- **다음 단계**: `raw_profile` 이 쌓여 30경주+ 되면 재측정. 단 위 결과대로면 **기대는 낮춰야 한다.**

### 작업4 — paceBonus 3안: **보류 유지(12경주)**
- 아침 9경주 → 현재 **12경주**(경륜 10·경마 2). 30까지 **18경주 부족** → 원칙 1로 보류 유지.
- 🔴 **30경주에 도달해도 `_final_picks` 미재현 문제는 그대로다.**
  **회수율을 낼 때는 "조합 생성 변화"까지만 유효**하고 그 뒤(EV필터·`_lowodds_exempt`·tier·히스테리시스)는
  재현 대상이 아님을 **반드시 명시**할 것.
- 유효한 결론은 이미 나와 있다(기계 효과·n=9): **현행이 추입+추입을 13개 만들고**(엣지 0.485 셀)
  **반전은 2개로 줄이며 선행+선행(1.049)을 15개로 늘린다** → **나쁜 칸에서 나은 칸으로 생성이 이동한다.**

### 작업5 — 🔴 해석 추가: **"약한 신호"가 아니라 "정확히 1.0"일 가능성이 반반이다**
> 각질·라인 두 축이 **모두 엣지 1.06대 · CI 하한 0.89대**로 수렴했다. 이건 두 가지로 읽힌다.

| 해석 | 내용 | 함의 |
|---|---|---|
| ⓐ 약한 신호가 실재 | 진짜 엣지가 1.05~1.07이고 표본이 부족해 유의하지 않을 뿐 | 축적하면 유의해진다 |
| ⓑ **진짜 엣지가 1.0** | 시장이 완전 효율적이고 **1.06은 표본 노이즈** | 축적하면 **1.0으로 내려간다** |

- 🔴 **현 데이터로는 ⓐ와 ⓑ를 구분할 수 없다. 가능성은 반반이다.**
  **축적에 기대를 걸기 전에 이 사실을 전제로 삼아야 한다** — "표본만 쌓으면 된다"는 낙관은 근거가 없다.
- **판별 근거로 삼을 관측(이번에 하나 확보)**: 프로파일 500행 → **732행**(채택 296→307경주)으로 늘렸을 때
  · `빠른|7두 선행+추입` 엣지 **1.063 → 1.071** (n 1,847→1,919 · CI 하한 0.895→**0.904**)
  · `빠른|7두 추입+추입` 엣지 **0.507 → 0.485** (CI 상한 0.868→**0.821** — 음 신호는 더 강해졌다)
  → 표본 +4%에서 **1.0으로 내려가지 않았다**. ⓑ의 반증까지는 아니지만 ⓐ에 약간 유리한 첫 관측이다.
  ⚠ +4%로는 판별 불가다. **이 지표를 매 갱신마다 추적**해 1.0 수렴 여부를 보는 것이 정직한 방법이다.
- **엔진 재생성 결과(732행)**: 채택 **307경주** · **생존 셀 0개 불변**(전기간·전반기 69·후반기 238 전부).

### ⛔ 착수 금지 유지
- `_apply_pace_analysis` 수정 금지 · Phase 4(추천 개입) 금지 · **결합(같은라인 ∩ 선행+추입) 금지**
  (작업1에서 겹침 93.7%로 **결합 자체가 기각**됐다 — 금지가 아니라 **불필요**해졌다).

## 🩺 [2026-07-30 13:05] 당일 기록 상태 점검 — **경륜 수집 창 이탈(발주완료 22경주 중 64%)**

### 정상인 것
| 항목 | 상태 |
|---|---|
| 분석 로그 | 오늘 24경주 · 최근 0.1분 전 갱신 · **발주완료 22경주 100% 로그 보유** |
| 결과 기록 | 21경주 저장 · **복승 확정배당 21/21(100%)** · 미입력 3경주(진행 중) |
| `raw_profile` | **24/24(100%)** — 어제 1.9%에서 급개선. `raw_profile.line` 18/24 |
| `gait`·`paceBonus`·`paceBonusBase` | 187행 중 181(97%) |
| `gradeAtBonus`(오늘 배선) | 92/187(49%) — 패치 후 갱신분만이라 정상, 계속 상승 |
| `declaredStyle` | starters_store 오늘 저장분 **84/84(100%)** |
| `line`·`tendency` | 오늘 저장 12경주 **12/12** |
| ai_training · race_report | 각 오늘 21건 · daily_summary 8분 전 갱신 |
| `det_review` · `pace_analysis` | **정상 작동 중**(아래 오탐 주의) |

### 🔴 문제 — 경륜만 수집 창을 놓친다 (미해결 #2 재발)
발주 완료 22경주 기준: **로그 100% · 스냅샷 3개+ 8경주(36%) · 부족/없음 14경주(64%)**

| 트랙 | 종목 | 발주완료 | 스냅3+ | 스냅0~1 | 첫 수집 시점(발주 대비) |
|---|---|---|---|---|---|
| **소노다** | 경마(NAR) | 5 | **5** | 0 | **−8.4 ~ −9.3분** ✅ 완벽 |
| 아오모리 | 경륜 | 7 | 2 | **5** | 1·2경주 −9.2분 ✅ → **3경주부터 +1.3~+5.5분** 🔴 |
| 기후 | 경륜 | 5 | **0** | **5** | 3경주만 +2.2분 · 나머지 **0틱** 🔴 |
| 와카야마 | 경륜 | 5 | 1 | 4 | 1경주 −0.5분(간신히) · 4경주 +0.9분 · 2·3·5 **0틱** 🔴 |

- 🔴 **전환점: 아오모리 2경주(08:50 발주) → 3경주(09:11 발주) 사이에 경륜 수집이 무너졌다.**
  그 전(08:30·08:50)은 발주 9분 전부터 24틱씩 정상이었다.
- ✅ **현재는 회복된 상태** — 와카야마 6경주(13:07 발주)는 −4.2분에 6틱 정상 수집 중.
- ✅ **서버·루프는 살아 있다** — 소노다(경마)는 같은 시간대에 5/5 완벽했다. **경륜 경로만의 문제**다.

### ⚠ 원인 판정 시 주의 — `minutes_before=None` 을 오독하지 말 것
- 실패 틱은 전부 `minutes_before=None` + `after_close=True` 였다. 처음엔 **"발주시각 미확정"** 으로 읽었으나 **틀렸다.**
- `_history_append`(app.py:1~) 실제 코드: **`minutes_before = mb if mb >= 0 else None` · `after_close = mb < 0`**
  → **`mb=None` 은 "발주시각을 모른다"가 아니라 "마감이 지났다"는 뜻**이다(하위호환용 처리).
- 그리고 `data/today_schedule.json` 은 **10트랙 106경주 전부 `postEpoch` 보유(100%)** 였다.
  **스케줄은 정상이고, 수집이 마감 후에야 도달한 것**이 사실이다. (CLAUDE.md 실패 사례 B와 같은 함정 —
  같은 오독을 두 번째로 피했다.)
- 🔍 **후보 단서(미확정)**: `소노다 12경주`가 **발주 224분 전인데 지금도 수집 중**(15틱 누적).
  수집 창(발주 10분전~2분후) 밖 경주가 사이클을 소비하면 임박 경주를 밀어낼 수 있다.
  ⚠ 단정하지 말 것 — 창 밖 수집은 현재 1경주뿐이고, 08:50→09:11 전환의 직접 증거는 아직 없다.

### 🩹 자동 경보는 작동하나 커버리지가 부족하다
- `data/collect_gaps/2026-07-30.json` **4건 기록**(기후 2·와카야마 3·기후 3·와카야마 5).
- 그런데 **실제 스냅샷 부족은 14경주** → **경보가 10건을 놓쳤다**(`_snapshot_shortage_check` 조건이
  '발주 3~15분 경과'라 그 창을 벗어나면 기록되지 않는 것으로 보인다 — 확인 필요).
- `/api/ingest/rejects` **0건** → 어제 배선한 `사설 우선(이력 기록 생략)` 등 신규 사유는 **미발동**.
  즉 이번 공백은 **게이트 거부가 아니라 수집 시도 자체의 부재**다.

### ⚠ 점검 중 낸 오탐 2건 (원칙 8 — 파일명 패턴을 확인하고 셀 것)
| 항목 | 잘못된 1차 판정 | 실제 |
|---|---|---|
| `det_review` | "오늘 0건" | **정상** — 파일명이 `20260730_...`(하이픈·언더바 없음)인데 `2026-07-30`/`2026_07_30` 로 검색했다 |
| `pace_analysis` | "오늘 0건" | **정상** — 파일명에 **날짜가 없다**(`기후_4경주.json`). 날짜로 필터한 것이 오류 |
- 교훈: 산출물 건수를 셀 때 **디렉터리별 파일명 규칙을 먼저 확인**한다. 세 저장소가 서로 다른 규칙을 쓴다
  (`2026_07_30_X.json` / `20260730_X.json` / `X.json`).

### 📌 여전히 0% 인 필드 (배선 안 했으므로 예상된 결과)
`winOdds` 0/84 · `pop` 0/84 · `weight` 0/84 · `distance`·`surface`·`trackCond` 0/12
— 스키마 드리프트 🔴1·2·3 순위 항목이며 **배선 승인 대기 상태 그대로**다.

## ✅ [2026-07-30 13:20] 수집 관측 2건 배선 완료 — **즉시 성과: 1경주 수집에 27.5초**

### 배선 1 — 수집 사이클 관측 (`GET /api/collect/cycles`)
- 신규: `_collect_cycle_begin`(app.py:31355) · `_collect_cycle_end`(31388) · 엔드포인트(31436) +
  `_COLLECT_CYCLES`(deque 400) · `_COLLECT_TARGET_SEEN` · `data/collect_cycles/<날짜>.json`(60초 스로틀).
  `import collections` 추가(기존 지역 `from collections import Counter` 와 무충돌).
- **답하려는 질문 3개**: ⓐ그 경주가 **수집 창에 들어온 적이 있는가**(`targetSeen` — 없으면 스케줄 편입 시각 문제)
  ⓑ**사이클이 얼마나 걸리는가**(창 12분보다 길어지면 경주가 통째로 누락) ⓒ**창 밖 경주가 갱신되는가**
  (`outsideWindowFresh` — `_targets` 는 창 안만 담으므로 창 밖 갱신은 **다른 경로**라는 뜻).
- ⚠ 완전 읽기 전용 관측이다 — 대상 선정·수집·추천·학습에 개입하지 않는다.
- 🔴 **첫 사이클에서 바로 잡혔다**: `[수집관측] 사이클#1 대상 1 · 27.5초 · ⚠느림`
  **경주 1건 수집이 27.5초**다. 루프 주기가 30초이므로 **1경주만으로 사이클이 거의 포화**된다.
  창 안에 경주가 여러 개면 병렬 6이라도 밀린다 → 오전 경륜 3트랙 동시 개최 때 무너진 것과 정합한다.
  ⚠ 아직 1사이클 관측이다(원칙 1). **누적 후 판정**할 것 — `elapsedSec.max`/`avg` 를 추적한다.

### 배선 2 — 스냅샷 부족 경보 창 확대 + **침묵 억제 버그 수정**
- `SNAPSHOT_WARN_FROM_SEC=180` · `SNAPSHOT_WARN_TO_SEC=7200` 상수화(종전 하드코딩 `180~900`=3~15분 → **3~120분**).
  7/30 실측에서 부족 14경주 중 경보가 4건만 남은 원인이 이 12분 창으로 추정됐다(스케줄 30분 주기 갱신 탓에
  창을 지난 뒤 목록에 편입된 경주가 있다). 경주당 1회 게이트가 있어 로그 도배는 늘지 않는다.
- 🔴 **함께 고친 버그**: 종전 코드는 이력 읽기 **전에** `_SNAP_WARNED.add(key)` 했다. 그래서 읽기가 예외로
  실패하면 **판정을 한 번도 못 했는데 영구히 '검사 완료'로 표시**돼 그 경주는 다시 경보 대상이 되지 않았다
  — **조용한 실패를 잡는 함수가 스스로 조용히 실패**하고 있었다. 판정 완료 후에만 표시하도록 이동(예외 시 재시도).

### ⚠ 배선 중 서버가 한 번 내려갔다 (원인·복구 기록)
- 편집 후 `/api/day/races` 가 **HTTP 000**(프로세스 부재). `logs/det_review` 가 13:13까지 갱신됐으므로
  그때까지는 살아 있었고, **debug 리로더 재시작 실패**로 보인다.
- ✅ **내 수정이 원인은 아니다** — `python -c "import app"` **IMPORT OK**, `python app.py` 도 정상 기동해
  요청을 처리했다(중복 라우트·문법·런타임 오류 없음).
- ✅ **복구**: `PYTHONIOENCODING=utf-8` + `Start-Process`(detached)로 상주 기동 · 로그 `logs/server_stdout.log`.
  ⚠ **교훈**: `python app.py` 를 **포그라운드 + timeout** 으로 띄우면 그 서버도 함께 죽는다.
  점검용으로 띄울 때는 반드시 detached 로 하고, 끝나면 살아 있는지 확인할 것.

### 🐛 로그에서 발견한 기존 버그 2건 (⏸ 수정 승인 대기 · 추천 경로 포함)
| 증상 | 빈도 | 판단 |
|---|---|---|
| `[경륜 B라인v2] 에러(무시): name 'key_horses' is not defined` | **26회** | 🔴 **미해결 버그 #3(B라인 미발동)의 직접 원인으로 보인다.** `key_horses` 는 `_compression_pattern`(5713)·`_reversal_backing_bets`(5862) 등의 **매개변수 이름**으로만 존재하고, 경륜 B라인v2 블록에는 그 이름이 정의돼 있지 않다 → 블록이 **매번 예외로 통째 스킵**된다. 그래서 "조건 충족에도 미추가" 였다 |
| `[결정론 검수] 실패(무시): cannot access local variable '_gemini_pending'` | **29회** | 🟠 det_review 가 **부분 실패** 중. 파일 자체는 정상 생성되고 있으나(와카야마 6경주 SAFE 판정 확인) 일부 호출이 죽는다. CLAUDE.md 가 경고한 *"`_gemini_pending` 묶음을 공유하므로 함께 죽는다"* 가 실현된 형태다 |
- ⚠ 둘 다 `except` 로 삼켜져 **콘솔에만** 남았다 — stdout 을 파일로 남기지 않으면 영구히 안 보인다.
  이번에 `logs/server_stdout.log` 로 리다이렉트한 것이 발견 계기였다(**관측이 먼저**라는 원칙의 실례).
- 📌 `det_review` 는 `급락미반영` 항목이 **비활성**이다(`DET_ITEM_DROP_ENABLED=False`) — 설계대로다(④가 의도와
  다른 것을 재던 항목).

## 📐 [설계안 · 승인 대기] 자동 감지 + 알림 재건 (2026-07-30)
> **문제**: 오늘 발견 3건(아오모리 4R 마번 어긋남 · 5R 배당 5.4배 · 배당판 불일치)이 **전부 육안 발견**이다.
> 외부에 있으면 **0건**이 된다. 버그는 계속 있는데 아무도 모르는 상태가 가장 큰 리스크다.

### 화면 불일치 자동 감지 3항목 — 발동률 실측
| 항목 | 판정식 | 발동률 | 판정 |
|---|---|---|---|
| ① `quinella` ≠ `finalQuinellas[0]` | 조합 비교 | **50.2%**(312/622) | ❌ 과다 — 조건 축소 필요 |
| ② 같은 조합이 리스트마다 다른 배당 | 값 집합 크기>1 | 패치 전 54.8% → **패치 후 0%**(n=1) | ✅ **해소됨**(표본 부족·재확인 필요) |
| ③ **추천 마번이 출주 명단에 없음** | `∪(추천 마번) − 출주 마번 ≠ ∅` | **4.0%**(15/374) | 🟠 5% 근접 — **채택 후보** |

**③ 발동 사례 3건 육안 확인(원칙 4 준수) — 전부 진짜 오표시였다:**
| 경주 | 출주 명단 | 유령 마번 | 추천 |
|---|---|---|---|
| 고쿠라 9R | 1~6 (6두) | **7** | 삼복승 `1+4+5`·`1+5+6` (`raceHorseCount=7`) |
| 부산 1R | 1,2,3,4,7~11 | **6** | 삼복승 **`1+6+8`** ← 없는 말 추천 |
| 제주 1R | 1~8,10 | **9** | 복승 **`1+9`**·삼복승 `1+2+9` ← 없는 말 추천 |
- **미해결 버그 #4 「14번 마번 오표시」와 같은 계열**이며, **결정론으로 잡힌다.**
- 공통점: `raceHorseCount` 가 실제 출주 두수보다 **1 크다**(7↔6 · 9↔9 · 10↔9) → **결번(取消) 처리 누락** 의심.
- ⚠ 4.0%는 적정 구간(5~30%) **바로 아래**다. 다만 **오탐이 0건**(3/3 진짜)이라 채택 가치가 있다.

### 알림 재건 설계 (검증된 항목만 · 도배 방지)
- **즉시 알림**(경주 진행 중): ③유령 마번 · 스냅샷 0건(`_snapshot_shortage_check` 기존) ·
  A 삼복승 정합 위반. **각각 on/off 플래그**(`ALERT_ITEM_*`)로 개별 차단 가능하게.
- **일일 요약**(`_daily_learning_sched` 에 편입 — 새 스케줄러 불필요):
  발동 건수 · 필드 보유율(`winOdds`·`paceBonusBase`·`drops_raw`) · `det_review` SAFE/WARNING 분포 · 결과 미입력.
- 🔴 **표기 규칙**: **A 삼복승 정합(11.6%·z=−2.75)만 성적 검증됐다.** 나머지는 발동률만 확인된 상태이므로
  **"정보"로 표기**하고 **검증 전에는 경고로 쓰지 않는다.**
- **발송 이력**: `data/kakao_sent/<날짜>.json`(append-only) — 발송시각·raceKey·mb·조합 스냅샷·원문.
  카나자와 7R 사건("카톡 후 조합이 바뀜")을 코드가 자동 대조할 수 있게 된다.
- ⚠ 카카오를 끈 이유가 **Gemini WARNING 99.5% 도배**였다. 같은 실수를 막으려면
  **발동률이 확인된 항목만** 보내고, 항목별 일일 상한을 둔다.

### 외부 접근 현황 (작업3 파악)
- `/admin` 패널이 **이미 존재**한다(`admin_page.py` · Blueprint `url_prefix="/admin"`).
  `admin_home`·`admin_status`·`admin_races`·`admin_race_detail` 등 모바일용 화면 구성.
- 🔴 **인증이 사실상 없다** — `_premium_gate` 는 `PREMIUM_ENFORCED=False` 라 **즉시 통과**,
  `_request_is_authed` 는 *"미강제 상태라 미사용"* 주석대로 실제 검사를 하지 않는다.
- 🔴 **바인딩**: `PORT` 환경변수가 있으면 `0.0.0.0`, 없으면 `127.0.0.1`. 현재 로컬 바인딩이므로
  **Tailscale(100.80.114.84)로는 접근되지 않을 가능성이 높다** — 열려면 바인딩 변경이 필요하고,
  그 순간 **인증 없이 노출**된다.
- ⚠ **권고**: 외부 노출 전에 **최소 토큰 인증**(`_request_is_authed` 활성화 + `/admin` 도 게이트 대상 포함)을
  먼저 넣어야 한다. 지금 상태로 `0.0.0.0` 바인딩하면 누구나 접근 가능하다.
- 📌 admin 에 추가할 항목(설계): `det_review` 최근 판정 · 필드 보유율 · 스냅샷 결손 · 유령 마번 발동 목록.

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
| 🟠 5 | **보너스 분해 8종 · `baseScore` · `rank`** | ⚠ **🟢→🟠 상향 → 2026-07-30 체크리스트로 이관** | 어제 "재계산 가능"으로 낮췄으나 **공식이 바뀌면 과거 재현이 불가**하다. 실제로 EV 곡선·tier 경계·페이크급락 필터가 **최근 2주에만 여러 번 바뀌었다** → "그때 왜 그 점수였나"가 이미 복원 불가 상태다. **점수 자체보다 분해값이 더 중요하다**<br>➡ **이관 완료**: 배선된 분해 필드(`gait`·`paceBonus`·`paceBonusBase`·`gradeAtBonus`·`paceDetail`)는 **체크리스트 A1·A2** 가 추적하고, **미배선인 `rank`·`baseScore` 는 별도 항목 `D5`** 가 추적한다. 드리프트 목록에는 남기지 않는다(이중 추적 방지). |
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

### 5) 총평 **유용성** 측정 (겹침률 다음 단계) — ⚠ 판정 불가
겹침률은 "새 정보인가"만 답한다. 그 새 정보가 **쓸모 있는가**를 따로 쟀다.
- **총평이 시장 1·2위와 다른 선수를 첫 언급한 경주: 14경주** → ⚠ **표본 30 미만 · 판정 불가**(결론에 쓰지 않음)
- 참고 수치(결론 아님): 3착 이내 진입률 **총평 지목 29%(4/14) ↔ 시장 1위 57%(8/14)**
- **방향만 보면 총평이 시장보다 나쁘다.** 다만 14경주로는 아무것도 확정할 수 없다.
- ✅ **기다리는 것이 옳다** — `comment` 는 이미 100% 저장 중이라 **축적 비용 0**이다.
  재검토 조건: **다른 선수 지목 경주 30건 이상**(현재 14).

### 6) `schema_version` 재수집 — 설계 + **요청량 계산** (구현은 다음 세션)
- **재수집 조건(안)**: `stored.schema_version != STARTERS_SCHEMA_VERSION` **또는** 필수 필드 미보유.
  현재 스킵 조건(`source` 일치)에 **AND 조건으로 추가**한다(기존 로직 무삭제).
- **대상 실측**: 필수 필드 미보유 **93경주**(oddspark 23 · 경륜 70).
- **요청량 계산**:
  | 소스 | 경주 | 경주당 요청 | 소계 |
  |---|---|---|---|
  | oddspark | 23 | 5 (RaceList 1 + HorseDetail ~4) | 115 |
  | 경륜 | 70 | 1 (RaceList) | 70 |
  | **합계** | **93** | — | **185건(1회성)** |
  하루 정상 수집 요청은 약 500~900건이므로 **1회성 185건 = 하루의 21~37%**.
- 🔴 **그런데 이 백필은 대부분 실익이 없다** — **24시간+ 경과 43경주는 출마표가 이미 내려가 재수집해도 빈손**이다.
  **실제 유효 대상은 당일 진행 중 경주뿐**이고, 그건 정상 수집 창에서 한 번 더 도는 것과 같다.
- ✅ **결론(권고)**: **과거 백필은 하지 않는다.** `schema_version` 게이트는 **"앞으로 새로 수집되는 경주가
  구스키마로 스킵되지 않게" 하는 용도로만** 넣는다. → **추가 요청 증가 사실상 0**, 차단 위험 없음.
  · 억제책이 필요 없어진다(배치·야간 스케줄링 불필요).
  · ⚠ 굳이 백필한다면 **개최 없는 시간대(오전 6~9시) · 분당 10요청 이하 · 당일분만** 으로 제한할 것.

### 7) 점수 분해 보존 — **🟠 → 🔴 상향(권대표 지적 반영)**
> "데이터 보존이 아니라 **검증 도구 신뢰성** 문제다."
- 리플레이는 저장된 값으로 **"무엇을 추천했나"** 는 재현하지만, 분해값이 없으면 **"왜 그랬나"** 는 재현하지 못한다.
- **실제 사고가 이미 있었다**: 경계 1.8→1.5 시뮬 **v1 137.9% → v2 85.6%**. 원인은 `_lowodds_exempt`
  면제 분기를 재현하지 못한 것 — **코드 경로 재현 실패로 결론이 뒤집혔다.**
  저장된 분해값이 있었다면 "재현 점수 vs 실제 점수"를 대조해 **그 자리에서 오류를 잡을 수 있었다.**
- → ~~**보너스 분해 8종·`baseScore`·`rank` 를 🔴로 올린다.**~~ ⚠️ **정정(2026-07-30) — 체크리스트 A1·A2·D5 로 이관됨.**
  · **불일치 정리**: 이 줄은 "🔴로 올린다"인데 위 「탈락 필드 우선순위」 표는 **🟠 5** 로 남아 있어 **문서가 상충**했다.
    🔴 **스키마 드리프트 목록은 `winOdds`·`pop`·`weight`·`surface`·`trackCond` 5개로 확정**한다(권대표 2026-07-30).
  · **이관 근거**: 🔴로 올린 이유가 "검증 도구 신뢰성"이었는데, 그 사이 `gait`·`paceBonus`·`paceBonusBase`·
    `gradeAtBonus`·`paceDetail` 이 실제로 배선됐다(오늘 실측 71~98%). 드리프트 목록에 남겨두면 **이중 추적**이 된다.
  · **배선 실측(2026-07-30 · 오늘 분석로그 45파일·372행)** — 원칙 5(저장 건수로 확인) 적용:
    `paceBonusBase` 98.4% · `paceBonus` 98.4% · `gait` 98.4% · `record_score` 98.4% · `gradeAtBonus` 74.5% · `paceDetail` 71.2%
    🔴 **`rank` 0.0% · `baseScore` 0.0% — 둘 다 미배선.**
  · ⚠ `paceBonusBase`(98.4%)가 `baseScore` 역할을 하는 것처럼 보이지만 **이름이 다른 별개 필드**다.
    대체한다고 단정하지 말 것 → **체크리스트 `D5`(점수 분해 rank·baseScore 보유율)** 로 별도 추적한다.
  · 근거: 이 프로젝트는 **리플레이로 의사결정한다.** 리플레이가 틀리면 그 위의 모든 판단이 틀린다.
    분해값은 **리플레이 자체를 검증하는 기준선(ground truth)** 이라 일반 관측치보다 가치가 높다.
  · 구현 시 함께 넣을 것: 리플레이가 재계산한 점수와 저장 점수를 대조하는 **자기검증 스텝**.
    불일치가 나오면 그 리플레이 결과는 **신뢰하지 않는다**(오늘 v1 같은 사고 차단).

## 🔴 [2026-07-30] 페이스 매핑 방향 오류 — 신호는 있는데 **반대로 쓰고 있다**

### 정의 확정(축이 같은지 먼저 검증함)
- **시스템 `pace`** = `_apply_pace_analysis`(app.py:26940): `lead = counts["선행"]` →
  `빠른 = lead≥3 · 느린 = lead≤1 · 보통 = 2`. 즉 **입력 = 출전마 중 선행마 마릿수 = 선행 경합 강도**
  ("경주 진행 속도"가 아니다).
- **실제 페이스(역산)** = 복승권(1·2착) **입상마의 각질 구성**. 즉 **출력 = 어느 각질이 유리했나**.
  (경륜 결과에 `決まり手` 가 저장돼 있지 않아 이 방법 외에 대안이 없다. `pace_stats.json` 도 같은 정의.)
- ✅ **두 축은 서로 다른 것을 재지만, 인과 방향이 `입력 → 출력` 으로 명확해 대조가 성립한다.**
  ⚠ 다만 이를 "페이스 판정 적중률"이라 부르면 오해를 낳는다 — **판정이 아니라 매핑 방향의 문제**다.

### 실측 (경륜 · 배당 미사용 · n=80)
| 시스템 판정 | 실제 선행 입상 | 실제 추입 입상 | 해석 |
|---|---|---|---|
| **빠른**(선행≥3) · 63건 | **41 (65%)** | 22 | 선행마가 많으면 **그중 하나가 선행에 성공**한다 |
| **느린**(선행≤1) · 17건 | 1 (5.9%) | **16 (94%)** | 선행할 말이 없으면 **추입이 이긴다** |

- **분리도가 매우 크다(65% ↔ 5.9%)** → **신호가 없는 게 아니라 있는 신호를 반대로 쓰고 있었다.**
- 🔴 **시스템 매핑이 정확히 반대다** — `_apply_pace_analysis` `_base`(app.py:26943):
  `빠른 → 추입 +15 · 선행 −10` / `느린 → 선행 +10 · 추입 −10`.
  실측은 `빠른 → 선행` · `느린 → 추입` 이다. **뒤집으면 71%.**
- **독립 측정 3건이 같은 방향을 가리킨다**: ⓐ이 대조 ⓑ`pace_stats.json`(빠른: 선행 56.7 ↔ 추입 37.7)
  ⓒ어제 리플레이(빠른×추입 포함 **z=−2.80** 유의하게 나쁨).
- ⚠ `_scenario_plan`(7311)은 이미 비활성이라 **실피해는 없다**. 그러나 `_apply_pace_analysis` 의
  `paceBonus` 는 **form 총점에 실제로 가산된다** → 통합등급에 반영 중이다. **영향 범위 확인 필요.**

### 🔬 엣지 측정 — **시장은 이 방향을 이미 알고 있다(밸류 없음)**
`엣지 = 실측 적중률 ÷ 시장암시확률(0.75/배당)`. 배당 사용은 여기서만(엣지 계산 목적).
| 페이스 | 각질 | n | 적중률 | 시장암시 | 엣지 | 1건제외 | 3건제외 |
|---|---|---|---|---|---|---|---|
| 빠른 | **선행2두** | 53 | 18.9% | **11.1%** | 1.70 | **0.69** | **0.25** |
| 빠른 | 추입2두 | 35 | **0.0%** | 2.6% | 0.00 | 0.00 | 0.00 |
| 느린 | 추입2두 | 20 | **0.0%** | 3.0% | 0.00 | 0.00 | 0.00 |
| 빠른 | 혼합 | 110 | 16.4% | 8.3% | 1.97 | 1.48 | 0.64 |
| 느린 | 혼합 | 52 | 15.4% | 4.9% | 3.11 | 1.39 | 0.50 |

> ⚠️ **[정정 2026-07-31] 이 표는 `flow_table.json` 과 ``다른 것을 재고 있다`` — 혼동 금지.**
> · **이 표(경주 단위)**: "추입형이 2두인 **경주**" n=35 · 적중률 0.0%.
> · **`flow_table.json`(조합 단위)**: `빠른|7두 추입+추입` **n=722 · hits=8 · 실측 1.11% · 엣지 0.4848**
>   (CLAUDE.md 1185행 기록 n=695·hits=8·엣지 0.507 은 표본이 더 적던 시점 값이며 **둘 다 hits=8**).
> · 즉 **"엣지 0.507"과 "적중 0"은 모순이 아니다** — 분모(경주 ↔ 조합)와 모집단이 다르다.
>   `flow_table` 검산: 0.0111 ÷ 0.0229 = **0.4847** ≒ edge 0.4848 ✅ 계산 일치.
> · 🔴 **네거티브 필터 설계의 근거는 `flow_table`(조합 단위) 쪽이다.** 이 표는 n=35 로 **판정 불가**다.

- 🔴 **핵심: 빠른 페이스에서 선행2두 조합의 시장암시확률이 11.1%로 이미 가장 높다.**
  다른 그룹(2.6~8.3%)보다 훨씬 낮은 배당이 붙어 있다 = **시장이 "선행마가 많으면 선행이 온다"를 이미 안다.**
- **엣지 1.70이지만 회수율은 1건 제외 0.69 · 3건 제외 0.25** → **극단값 의존이 심해 밸류가 아니다.**
  (오늘 네 번째 극단값 착시 패턴. 엣지 숫자만 보면 안 된다.)
- ⚠ **표본 경고**: 느린 그룹 n=17경주(조합 20~52)로 **판정 기준 30 미달 — 결론에 쓰지 않는다.**
- ⚠ **측정 한계**: `pace_analysis` 에는 **입상마 각질만** 저장돼 있어 미입상마 조합이 분류에서 빠진다.
  적중률이 구조적으로 과대평가되므로 **절대값이 아니라 그룹 간 상대 비교만** 유효하다.
  → **전 출전마 각질 저장이 선행되어야 정확한 측정이 가능하다**(스키마 확장 대상).
- **잠정 결론**: 매핑 방향은 뒤집는 것이 맞아 보이나, **그 자체로 수익이 생기지는 않는다**
  (시장이 이미 반영). 가치가 있다면 **다른 신호와의 조합**에서 나올 것이다.
  ⏸ `_apply_pace_analysis` 수정은 **승인 대기** — `paceBonus` 가 통합등급에 실제 가산되므로 영향이 크다.

### 📉 총평 전개 서술 — 기대치 하향(근거 기록)
| 범주 | 보유율 |
|---|---|
| 전개 흐름(`番手`·`差し`·`仕掛け`) | 34% |
| 기동력(`先行`·`逃げ`·`機動力`) | 30% |
| 라인 구도(`分戦`·`ライン`) | **14%** |
| **전개 표현이 하나도 없음** | **23% (16/70)** |
| **페이스 예측까지 도달** | **5.7% (4/70)** |
- 🔴 **규칙 추출 실패는 "규칙 부족"이 아니라 "원문에 정보가 없음"일 가능성이 높다.**
  총평 평균 60~70자에 전개 서술은 한두 구절뿐이다. **LLM 을 붙여도 없는 정보는 만들 수 없고 환각 위험만 커진다.**
- ⏸ **LLM 구조화 설계 보류.** 재검토 조건: **전개 표현 보유 총평 30건 이상 축적**(현재 페이스 도달 4건).
  `comment` 는 이미 100% 저장 중이라 **대기 비용 0**.

### 🚫 경마 예상문 — **정보원 없음(종결)**
| 소스 | 予想 | 短評 | コメント | 展開 | ペース |
|---|---|---|---|---|---|
| oddspark 경마 출마표 | 있음* | − | − | **−** | **−** |
| keiba.go.jp DebaTable | − | − | − | **−** | **−** |

\* oddspark 의 `予想` 는 **전부 메타태그·네비게이션 문구**다(`競馬予想ならオッズパーク`·`予想情報` 탭 링크).
경주별 예상문 본문이 아니다. `予想情報` 탭은 미확인이며 `会員`·`ログイン` 문구가 있어 유료 가능성.
- ✅ **결론: 무료 공개분에 경마 전개 정보원은 없다. 전개 연구는 경륜 우선으로 간다.**

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

## ⏸️ [2026-07-30] Gemini 호출·카카오 발송 **일시 중단** (삭제 아님 · 플래그)

| 플래그 | 기본값 | 효과 |
|---|---|---|
| `GEMINI_REVIEW_ENABLED` | **0 (꺼짐)** | Gemini **API 호출 자체**를 하지 않음 |
| `GEMINI_KAKAO_ENABLED` | **0 (꺼짐)** | 검수 결과 **카카오 발송**만 차단(로그는 그대로 저장) |

- **중단 사유**(로그 740건 전수 감사): ①`WARNING` **99.5%**(SAFE 4건)로 "이상 없음"을 못 내 선별 도구로
  기능하지 않고 **사실상 전 경주에 알림**이 나갔다 ②출력이 **베팅 조합이 아니라 코드 수정 지시문**이라
  결과와 대조할 대상이 없다 ③지적의 **84%가 `_final_picks`** 인데 **코드는 경주마다 바뀌지 않는다**
  → 같은 코드를 하루 625회 리뷰. 7/29 **670건**(경주당 7회)은 낭비였고, 감사는 740건으로 이미 끝났다.
- **되돌리는 법**: `set GEMINI_REVIEW_ENABLED=1` (호출 재개) / `set GEMINI_KAKAO_ENABLED=1` (발송 재개).
  모듈 기본값(`gemini_reviewer.py` `_flag(..., "0")`)을 `"1"` 로 바꿔도 된다.
- **재개 조건**: **역할 재설계(예측 기록 방식 Phase A/B) 설계 확정 후.** 그 전에는 켜지 않는다.
- **서버 시작 로그에 상태가 찍힌다** — 꺼진 걸 모르고 "Gemini 가 안 돈다"고 오해하지 않도록.
  ```
    · Gemini 검수 호출 : ⏸️ 꺼짐 — 일시 중단 (GEMINI_REVIEW_ENABLED)
    · Gemini 카카오발송: ⏸️ 꺼짐 — 일시 중단 (GEMINI_KAKAO_ENABLED)
    · 결정론 검수(②④) : 🟢 항상 동작 (logs/det_review/) · 경주 추천 카카오: 🟢 정상
  ```
- ✅ **영향 없음이 확인된 것**(코드로 검증):
  · **경주 추천 카카오**(T-7분·T-5분) — `_kakao_send_to_me` 호출 **7곳 전부 Gemini 플래그와 무관**.
  · **`_deterministic_review`(②④ 판정 · `logs/det_review/`)** — Gemini 게이트보다 **앞에서 실행**되며
    플래그와 독립적으로 계속 동작한다.
  · **`logs/gemini_review/` 740건** — 감사 근거이므로 **전량 보존**(삭제·이동 없음).
- ⚠ **`gemini_reviewer.py` 는 삭제·주석처리하지 않았다.** `_send_kakao` 등 기존 함수 전부 그대로다.
- ⚠ **주의**: 결정론 검수는 입력 묶음 `_gemini_pending` 을 공유한다(이름만 gemini). 훗날 Gemini 를
  완전히 걷어낼 때 이 묶음까지 지우면 **결정론 검수도 함께 죽는다.**

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

## ✅ [2026-07-30] 완료 조건 체크리스트 — 19항목 (외부 자동 확인)

> **설계 의도**: "감시"가 아니라 **완료 조건 체크리스트**다. 각 항목에 숫자 완료선이 있고 충족되면 목록에서 빠진다.
> **조용해지는 것이 곧 진행 상황이다.** 지금까지 발견이 대부분 육안이었고, 외부에 있으면 0건이 됐다.
> 구현: `tools/health_check.py` · `GET /api/health/checklist`(읽기 전용·UTF-8) · `python tools/health_check.py`

### 🔴 이 체크리스트의 제1 규약 — `denominator`(분모 정의)는 필수 필드다
**분모를 좁히면 가장 실패한 것이 통계에서 사라진다.** 실사고(2026-07-30):
"스냅샷 3틱+"를 **스냅샷 보유 파일만** 분모로 잡으면 **81.6%**, **0틱 경주 123개를 포함**하면 **69.4%**.
애매하면 **넓은 쪽(실패가 포함되는 쪽)** 을 고른다. 좁히는 것은 **대상 자체가 아닐 때만** 허용한다.

### 완료선 (⚠️ 사후 하향 방지 — 바꾸려면 근거와 함께 이 표를 먼저 고친다)
| id | 항목 | 분모 | 완료선 |
|---|---|---|---|
| **D1** | 스냅샷 3틱+ 경주 비율 | `odds_history` 전체(**0틱 포함**)·당일 rolling·**distinct** 틱 | **≥90%** |
| **D2a** | 저장 실패 — **수정 경로**(`_json_atomic`) | 당일 로그 전체(**회전본 포함**) | **0건** |
| **D2b** | 저장 실패 — **미수정 17곳**(`path+".tmp"`) | 동상 | **0건** ← ⓐ 완료 판정 |
| **D3** | schema contract test | `tools/schema_contract.py` 계약 대상 | **통과**(미구현=실패) |
| **D4** | 스키마 드리프트 🔴 필드 보유율 | 당일 `source=="oddspark"` 경주 행 | **≥90%** |
| **D5** | 점수 분해 `rank`·`baseScore` 보유율 | 당일 `analysis_log` horses 전체 행 | **≥90%** |
| A1~A5 | ① 적중왕전개 준비 5항목 | (설계 완료·구현 대기) | — |
| B1~B4 | ② 배당판 오류 4항목 | (설계 완료·구현 대기) | — |
| C1~C4 | ③ 예상·복기 4항목 | (설계 완료·구현 대기) | — |

- **`n<10` 이면 `ok=null`** — 하루 경주가 적으면 n=2 로 100% 가 나와 **rolling 이 스스로를 속인다.**
- **당일 rolling + 누적 병기** — 당일분만 보면 "과거가 오염돼 있다"는 사실이 화면에서 사라진다.
  **리플레이·시뮬레이션은 그 과거 데이터로 돌아가므로** 누적값을 반드시 함께 표시한다.
- **미구현은 통과가 아니다**(D3). 미구현 항목도 목록에서 빼지 않는다 — **19개 중 몇 개가 미구현인지도 진행 상황**이다.

### 🔴 D4 분모에서 `sport` 태그를 쓰지 않는 이유 (2026-07-30 실측)
`winOdds`·`pop`·`weight` 는 경마 出走表 파서 전용이라 분모를 경마로 좁혀야 한다. 그런데—
- 경륜장 `sport=horse` **오분류 213건**을 소급 정정했고 **실시간 재발 3경주**(와카야마 7/24·코치 6R 7/25·코치 10R 7/26).
- 🔴 **결정적**: `starters_store` **132경주 전부 `sport` 가 `None`** 이다 — **애초에 태그로 분모를 잡을 수 없다.**
- → **`source == "oddspark"`**(파서 유래값)를 분모로 쓴다. 태그 오염과 무관하다.
  실측 분포: `keirin` 87 · `oddspark` 23 · `korea` 14 · `keiba_nar` 1 · 없음 7 · **source↔sport 불일치 0건**.
- 키에 날짜가 없으므로(0/132) **레코드의 `t` 타임스탬프**로 당일분을 가른다.

### 📌 rolling 전환이 드러낸 것 — 누적값은 배선 성공을 가린다
| 필드 | 누적(262행) | **당일(22행)** |
|---|---|---|
| `winOdds` | 7.3% | **86.4%** |
| `pop` | 7.3% | **86.4%** |
| `weight` | 7.6% | **90.9%** |
| `surface`·`trackCond` | 0.0% | **0.0%** |
- **배선은 이미 작동하고 있었다** — 누적 7.3% 는 배선 전 과거가 섞인 값이다. D1 과 정확히 같은 구조.
- `surface`·`trackCond` 는 rolling 으로도 0% 이고 **그게 정확한 판정**이다(1계층 재수집이 선행 조건).
  **낮게 나온다고 기준을 낮추지 않는다.**

### 🔒 외부 접근 — 현재 **불가**(설계상 안전, 변경은 승인 필요)
- 바인딩 `127.0.0.1:8011`(`app.py`: `_host = "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1"`).
- 실검증: Tailscale `100.80.114.84:8011` → **연결 실패** · `127.0.0.1:8011` → 200. 8011 전용 방화벽 규칙 **없음**.
- ⚠ 이 서버에는 `.env`(카카오 토큰·API 키 3종)와 `/admin` 패널이 있다. `0.0.0.0` 으로 열면
  Tailscale 뿐 아니라 **공유기 내부망 전체**에, 포트포워딩이 있으면 **인터넷**에도 열린다.
- 권고안(⏸ 승인 대기): `0.0.0.0` 바인딩 + **Windows 방화벽에서 8011 을 Tailscale 대역(100.64.0.0/10)만 허용**.

## 🔴 [2026-07-30 오후] 마감 후 재분석이 화면·저장소를 덮어쓴다 — 나고야 3R 케이스

> **결론 먼저**: `raceGrade` 는 **동결되지 않는다**(마감 전 스냅샷과 일치율 **30.4%**).
> 따라서 **등급별 성적으로 판정하는 모든 측정은 현재 "측정 불가"** 다.

### ✏️ 전제 3회 오판 — 원인은 "화면 캡처만으로 케이스를 규정한 것"
| 회차 | 프레이밍 | 실제(원자료) |
|---|---|---|
| 1 | "5번이 막판 편입 → 2착" | **막판 이탈** — 15:10~15:14:10 유력마였다가 15:14:20 에 빠짐 |
| 2 | "정답을 알고 패스" | `betGrade` = **🔥 강력추천** · `strong_signals` 3건 |
| 3 | "생성·적중했으나 표시 실패" | 삼복승은 **섀도우 모드**(추천 대상 아님) + `[3,5,9]` 는 6개 중 **5순위** |

**정확한 사실**: 마감 확정은 `[9,3,8]`(⚖️ 관찰)로 **빗나갔고**, 마감 후 재분석이 `[9,3,5]` 로 바뀌어
**우연히 정답과 일치해 보였으며**, 화면에는 그 마감 후 상태가 표시됐다.
· `finalTrifectas` 에 `[3,5,9]`(11.7배)가 있었지만 `displayedCombos.trifectas = []` 라 판정 대상이 아니었다
  (`trioShadow=True` → 통째 제외 · 게다가 `[:2]` 상한이라 5순위는 shadow 가 아니어도 제외).
· `shadowWouldBet = [[1,3,10],[3,5,7]]` — **섀도우로 "걸었다면"에도 정답은 없다.**

🔴 **원칙 8 의 새 사례로 고정한다 — "화면은 마감 후 상태다. 화면으로 케이스를 규정하지 말 것."**
사후 조회 화면은 `_triple_analyze` 재계산 결과이지 **회원이 마감 전에 본 것이 아니다.**
케이스 분석은 반드시 **`analysis_log`(동결) 또는 `timeline_snapshot`(T-7/T-5)** 원자료로 시작한다.

### 🔴 아키텍처 결함 ① — `_history_save_analysis` 가 마감 후 가드 **밖**에 있다
`triple_analyze()` 엔드포인트 실행 순서:
```python
an = _triple_analyze(rk, ...)      # ① 마감 후에도 재계산
_history_save_analysis(rk, an)     # ② 가드 밖 → odds_history.analysis 를 마감 후에도 덮어쓴다 🔴
# ── [마감 후 폴링 저장 가드] ──      # ③ 가드는 여기부터
_analysis_log_save(...)            # ④ 가드가 막는 것은 이것뿐
```
| 저장소 | 마감 후 갱신 | 나고야 3R 값 |
|---|---|---|
| `analysis_log` | ❌ 막힘(가드+readonly) | `[9,3,8]` **마감 확정** ✅ |
| `odds_history.analysis` | ✅ **계속 덮어씀** | `[9,3,5]` 마감 후 🔴 |
| API 응답(화면) | ✅ 매 폴링 재계산 | 마감 후 🔴 |

### 🔴 아키텍처 결함 ② — 가드는 **저장만** 막고 화면은 막지 않는다
가드 주석에 이미 명시돼 있었다: *"분석은 그대로 수행하고 **저장만 건너뛴다** — 화면 표시는 영향 없음."*
의도된 설계지만, **그 결과 화면이 사후 상태가 된다**는 부작용이 문서화돼 있지 않았다.

### 🔴 결함 ③ — `strong_signals` 동결 누락 (39.5% 오염)
같은 파일 안에서 `raceGrade.basis`(신호 N개)와 `strong_signals.count` 가 **318/805 = 39.5%** 불일치.
`raceGrade` 는 동결 시도가 있는데 `strong_signals` 는 마감 후 재분석이 그대로 덮는다.
· ✅ **오늘 수행한 측정 4종은 `strong_signals` 를 입력으로 쓰지 않아 무영향**:
  ①raceGrade 성적(=`raceGrade.label`+`payouts_raw`) ②det_review(=`drops`·`finalQ`·`finalT`·`linePairs`)
  ③확신도 분해(=`signal_quality_full`) ④급락 미반영(=`drops`+`finalQuinellas`).
· ⚠ 단 **런타임에서는 `strongSignals.count` 가 `raceGrade` 의 입력**이다 → 아래 ④와 연결된다.

### 🔴 결함 ④ — `raceGrade` 는 **동결되지 않는다** (등급별 성적 정정)
- `corePicks.raceGrade.locked=True` **0건 / 805건**.
- **대조 검증**(`analysis_log` ↔ `timeline_snapshot` 의 마감 전 T-5/T-7/T-10, **n=191**):
  **일치 58 (30.4%) · 불일치 133 (69.6%)**
- **분포가 체계적으로 하향 이동**했다:
  | | 🔥 강력승부 | ✅ 추천 | ⚖️ 관찰 | 🛡 참고·패스 |
  |---|---|---|---|---|
  | **마감 전(T-5/7/10)** | 33 | **72** | 67 | **19** |
  | **analysis_log(사후)** | 28 | **30** | 83 | **50** |
  마감 후엔 신호가 억제돼 `_gs` 가 줄고 등급이 떨어진다 → **참고·패스가 2.6배로 불어난다.**
- ⛔ **정정**: 직전 보고의 등급별 성적 **`🔥269.2% > ✅259.3% > ⚖️231.4% > 🛡177.6%`(n=90~258)** 는
  **마감 후 하향된 등급으로 잰 값이므로 "측정 불가"로 정정한다.** 회원이 실제로 본 등급이 아니다.
  · 그럼에도 상관이 보인 이유(추정): "마감 후 신호가 사라진 경주"가 실제로 약한 경주와 겹칠 수 있다.
    그러나 **실전 의미는 없다** — 베팅 시점에 그 등급을 볼 수 없기 때문이다.
- ✅ **재측정 조건**: `raceGrade` 를 마감 시점에 **파일로 동결**하거나(`locked` 저장),
  `timeline_snapshot` 의 **T-5/T-7 등급**을 기준으로 재산출한다(현재 대조 가능 191경주).

### 📌 결함 ⑤ — 결과 백필 데몬 sleep-first
`_kra_backfill_loop` 가 `while True:` 안에서 **`time.sleep(1200)` 을 먼저** 실행한다 →
기동 후 20분이 지나야 첫 백필이 돈다. **개발 중 잦은 리로드로 타이머가 계속 리셋**돼
2026-07-30 에는 하루 종일 한 번도 돌지 않았다(`[결과 백필] 데몬 시작` 6회 ↔ 실행 로그 0건).
⚠ 운영 중(코드 수정 없음)에는 정상 동작한다. **개발 중에만 생기는 사각지대**다.
→ 새로 만든 `_start_health_kakao_scheduler` 는 **60초 폴링**으로 만들어 이 함정을 피했다.

### 📌 결함 ⑥ — `bmedSpecial` 이 판정 명단에서 빠진다
나고야 3R `bmedSpecial [5,9] 21.8배`(스마트머니)는 **정답 복승**인데 `displayedCombos.quinellas`
(=`finalQuinellas` 만)에 없어 판정 대상이 아니다. 💎가 화면에 표시된다면 "표시=판정 일치" 원칙과 어긋난다.

## 📮 [2026-07-30] 체크리스트 카카오 푸시 — 서버 개방 대신 Push
> 외부 확인을 위해 8011 을 열면 **방화벽이 유일한 방어선**이 되고 리셋 리스크가 있다
> (`.env` 키 3종·`/admin` 존재). → **공격 표면을 늘리지 않고 서버가 밖으로 밀어낸다.**
- 바인딩은 **`127.0.0.1` 유지**(변경 금지).
- `GET /api/health/send_kakao`(미리보기) · `POST`(발송) · **매일 22:00 자동**(`HEALTH_KAKAO_HOUR`).
- 본문 = `summary` 1줄 + **미충족 항목만**. 충족 항목은 넣지 않는다(짧아지는 것이 진행 신호).
- ⚠ **미충족 0개여도 `✅ 전부 충족 N/N` 한 줄은 반드시 보낸다** —
  비어서 안 보내면 "문제 없음"과 "발송 실패"가 구분되지 않는다. **침묵이 이상 신호가 되게 한다.**
- 🔒 기존 추천 픽 카카오(`_kakao_notify_race`·`_kakao_build_message`·`_kakao_rich_message`·
  `_KAKAO_SENT_FILE`)는 **일절 건드리지 않았다.** `_kakao_send_to_me` 만 재사용하고
  이력은 별도 `data/kakao_sent/<날짜>.json`(성공·실패 모두 기록).
- ✅ 실발송 검증 완료(2026-07-30 15:49 · 폰 도착 확인 · 359자).

## 🧭 [2026-07-30] 오염 범위 확정 — **무엇이 안전하고 무엇이 오염됐나**

> 앞으로 측정할 때마다 이걸 다시 묻지 않기 위해 고정한다.
> **결론: 오염은 표시·등급 계층에 국한된다. 판정·회수율 경로는 안전하다.**

### ① 오염 범위 표
| 항목 | 상태 | 근거(실측) |
|---|---|---|
| `hit` · `payouts` · `payouts_raw` | ✅ **안전** | `hit` 은 `odds_history.review` 에서 **읽어올 뿐**이고 계산은 `_apply_result_learning`(**결과 입력 이벤트**)에서만. 폴링 경로는 `hit` 을 만들지 않는다 |
| `corePicks.displayedCombos`(판정 명단) | ✅ **안전** | 동결 시각 실측 n=587 — **발주 전 87.7%**(0~5분 전 83.1%) · 발주 30분+ 후 **1.0%(6건)** 뿐 |
| `corePicks.finalQuinellas/Trifectas`·`final_recommendation` | ✅ 보존 | `readonly` 보존 목록 |
| `recommendation_history`·`signals_detected`·`odds_timeline` | ✅ 보존 | `readonly` 보존 목록 |
| **`raceGrade`** | 🔴 **오염** | `timeline_snapshot`(T-5/7/10) 대조 **일치 30.4%**(58/191) · `locked=True` **0건/805** |
| **`strong_signals`** | 🔴 **오염** | 보존 목록에 **없음** · `raceGrade.basis` 와 **318/805 = 39.5%** 불일치 |
| **`odds_history.analysis`** | 🔴 **오염** | `_history_save_analysis` 가 마감 후 폴링 가드 **밖** |
| **API 응답(화면)** | 🔴 **오염** | 매 폴링 `_triple_analyze` 재계산 |

- 🟢 **회수율 67.6% 는 재검토 대상이 아니다.** `hit`·`payouts` 가 **이벤트 전용 경로**라 보호된다.
  오염은 **표시·등급 계층에 국한**된다. 성적 집계·학습 원장은 영향 없음.
- `readonly` 보존 목록(app.py 약 14804)은 **5개**뿐: `corePicks` · `final_recommendation` ·
  `recommendation_history` · `signals_detected` · `odds_timeline`.

### ② 🔴 근본 원인 — `readonly` **타이밍** 결함
`readonly` 는 **"마감 후 추천 있는 파일"** 에 붙는다(app.py 약 14777) → **마감 후 첫 저장에서 걸린다.**
그 저장에는 **이미 마감 후 값이 들어가 있고**, 그 상태 그대로 굳는다.
> **즉 `readonly` 는 두 번째 오염부터 막고 첫 오염은 통과시킨다.**

- 🔴 **그래서 `raceGrade` 가 `corePicks` 안에 있어 형식상 보존 대상인데도 오염됐다.**
  **보존 목록에 넣는 것으로는 해결되지 않는다** — 걸리는 **시점**을 마감 후가 아니라 **마감 시점**으로 옮겨야 한다.
- **두 번째 요인**: `_GRADE_LOCK` 이 **메모리** 딕셔너리라 서버 재시작에 소실된다. 복원 경로가
  ①T-스냅샷 ②분석로그 ③**현재값(마감 후)** 인데, 오늘처럼 리로드가 잦으면 ③으로 떨어진다.
  → **파일 동결로 전환**해야 한다.
- ⚠ **모리오카 3R(2026-07-28)과는 별개 결함이다(같은 뿌리)** — 재확인 결과:
  · 모리오카 = 보호 **조건**의 결함(`doc.readonly AND an.afterClose` 로 묶여 afterClose 가 풀리면 보호도 풀림).
    **이미 수정됨** — 현재 파일은 정상이다(`displayedCombos.at` 12:55 = **발주 35분 전** 동결,
    `finalQuinellas` 와 일치, 결과 8-9-7 에 복승·삼복승 **둘 다 적중** `pnl=+3800`).
  · `raceGrade` = 보호 **시점**의 결함. **미수정.**
  · 공통 뿌리는 **"`readonly` 가 언제·어떤 조건으로 작동하는지가 정의돼 있지 않다"** 는 것이다.
    별개 버그 목록에서 통합하지 않되, **동결 설계에서 함께 다룬다.**

### ③ T-5(마감 전) 기준 등급별 성적 재산출 — **판정 불가**
🔴 **선택 편향**: 대상 **191 / raceGrade 보유 809 = 23.6%**.
`timeline_snapshot` 보유는 랜덤이 아니라 **"T-5 까지 수집이 된 경주"** 다(오늘 스냅샷 결손 64% 실측).
| | n | 수집틱 중앙 | 0~2틱 | **결과 보유율** |
|---|---|---|---|---|
| 전체 | 809 | 10.0 | 37.3% | **84.2%** |
| 대상 | 191 | 11.0 | 37.7% | **92.1%** |
→ **"끝까지 잘 굴러간 경주"에 치우쳐 있다. 전체를 대표하지 않는다.**

| 등급 | n | 적중률 | 회수율 | 1건제외 | 3건제외 | 판정 |
|---|---|---|---|---|---|---|
| 🔥 강력승부 | 31 | 32.3% | 258.7% | **90.3%** | **32.9%** | n≥30 |
| ✅ 추천 | 69 | 33.3% | 275.5% | 195.3% | 115.5% | n≥30 |
| ⚖️ 관찰 | 60 | 38.3% | 152.2% | 126.1% | 95.6% | n≥30 |
| 🛡 참고·패스 | **16** | 50.0% | 153.1% | 111.3% | 51.5% | ⚠ **n<30 판정불가** |
- 종목별: 경륜은 ✅63·⚖️54 만 n≥30 / **경마는 전 등급 n=2~6 로 전부 판정 불가**.
- ⛔ **결론 = 판정 불가.** 편향 + `🛡 n=16` + 극단값 의존 **3중 제약**.
- ⚠ **정정 전 수치(269>259>231>178)와 비교하지 말 것** — 분모(809↔191)도 기준(사후↔T-5)도 달라 **별개 측정**이다.

### ④ 💡 가설(판정 불가 · 동결 완료 후 확인할 첫 항목)
> **"🔥 강력승부는 확신이 높은 것이 아니라 고배당 소수 적중에 의존하는 구조일 수 있다."**
- 근거: 회수율 **258.7% → 1건 제외 90.3% → 3건 제외 32.9%**. **3건 제외 시 4등급 중 최하위**로 떨어진다.
- 적중률도 32.3% 로 최저다(⚖️ 관찰 38.3%). 즉 **적게 맞히고 크게 먹는다.**
- 오늘 다섯 번째 극단값 착시 패턴. **n=31 이라 지금 판정하지 않는다** — 동결 후 표본을 다시 모아 확인한다.

## 🧭 [2026-07-30] 위임 범위 · 세션 규칙 (권대표 지시)

> **배경**: 대표가 매 단계 라우터가 되는 상태를 줄인다.
> CLAUDE.md **자체 검증 원칙 10개**가 이미 작동하고 있으므로(2026-07-30 하루에만
> **자기 정정·중단 5회 실증**: 회수율 계산식 오류 자체 발견 → `payouts`→`payouts_raw` 재계산 /
> 나고야 3R 전제 3회 정정 / `raceGrade` 성적 "측정 불가" 자진 철회 /
> 잘린 지시를 추측으로 확정하지 않고 확인 요청 / 중복 지시 재실행 거부),
> 아래 범위는 **승인 없이 진행**한다.

### ✅ 승인 없이 진행
| 범위 | 조건 |
|---|---|
| **리플레이 · 측정** | **읽기 전용** — 저장·수정 없음 |
| **관측 배선** | **추천 경로 무개입** |
| **문서 갱신 · 커밋 · 푸시** | — |
| **발견 사항 기록** | **보류 목록에만** 적는다. **변경은 하지 않는다** |

### 🔴 승인 필요 (변경 없음 = 손대지 않는다)
- **추천 경로**: `_final_picks` · **EV필터** · `_apply_pace_analysis` · `_scenario_plan` · `_profit_tier_of`
- **파일 삭제** · **프로세스 종료** · **서버 바인딩**
- **완료선 변경** (⚠ **사후 하향 방지** — 바꾸려면 근거와 함께 체크리스트 표를 먼저 고친다)

### 📏 세션 규칙
1. 🔴 **한 세션 = 한 목표. 새 발견은 목록에만 넣고 쫓지 않는다.**
   (2026-07-30 하루에 발견이 계속 새 작업을 낳아 크리티컬 패스가 계속 밀린 것에 대한 규칙)
2. 🔴 **"이게 더 급하다"는 판단을 세션 중에 하지 않는다.**
   **세션 끝에 우선순위만 제안하고, 다음 세션에서 정한다.**
3. **세션 종료 보고 형식** — 이 3가지만 낸다:
   ① **목표 달성 여부** ② **발견 목록** ③ **다음 목표 제안 3줄**

### ✅ 완료 정의
> **체크리스트가 전부 초록이면 이 단계는 끝이다.**
- 보고 시 **현재 `N/19` 를 함께 보고**한다(고정값이 아니라 그 시점 실측값).
- 완료선은 **사후에 낮추지 않는다** — 바꿔야 할 근거가 생기면 **근거와 함께 기록하고** 바꾼다.
- ⚠ **미구현은 통과가 아니다**(D3). 미구현 항목도 목록에서 빼지 않는다 —
  **19개 중 몇 개가 미구현인지도 진행 상황**이다.

### 🎯 다음 세션 목표 — **하나만**
🔴 **마감 시점 단일 동결** — 설계안 제출 → 승인 → 구현
> **완료되면 처음으로 "회원이 실제로 본 등급"으로 성적을 잴 수 있다.
> 그 전의 모든 등급 측정은 무의미하다.**
- 핵심은 `readonly` **타이밍** — 마감 후가 아니라 **마감 시점**에 걸어야 한다.
- `timeline_snapshot` 이 이미 T-5/T-7 값을 담고 있으므로 **새로 만드는 게 아니라 정식 판정으로 승격**.
- 대상: `raceGrade` · `strong_signals` · `betGrade` · 화면 API 응답 · `odds_history.analysis`
- 부수 결함: `_GRADE_LOCK` **메모리 의존 → 파일 동결**로 전환.

## 🔬 [2026-07-30] 막판 편입 측정 — 병목은 신호 속도가 아니라 **조합 전개**

> **배경 정정**: *"마감 후 유력마가 잘 들어온다"* 는 관찰은 맞지만 **쓸 수 없는 정확도**다 —
> 마감 후에는 살 수 없고, 그건 **시장의 최종 답안을 베낀 것**이다.
> → **마감 후는 측정 대상에서 제외**하고 **마감 전 막판 신호**만 잰다.

### 편입 시점별 입상률 (편입 2,517건 · 510경주 · 마감 후 제외)
| 구간 | n | 입상률 |
|---|---|---|
| T-10분 이전 | 86 | 43.0% |
| T-10~T-3분 | 1,718 | **51.1%** |
| **T-3분~마감(막판)** | 713 | 49.2% |
- **막판 편입이 특별히 좋지도 나쁘지도 않다**(차이 2%p). **"신호가 늦다"는 가설은 기각.**
- 종목별로도 경륜 52.9/51.6/51.3 · 경마 40.6/50.6/48.5 로 같은 결론.
- ⚠ **배당대 통제는 실패했다** — `horses[].odds` 보유율이 12.9%뿐이라 셀마다 n=1~21 이 됐다(아래).
- Accept 수정 전후: 입상률 불변, 단 **막판 편입 비중 29.2% → 19.8%**(수집이 빨라져 신호가 일찍 잡힘).
  ⚠ 수정 후 표본 49경주로 얇다 — 며칠 후 재측정.

### 🔴 진짜 병목 — 막판말이 입상한 207경주 (⚠ 분모 통일: 아래 %는 전부 /207)
| | n | % |
|---|---|---|
| 복승 표시에 그 말 포함 | 127 | 61.4% |
| **복승 표시에 그 말조차 없음** | **80** | **38.6%** |
| 정답 복승이 표시 명단에 | 70 | 33.8% |
| 정답 생성됐으나 표시 제외 | 53 | 25.6% |
| **정답 복승 아예 미생성** | **84** | **40.6%** |

### 유형 분해 — **고칠 곳은 교차 생성과 EV 필터다**
**집단 A · 정답 복승 미생성 78경주** (⚠ 분모 /78)
| 유형 | n | % |
|---|---|---|
| **① 두 말 모두 후보인데 교차 미생성** | **54** | **69.2%** |
| ④-a 한쪽만 후보 | 19 | 24.4% |
| ④-b 둘 다 후보 아님 | 5 | 6.4% |
- 육안 3건: `いわき平 2R` 후보[1,2,3,4,5,7] 정답 **1+4** → 표시 `3+5` /
  `사세보 5R` 후보[2..9] 정답 **3+7** → 표시 `4+7` / `코치 8R` 후보 8두 정답 **1+3** → 표시 `2+10`.
- **나고야 12R 이 전형**: 축 `[3,4]` · 9번 T-0.9분 편입 · 결과 **9-4-3** ·
  `4+9`(33.3배)가 **corePicks 어느 목록에도 없음 = 필터 탈락이 아니라 미생성**.
  9번은 삼복승 `[3,7,9]`·`[2,3,9]` 에만 들어갔다.

**집단 B · 생성됐으나 표시 제외 59경주** (⚠ 분모 /59)
| 유형 | n | % |
|---|---|---|
| **③ EV·저배당 강등**(`quinellaRef` 로 밀림) | **49** | **83.1%** |
| ④ 기타 목록에만 존재 | 10 | 16.9% |
- ⚠ **② 개수 상한 컷은 0건**이다. 육안 확인 결과 `松山 2R`·`奈良 4R/7R`·`松山 4R` 은
  표시 복승이 **빈 리스트** — 상한이 아니라 **전멸 후 참고 강등**이었다.

### 🚫 이 수치를 "고쳐야 할 40%"로 읽지 말 것
축이 2두면 교차 1개가 늘지만 후보풀 6~8두에서 전 교차를 만들면 **조합이 15~28개**가 된다.
**총투자 동일 기준으로 순이익을 계산하기 전에는 조합 확대안을 내지 않는다**(원칙: 조합을 늘리는 안은
그렇게 하지 않으면 자동으로 유리해 보인다). ⏸ **조합 확대 = 승인 대기.**

### ⚠ `horses[].odds` 보유율 12.9% — 순이익 계산의 선행 조건
| 구분 | 보유율 |
|---|---|
| 전체 3,900행 | **12.9%** |
| 경마 2,493행 | 20.3% |
| **경륜 1,407행** | **0.0%** |
- 원인(app.py:14501·14512): `win = an.get("single")` — **단승(単勝) 배당만** 받는다.
  **경륜은 단승을 수집하지 않으므로 구조적으로 0%**이고, 경마도 단승 수집이 2026-07-29 에야 복구됐다.
- 🔴 **"파싱은 되는데 저장행에서 탈락" 유형이 아니다** — **애초에 수집 대상이 아니었다.** 유형을 구분할 것.
- ✅ **대안**: 배당대 통제·순이익 계산에는 단승 대신 **`curQ`(복승 대표배당)** 를 쓴다(보유율 거의 100%).

## ✅ [2026-07-30] 카와사키 10R — **조합이 만들어진 사례**(성공 사례 기록 · n=1)

> ⚠ **"이 로직이 좋다"로 읽지 말 것.** 아래는 **"이 조건에서 조합이 만들어졌다"는 사실 기록**이다.
> 좋은지 나쁜지는 **84경주 분해 후에 판정**한다.

### 사실 (저장값 기준 · 화면 아님)
| 항목 | 값 |
|---|---|
| 결과 | **6-5-7** · 정답 복승 `5+6` **21.5배** · 삼복승 `5+6+7` 168.5배 |
| `displayedCombos`(19:39:53 = 마감 7초 전) | 복승 `[3,6]`·**`[5,6]`** / 삼복승 `[]` |
| 판정 | `quinella_hit=True` · `trifecta_hit=False` · **`pnl=+20,500`** |
| 정답 조합 근거 | **"시장유력+급락"** |
| `axis` | `[3, 6]` · `third` 1 · `favAxis` `[6,3]` |
| `strong_signals.count` | **0** |

### ⚠ 전제 정정 — "마감 3분 전 편입"이 아니다
`keyHorses` 는 **처음부터 끝까지 `[6,3,1]` 고정**이고 **5번은 한 번도 유력마가 아니었다.**
`5+6` 은 **T-6.8분(19:33:13)에 이미 등장**했고 이후 **들락날락**했다:
`T-6.8 있음 → T-6.3 빠짐 → T-2.8 복귀 → T-2.1 빠짐 → T-1.0 최종 복귀`.
→ 🔴 **이 경주는 「막판말 입상 207경주」 그룹의 사례가 아니다**(그 정의는 `keyHorses` 첫 등장이 T-3~마감).

### 🔴 회원은 정규 발송으로 이 조합을 받지 못했다
```
카톡 T-7 19:33:08  복승 3+6 (5.5배)
카톡 T-5 19:34:55  복승 3+6 (5.4배)      ← 정답 5+6 없음
[변경알림·즉시] 19:36:01(남은 239s)  복승 추가: 5+6 / 복승 제외: 3+12
```
- **T-5 정규 발송에는 없었고 마감 4분 전 '즉시 변경알림'으로 나갔다.**
- ⚠ 단 이 알림은 **마감 전**이라 나고야 9R 의 T+1(발주 후) 알림과는 성격이 다르다.
  회원이 그 알림을 봤다면 살 수 있었다. **"시스템은 잡았고 회원은 늦게 받았다"** 가 정확한 표현.

### 🔬 나고야 12R(실패)과 대조 — 조건 후보 1개 (⚠ n=2 · 확정 아님)
| | 카와사키 10R (성공) | 나고야 12R (실패) |
|---|---|---|
| `axis` | `[3,6]` | `[3,4]` |
| 정답 복승 | `5+6` (축 6 + 축밖 5) | `4+9` (축 4 + 축밖 9) |
| `keyHorses` 등장 이력 | `[1,3,6]` — **5번 없음** | `[3,4,5,7,8,9,10,12]` — **9번 있음** |
| `strong_signals.count` | **0** | **0** |
| 정답 조합 근거 | **"시장유력+급락"** | 🔴 **생성 안 됨** |
- 🔴 **`strong_signals.count` 는 조건이 아니다** — 둘 다 0이다(예상은 틀렸다).
- 🔴 **축 구조도 조건이 아니다** — 둘 다 "축 1두 + 축 밖 1두" 구조다.
- ⚠ **역설**: 나고야 12R 이 조건상 **더 유리했다**(9번은 `keyHorses` 에 실제로 등장했고,
  카와사키 5번은 등장조차 안 했다). 그런데도 나고야는 조합이 안 만들어졌다.
- ➡ **조건 후보(단 하나)**: **「시장유력+급락」 경로가 발동했는가.**
  **84경주를 이 축으로 분해하면 검증된다.** 지금은 확정하지 않는다.

### 📶 Accept 수정 기여 (⚠ n=1 · 반사실 추정)
```
16틱 · distinct 16틱(2중 기록 없음) · 첫 T-9.8분 ~ 끝 T-1.5분
수집 간격 중앙 34.0초 · 최대 41.0초       ← 수정 전 62~71초
src: oddspark 15 + private 1              ← 南関東 서버 수집(7/29 배선분)
```
정답 `5+6` 의 **최종 복귀 시각은 19:39:03(T-1.0분)** 이고 동결은 19:39:53 이다.
수정 전 62~71초 주기였다면 T-1.5분 다음 수집이 T-0.4분 이후이거나 없어,
동결 시점에 직전 값(`3+13` 등)이 남았을 가능성이 있다.
⚠ **반사실 추정이므로 단정하지 않는다. n=1 사례 기록.**

## 🔮 [2026-07-31] Gemini 독립 예측 Phase A/B — 판정선 고정

> **역할 전환**: Gemini 를 **코드 리뷰가 아니라 경주 예측 엔진**으로 쓴다.
> 통계 테이블(`flow_table`)은 셀 단위로만 봐서 *"같은 라인에 선행형이 둘이라 내부 경쟁"* 같은 건 표현되지 않는다.
> ⚠ `GEMINI_REVIEW_ENABLED`(코드 리뷰)는 **끈 채로 둔다 — 재개하지 않는다.**

### 구성
| 단계 | 위치 | 시점 |
|---|---|---|
| **Phase A** 예측 기록 | `_multi_collect_one` 의 전적 수집 **직후**(`_forecast_after_form`) | 수집 창(발주 10분전~2분후) · 경주당 1회 |
| **Phase B** 기계 채점 | `_apply_result_learning` 진입 직후(`_grade_forecast_on_result`) | 결과 확정 이벤트 |
- 🔴 **분석 경로(`_triple_analyze`)에 넣지 않은 이유**: 마감 후 폴링마다 재호출된다.
  2026-07-30 Gemini 하루 **670건**(경주당 7회) 사고가 정확히 그 경로였다.
- 🔴 **완전 격리**: 예측·채점이 실패해도 배당 수집·추천 생성·결과 학습에 **일절 영향 없다**(각 호출부 자체 try).
- 저장 `logs/forecast/<YYYYMMDD>_<경주>.json` · 플래그 `GEMINI_FORECAST_ENABLED`.

### 🔴 3대 금지 입력 (Overfitting 방지 · 실측 검증 완료)
① 배당·인기순위·시장 최저 조합 ② 우리 추천(`finalQuinellas`·`keyHorses`·`axis`)
③ paceBonus **가산된** 점수(`record_score`·`totalScore`) → **가산 전 `paceBonusBase` 만**
- 실측(平塚 2경주): `horses[0]` 키 = `no·name·gait·grade·paceBonusBase·recentPlacings·weeksInARow` — **금지 항목 없음** ✅
- 넣으면 시장·우리 답안을 베껴 **잘 맞아도 새 정보가 아니다**(7/30 "마감 후 유력마" 함정과 동형).

### 🔴 판정선 — **사후에 낮추지 않는다**
| 도달 | 판정 |
|---|---|
| **30경주** | 예측 JSON **형식 점검**. **폐기율 20% 초과 시 프롬프트 수정** |
| **100경주** | **Gemini 평균 `hit_count` ↔ 시장 평균 `market_hit_count`** 대조<br>· **낮으면 종결** · **비슷하면 이변 경주만 재판정** · **높으면 추천 입력 편입 검토** |
- **시장 대조군**: 마감 전(T-8~T-0) 스냅샷의 **단승 최저 3두**. ⚠ **마감 후 배당 사용 금지**(시장이 유리해져 비교 불성립).
- 체크리스트에 **폐기율·실패율** 2항목 추가 → **19 → 21항목**.

### 검증 (2026-07-31 · 실저장 건수로 확인)
`平塚 2경주` 1건 실제 저장 — `top3=[2,4,1] conf=4 · 폐기 0 · 실패 0 · 폐기율 0.0%`
예측 근거: *"선행형이 셋이지만 코멘트에서 2번·5번 대결을 예고 → 특정 라인에 힘이 실림"*
→ **통계 셀로는 표현되지 않는 관찰**이다(이 시스템을 만든 목적 그대로).

## 🚫 [2026-07-31] 네거티브 필터 근거 — 확정 기록 (⏸ 구현은 별도 승인)
> **선행 많은 판에서 추입끼리 묶은 조합은 실측 1.11%로 시장 기대(2.29%)의 절반이다.
> 중앙 배당 75.5배로 고배당 구간이다. 조합 단위 n=722 기준이며 경주 단위 n=35 는 판정 불가다.**

| 항목 | 값 |
|---|---|
| 셀 | `빠른\|7두 · 추입+추입` (`data/simulation_db/flow_table.json`) |
| n / 적중 | **722 / 8** |
| 실측 확률 | **1.11%** · CI [0.0056, 0.0217] |
| 시장암시 | 2.29% |
| **엣지** | **0.4848** · CI **[0.179, 0.821]** ← **상한이 1.0 미만 = 통계적으로 유의** |
| 중앙 배당 | 75.5배 |
- 검산 0.0111 ÷ 0.0229 = 0.4847 ≒ edge ✅
- 🔴 **근거는 조합 단위다.** 경주 단위(n=35·적중 0)는 **판정 불가**이므로 근거로 쓰지 않는다.

## ✏️ [2026-07-31] 정정 2건 — 총평 활용 · 주로 상태 보유율

### ① 총평(comment) — **"우선순위 하향" 판단을 뒤집는다**
> ⚠️ **종전 기록 정정**: *"규칙 기반 축 추출이 5/70(7%)에 그쳐 총평 구조화 우선순위를 하향한다"* 는
> **결론이 틀렸다.** 문제는 총평이 아니라 **정규식으로 구조화하려 한 접근**이었다.
- **정규식 추출은 7%로 낮으나, LLM 에 원문을 그대로 넘기면 작동한다.**
  2026-07-31 예측 첫 건(平塚 2경주)에서 Gemini 가 총평 원문
  *「こちらも小川と友永の２分戦だ。…高橋連れて決める。」* 를 읽어
  **「2번(小川)이 4번(高橋)을 동반하는 라인 구축」·「2번·5번 2분전」** 을 짚었다.
  → **라인 불균형 지목**. **통계 테이블(`flow_table` 셀)이 못 하는 일이다.**
- ➡ **구조화(파싱)하지 말고 원문을 그대로 LLM 에 넘긴다.** `starters_store[rk].comment` 는
  이미 100% 저장 중이므로 **추가 수집 비용 0**이다.
- ⚠ 단 **n=1** 이다. 예측이 쌓이면 총평 보유 경주 ↔ 미보유 경주의 `hit_count` 를 대조해 판정한다.

### ② 주로 상태 — **0% 가 아니다. 분모를 잘못 잡았다**
| 분모 | 보유율 |
|---|---|
| 7월 경마 전체 (776+49) | 0.6% |
| **7/29 이전** | **0/776 = 0.0%** |
| **7/30 이후** | **5/49 = 10.2%** |
- 실값 확인: 나고야 `더트/重/1500` · 카와사키 `더트/良/1400` → **배선이 최근 작동을 시작했다.**
- 🔴 **오늘 분모 문제 여섯 번째다.** D1·D4 에 rolling 을 도입해 놓고 여기서는 누적 분모를 썼다
  (원칙 8-C 를 만든 세션에서 같은 실수를 반복). **비율을 낼 때마다 분모를 먼저 적는다.**

### 📌 10.2% 의 원인 — "경로 문제"가 아니라 "그 경로가 원래 소수"
| 전적 수집 경로 | 경주 | 주로 컬럼 |
|---|---|---|
| `keirin`(경륜 출마표) | 100 | 개념 없음 |
| **`oddspark`(경마 出走表)** | **24** | ✅ **유일하게 있음**(`_keiba_parse_shutsuba`) |
| `korea`(PDF) | 18 | 없음 |
| `keiba_nar`(DebaTable) | 2 | 없음 |
- 7/30 경마 49경주의 `raw_profile.source`: oddspark 20 · keirin 10 · korea 9 · 없음 8 · keiba_nar 2.
- ➡ **주로를 주는 경로가 oddspark 하나뿐**이고 그 경로가 전체의 40% 수준이라 10.2% 가 나온다.
  **다른 경로(NAR DebaTable·한국 PDF)에는 주로 컬럼 자체가 없다** — 배선으로 해결되지 않는다.

## 🌬 [2026-07-31] 날씨·바람 소스 조사 — **JMA 아메다스 권고(키 불필요)**

> 경륜은 **맞바람이면 선행이 불리**하다. 각질로 예측하면서 바람을 모르면 전제가 흔들린다.
> 그런데 현재 `WEATHER_VENUES` 에 **경륜장이 0곳**이고 API 키도 없어 **전부 더미**다.

| 소스 | 키 | 일본 | 바람 | 판정 |
|---|---|---|---|---|
| **OpenWeatherMap**(현행 `_weather_fetch_raw`) | **필요** | 지원 | 풍속 m/s | 🟡 키 발급 필요 · 무료 60회/분·1000회/일 |
| **JMA 예보** `bosai/forecast/data/forecast/<지역>.json` | **불필요** | ✅ | 풍향 **텍스트만**(`西の風　後　北西の風`) | 🟡 광역·풍속 없음 |
| 🟢 **JMA 아메다스** `bosai/amedas/data/map/<YYYYMMDDHHMM>.json` | **불필요** | ✅ | **풍속(m/s)+풍향 실측** | ✅ **권고** |
- 아메다스 실호출 검증(2026-07-31 03:50): **HTTP 200 · 251KB · 관측지점 1,286개**
  예) 지점 11001 `풍속 9.4 m/s · 풍향 4 · 기온 16.5 · 강수1h 0.0` (값은 `[관측치, 품질플래그]` 배열)
- **10분 주기 갱신** · 최신 시각은 `bosai/amedas/data/latest_time.txt` 로 조회.
- ⚠ **매핑 단위가 다르다** — 아메다스는 **지점코드**(위경도 아님)라 경기장↔최근접 지점 매핑표가 필요하다.
  지점 목록: `bosai/amedas/const/amedastable.json`(위경도 포함) → 경기장 좌표와 최근접 계산으로 1회 생성 가능.
- **OpenWeather 키 발급**(대안·대표 직접): `openweathermap.org` 가입 → API keys 탭 → 기본 키 자동 발급
  (활성화까지 수십 분~2시간) → `.env` 에 `WEATHER_API_KEY=<키>` 추가.

### ⚠ 좌표 테이블 키 설계 — **경기장명 표준키 문제와 얽혀 있다**
현재 `WEATHER_VENUES` 는 한글 키 6곳(서울·부산·제주·모리오카·나고야·가나자와)뿐이고,
`WEATHER_ALIASES` 는 **한국 경마장 별칭만** 담고 있다.
오늘 개최 13곳 중 **나고야 1곳만 등록**돼 있다: `いわき平·武雄·防府·고치·기후·나고야·사세보·소노다·아오모리·야히코·와카야마·카와사키·케이오카쿠`
→ 🔴 **한자(`伊東`·`平塚`)와 한글(`이토`·`히라츠카`)이 섞여 있어 좌표 조회도 실패한다**
  (오늘 조회 오판 3건과 **같은 뿌리**). **좌표 테이블을 만들기 전에 표준키가 먼저다.**
  기존 `_TRACK_GROUPS`/`_track_norm`(한/일/영 별칭 25개)을 **재사용**하는 것이 맞다 — 새로 만들지 않는다.

## 🚫 [2026-07-31] 조교 평가 — **oddspark 에 없다(종결)**
`HorseDetail.do` 3건 실측(나고야 3R · 18.7KB/21.3KB/19.7KB):
- 검출 키워드: `調教` 1회 · `厩舎` 1회 **뿐이고 둘 다 네비게이션/프로필 라벨**이다
  (`調教師検索` 메뉴 · `厩舎（所属） 坂口義幸` 소속 표기).
- `追切`(추입조교)·`短評`·`評価`·`仕上`·`脚元` **전부 0건**.
- ➡ **무료 공개분에 조교 평가 본문은 없다.** 「경마 예상문 정보원 없음」과 **같은 결론**이다.
  프롬프트의 「제공되지 않는 정보」에 계속 명시한다.

## 🗝 [2026-07-31] 경기장 표준키 — 실측 결과 **`_track_norm` 이 이미 12/13 을 커버한다**

> **순서 결정(권대표)**: 표준키가 아메다스보다 먼저다. 표준키 없이 매핑표를 만들면 두 번 만들게 된다.
> 표준키 하나가 **넷을 막고 있다** — 날씨 좌표 · 조회 오판(`伊東`/이토) · 결과 매칭(`平塚` 33건) · 카드 날짜 섞임.

### 실측 (⚠ 분모 = 오늘 개최 13곳)
| | 결과 |
|---|---|
| `_TRACK_GROUPS` 표준키 | **55개** · `_TRACK_REVERSE` **197 항목** |
| `_track_norm` 표준키 등록 | **12 / 13 (92.3%)** — 미등록은 **`防府`(호후) 1곳뿐** |
| **좌표(`WEATHER_VENUES`) 등록** | **1 / 13** — 나고야만 |
- 한자 변환 실측: `伊東→이토` · `西武園→세이부엔` · `和歌山→와카야마` · `いわき平→이와키타이라`
  · `青森→아오모리` · `岐阜→기후` · `川崎→카와사키` · `園田→소노다` · `高知→코치` **모두 정상**
- ⚠ **미변환**: `平塚`(→히라츠카)·`防府`(→호후) **2곳이 별칭 사전에 없다.**
  🔴 **`平塚` 33건 매칭 실패의 직접 원인이 이것이다** — 표준키 체계 결함이 아니라 **별칭 2개 누락**이다.

### ⚠ 내 검증이 한 번 틀렸다 (원칙 8-D 사례)
처음 측정에서 *"`_track_norm` 이 한자를 전혀 변환 못 한다(0/13)"* 로 나왔으나,
**AST 추출에서 `_TRACK_REVERSE` 를 만드는 `for` 루프를 빼먹은** 것이었다(`ast.Assign` 만 수집).
→ **결함이 아니라 검사식 오류.** 원자료로 재확인해 12/13 으로 정정했다.

### ➡ 통일 방안 (⏸ 구현 승인 대기)
| 단계 | 내용 | 위험 |
|---|---|---|
| **1** | `_TRACK_GROUPS` 에 **`히라츠카: [平塚, hiratsuka]` · `호후: [防府, hofu]` 2줄 추가** | **매우 낮음** — 추가만 |
| **2** | **조회 시점 정규화** — 저장 형식은 그대로 두고 조회·매칭에서 `_track_norm` 을 태운다 | 낮음 · **과거 데이터 즉시 해소** |
| 3 | 소급 정규화(저장값 자체 변경) | 🔴 **높음** — 기존 데이터를 못 찾게 될 수 있다 |
- 🔴 **소급 정규화는 하지 않는다.** 저장은 원문 유지, **조회에서만 정규화**한다.
  (오늘 `伊東`/`이토` 오판도 조회 시점 문제였고 데이터는 멀쩡했다.)
- **경기장명 사용 지점**: 저장(`starters_store`·`odds_history`·`analysis_log` 파일명) ·
  조회(`_resolve_race_key`·`_snapshot_result_for`·`_area_num`) · 매칭(결과 백필·`_JP_BABA_CODE`) ·
  표시(카드·카톡). **조회·매칭 계층에만 `_track_norm` 을 태우면 된다.**

## 🌬 [2026-07-31] 아메다스 매핑 — **전 경기장 10km 이내(예상보다 훨씬 좋다)**

### 최근접 관측지점 거리 (⚠ 분모 = 검증 16곳 · 풍속 관측 지점만)
| 거리 | 곳 |
|---|---|
| **≤10km** | **16 / 16 (100%)** |
| 10~20km | 0 |
| >20km | 0 |
- 중앙 **4.3km** · 평균 4.7km · 최대 **9.8km**(平塚↔辻堂)
- 예: 사세보 **0.4km**(佐世保) · 와카야마 0.7km(和歌山) · 이와키타이라 1.0km(小名浜) ·
  기후 2.6km(岐阜) · 소노다 4.9km(豊中) · 카와사키 7.4km(羽田) · 이토 8.9km(網代)

### 🔴 임계 권고 — **20km**, 초과 시 "바람 정보 없음"
- **근거**: 지상 바람은 **해륙풍·산곡풍의 국지 변동 규모가 대략 10~20km**다.
  그 안에서는 같은 기류로 볼 수 있고, 넘어가면 **다른 지역 바람**이 된다.
  현재 전 경기장이 10km 이내라 **임계 20km 는 여유 있게 안전**하다.
- ⚠ **틀린 바람보다 없는 게 낫다.** 임계 초과 시 값을 넣지 말고 프롬프트의
  「제공되지 않는 정보」에 남긴다 — Gemini 가 지어내는 것보다 **명시적 부재가 안전**하다.
- ⚠ **지점거리를 반드시 함께 저장**한다. 나중에 "이 바람이 믿을 만한가"를 판단해야 한다.

### 소스 사양
- `bosai/amedas/const/amedastable.json`(**188KB · 1,286지점 · 위경도·고도**) — 1회 받아 매핑표 생성
- `bosai/amedas/data/latest_time.txt` → `bosai/amedas/data/map/<YYYYMMDDHHMM>00.json`(**251KB · 10분 주기**)
- 값 형식 `[관측치, 품질플래그]` · 풍속 관측 지점은 `elems` 3번째 자리가 `1`
- ⚠ 251KB 전체를 받으므로 **경주가 있는 시간대에만** 호출한다(수집 창 기준).

## 🧪 [2026-07-31] 원칙 12 — 회귀 테스트는 **실제 함수를 호출해야 한다**

> 🔴 **오늘 실제로 걸린 사례다.** "오늘 문제된 경주를 스냅샷으로 떠서 고정"이라는 지시를
> `run_freeze_regression.py` 가 **고정 Fixture 만 읽는 형태**로 구현했다.
> 그 파일은 `_build_analysis_log` 를 **한 번도 호출하지 않는다**.
> ⇒ Fixture 는 *이미 오염된 과거를 찍어둔 사진*이라 **코드를 고쳐도 영원히 초록이 되지 않는다.**
> 그런데도 "동결하면 4개 전부 초록이 된다"고 **두 번 단언**했다. 확인 없이 한 말이었다.

- **테스트가 무엇을 읽는지 먼저 본다.** 고정 파일만 읽으면 그것은 *역사 기록*이지 *동작 검증*이 아니다.
- **동작 검증은 실제 함수를 통과시켜야 한다.** 입력을 넣고 **출력이 바뀌는지**를 봐야 한다.
- **Fixture 를 지우지 않는다.** 외부 앵커(회원이 받은 카톡 원문 = 파이프라인 밖의 사실)로서
  독립적 가치가 있다. 내부 저장값끼리 비교하면 둘 다 오염됐을 때 "일치"가 나와 통과해 버린다.
- 🔴 **역할을 분리한다**:

| 파일 | 역할 | 등급 |
|---|---|---|
| `tests/run_freeze_regression.py` | 외부 앵커 · 과거 사실 기록 | 🟡 `EXPECTED_FAIL` **영구 유지**(초록 불가) |
| `tests/run_freeze_behavior.py` | 실제 동결 함수 호출 · 동작 검증 | 🔴 **차단 등급**(실패 시 커밋 불가) |

- ⚠ "고치면 초록이 된다"는 **테스트를 돌려본 뒤에만** 말한다. 원칙 7("이미 고쳐졌다" 전제 금지)의 확장이다.

## 🔒 [2026-07-31] 마감 시점 단일 동결 — 구현 완료 · 실측

### 무엇이 뚫렸나
`app.py` 의 readonly 보호 블록은 **5개 필드만** 지켰다(`corePicks`·`final_recommendation`·
`recommendation_history`·`signals_detected`·`odds_timeline`).
`keyHorses`·`summary`·`strong_signals`·`horses`·`elimination`·`compression_pattern`·
`third_place_hunt` 는 **보호 목록에 아예 없었다**.
🔴 나고야 9R 이 `readonly=True` 인데도 19:41 에 `keyHorses [12,1,2]→[13,4,2]` ·
`strongSignals 3→0` 으로 바뀐 이유가 이것이다. `displayedCombos` 만 `corePicks` 안이라 살아남았다.

### 실측 (⚠ 분모 = readonly=True 이고 마감 시점 확정값이 남아 있는 파일 **529개**)
| | 건수 |
|---|---|
| 유실(현재값 ≠ 확정값) | **492 / 529 (93.0%)** |
| 3단 폴백 복원 성공 | **492 / 492 (100.0%)** |
| 복원 출처 ① `closed_row` | 235 (47.8%) |
| 복원 출처 ③ `pre_close_row` | 257 (52.2%) |
| 복원 출처 ② `timeline_snapshot` | **0 (0.0%)** — keyHorses 가 담기지 않는다 |

> ⚠ **이전 보고의 "235개"는 분모가 좁았다** — `closed` 행이 있는 파일만 셌다. 실제는 492개다.

### 복원 실패 시 — **잠그지 않는다**
종전 `_GRADE_LOCK` 은 *"복원 실패 시 현재 계산값으로라도 고정(재요동 방지)"* 이었고,
**그것이 곧 마감 후 값을 굳히는 경로**였다. 이제 실패하면 `lockFailed` 표기만 남기고 잠그지 않는다.
🔴 **틀린 값을 잠그는 것보다 안 잠긴 채로 표시하는 게 낫다.**

## 🔴 [2026-07-31] `closed` 행 55.6% 누락 — **붙인 뒤 지워진다**

### 실측 (⚠ 분모 = readonly + 확정값 보유 529개)
| | 건수 |
|---|---|
| `closed` 행 있음 | 235 (44.4%) |
| **없음** | **294 (55.6%)** |
- 종목별 미부착률: 경륜 **57.5%**(분모 341) · 경마 **52.7%**(분모 186) — **둘 다 절반**이다.
- 결과 입력 여부와 무관: 미부착 294건 중 `result` 보유가 **290건(98.6%)**.

### 🔴 원인 — 조건이 아니라 **덮어쓰기**
미부착 294건 중 **293건(99.7%)** 이 부착 조건 5가지를 **전부 충족**한 상태다
(`afterClose`·이력 존재·`displayedCombos` 존재·조합 1개 이상·기존 closed 없음).
⇒ 조건 문제가 아니다. **붙인 다음 지워진다.**

경로: `rec_history = list(prev_hist)` 는 **복사본**이라 `closed` 행을 append 해도
`doc["recommendation_history"]` 는 안 바뀐다. 그런데 그 아래 readonly 보호 블록이
`log["recommendation_history"] = _doc.get("recommendation_history")` 로 **디스크 원본(= closed 행이 없는 쪽)** 을
다시 씌운다. **그 행이 만들어진 바로 그 저장에서 폐기된다.**

### ⚠ 다행인 점 — ③ 폴백은 실질적으로 믿을 만하다
마지막 마감 전 행이 T-몇 분인가 (⚠ 분모 = 미부착 + 마감시각 확보 **249건**, 마감시각 없어 118건 제외):
| 구간 | 건수 |
|---|---|
| T-1분 이내 | 100 (40.2%) |
| T-1~3분 | 113 (45.4%) |
| T-3~5분 | 23 (9.2%) |
| **T-5분 이내 소계** | **238 / 249 (95.6%)** |
| 🔴 T-10분 초과 | 5 / 249 (2.0%) |
- 중앙 **T-1.4분** (대조: `closed` 행 보유분의 확정행은 중앙 T-0.5분)
- ⇒ ③으로 복원한 값도 **95.6%는 마감 직전 값**이다. 다만 **마감 시점 값이라고 표기하면 안 된다.**

### 작업3 추정 — `timeline_snapshot` 에 `keyHorses` 를 넣으면
- 미부착 294건 중 **285건(96.9%)** 이 스냅샷을 갖고 있다(T-5 **283** · T-7 2) → ②로 복원 가능해지는 **상한**
- 저장 비용: 383파일 × 약 150B = **0.05 MB (현재 0.7MB 대비 +7.7%)** — 무시할 수준
- ⚠ 단 T-5 는 마감 5분 전이라 **③의 중앙 T-1.4분보다 오히려 멀다.** 정확도가 아니라
  **출처 명시성**이 이득이다(어느 시점 값인지 확실해진다).

## 📏 [2026-07-31] 리플레이 가용 분모 — **고정. 다음에 또 세지 말 것**

> 오늘 리플레이마다 "마감시각 확보 N건"이 다르게 나와 **분모를 매번 다시 셌다.**
> 그때마다 결론의 신뢰도가 달라 보였다. **한 번 정하고 고정한다.**

### 🔴 가용 분모 정의
```
odds_history 전체                      2,505건 (7월)
  └ 배당(quinella)이 든 파일            1,643건 (65.6%)   ← 여기부터가 분모
      └ deadline_epoch 보유            1,012건 (61.6%)   ← 🔴 **리플레이 가용 분모**
                                                 (전체 대비 40.4%)
```

- ⚠ **배당 없는 파일 862건(34.4%)은 분모에서 제외한다.** 조사하지 않는다 —
  스냅샷이 0개이거나 빈 `{}` 라 **복구할 근거 자체가 없다.**
  (내역: 스냅샷 0개 33.8% · 스냅샷은 있으나 전부 빈 dict 22.8%)

### 종목별 (⚠ 분모 = 배당 든 파일)
| 종목 | 배당 든 파일 | deadline 보유 |
|---|---|---|
| `cycle`(경륜) | 499 | **495 (99.2%)** |
| `horse`(경마) | 284 | **266 (93.7%)** |
| `?`(분석로그 없음·구데이터) | 858 | 249 (29.0%) |
| `boat` | 2 | 2 (100%) |

### 🔴 오해였던 것 (기록해 둔다)
1. **"`.gz` 압축이 분모를 깎는다"** → ❌ 아니다. 87 → 95경주(1.1배)에 그쳤다.
   압축된 것은 **오래되고 스냅샷도 적은** 파일이라 어차피 못 쓴다.
2. **"`other` 수집 경로가 `deadline` 을 안 넘긴다"** → ❌ 아니다.
   실제 수집 경로는 전부 정상이다: `oddspark` 96.1% · `private` 84.7% · 혼합 91.2%.
   0% 인 1,417건은 **경로가 아니라 배당이 아예 없는 파일**이었다.
3. **`deadline_epoch` 자체는 2026-07-20 도입**이라 그 이전은 전부 없다(구조적).
   오늘(07-31) 신규는 **33/33 = 100%** — **신규 유출은 없다.**

### ⇒ 앞으로 리플레이 보고 시
- 분모를 **"배당 든 파일 중 deadline 보유"** 로 쓰고, 위 표를 인용한다.
- 조건 필터로 더 줄어들면 **그 감소분만** 별도로 밝힌다(예: 95/1,012).

## 🔴 [2026-07-31] 소표본 착시 — **여섯 번째 사례** (Gemini 고배당 3건)

**철회한 결론**: *"Gemini가 시장과 다르게 찍고 맞으면 고배당 밸류 단서라는 설계 메모의 가설이
실측으로 지지된다"* — ❌ **철회한다.**

| | |
|---|---|
| 관측 | 우위 8건 중 10배 이상 **3건 = 37.5%** |
| 기대값 | 시장 전체 10배 이상 29.1% → **2.3건** |
| 차이 | **0.7건** |

- 🔴 **n=8에서 한 건 차이로 25% ↔ 37.5% 가 바뀐다.** 우연과 구분되지 않는다.
- ⇒ **"판정 보류. 변별 100경주까지"** 로 정정.
- ⚠ 개별 사례(와카야마 8R — Gemini 가 4·6 을 찍었고 우리 유력마에 4가 없었다)는
  **실물 증거로 남기되 통계적 지지로 쓰지 않는다.**

## 🔴 [2026-07-31] 유력마 ↔ 조합 생성 단절 — **구조 결함 확인**

⚠ 분모 = 오늘 경륜 결과확정 **54경주**(추천 없음 0경주)

### ③ 유력마에 **없는** 말이 추천 조합에 들어간다 — **17경주 (31.5%)**
```
기후 1경주     유력마[5,3,1]     추천[[4,6]]        ← 조합 전부가 유력마 밖
사세보 12경주   유력마[3,6,2]     추천[[4,6],[4,7]]  ← 조합 전부가 유력마 밖
야히코 1경주    유력마[3,7,5]     추천[[1,5]]        ← 1번이 유력마에 없다
```
🔴 **`keyHorses` 와 복승 조합이 다른 소스에서 나온다.** 같은 분석이 두 개의 다른 답을 낸다.

### ① 유력마를 조합으로 만드는 비율 — **39.2%**
```
경주 54 · 유력마 평균 3.3두 · 가능조합 212 · 실제 생성 83 (39.2%)
유력마 두수 분포: 3두 40경주 · 4두 12 · 5두 2
```

### ② 1·2착이 **둘 다 유력마인데 조합을 안 만든** 경주 — **15경주 (27.8%)**
```
20.2배 사세보 2R    유력마[7,2,5]  추천[2+7]        정답[2,5]
17.8배 와카야마 7R   유력마[2,3,4]  추천[2+3]        정답[3,4]
14.6배 와카야마 2R   유력마[2,5,7,1] 추천[2+5][1+5]  정답[1,2]
…
🔴 놓친 회수 합계 126.4 (현재 총회수 46.7 의 **2.7배**)
```

### 작업2 — 조합 확대 K별 리플레이 (⚠ **오늘 하루 54경주 · 판정 불가**)
| 안 | 투자구좌 | 적중 | 회수 | 회수율 |
|---|---|---|---|---|
| 현행(기준선) | 83 | 18 | 46.7 | **56.3%** |
| 현행 +1 | 137 | 24 | 98.7 | 72.0% |
| 🔴 **현행 +2** | 183 | 31 | 164.2 | **89.7%** |
| 현행 +3 | 201 | 33 | 173.1 | 86.1% |
| 유력마 전조합 | 212 | 30 | 154.2 | 72.7% |
| 현행 ∪ 전조합 | 231 | 33 | 173.1 | 74.9% |

- **+2 에서 최대**이고 그 뒤로는 **떨어진다** — "많이 사면 좋다"가 아니다.
- ⚠ **표본이 오늘 하루뿐이다. 판정 불가.** 최소 **300경주(약 6일)** 필요.
- ⚠ **실전 반영 금지.** 측정 결과만이다.

### 작업3 — 5~10배·20배 이상 **20경주 0적중**(⚠ n<30 판정 불가·사례 관찰)
1·2착이 둘 다 유력마 안: **5 / 20 (25.0%)** — 나머지 15건은 유력마 자체가 빗나갔다.
⇒ **조합 생성만 고쳐서는 절반도 못 잡는다.** 유력마 선정 자체의 문제가 더 크다.

## 🔴 [2026-07-31] 정정 2건 — 조합 확대 해석

### 정정 ① **"+2가 답"이 아니다**
> **+2 회수율 89.7% 는 여전히 100% 미만이다. 최적 K 로 바꿔도 계속 잃는다.**
> **조합 확대는 "덜 지는" 방법이지 이기는 방법이 아니다.**

- ⚠ 그리고 +2 는 **6개 안 중 오늘 데이터에서 최대를 고른 값**이다 — **사후 최적화**다.
  내일은 최대가 +1 일 수도 +3 일 수도 있다.
- ⇒ **"최적 K 미확정 · 하루 표본(54경주)"** 으로만 쓴다.

### 정정 ② **"놓친 회수 126.4 = 2.7배"를 단독 인용하지 말 것**
공짜로 얻는 값이 아니다. **그 조합을 사려면 구좌가 늘어난다.**
정확한 값은 K별 표다: `+2 → 183구좌 · 164.2회수 · 89.7%`.
⇒ **항상 구좌 증가와 함께 표기한다.**

## 🔴 [2026-07-31] `keyHorses` ↔ 조합 소스 단절 — **원인 확정**

### 분기점 (코드)
```
key_horses  = ranked[:3]           (app.py:9796)  … 통합 유력마 순위
              → _io[:3] 로 재정렬  (app.py:10029)

_final_picks(cp, curQ, valid_nos, smart_quinella, max_q,
             reversal_quinellas, dark_quinellas, signal_horses, sig_meta, sport)
🔴 **`key_horses` 를 인자로 받지 않는다.**
   조합은 `curQ`(배당판) · `smart_quinella` · `reversal/dark_quinellas` · `signal_horses` 로 만든다.
```
⇒ **같은 분석이 두 경로에서 다른 답을 낸다.** 공통 입력을 공유하지 않는다.
   실측: 유력마에 **없는** 말이 추천 조합에 들어간 경주 **17/54 (31.5%)**.
   ⚠ 회원 화면에 *"유력마 3·6·2 / 추천 4+6"* 이 나란히 보이면 **신뢰가 깨진다.**
   회수율과 별개로 **표시 일관성 문제**다.

### 어느 쪽이 맞는가 (⚠ 분모 = 오늘 경륜 54경주 · **하루 표본 · 판정 불가**)
| 안 | 투자구좌 | 적중 | 회수 | 회수율 |
|---|---|---|---|---|
| 현행 추천(`displayedCombos`) | 83 | 18 | 46.7 | **56.3%** |
| 유력마 상위2 조합만(1개) | 54 | 14 | 25.4 | **47.0%** |
| 유력마 전조합 중 저배당 2개 | 108 | 23 | 80.0 | 74.1% |
| 유력마 전조합 중 저배당 3개 | 162 | 28 | 134.3 | **82.9%** |
| 유력마 전조합 | 212 | 30 | 154.2 | 72.7% |

- 🔴 **유력마만으로 1개를 뽑으면 현행보다 나쁘다(47.0% < 56.3%).**
  ⇒ **"유력마 쪽이 맞다"고 단정할 수 없다.** 어느 쪽도 우월하지 않다.
- ⚠ **모르는 채 통일하면 나쁜 쪽으로 통일된다.** 표시 일관성은 별도로 풀어야 한다.

## 🔴 [2026-07-31] 유력마 정확도 = **천장 55.6%**

⚠ 분모 = 오늘 경륜 결과확정·유력마 보유 **54경주**
| | |
|---|---|
| 1·2착 **둘 다** 유력마 안 | **30 / 54 = 55.6%** ← 🔴 **천장** |
| 1두만 안에 | 20 (37.0%) |
| 둘 다 밖 | 4 (7.4%) |

두수별 (⚠ 분모 = 각 그룹)
| 유력마 | 경주 | 포함 | 비율 | 7두 무작위 기대 |
|---|---|---|---|---|
| 3두 | 40 | 23 | **57.5%** | 14.3% |
| 4두 | 12 | 6 | 50.0% | 28.6% |
| 5두 | 2 | 1 | 50.0% | 47.6% |

### 🔴 시장 상위 3두와 대조 (⚠ 분모 = 시장 산출 가능 51경주)
| | 포함률 |
|---|---|
| 우리 유력마 | 28 / 51 = **54.9%** |
| 시장 상위 3두 | 27 / 51 = **52.9%** |
| 차이 | **+2.0%p** |

- 🔴 **우리 유력마는 시장보다 2.0%p 나을 뿐이다.** n=51 이라 이 차이는 우연과 구분되지 않는다.
- ⇒ **조합을 아무리 잘 만들어도 55.6% 가 천장**이다. 조합 확대는 천장 아래를 채우는 일이고,
  **천장 자체를 올리려면 유력마 선정을 고쳐야 한다.**

## 🔴 [2026-07-31] 확정 사실 3건 — **다음 판단의 전제**

1. **유력마 선정이 배당판의 복사본이다.**
   우리 54.9% ↔ 시장 52.9% (⚠ 분모 51경주) — **n=51 에서 1건 차이**로 우연과 구분 안 된다.
   ⇒ **71.6% 시장 종속의 근원은 조합 생성이 아니라 유력마 선정이다.**
2. **유력마를 늘리는 것은 답이 아니다.**
   무작위 대비 배수: 3두 **4.0배** · 4두 **1.7배** · 5두 **1.05배**.
   ⇒ **4두째부터 정보가 없다.**
3. **조합 최적화 천장 89.7% 는 100% 미만이다.**
   ⇒ **시장을 못 이기면 공제율을 못 넘는다.**

## 🔴 [2026-07-31] 문제 재정의 — **"경주 예측"이 아니라 "짝 찾기"**

⚠ 분모 = 오늘 경륜 결과확정 **54경주**
```
1·2착 둘 다 유력마 안   30 (55.6%)
1두만 안에             20 (37.0%)   ← 🔴 여기가 문제 구간
둘 다 밖                4 ( 7.4%)
⇒ **92.6% 가 최소 1두를 맞춘다.** 완전히 빗나가는 것은 7.4% 뿐이다.
```

### ② 놓친 짝이 **우리 점수**에서 몇 위였나 (⚠ 분모 = 20경주)
| 구간 | 건수 |
|---|---|
| 1~3위 | 10 (50%) |
| 4~5위 | 9 (45%) |
| 6위 이하 | 1 (5%) |
- 중앙 **3위** · 유력마 컷(상위3) 밖 **10건(50%)**

### ③ 놓친 짝 조합의 **배당** (시장 전체 중앙 5.4배)
| 구간 | 건수 |
|---|---|
| 2~5배 | 2 (10%) |
| 5~10배 | 9 (45%) |
| 10~20배 | 5 (25%) |
| 20배 이상 | 4 (20%) |
- 🔴 **중앙 9.1배 · 평균 17.6배 · 최대 82.2배** — 시장 중앙의 **1.7배**
- ⇒ **대표가 찾는 고배당이 정확히 여기 있다.**

### ④ 놓친 짝의 **시장 순위** (⚠ 분모 = 산출 가능 19건)
중앙 **4위** · 시장 상위3 밖 **15건(79%)** — **시장도 대부분 못 봤다.**

### 🔴 ②×④ 교차 — **가장 중요한 표**
| | 건수 | 뜻 |
|---|---|---|
| 🔴 **우리 안 + 시장 밖** | **9 (47%)** | **우리가 봤는데 컷했다** |
| 우리 밖 + 시장 밖 | 6 (32%) | 새 정보 필요 |
| 우리 밖 + 시장 안 | 3 (16%) | 우리 점수식 문제 |
| 우리 안 + 시장 안 | 1 (5%) | — |

- 🔴 **47% 는 우리 점수표 상위3 안에 있었는데 조합으로 안 만든 것**이고,
  그 대부분은 **시장 상위3 밖**이다 — **시장이 못 본 것을 우리는 봤는데 버렸다.**
- ⇒ **점수식을 고치기 전에 "컷 기준"부터 봐야 한다.** 새 데이터가 없어도 47% 는 손이 닿는다.

## 🔴 [2026-07-31] 축 고정 리플레이 (⚠ 분모 = 54경주 · **하루 · 판정 불가**)
| 안 | 투자구좌 | 적중 | 회수 | 회수율 |
|---|---|---|---|---|
| 현행(기준선) | 83 | 18 | 46.7 | 56.3% |
| 축1 × 상위2두 | 108 | 7 | 84.3 | 78.1% |
| 🔴 **축1 × 상위3두** | 162 | 17 | 128.0 | **79.0%** |
| 축1 × 상위4두 | 216 | 23 | 154.3 | 71.4% |
| 축1 × 전체출주 | 315 | 29 | 170.7 | 54.2% |

- 🔴 **축(유력마 1위)이 1·2착에 든 비율 29/54 = 53.7%** — **축이 틀리면 통째로 실패한다.**
- ⚠ 조합 확대 +2(89.7%)보다 낮다. **축 고정이 우월하지 않다.**
- ⚠ 최적값이 상위3두인 것도 **오늘 데이터 사후 최적화**다. 하루 표본이라 판정 불가.

## 🔴 [2026-07-31] 정정 — **"47%는 우리가 봤는데 컷했다"는 무효** (원칙 8-D 사례)

### 무엇이 틀렸나
20경주의 **"우리 순위"** 를 `horses[].totalScore` 로 계산했는데,
🔴 **경륜 로그에는 `totalScore` 필드가 없다.**
`-(h.get('totalScore') or 0)` 가 **전 항목 0** 이 되어 **정렬이 무의미**했다 —
`horses` 배열의 원래 순서가 그대로 "순위"로 나왔다.

- 대표 지적이 정확했다: `key_horses = ranked[:3]` 인데 순위 1~3위가 유력마에 없을 수 없다.
  **그 모순이 곧 계측 오류의 신호였다.**
- ⇒ **②×④ 교차표(우리 안+시장 밖 47%)는 통째로 무효다. 인용 금지.**

### 경륜 `horses[]` 의 실제 점수 필드
```
record_score · paceBonus · paceBonusBase · gradeAtBonus · odds · grade · gait
🔴 totalScore · combinedProb · formScore · total 은 **없다**(경마 전용).
```

### 🔴 소스별 상위3두가 1·2착을 포함한 비율 (⚠ 분모 = 오늘 경륜 55경주)
| 소스 | 포함률 |
|---|---|
| 🔴 **`keyHorses`(현행·화면 표시)** | **47.3%** |
| `paceBonusBase` 상위3 | 32.7% |
| `record_score`+`paceBonus` 상위3 | 23.6% |
| `record_score` 상위3 (전적만) | 18.2% |
| 배당 낮은순 상위3 (시장 복사) | 16.4% |
| **시장 내재확률 상위3 (기준선)** | **53.8%** (분모 52) |

### ⇒ 다시 세운 결론
1. 🔴 **현행 `keyHorses` 47.3% 가 우리 내부 소스 중 가장 낫다.**
   `record_score` 단독(18.2%) · `paceBonusBase`(32.7%) 전부 그보다 나쁘다.
   **재정렬(`_io[:3]`)이 신호를 깎는다는 가설은 지지되지 않는다** —
   오히려 재정렬이 있어야 47.3% 가 나온다.
2. 🔴 **그런데 시장 내재확률 53.8% 가 여전히 더 높다.**
   ⇒ 우리 점수 어느 조합도 시장을 못 이긴다. **"짝을 컷했다"가 아니라 "짝을 애초에 못 봤다"** 이다.
3. ⚠ 놓친 짝이 `record_score` 상위3 안에 있던 것은 **12/23** — 절반이다.
   "새 데이터 없이 손이 닿는다"고 말할 근거가 못 된다.

### ⚠ 앞선 보고와 달라진 점
- 이전: *"유력마 54.9% vs 시장 52.9% (+2.0%p)"* — 이건 **`keyHorses` 전체(3~5두)** 기준이었다.
- 이번: **상위 3두로 통일**하면 `keyHorses` 47.3% vs 시장 53.8% → **-6.5%p 열세**.
- ⇒ **두수를 맞추지 않고 비교한 것도 오류였다.** 같은 3두로 맞추면 우리가 진다.

## ❌ [2026-07-31] 축 고정 방향 — **기각**

| | |
|---|---|
| 최대 회수율 | 축1 × 상위3두 **79.0%** (162구좌·17적중·128.0회수) |
| 비교 | 조합 확대 +2 **89.7%** 보다 낮다 |
| 🔴 축 정확도 | 유력마 1위가 1·2착에 든 비율 **29/54 = 53.7%** |

- **축이 틀리면 그 경주는 통째로 실패**한다. 46.3% 에서 전액 손실이다.
- ⚠ 79.0% 도 **100% 미만**이다. 최적값(상위3두)조차 **오늘 데이터 사후 최적화**다.
- ⇒ **기각.** 다만 ⚠ **하루 표본(54경주)** 이므로 "구조적으로 열등"이 아니라
  **"현재 근거로 채택할 이유가 없다"** 는 뜻이다.
