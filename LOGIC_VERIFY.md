# 리플레이 검증 (LOGIC_VERIFY) — 2026-07-24

> `review_engine.replay_day` 정책별 리플레이 측정 결과. 본 코드 무영향(측정 전용).
> 관련: [[LOGIC_AUDIT]] · [[LOGIC_MAP]] · [[HITRATE_AUDIT]]
> ⚠ 리플레이 ROI는 경주당 stake 고정 — 조합 수 증가 비용 미반영(실전 ROI는 더 낮음).

## 정책 목록 (review_engine.py)

- **기본**: baseline · signal_gate · lowodds_trio
- **동결 커브**: t5_freeze · t2_freeze · t1_freeze
- **동결 강화**: t2_strong(전종목) · t2_strong_cycle(경륜만) · t2_strong_all(전종목·명시)
- **수정안**: fix_main_keep · fix_axis2_trio · fix_special_incl · fix_conf_pair · fix_backing_ev
- **오늘 추가**: fix_lowodds_exempt · fix_connectors · fix_connectors_top1 · fix_connectors_top2 · fix_odds_cap_new

## 동결 정책 4일 비교 (baseline vs t2_strong_cycle vs t2_strong_all)

| 날짜 | baseline | t2_strong_cycle(경륜만) | t2_strong_all(전종목) |
|---|---|---|---|
| 7/21 | 17(33%)/216% | 17(33%)/216% (+0) | **23(44%)/276% (+6)** |
| 7/22 | 12(21%)/71% | 18(32%)/79% (+6) | **21(38%)/79% (+9)** |
| 7/23 | 6(17%)/160% | 10(29%)/179% (+4) | **11(31%)/152% (+5)** |
| 7/24 | 11(39%)/173% | 13(46%)/192% (+2) | 12(43%)/192% (+1) |

- **t2_strong_all(경마 포함)이 hits 최대**(+5~9). 단 ROI 개선은 미미(되살린 게 저배당 위주).
- **t2_strong_cycle(경륜만)은 7/21에 +0** — 경마 동결이 큰 날은 cycle-only가 놓침.
- t2_strong_all == 기존 t2_strong (동일 로직·명시 비교용).

## 7/22 전 정책 (judged 56, baseline 대비 순증)

| 정책 | hits | 적중률 | ROI | 순증 |
|---|---|---|---|---|
| baseline | 12 | 21% | 71% | — |
| t5_freeze | 26 | 46% | 4007%⚠ | +14 (이상치) |
| **t2_strong/all** | 21 | 38% | 79% | +9 |
| t2_freeze | 20 | 36% | 68% | +8 |
| t2_strong_cycle | 18 | 32% | 79% | +6 |
| t1_freeze | 17 | 30% | 47% | +5 |
| fix_main_keep | 16 | 29% | 71% | +4 |
| **fix_odds_cap_new** | 15 | 27% | **105%** | +3 |
| fix_special_incl | 14 | 25% | **150%** | +2 |
| fix_conf_pair | 14 | 25% | 135% | +2 |
| fix_lowodds_exempt | 14 | 25% | 92% | +2 |
| fix_connectors(_top1/2) | 13 | 23% | 131% | +1 |

## 라이브 반영 상태 (app.py)

| 개선 | 커밋 | 라이브 |
|---|---|---|
| fix_lowodds_exempt (정액컷 EV면제) | fc24ecab | ✅ |
| fix_connectors_top1 (축-연결 EV면제) | 2e25f62f | ✅ |
| MAIN_ODDS_MAX 20~35 | c35bdb5b | ✅ |
| t2_strong_cycle (경륜·13시~) | 44e36b3d | ✅ |
| 와카야마 joCode·오분류 | 33614316 | ✅ |
| **t2_freeze·t2_strong·t2_strong_all·fix_odds_cap(30~70)** | — | ❌ 측정만 |

## 관찰

- **적중률(hits) 우선** → t2_strong_all(전종목 동결). +5~9. ROI 개선은 적음.
- **ROI(수익) 우선** → fix_special_incl(150%)·fix_conf_pair(135%)·fix_connectors(131%). hits +1~2.
- **균형** → fix_odds_cap_new(+3·ROI 105%·unpaid 최소). 오늘 20~35로 축소 반영.
- 날짜 편차 큼(7/22 caught_then_lost 27건 → 동결 강세 / 7/21 pure_upset 17건 → 동결 약세). 단일일 최적 ≠ 상시 최적.
