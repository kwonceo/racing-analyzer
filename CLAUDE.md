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
├── analysis_log/    ← 분석 로그(패턴학습 코퍼스)            [추적]
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
- **추적 유지(백업 대상)**: `analysis_log/`(패턴학습 코퍼스)·`race_results/`(경주별 완전 저장)·`race_report/`(고배당 적중 재현 리포트)·`ai_training/`(AI 학습 완전 데이터·품질점수)·`korea_session.json`·`korea_history/`·`prerace/`·`discovered_patterns.json`·`pattern_learning.json`. **`dist/`(내보내기 출력)·`highlight_wins.json`은 gitignore.**
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
- **할 일**: nankankeiba 소스 추가 → `_multi_bg_loop` 수집 대상 편입.

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
>   → 조치안: 게이트에 **'그 경주의 private 스냅샷이 실제 존재하는가'** 조건 추가 + **src 정규화**.
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
- ⚠ **Gemini 인용 오류 주의**: `_simulate_race_flow` 라는 함수는 **존재하지 않는다**(인용한 `app.py:25341~` 도 무관).
  실제로는 **`_scenario_combos`(app.py:7218)에 이미 같은 취지가 구현돼 있다** —
  `target = "추입" if pace == "빠른" else ("선행" if pace == "느린" else None)`.
  **없는 걸 새로 만드는 게 아니라, 있는 코드에 데이터를 채워 살리는 작업이다.**
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
- ⛔ **할 일 4(`_scenario_combos` 부스팅 활성화)는 기각한다.** `target = "추입" if pace == "빠른"`
  (app.py:7241)을 살리면 **유의하게 나쁜 쪽으로** 추천이 기운다. 데이터가 채워지길 기다리던 로직이었으나
  막상 채워 보니 방향이 반대였다. ⚠ 코드는 **삭제하지 않고 비활성 그대로 보존**(다른 시장·조건 재검증 여지).
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
- ⚠️ **운영 주의**: `status=="WARNING"`이면 **카카오 알림이 실제 발송**된다(`data/kakao_token.json` 연동 시). Gemini는 WARNING을 후하게 내는 경향이 있어, 경주당 최대 5분 1회(`_CALL_INTERVAL=300`) 알림이 쌓일 수 있음 → 알림 과다 시 `_CALL_INTERVAL` 상향 또는 발송 조건(예: `issues` 2건 이상)을 조여야 한다.
- ⚠️ **콘솔 인코딩**: app.py는 기동 로그에 em-dash(`—`) 등 비-cp949 문자를 출력하므로, **stdout을 파일로 리다이렉트하면 `UnicodeEncodeError`로 기동 실패**한다. `경마서버_자동시작.bat`처럼 `chcp 65001`(UTF-8 콘솔)로 띄우거나 `PYTHONIOENCODING=utf-8`을 설정할 것.
- **진단 명령**
  ```bash
  python -c "import requests; print('requests OK')"
  python -c "import os; print('KEY:', bool(os.environ.get('GEMINI_API_KEY')))"
  python -c "import glob; print('Gemini 로그수:', len(glob.glob('logs/gemini_review/*.json')))"
  ```
  로그 0건 + `logs/gemini_review/` 디렉터리 자체가 없음 = **모듈 import 실패**(디렉터리는 import 시 `os.makedirs`로 생성되므로).
