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
