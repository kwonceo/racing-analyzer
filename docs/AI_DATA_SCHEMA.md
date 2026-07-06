# 🤖 AI 학습 데이터 스키마 (data/ai_training/*.json)

> 결과 입력마다 경주 1개당 파일 1개(`{race_id}.json`)가 저장된다. ML 학습용 완전 구조.
> 생성: `app.py _build_ai_training()` · 품질점수: `_ai_quality_score()` · 현황: `_ai_data_status()`

**현재 스키마 버전: `1.0`** (`AI_SCHEMA_VERSION`)

---

## 최상위 필드

| 필드 | 타입 | 설명 |
|---|---|---|
| `schema_version` | string | 스키마 버전(예 `"1.0"`). 구조 변경 시 상향. 없는 파일 = `legacy`(구버전) |
| `race_id` | string | 경주 고유 ID(`YYYY_MM_DD_경마장_N경주`) |
| `race_info` | object | 경주 메타(아래) |
| `horses` | array | 말별 피처(아래) |
| `odds_features` | object | 배당 파생 피처(아래) |
| `prediction` | object | 분석기 예측(추천·유력/제거·신뢰도) |
| `result` | object | 실제 착순 + 확정배당 + 적중 |
| `labels` | object | 지도학습 라벨 |
| `quality` | object | 품질 점수/등급(`_ai_quality_score`) |
| `saved_at` | string | 저장 시각 |

## race_info
`date` · `track` · `round` · `distance` · `condition`(주로) · `weather` · `horse_count` · `grade`

## horses[] (말별)
`no` · `name` · `jockey` · `jockey_winrate` · `recent_results`(최근 착순) · `record_score`(전적점수) · `distance_score` · `interval_days` · `weight_change` · `odds`(단승)
> 미수집 항목은 `null`(스키마는 유지 → 미래 수집 대비).

## odds_features (배당 파생 신호 재사용)
| 필드 | 설명 |
|---|---|
| `timeline` | 배당 스냅샷 시계열(`time`·`quinella`) |
| `drop_rates` | 말별 최대 급락률(%) |
| `excess_drops` | 말별 초과급락(시장평균 대비 %p) |
| `drop_speed` | 말별 분당 급락속도 |
| `exacta_reversal` | 쌍승 역전 감지 여부 |
| `reversal_ratio` | 역전 비율 |
| `quinella_mismatch` | 복승 불일치 점수 |
| `refund_rate` | 환급률(복승 역수합 기반) |
| `consecutive_drops` | 말별 연속 하락 횟수 |
| `fake_betting` | 페이크 베팅 감지 여부 |
| `large_scale_drop` | 대규모 급락 여부 |

## prediction
`candidates`(후보/유력마) · `eliminated`(제거마) · `recommend`(복승 메인 조합) · `strategy`(BMED 전략) · `confidence`(최고 신뢰도) · `signal_quality`

## result
`1st`~`4th` · `quinella_odds` · `exacta_odds` · `hit`(추천 적중) · `hit_pattern`

## labels (지도학습용)
`quinella_hit`(복승 적중 bool) · `winner`(1착) · `second`(2착) · `odds_range`(배당대 라벨)

## quality
`score`(0~100) · 등급(80+ AI학습용 / 60~79 참고용 / 60미만 제외) · 범위오류 시 제외 강등
> 필수 5항목 각 20점: ①배당 타임라인 2회+ ②이상감지 결과 ③전적 데이터 ④결과(1~4착) ⑤확정 배당

---

## 정비/마이그레이션 원칙
- **필드 삭제 금지**: 미수집 항목도 `null`로 유지(스키마 안정). 새 항목은 추가만.
- **버전 상향 조건**: 필드 의미 변경·구조 재편 시 `AI_SCHEMA_VERSION` 상향 + 이 문서 갱신.
- **현황 확인**: `GET /api/ai-training/status` → `schema_version`(현재) · `schema_counts`(버전별 개수, `legacy`=구버전 파일 수).
- **내보내기**: `tools/export_ai_data.py`(CSV 평탄화 + 원본 JSON, `--min-quality`·`--format`).

## 변경 이력
- **1.0** (2026-07): `schema_version` 필드 도입. 기존 필드 구조 확정(위 표 기준). 이전 파일은 `legacy`로 집계.
