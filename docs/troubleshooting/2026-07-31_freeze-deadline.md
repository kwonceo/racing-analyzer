# 마감 시점 동결 — 무방비 7필드

| | |
|---|---|
| **발생** | 2026-07-31 (오염은 그 이전부터 누적) |
| **상태** | ✅ **해결** (신규) · 🟡 과거 소급은 부분(아래 5번) |
| **영향 범위** | 판정 핵심 경로 — `keyHorses`·등급·신호 |

## 1. 증상
나고야 9R 이 `readonly=True` 인데도 **19:41 에 값이 바뀌었다.**

```
keyHorses     [12, 1, 2]  →  [13, 4, 2]
strongSignals          3  →  0
displayedCombos            변화 없음 (18:44:53 그대로)
```

마감 후 재분석이 확정본을 덮어쓰고 있었다.

## 2. 원인
`app.py` 의 readonly 보호 블록이 **5개 필드만** 지켰다.

```python
if _doc.get("readonly") or log.get("readonly"):
    log["corePicks"]              = ...   # ✅
    log["final_recommendation"]   = ...   # ✅
    log["recommendation_history"] = ...   # ✅
    log["signals_detected"]       = ...   # ✅
    log["odds_timeline"]          = ...   # ✅
```

그런데 `log` 를 만드는 쪽에는 **보호 목록에 없는 필드**가 있었다.

```python
"strong_signals": an.get("strongSignals"),   # 🔴 무방비
"summary":        an.get("summary"),         # 🔴 무방비
"keyHorses":      an.get("keyHorses"),       # 🔴 무방비
"horses" / "elimination" / "compression_pattern" / "third_place_hunt"  # 🔴
```

🔴 **`displayedCombos` 만 불변이었던 이유는 그것만 `corePicks` 안에 있어서였다.**
설계된 보호가 아니라 **우연**이었다.

### 두 번째 원인 — 잠그는 시점
`readonly` 를 **"마감 후 첫 저장"** 에 걸었다. 그 저장의 `an` 은 이미 마감 후
재계산값이다. ⇒ **틀린 값을 잠그고 있었다.**

## 3. 조치
### ⓐ `_FREEZE_FIELDS` 신설 (기존 5개 무변경 · **추가만**)
```python
_FREEZE_FIELDS = ["keyHorses", "summary", "strong_signals", "horses",
                  "elimination", "compression_pattern", "third_place_hunt"]
```

### ⓑ 3단 폴백으로 **확정본에서 복원** (`_frozen_capture`)
| 순위 | 소스 |
|---|---|
| ① | `recommendation_history[closed=True]` — 카톡 발송본과 같은 시각 |
| ② | `timeline_snapshot` T-5 → T-7 |
| ③ | 마지막 **마감 전** 추천 행 |

### ⓒ 🔴 복원 실패 시 **잠그지 않는다**
종전 `_GRADE_LOCK` 은 *"복원 실패 시 현재 계산값으로라도 고정"* 이었고
**그것이 곧 마감 후 값을 굳히는 경로**였다. 이제 `lockFailed` 표기만 남긴다.
> 틀린 값을 잠그는 것보다 안 잠긴 채로 표시하는 게 낫다.

### ⓓ `readonlyAt` 기록 (종전 `None` — 언제 잠겼는지 추적 불가였다)

## 4. 근거 (⚠ 분모 명시)
**분모 = `readonly=True` 이고 마감 시점 확정값이 남아 있는 파일 529개**

| 지표 | 값 |
|---|---|
| 유실(현재값 ≠ 확정값) | **492 / 529 (93.0%)** |
| 3단 폴백 복원 성공 | **492 / 492 (100.0%)** |
| 출처 ① `closed_row` | 235 (47.8%) |
| 출처 ③ `pre_close_row` | 257 (52.2%) |
| 출처 ② `timeline_snapshot` | **0 (0.0%)** — `keyHorses` 가 담기지 않는다 |

행위 테스트 `tests/run_freeze_behavior.py` **4/4 통과**.

## 5. 상태
✅ **신규 경주는 해결.**

🟡 **과거 소급은 의도적으로 부분이다.**
- `closed_row` 235건 → top-level `keyHorses` 복구 **완료**
- `pre_close_row` 257건 → **본문을 덮어쓰지 않았다.** 마감 시점 값이 아니므로
  `frozen` 블록에만 기록하고 `srcTrust` 로 신뢰도를 표기했다.
  (실측: 그 중 95.6%는 T-5분 이내, 중앙 T-1.4분. 🔴 T-10분 초과 4건은 별도 표기)

## 6. 재발 방지
- 🔴 **원칙 12 신설**: *"회귀 테스트는 고정 데이터를 읽는 것이 아니라 실제 함수를 호출해야 한다."*
  이 사건을 잡으려고 만든 `run_freeze_regression.py` 가 **fixture 만 읽어**
  코드를 고쳐도 영원히 초록이 되지 않는 구조였다. 그런데도 "고치면 초록"이라 **두 번 단언**했다.
  ⇒ 역할 분리: 앵커는 `EXPECTED_FAIL`, 동작 검증은 `run_freeze_behavior.py`(차단 등급).
- 체크리스트 **D6**(마감 확정행 동결 성공률, 목표 ≥95%) 신설.
- 🔴 **보호 목록은 "무엇을 지키나"가 아니라 "무엇이 빠졌나"로 점검한다.**
  `displayedCombos` 가 우연히 살아남은 것을 보호라고 믿었던 것이 이 사고의 본질이다.
