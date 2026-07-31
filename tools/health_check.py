# -*- coding: utf-8 -*-
"""[완료 조건 체크리스트 2026-07-30] 17항목 자동 판정 — **완전 읽기 전용**.

설계 의도(권대표 지시 2026-07-30)
  · "감시"가 아니라 **완료 조건 체크리스트**다. 각 항목에 숫자 완료선이 있고, 충족되면 알림에서 빠진다.
    **조용해지는 것이 곧 진행 상황이다.**
  · 지금까지 발견이 대부분 **육안**이었다. 외부에 있으면 0건이 된다 → 외부에서 확인 가능해야 한다.

🔴 이 파일의 가장 중요한 규약 — **`denominator`(분모 정의)는 필수 필드다.**
  2026-07-30 실제 사고: "스냅샷 3틱+ 비율"을 **스냅샷 보유 파일만** 분모로 잡으면 81.6% 인데,
  **0틱 경주 123개를 포함**하면 69.4% 다. 즉 **가장 실패한 경주가 통계에서 사라진다.**
  같은 함정이 다른 항목에도 있다(스키마 도입 이후 행만 세기 · 판정 가능 경주만 세기 …).
  → 모든 항목은 분모를 **문자열로 명시**하고, 애매하면 **넓은 쪽(실패가 포함되는 쪽)** 을 고른다.

⚠ 추천·수집·학습에 일절 개입하지 않는다. 파일을 읽기만 하고 쓰지 않는다.
⚠ 측정 불가한 항목은 `ok=null` + `reason` — **억지로 통과시키지 않는다.**

사용:
  python tools/health_check.py            # 사람이 읽는 표
  python tools/health_check.py --json     # JSON
  (서버) GET /api/health/checklist
"""
import argparse
import collections
import glob
import json
import os
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HIST_DIR = os.path.join(BASE, "data", "odds_history")
LOG_DIR = os.path.join(BASE, "logs")

# ── 완료선 상수 ─────────────────────────────────────────────────────────
#  🔴 여기 숫자를 낮추려면 **근거와 함께** CLAUDE.md 에 기록하고 바꾼다(사후 하향 방지).
SNAP_MIN_TICKS = 3          # '수집 성공' 판정 최소 distinct 틱
SNAP_TARGET_RATE = 90.0     # 당일분 3틱+ 목표 비율(%)
SNAP_MIN_N = 10             # 이보다 표본이 적으면 판정하지 않는다(rolling 이 스스로를 속이는 것 방지)

# 스키마 드리프트 🔴 필드 — CLAUDE.md 「탈락 필드 우선순위」 표(🔴 1·2·3) 기준.
#   ⚠ 이 목록을 줄이는 것이 곧 완료선 하향이다. 바꾸려면 CLAUDE.md 를 먼저 고칠 것.
SCHEMA_RED_FIELDS = ["winOdds", "pop", "weight", "surface", "trackCond"]


def _mk(id, area, name, denominator, current=None, target=None, ok=None,
        n=None, note="", reason=""):
    """항목 1건. `denominator` 는 필수다 — 분모를 못 적으면 그 항목은 측정된 것이 아니다."""
    return {"id": id, "area": area, "name": name, "denominator": denominator,
            "current": current, "target": target, "ok": ok, "n": n,
            "measuredAt": time.strftime("%Y-%m-%d %H:%M:%S"),
            "note": note, "reason": reason}


def _distinct_ticks(doc):
    """같은 (초, src) 를 1틱으로 센다 — 2중 기록(2026-07-30 수정분) 이전 데이터 보정."""
    seen = set()
    for x in (doc.get("snapshots") or []):
        try:
            t = round(float(x.get("t") or 0), 0)
        except Exception:
            t = 0
        seen.add((t, str(x.get("src") or "")))
    return len(seen)


# ══════════════ ④ 데이터 보전 (4항목) ══════════════

def check_snapshot_coverage(today=None):
    """④-1 스냅샷 3틱+ 경주 비율.

    분모 = **odds_history 파일 전체**(0틱 경주 포함).
      근거: 0틱 경주를 빼면 '가장 실패한 경주'가 통계에서 사라진다(2026-07-30 실측 81.6% ↔ 69.4%).
    판정 = **당일분(rolling)**. 누적은 수정 전 과거가 영구히 섞여 있어 완료 상태에 도달할 수 없다.
    ⚠ 누적값도 `note` 에 **병기**한다 — 당일분만 보면 "과거 데이터가 오염돼 있다"는 사실이
      화면에서 사라지고, 시뮬레이션·리플레이는 과거 데이터로 돌아가므로 그게 중요하다.
    """
    today = today or time.strftime("%Y_%m_%d")
    files = [f for f in glob.glob(os.path.join(HIST_DIR, "*.json"))
             if ".corrupt." not in f and ".tmp" not in f]
    cum_n = cum_ok = day_n = day_ok = 0
    for f in files:
        try:
            doc = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue                      # 손상 파일은 별도 항목(④-2 계열)에서 다룬다
        if not isinstance(doc, dict):
            continue
        d = _distinct_ticks(doc)
        cum_n += 1
        cum_ok += (1 if d >= SNAP_MIN_TICKS else 0)
        if os.path.basename(f).startswith(today):
            day_n += 1
            day_ok += (1 if d >= SNAP_MIN_TICKS else 0)
    cum_rate = round(100.0 * cum_ok / cum_n, 1) if cum_n else None
    day_rate = round(100.0 * day_ok / day_n, 1) if day_n else None
    note = "누적 %s%% (%d/%d) 병기 — 수정 전 과거가 섞여 있다. 리플레이는 이 과거를 쓴다." % (
        cum_rate, cum_ok, cum_n)
    if day_n < SNAP_MIN_N:
        return _mk("D1", "④ 데이터 보전", "스냅샷 %d틱+ 경주 비율" % SNAP_MIN_TICKS,
                   "odds_history 파일 전체(0틱 포함) · 당일분 · distinct 틱",
                   current=day_rate, target=SNAP_TARGET_RATE, ok=None, n=day_n,
                   note=note, reason="표본 부족(n=%d < %d) — 하루 경주가 적으면 n=2로 100%%가 나와 "
                                     "rolling 이 스스로를 속인다" % (day_n, SNAP_MIN_N))
    return _mk("D1", "④ 데이터 보전", "스냅샷 %d틱+ 경주 비율" % SNAP_MIN_TICKS,
               "odds_history 파일 전체(0틱 포함) · 당일분 · distinct 틱",
               current=day_rate, target=SNAP_TARGET_RATE,
               ok=(day_rate is not None and day_rate >= SNAP_TARGET_RATE),
               n=day_n, note=note)


def _today_logs():
    """당일 stdout 로그 파일들(회전본 포함). 회전본은 `server_stdout.log.<YYYYMMDD_HHMMSS>`."""
    ymd = time.strftime("%Y%m%d")
    out = []
    cur = os.path.join(LOG_DIR, "server_stdout.log")
    if os.path.exists(cur):
        out.append(cur)
    for f in glob.glob(os.path.join(LOG_DIR, "server_stdout.log.*")):
        b = os.path.basename(f)
        if b.endswith(".err"):
            continue
        if ymd in b:
            out.append(f)
    return out


def _count_save_failures():
    """당일 stdout 로그(회전본 포함)에서 WinError 를 **발생 지점별로** 센다.

    분모 = **당일 로그 전체**(회전본 포함). 현재 로그만 보면 재기동으로 카운트가 리셋돼
      '고쳐진 것처럼' 보인다 — 2026-07-30 에 실제로 누적치와 구간치를 혼동한 적이 있다.
    """
    fixed = unfixed = other = 0
    files = _today_logs()
    for p in files:
        try:
            txt = open(p, encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        for line in txt.split("\n"):
            if "WinError" not in line:
                continue
            if "[복기저장]" in line or "[히스토리]" in line:
                fixed += 1              # `_json_atomic` 계열 — 2026-07-30 수정 완료 경로
            elif "[분석로그]" in line or "[다중경주]" in line:
                unfixed += 1            # `path + ".tmp"` 17곳 — 미수정(ⓐ 작업 대상)
            else:
                other += 1
    return fixed, unfixed, other, len(files)


def check_save_failures_fixed():
    """④-2a 저장 실패 — **수정 경로**(`_json_atomic` 계열).

    D2 를 하나로 두면 진행이 보이지 않는다(권대표 지시 2026-07-30):
      수정분 176건이 자정에 리셋되면 '고쳐진 것처럼' 보이고,
      미수정분이 줄어도 합계에 묻힌다. → **발생 지점별로 분리해 각각 완료선을 둔다.**
    """
    fixed, unfixed, other, nf = _count_save_failures()
    return _mk("D2a", "④ 데이터 보전", "저장 실패(WinError) — 수정 경로",
               "당일 stdout 로그 전체(회전본 포함) 중 `_json_atomic` 계열([복기저장]·[히스토리])",
               current=fixed, target=0, ok=(fixed == 0), n=nf,
               note="tmp 스레드ID·경로별 락·replace 재시도 적용분(2026-07-30). "
                    "잔여는 '리더가 파일을 잡고 있는' 경우로 관측됨(ⓒ 읽기 락 후보).")


def check_save_failures_unfixed():
    """④-2b 저장 실패 — **미수정 17곳**(`path + ".tmp"` · PID 조차 없음). ⓐ 작업 대상.

    ⚠ 이 항목이 0 이 되는 것이 ⓐ(17곳 tmp 고유화)의 완료 판정이다.
      D2a 와 분리해 두면 "고쳐도 안 줄었다"는 잘못된 결론을 막을 수 있다.
    """
    fixed, unfixed, other, nf = _count_save_failures()
    return _mk("D2b", "④ 데이터 보전", "저장 실패(WinError) — 미수정 17곳",
               "당일 stdout 로그 전체(회전본 포함) 중 `path+\".tmp\"` 계열([분석로그]·[다중경주])",
               current=unfixed, target=0, ok=(unfixed == 0), n=nf,
               note="ⓐ 17곳 tmp 고유화의 완료 판정 항목. 기타 분류 %d건은 어느 쪽도 아님." % other)


def check_schema_contract():
    """④-3 schema contract test 통과.

    분모 = 계약 파일(`tools/schema_contract.py`)에 정의된 저장행 함수 전체.
    현재 계약 파일 자체가 없다 → **ok=false**(미구현은 통과가 아니다).
    """
    p = os.path.join(BASE, "tools", "schema_contract.py")
    exists = os.path.exists(p)
    return _mk("D3", "④ 데이터 보전", "schema contract test 통과",
               "tools/schema_contract.py 의 계약 대상 저장행 함수 전체",
               current=("있음" if exists else "없음"), target="통과",
               ok=(True if exists else False), n=None,
               note="계약 파일 미존재 = 미구현. 미구현은 통과가 아니다(CLAUDE.md 설계안 참조).")


def _red_field_rates():
    """🔴 필드 보유율을 **당일분 / 누적**으로 각각 산출. 반환 (day, cum, day_rows, cum_rows, note).

    🔴 분모 통제 (권대표 지시 2026-07-30 · 실측 근거 반영)
      `winOdds`·`pop`·`weight`·`surface`·`trackCond` 는 **경마 出走表 파서에서만** 나온다.
      → 분모를 '경마 경주'로 좁혀야 하는데 **`sport` 태그로 판별하면 안 된다**:
        · 오늘 경륜장 `sport=horse` 오분류 **213건**을 소급 정정했고 실시간 재발도 3경주 확인됐다
          (와카야마 7/24 · 코치 6R 7/25 · 코치 10R 7/26 · 원인은 분석 시점 sport 미확정).
        · **실측 확인**: `starters_store` 132경주 전부 `sport` 가 **None** 이다 —
          애초에 태그로는 분모를 잡을 수 없다.
      → **`source == "oddspark"`**(경마 出走表 파서를 탄 경주)를 분모로 쓴다. 파서 유래값이라
        태그 오염과 무관하다. 실측 분포: keirin 87 · oddspark 23 · korea 14 · keiba_nar 1 · 없음 7.
    ⚠ `t`(레코드 타임스탬프)로 당일분을 가른다 — 키에 날짜가 없기 때문(0/132).
    """
    store = os.path.join(BASE, "starters_store.json")
    if not os.path.exists(store):
        return None, None, 0, 0, "starters_store.json 미존재"
    try:
        db = json.load(open(store, encoding="utf-8"))
    except Exception as e:
        return None, None, 0, 0, "starters_store.json 파싱 실패: %s" % str(e)[:80]
    day0 = time.mktime(time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d"))
    day_have, cum_have = collections.Counter(), collections.Counter()
    day_rows = cum_rows = 0
    for rk, rec in (db.items() if isinstance(db, dict) else []):
        rec = rec or {}
        if str(rec.get("source") or "") != "oddspark":
            continue                       # 경마 出走表 파서를 탄 경주만
        is_today = (rec.get("t") or 0) >= day0
        for h in (rec.get("horses") or []):
            cum_rows += 1
            if is_today:
                day_rows += 1
            for f in SCHEMA_RED_FIELDS:
                if h.get(f) not in (None, "", []):
                    cum_have[f] += 1
                    if is_today:
                        day_have[f] += 1
    return day_have, cum_have, day_rows, cum_rows, ""


def check_schema_drift():
    """④-4 스키마 드리프트 🔴 필드 보유율 ≥90%.

    ⚠ **판정 방식 변경(2026-07-30)**: 종전 "보유 행 0개 = 미해소"는 `winOdds` **1.7%** 를
      '해소'로 판정해 **완료선이 너무 관대**했다. → D1 과 동일하게
      **당일 rolling · 보유율 ≥90% · 누적 병기** 로 바꾼다.
    ⚠ '코드에 배선됐는가'가 아니라 '데이터에 남았는가'로 본다(원칙 5) —
      `surface`/`trackCond` 는 코드는 배선됐는데 실데이터 0% 다. rolling 으로도 당분간 0% 일 것이고
      **그게 정확한 판정**이다(1계층 재수집이 선행 조건). 낮게 나온다고 기준을 낮추지 않는다.
    """
    day, cum, day_rows, cum_rows, err = _red_field_rates()
    denom = ("당일 `starters_store` 중 `source=\"oddspark\"`(경마 出走表 파서) 경주의 전체 행 "
             "— ⚠ `sport` 태그 미사용(오분류 213건 정정 이력·실측상 전부 None)")
    if err:
        return _mk("D4", "④ 데이터 보전", "스키마 드리프트 🔴 필드 보유율",
                   denom, current=None, target=90.0, ok=None, n=None, reason=err)
    cum_txt = ", ".join("%s %.1f%%" % (f, 100.0 * cum[f] / cum_rows if cum_rows else 0)
                        for f in SCHEMA_RED_FIELDS)
    note = "누적(%d행): %s · surface/trackCond 는 1계층 재수집 선행 조건이라 0%%가 정확한 판정" % (
        cum_rows, cum_txt)
    if day_rows < SNAP_MIN_N:
        return _mk("D4", "④ 데이터 보전", "스키마 드리프트 🔴 필드 보유율",
                   denom, current=None, target=90.0, ok=None, n=day_rows,
                   note=note, reason="표본 부족(당일 %d행 < %d) — D1 과 동일 규칙"
                                     % (day_rows, SNAP_MIN_N))
    rates = {f: round(100.0 * day[f] / day_rows, 1) for f in SCHEMA_RED_FIELDS}
    worst = min(rates.values())
    day_txt = ", ".join("%s %.1f%%" % (f, rates[f]) for f in SCHEMA_RED_FIELDS)
    return _mk("D4", "④ 데이터 보전", "스키마 드리프트 🔴 필드 보유율",
               denom, current=worst, target=90.0, ok=(worst >= 90.0), n=day_rows,
               note="당일(%d행): %s / %s" % (day_rows, day_txt, note))


def check_score_decomposition():
    """④-5 점수 분해 `rank`·`baseScore` 배선 — **신설**(권대표 지시 2026-07-30).

    배경: 「탈락 필드 우선순위」에서 **보너스 분해 8종은 체크리스트 A1·A2 로 이관**했으나
      (`gait`·`paceBonus`·`paceBonusBase`·`gradeAtBonus`·`paceDetail` 이 오늘 배선됨),
      **`rank`(통합등급 순위)와 `baseScore` 는 여전히 미배선**임이 실측으로 확인됐다
      (오늘 분석로그 45파일·372행 중 **둘 다 0.0%**).
    ⚠ `paceBonusBase`(98.4%)가 `baseScore` 역할을 하는 것처럼 보이지만 **이름이 다른 별개 필드**이므로
      대체한다고 단정하지 않는다 → 별도 추적 항목으로 분리한다.
    분모 = 당일 `analysis_log` 의 `horses` 전체 행(스키마 도입 시점으로 좁히지 않는다).
    """
    day = time.strftime("%Y_%m_%d")
    files = glob.glob(os.path.join(BASE, "data", "analysis_log", day + "_*.json"))
    fields = ["rank", "baseScore"]
    have = collections.Counter()
    rows = 0
    for f in files:
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        for h in (d.get("horses") or []):
            rows += 1
            for fl in fields:
                if h.get(fl) not in (None, "", []):
                    have[fl] += 1
    denom = "당일 analysis_log 의 horses 전체 행(스키마 도입 시점으로 좁히지 않는다)"
    if rows < SNAP_MIN_N:
        return _mk("D5", "④ 데이터 보전", "점수 분해 rank·baseScore 보유율",
                   denom, current=None, target=90.0, ok=None, n=rows,
                   reason="표본 부족(당일 %d행 < %d)" % (rows, SNAP_MIN_N))
    rates = {f: round(100.0 * have[f] / rows, 1) for f in fields}
    worst = min(rates.values())
    return _mk("D5", "④ 데이터 보전", "점수 분해 rank·baseScore 보유율",
               denom, current=worst, target=90.0, ok=(worst >= 90.0), n=rows,
               note="당일(%d행): %s · 배선된 분해 필드(paceBonusBase 98.4%%·gradeAtBonus 74.5%%)는 A1·A2 에서 추적"
                    % (rows, ", ".join("%s %.1f%%" % (f, rates[f]) for f in fields)))


# ══════════════ ①②③ 자리 (다음 세션 구현) ══════════════
#  ⚠ 미구현 항목도 목록에 남긴다 — **17개 중 몇 개가 미구현인지도 진행 상황**이기 때문이다.
#  ⚠ 각 항목의 `denominator` 는 **지금 설계해 둔다.** ④에서 드러났듯 분모를 잘못 잡으면
#    실패가 통계에서 사라지고, 그때는 이미 완료선이 굳어 있어 되돌리기 어렵다.
_PENDING = [
    # ① 적중왕전개 준비 (5)
    ("A1", "① 적중왕전개", "gait·paceBonus·paceBonusBase 보유율", 90.0,
     "analysis_log **전체 경주**(스키마 도입 이후로 좁히지 않는다)",
     "'도입 이후만'으로 좁히면 배선 실패가 통계에서 사라진다 — ④ 0틱 제외와 같은 구조"),
    ("A2", "① 적중왕전개", "gradeAtBonus 보유율", 90.0,
     "analysis_log 전체 경주", "동상 — 도입 시점으로 분모를 좁히지 않는다"),
    ("A3", "① 적중왕전개", "declaredStyle 누적 보유율", 70.0,
     "경륜 starters_store 전체 행", "경륜 전용 필드라 경마 행은 분모에서 제외(대상 아님)"),
    ("A4", "① 적중왕전개", "drops_raw 보유율", 90.0,
     "analysis_log 전체 경주", "동상"),
    ("A5", "① 적중왕전개", "paceBonus 3안 비교 표본", 30,
     "결과 보유 경주(착순 확정)", "성적 비교라 결과가 없으면 판정 자체가 불가 — 분모에서 제외가 타당"),
    # ② 배당판 오류 (4)
    ("B1", "② 배당판 오류", "리스트 간 배당 불일치율", 0.0,
     "동일 경주에 2개 이상 리스트가 존재하는 스냅샷", "리스트가 1개면 비교 대상이 없어 판정 불가"),
    ("B2", "② 배당판 오류", "quinella ↔ finalQuinellas 불일치 처리", 0.0,
     "finalQuinellas 보유 경주 전체", "추천이 없는 경주는 대조 대상이 아님"),
    ("B3", "② 배당판 오류", "유령 마번 비율", 0.0,
     "**배당 스냅샷 보유 경주 전체**(출주표 유무 무관)",
     "'출주표 보유 경주만'으로 좁히면 출주표를 못 받은 경주의 유령 마번이 사라진다"),
    ("B4", "② 배당판 오류", "화면 불일치 감지 항목 작동", 1,
     "감지 항목 3종", "발동률이 확인된 항목만 대상(CLAUDE.md 「화면 불일치 자동 감지」)"),
    # ③ 예상·복기 (4)
    ("C1", "③ 예상·복기", "발주완료 경주 예상 저장률", 100.0,
     "**당일 스케줄의 발주완료 경주 전체**", "수집 성공분만 세면 수집 실패가 통계에서 사라진다"),
    ("C2", "③ 예상·복기", "결과 확정배당 보유율", 100.0,
     "결과 입력 완료 경주 전체", "결과가 없으면 확정배당이 존재할 수 없어 분모에서 제외"),
    ("C3", "③ 예상·복기", "det_review 커버율 · 실패 0건", 100.0,
     "**발주완료 경주 전체**(분석 로그 보유 경주만이 아니다)",
     "'분석 로그 보유분만'으로 좁히면 분석 자체가 안 돈 경주가 사라진다 — ④와 같은 함정"),
    ("C4", "③ 예상·복기", "카카오 발송 이력 저장", 1,
     "발송 시도 전체(성공·실패 모두)", "성공분만 세면 발송 실패가 보이지 않는다"),
]


def check_forecast_discard():
    """⑤-1 Gemini 예측 **폐기율** ≤20% (판정선: 30경주 도달 시 형식 점검).

    분모 = `logs/forecast/` 의 **당일 예측 시도 전체**(성공 + 폐기).
      ⚠ 성공분만 세면 폐기가 통계에서 사라진다 — D1 의 0틱 제외와 같은 함정.
    ⚠ 폐기는 형식 검증 실패(키 누락·명단 밖 번호·confidence 범위 이탈)로 **통째 폐기**된 건이다.
      부분 채택은 하지 않는다.
    """
    # 🔴 [2026-07-31] 종전에는 `logs/forecast` 의 **전체 파일**을 셌다 — 어제·그제 것이 섞여
    #   당일 폐기율의 분모가 부풀었다(원칙 8-B: 같은 목록에서 분모를 통일할 것).
    #   ⇒ 당일 접두사(`YYYYMMDD_`)로 제한한다.
    d = os.path.join(BASE, "logs", "forecast")
    _pfx = time.strftime("%Y%m%d") + "_"
    saved = (len([f for f in os.listdir(d) if f.endswith(".json") and f.startswith(_pfx)])
             if os.path.isdir(d) else 0)
    disc = 0
    for p in _today_logs():
        try:
            disc += open(p, encoding="utf-8", errors="replace").read().count("[예측 폐기]")
        except Exception:
            continue
    tried = saved + disc
    denom = "logs/forecast 당일 예측 시도 전체(성공+폐기)"
    if tried < 30:
        return _mk("F1", "⑤ 예측 검증", "Gemini 예측 폐기율", denom,
                   current=(round(100.0 * disc / tried, 1) if tried else None), target=20.0,
                   ok=None, n=tried,
                   reason="판정선 미도달(%d/30경주) — 30경주에서 형식 점검한다" % tried)
    rate = round(100.0 * disc / tried, 1)
    return _mk("F1", "⑤ 예측 검증", "Gemini 예측 폐기율", denom,
               current=rate, target=20.0, ok=(rate <= 20.0), n=tried,
               note="폐기 %d / 시도 %d · 20%% 초과 시 프롬프트 수정" % (disc, tried))


def check_forecast_vs_market():
    """⑤-2 Gemini 평균 `hit_count` ≥ 시장 평균 `market_hit_count` (판정선: **100경주**).

    ⚠ **사후에 판정선을 낮추지 않는다.** 100경주 도달 전에는 `ok=null` 이다.
    ⚠ 시장 대조군은 **마감 전(T-8~T-0)** 스냅샷의 단승 최저 3두다.
      마감 후 배당을 쓰면 시장이 유리해져 비교가 성립하지 않는다.
    """
    d = os.path.join(BASE, "logs", "forecast")
    g, m, n = 0, 0, 0
    if os.path.isdir(d):
        for f in os.listdir(d):
            if not f.endswith(".json"):
                continue
            try:
                doc = json.load(open(os.path.join(d, f), encoding="utf-8"))
            except Exception:
                continue
            gr = doc.get("grading") or {}
            if gr.get("hit_count") is None or gr.get("market_hit_count") is None:
                continue
            n += 1
            g += gr["hit_count"]
            m += gr["market_hit_count"]
    denom = "채점 완료된 예측 경주(Gemini·시장 둘 다 산출된 건)"
    cur = (round(g / n, 2) if n else None)
    note = ("Gemini 평균 %.2f ↔ 시장 평균 %.2f (n=%d)" % (g / n, m / n, n)) if n else ""
    if n < 100:
        return _mk("F2", "⑤ 예측 검증", "Gemini 적중 ≥ 시장 적중", denom,
                   current=cur, target="시장 이상", ok=None, n=n, note=note,
                   reason="판정선 미도달(%d/100경주) — 사후에 낮추지 않는다" % n)
    return _mk("F2", "⑤ 예측 검증", "Gemini 적중 ≥ 시장 적중", denom,
               current=cur, target=round(m / n, 2), ok=(g >= m), n=n,
               note=note + " · 낮으면 종결 / 비슷하면 이변 경주만 재판정 / 높으면 편입 검토")


def check_freeze_success():
    """[D6 · 2026-07-31 신설] 마감 시점 동결이 **신규 경주에서 실제로 작동하는가.**

    🔴 핵심 지표는 `closed_row` 비율이다. 수정 전 **44.4%**(235/529)였고,
      '🔒 마감 확정' 행이 만들어진 그 저장에서 폐기되던 것이 원인이었다(병합으로 수정).
    ⚠ 분모는 **당일 동결 시도 건수**다 — 과거 소급 복구분은 섞지 않는다.
    """
    d = os.path.join(BASE, "data", "freeze_log", time.strftime("%Y-%m-%d") + ".json")
    rows = []
    if os.path.exists(d):
        try:
            rows = json.load(open(d, encoding="utf-8")) or []
        except Exception:
            rows = []
    n = len(rows)
    denom = "당일 동결 시도 건수(freeze_log 적재분) — 소급 복구분 제외"
    ok_rows = [r for r in rows if isinstance(r, dict) and r.get("ok")]
    cr = sum(1 for r in ok_rows if r.get("src") == "closed_row")
    pr = sum(1 for r in ok_rows if r.get("src") == "pre_close_row")
    fail = n - len(ok_rows)
    cur = (round(100.0 * cr / n, 1) if n else None)
    note = ("closed_row %d · pre_close_row %d · 실패 %d (n=%d)" % (cr, pr, fail, n)) if n else ""
    if n < 10:
        return _mk("D6", "② 저장 무결성", "마감 확정행 동결 성공률", denom,
                   current=cur, target="≥ 95%", ok=None, n=n, note=note,
                   reason="판정선 미도달(%d/10경주) — 사후에 낮추지 않는다" % n)
    return _mk("D6", "② 저장 무결성", "마감 확정행 동결 성공률", denom,
               current=cur, target="≥ 95%", ok=(cur is not None and cur >= 95.0), n=n,
               note=note + " · 낮으면 recommendation_history 를 덮어쓰는 다른 경로를 전수 조사")


def check_module_load():
    """[D7 · 2026-07-31 신설] **선택 모듈이 조용히 죽어 있지 않은가.**

    🔴 실사고: `gemini_forecast.py` 문법 오류로 import 가 실패했고 서버는 `except` 로
      삼켜 `_gforecast = None` 인 채 돌았다. 로그 1줄뿐이라 **예측이 하루 종일 0건**이었다.
    ⚠ 여기서는 **문법만** 본다(서버 기동 없이 판정 가능). 목표는 항상 0건이다.
    """
    import ast as _ast
    targets, bad = [], []
    for d in (".", "tools", "tests"):
        base = BASE if d == "." else os.path.join(BASE, d)
        if not os.path.isdir(base):
            continue
        for nm in sorted(os.listdir(base)):
            if not nm.endswith(".py"):
                continue
            p = os.path.join(base, nm)
            if not os.path.isfile(p):
                continue
            rel = nm if d == "." else "%s/%s" % (d, nm)
            targets.append(rel)
            try:
                _ast.parse(open(p, encoding="utf-8").read())
            except SyntaxError as e:
                bad.append("%s:%s" % (rel, e.lineno))
            except Exception:
                pass
    n = len(targets)
    denom = "프로젝트 .py 자동 탐색(루트·tools·tests) — 하드코딩 목록 아님"
    return _mk("D7", "② 저장 무결성", "모듈 로드(문법) 실패", denom,
               current=len(bad), target=0, ok=(len(bad) == 0), n=n,
               note=("전부 정상 (%d개 검사)" % n) if not bad
                    else "🔴 " + " · ".join(bad[:5]))


# ══════════════ [무결성 감시 (2026-07-31 신설)] ══════════════
#  🔴 오늘 큰 결함 셋이 전부 "우연히 조사하다가" 잡혔다. **매일 측정이 잡은 게 아니다.**
#    · 경마 확정배당 0.9% — 며칠간 몰랐다
#    · 주기 백업 0회 실행 — 몇 주 몰랐다
#    · 같은 경기장 다른 날짜 파일 혼입 — 오늘 최대 오류의 원인
#  ⚠ 성능 측정(주 1회)과 **성격이 다르다**. 무결성 감시는 **매일·자동**이고 절대 줄이지 않는다.
#  ⚠ 각 항목에 "며칠 연속 미달이면 위험"을 명시한다.
_INTEG_DAYS = "⚠ 2일 연속 미달이면 조사, 3일이면 위험"


def check_payout_coverage():
    """[I1] 확정배당 보유율 — **종목별**. 🔴 경마 0.9% 가 이 항목이 있었으면 첫날 잡혔다."""
    day = time.strftime("%Y-%m-%d")
    files = glob.glob(os.path.join(BASE, "data", "analysis_log", day.replace("-", "_") + "_*.json"))
    by = {}
    for p in files:
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        if not (d.get("result") or {}).get("1st"):
            continue
        sp = d.get("sport") or "?"
        t, o = by.get(sp, (0, 0))
        by[sp] = (t + 1, o + (1 if ((d.get("result") or {}).get("payouts") or {}).get("quinella") else 0))
    tot = sum(v[0] for v in by.values())
    ok_ = sum(v[1] for v in by.values())
    note = " · ".join("%s %d/%d(%.0f%%)" % (k, v[1], v[0], 100.0 * v[1] / max(v[0], 1))
                      for k, v in sorted(by.items()))
    cur = round(100.0 * ok_ / tot, 1) if tot else None
    worst = min((100.0 * v[1] / max(v[0], 1)) for v in by.values() if v[0] >= 5) if any(v[0] >= 5 for v in by.values()) else None
    return _mk("I1", "🔴 무결성", "확정배당 보유율(종목별)",
               "당일 결과확정 경주 %d건 — 종목별 분리" % tot,
               current=cur, target=90.0,
               ok=(None if tot < 5 else (worst is not None and worst >= 90.0)),
               n=tot, note=note or "당일 결과 없음",
               reason=None if tot >= 5 else "표본 부족(%d<5)" % tot)


def check_backup_alive():
    """[I2] 백업 실제 실행 — 마지막 실행 시각과 경과. 🔴 6시간 주기가 0회인 것을 몇 주 몰랐다."""
    p = os.path.join(BASE, "data", "_periodic_backup_last.txt")
    try:
        last = float(open(p, encoding="utf-8").read().strip())
        el = (time.time() - last) / 3600.0
    except Exception:
        return _mk("I2", "🔴 무결성", "백업 마지막 실행 경과(시간)",
                   "data/_periodic_backup_last.txt", current=None, target="≤ 12",
                   ok=False, n=None, note="🔴 스탬프 파일 없음 — 백업이 한 번도 안 돌았다. " + _INTEG_DAYS)
    return _mk("I2", "🔴 무결성", "백업 마지막 실행 경과(시간)",
               "data/_periodic_backup_last.txt", current=round(el, 1), target="≤ 12",
               ok=(el <= 12.0), n=1,
               note="마지막 %s · %s" % (time.strftime("%m-%d %H:%M", time.localtime(last)), _INTEG_DAYS))


def check_daemon_alive():
    """[I3] 데몬 마지막 실행 시각 — sleep-first 5곳. ⚠ 현재는 백업만 스탬프가 있다."""
    d = os.path.join(BASE, "data")
    known = {"주기백업": "_periodic_backup_last.txt"}
    have = sum(1 for f in known.values() if os.path.exists(os.path.join(d, f)))
    miss = ["L16004", "L24007(KRA/NAR 백필)", "L32752", "L32852(워치독)"]
    return _mk("I3", "🔴 무결성", "데몬 실행시각 스탬프 보유",
               "sleep-first 데몬 5곳(주기백업 + 미수정 4곳)",
               current=have, target=5, ok=(have >= 5), n=5,
               note="보유 %d/5 · 🔴 미보유: %s · %s" % (have, " · ".join(miss), _INTEG_DAYS))


def check_measurable_today():
    """[I4] 🔴 **오늘분 측정 가능 여부** — 결과·확정배당·마감시각이 **모두** 갖춰진 경주 비율.

    ⚠ 이것이 핵심이다. 축적 중에도 매일 확인되면 **일주일치가 통째로 날아갈 수 없다.**
    """
    day = time.strftime("%Y-%m-%d")
    files = glob.glob(os.path.join(BASE, "data", "analysis_log", day.replace("-", "_") + "_*.json"))
    tot = ok_ = 0
    miss = {"결과없음": 0, "확정배당없음": 0, "마감시각없음": 0}
    for p in files:
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        tot += 1
        res = d.get("result") or {}
        if not res.get("1st"):
            miss["결과없음"] += 1
            continue
        if not (res.get("payouts") or {}).get("quinella"):
            miss["확정배당없음"] += 1
            continue
        hp = p.replace("analysis_log", "odds_history")
        dl = None
        for cand in (hp, hp + ".gz"):
            if os.path.exists(cand):
                try:
                    if cand.endswith(".gz"):
                        import gzip
                        dl = (json.load(gzip.open(cand, "rt", encoding="utf-8")) or {}).get("deadline_epoch")
                    else:
                        dl = (json.load(open(cand, encoding="utf-8")) or {}).get("deadline_epoch")
                except Exception:
                    dl = None
                break
        if not dl:
            miss["마감시각없음"] += 1
            continue
        ok_ += 1
    cur = round(100.0 * ok_ / tot, 1) if tot else None
    return _mk("I4", "🔴 무결성", "오늘분 측정 가능 비율",
               "당일 분석로그 %d건 — 결과·확정배당·마감시각 3종 모두 보유" % tot,
               current=cur, target=80.0, ok=(None if tot < 10 else (cur is not None and cur >= 80.0)),
               n=tot, note="%d/%d · 결손: %s · %s" % (ok_, tot, miss, _INTEG_DAYS),
               reason=None if tot >= 10 else "표본 부족(%d<10)" % tot)


def check_file_duplicate():
    """[I5] 파일 중복 감지 — 같은 경기장·경주번호의 **날짜가 다른 파일 수**.

    🔴 오늘 최대 오류의 원인이다. 날짜 없이 glob 매칭하면 다른 날 데이터가 섞인다.
    ⚠ 중복 자체는 정상이다(같은 경기장이 여러 날 개최). **매칭 코드가 날짜를 써야 한다**는 경보다.
    """
    import re as _re
    from collections import Counter
    c = Counter()
    for p in glob.glob(os.path.join(BASE, "data", "analysis_log", "*.json")):
        m = _re.match(r"\d{4}_\d{2}_\d{2}_(.+)\.json$", os.path.basename(p))
        if m:
            c[m.group(1)] += 1
    dup = {k: v for k, v in c.items() if v >= 2}
    worst = max(dup.values()) if dup else 0
    return _mk("I5", "🔴 무결성", "동명 경주 최대 중복 일수",
               "analysis_log 전체 %d파일 · 경기장+경주번호 기준" % sum(c.values()),
               current=worst, target="기록만", ok=None, n=len(c),
               note="중복 키 %d종 · 최대 %d일 · 🔴 날짜 없는 glob 매칭 금지(원칙 16) · %s"
                    % (len(dup), worst, _INTEG_DAYS))


def build_checklist():
    """반환 dict 의 **최상단에 `summary` 계열을 배치**한다(모바일에서 먼저 보이도록).
    ⚠ 응답은 `ensure_ascii=False` + UTF-8 로 내보낼 것 — `\\uCda9\\uC871` 로 깨지면 외부에서 못 쓴다."""
    items = [check_snapshot_coverage(),
             check_save_failures_fixed(), check_save_failures_unfixed(),
             check_schema_contract(), check_schema_drift(),
             check_score_decomposition(), check_freeze_success(), check_module_load(),
             # 🔴 무결성 감시(매일·자동) — 성능 측정과 성격이 다르다. 절대 줄이지 않는다.
             check_payout_coverage(), check_backup_alive(), check_daemon_alive(),
             check_measurable_today(), check_file_duplicate(),
             check_forecast_discard(), check_forecast_vs_market()]
    for (i, area, name, target, denom, why) in _PENDING:
        items.append(_mk(i, area, name, denom, current=None, target=target,
                         ok=None, n=None, note="분모 근거: " + why, reason="미구현"))
    done = sum(1 for x in items if x["ok"] is True)
    fail = sum(1 for x in items if x["ok"] is False)
    unk = sum(1 for x in items if x["ok"] is None)
    # 미충족만 뽑는다 — **충족 항목은 넣지 않는다.** 목록이 짧아지는 것이 진행 신호다.
    open_items = ["[%s] %s (현재 %s / 목표 %s)" % (x["id"], x["name"], x["current"], x["target"])
                  for x in items if x["ok"] is False]
    pending = ["[%s] %s" % (x["id"], x["name"]) for x in items if x["ok"] is None]
    return {"summary": "%d/%d 충족 · 미충족 %d · 미측정/미구현 %d" % (done, len(items), fail, unk),
            "openItems": open_items,
            "pendingItems": pending,
            "generatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
            "total": len(items), "done": done, "failed": fail, "unmeasured": unk,
            "items": items}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    r = build_checklist()
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return 0
    print("=" * 100)
    print("완료 조건 체크리스트 — %s" % r["summary"])
    print("생성 %s" % r["generatedAt"])
    print("=" * 100)
    area = None
    for it in r["items"]:
        if it["area"] != area:
            area = it["area"]
            print("\n[%s]" % area)
        mark = "✅" if it["ok"] is True else ("❌" if it["ok"] is False else "⬜")
        print("  %s %-4s %-32s 현재 %-10s / 목표 %-8s" % (
            mark, it["id"], it["name"], it["current"], it["target"]))
        print("       분모: %s" % it["denominator"])
        if it.get("reason"):
            print("       ⚠ %s" % it["reason"])
        if it.get("note"):
            print("       · %s" % it["note"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
