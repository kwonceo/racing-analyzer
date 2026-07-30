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


def check_save_failures():
    """④-2 저장 실패(WinError) = 0건.

    분모 = **당일 stdout 로그 전체**(회전본 포함). 현재 로그만 보면 재기동으로 카운트가 리셋돼
      '고쳐진 것처럼' 보인다 — 2026-07-30 에 실제로 누적치와 구간치를 혼동한 적이 있다.
    ⚠ 발생 지점별로 나눠 센다. `_json_atomic` 계열은 수정됐고 `path+".tmp"` 17곳은 미수정이라,
      합계만 보면 "고쳐도 안 줄었다"는 잘못된 결론이 난다.
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
                fixed += 1
            elif "[분석로그]" in line or "[다중경주]" in line:
                unfixed += 1
            else:
                other += 1
    total = fixed + unfixed + other
    return _mk("D2", "④ 데이터 보전", "저장 실패(WinError) 건수",
               "당일 stdout 로그 전체(회전본 포함)",
               current=total, target=0, ok=(total == 0), n=len(files),
               note="_json_atomic 계열(수정됨) %d건 · path+\".tmp\" 17곳(미수정) %d건 · 기타 %d건"
                    % (fixed, unfixed, other))


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


def check_schema_drift():
    """④-4 스키마 드리프트 🔴 항목 = 0개.

    분모 = `SCHEMA_RED_FIELDS` 전체(CLAUDE.md 「탈락 필드 우선순위」 🔴 1·2·3).
    판정 = 각 🔴 필드가 **실데이터에 실제로 저장되고 있는가**(보유 행 1건이라도 있으면 해소).
    ⚠ '코드에 배선됐는가'가 아니라 '데이터에 남았는가'로 본다 —
      2026-07-30 에 `surface`/`trackCond` 가 **코드는 배선됐는데 실데이터 0%** 인 사례가 있었다(원칙 5).
    """
    store = os.path.join(BASE, "starters_store.json")
    if not os.path.exists(store):
        return _mk("D4", "④ 데이터 보전", "스키마 드리프트 🔴 항목 수",
                   "SCHEMA_RED_FIELDS %d개 (CLAUDE.md 🔴 1·2·3)" % len(SCHEMA_RED_FIELDS),
                   current=None, target=0, ok=None, n=None,
                   reason="starters_store.json 미존재 — 측정 불가")
    try:
        db = json.load(open(store, encoding="utf-8"))
    except Exception as e:
        return _mk("D4", "④ 데이터 보전", "스키마 드리프트 🔴 항목 수",
                   "SCHEMA_RED_FIELDS %d개" % len(SCHEMA_RED_FIELDS),
                   current=None, target=0, ok=None, n=None,
                   reason="starters_store.json 파싱 실패: %s" % str(e)[:80])
    have = collections.Counter()
    rows = 0
    for rk, rec in (db.items() if isinstance(db, dict) else []):
        for h in ((rec or {}).get("horses") or []):
            rows += 1
            for f in SCHEMA_RED_FIELDS:
                if h.get(f) not in (None, "", []):
                    have[f] += 1
    missing = [f for f in SCHEMA_RED_FIELDS if have[f] == 0]
    return _mk("D4", "④ 데이터 보전", "스키마 드리프트 🔴 항목 수",
               "SCHEMA_RED_FIELDS %d개 · 분자=starters_store 전체 행(%d행) 중 보유 행"
               % (len(SCHEMA_RED_FIELDS), rows),
               current=len(missing), target=0, ok=(len(missing) == 0), n=rows,
               note="미보유: %s · 보유율: %s" % (
                   (", ".join(missing) if missing else "없음"),
                   ", ".join("%s %.1f%%" % (f, 100.0 * have[f] / rows if rows else 0)
                             for f in SCHEMA_RED_FIELDS)))


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


def build_checklist():
    items = [check_snapshot_coverage(), check_save_failures(),
             check_schema_contract(), check_schema_drift()]
    for (i, area, name, target, denom, why) in _PENDING:
        items.append(_mk(i, area, name, denom, current=None, target=target,
                         ok=None, n=None, note="분모 근거: " + why, reason="미구현"))
    done = sum(1 for x in items if x["ok"] is True)
    fail = sum(1 for x in items if x["ok"] is False)
    unk = sum(1 for x in items if x["ok"] is None)
    return {"generatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
            "total": len(items), "done": done, "failed": fail, "unmeasured": unk,
            "summary": "%d/%d 충족 (미충족 %d · 미측정/미구현 %d)" % (done, len(items), fail, unk),
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
