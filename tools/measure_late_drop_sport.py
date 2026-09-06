# -*- coding: utf-8 -*-
"""[읽기 전용] 마감 직전 진성 급락 — **종목별 판정 + 확장안 비교**.

🔴 2026-08-28 에 이 축을 켤 때 경마만 켰다:
     ④ 종목별  경마 엣지 1.471(하한 **1.227**) 🟢 · 경륜 1.241(하한 **0.874**) 🟡
🔴 2026-09-01 경륜 재검토 → 실전 필터로 엣지 **0.651** · 사실상 종결(CLAUDE.md 참조).

🔴 원칙 3 — 발동 조건을 **재현하지 않는다.** `tools/late_drop.picks` 를 **그대로 import** 한다.
   문턱을 바꿔 볼 때도 **재구현하지 않고 모듈 상수를 잠시 바꿔 같은 함수를 부른다.**
   (`DROP_MIN`·`REBOUND_MAX`·`ODDS_LO/HI`·`MAX_PICKS` 는 함수 **본문**에서 호출 시점에 읽으므로
    이 방법이 성립한다. `mb_max` 만 인자로 넘긴다.)
   ⚠ 바꾼 상수는 **반드시 원복**한다 — 안 그러면 다음 측정이 오염된다.

⚠ 재현 못 한 것: 실전은 `_late_drop_ctx` 로 **그 시점 저장분**의 finalQuinellas 를 제외 집합으로
  쓴다. 여기서는 **저장된 최종 finalQuinellas** 를 쓴다 — 마감 직전이라 대개 같지만 동일 보장은 없다.

⚠ 원칙 1(적중 30) · 원칙 2(대박 1·3건 제외 병기) · 원칙 15(확정배당) · 원칙 16(날짜 매칭)
⚠ 엣지 = 실측 적중률 ÷ 시장암시확률(최종 배당 기준 · Σ정규화). 1.0 을 넘어야 시장 위의 정보다.

실행:
  python tools/measure_late_drop_sport.py                 종목별 판정(기본)
  python tools/measure_late_drop_sport.py 2026_0* --vary  **확장안 비교 + 기간 3분할**
"""
import io
import os
import sys
import json
import gzip
import glob
import math
import random
import contextlib
import statistics as st

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "tools"))
import late_drop as LD                      # 🔴 실전과 **같은 함수**를 쓴다

BAD = ("odds_suspect", "baseline_reset", "next_race_blocked")
BOOT_N = 2000
SEED = 20260901

# 🔴🔴 [2026-09-05] 실전 재현 스위치 — **look-ahead 차단**(원칙 27)
#   종전엔 `LD.picks(전체 히스토리)` 를 **한 번** 불렀다. 그러면 반등 판정이 급락 시점
#   **이후 틱**까지 보게 되어 「진성 vs 페이크」가 미래 정보로 갈렸다.
#   실전은 마감 2.5분 이내 **매 폴링**마다 부르고 **최근 12틱**만 보며 **첫 발동에서 잠근다.**
#   ⇒ `LD.replay_live` 가 그 셋을 재현한다(정의는 late_drop.py 에 둔다 — 여기서 재구현하지 않는다).
#   실측 영향: 경마 엣지 1.576(종전) → **1.445**(실전 재현). 측정이 8% 낙관적이었다.
#   🔧 되돌리기: LIVE_REPLAY = False (그러면 종전 수치가 그대로 재현된다)
LIVE_REPLAY = True

# 🔴 현행 실전 문턱 — `late_drop.py` 에서 읽어 온다(여기에 손으로 적지 않는다).
CUR = {"drop": LD.DROP_MIN, "reb": LD.REBOUND_MAX,
       "lo": LD.ODDS_LO, "hi": LD.ODDS_HI, "mb": LD.MB_MAX, "cap": LD.MAX_PICKS}


@contextlib.contextmanager
def thresholds(drop=None, reb=None, lo=None, hi=None, cap=None):
    """`late_drop` 모듈 상수를 잠시 바꾼다 — **함수는 그대로 실전 것을 쓴다.**
    🔴 반드시 원복한다(finally). 안 그러면 뒤 측정이 오염된다."""
    old = (LD.DROP_MIN, LD.REBOUND_MAX, LD.ODDS_LO, LD.ODDS_HI, LD.MAX_PICKS)
    try:
        if drop is not None:
            LD.DROP_MIN = drop
        if reb is not None:
            LD.REBOUND_MAX = reb
        if lo is not None:
            LD.ODDS_LO = lo
        if hi is not None:
            LD.ODDS_HI = hi
        if cap is not None:
            LD.MAX_PICKS = cap
        yield
    finally:
        (LD.DROP_MIN, LD.REBOUND_MAX, LD.ODDS_LO, LD.ODDS_HI, LD.MAX_PICKS) = old


def _load(p):
    for path, gz in ((p, False), (p + ".gz", True)):
        try:
            if gz:
                with gzip.open(path, "rt", encoding="utf-8") as f:
                    return json.load(f)
            with io.open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            continue
    return None


def _ticks(doc):
    out = []
    for s in (doc.get("snapshots") or []):
        if not isinstance(s, dict) or any(s.get(b) for b in BAD):
            continue
        if s.get("after_close") or not s.get("quinella"):
            continue
        mb = s.get("minutes_before")
        if mb is None or mb < 0:
            continue
        out.append(s)
    return out


_CACHE = {}


def races(pattern):
    """경주별 재료를 한 번만 읽어 캐시한다(문턱을 바꿔 여러 번 돌리므로)."""
    if pattern in _CACHE:
        return _CACHE[pattern]
    out = []
    for f in sorted(glob.glob(os.path.join(BASE, "data", "analysis_log", pattern + ".json"))):
        name = os.path.basename(f)[:-5]
        d = _load(f)
        if not isinstance(d, dict):
            continue
        cp = d.get("corePicks") or {}
        rr = _load(os.path.join(BASE, "data", "race_results", name + ".json"))
        if not isinstance(rr, dict) or rr.get("payouts_approx"):
            continue
        r = rr.get("result") or {}
        try:
            top2 = (min(int(r["1st"]), int(r["2nd"])), max(int(r["1st"]), int(r["2nd"])))
            po = float((rr.get("payouts") or {}).get("quinella"))
        except (TypeError, ValueError, KeyError):
            continue
        oh = _load(os.path.join(BASE, "data", "odds_history", name + ".json")) or {}
        tk = _ticks(oh)
        if len(tk) < 3:
            continue
        ex = set()
        for q in (cp.get("finalQuinellas") or []):
            c = q.get("combo") if isinstance(q, dict) else q
            if isinstance(c, (list, tuple)) and len(c) >= 2:
                ex.add((min(int(c[0]), int(c[1])), max(int(c[0]), int(c[1]))))
        fin = LD._qmap((tk[-1] or {}).get("quinella"))
        s = sum(1.0 / o for o in fin.values() if o > 0)
        if s <= 0:
            continue
        out.append({"rk": name, "sport": d.get("sport"), "ticks": tk, "ex": ex,
                    "top2": top2, "po": po, "fin": fin, "isum": s})
    out.sort(key=lambda x: x["rk"])
    _CACHE[pattern] = out
    return out


def signals(rc, mb=None, **kw):
    """문턱을 적용해 신호를 뽑는다 — **실전 호출을 그대로 재현**한다(LIVE_REPLAY)."""
    out = []
    with thresholds(**kw):
        _mb = CUR["mb"] if mb is None else mb
        for r in rc:
            if LIVE_REPLAY:
                _ps, _fire = LD.replay_live(r["ticks"], r["ex"], _mb)
            else:                              # 🔧 종전(전체 히스토리 1회 · 미래 틱 포함)
                _ps, _fire = LD.picks(r["ticks"], r["ex"], _mb), None
            for combo, odds, drop, m in _ps:
                if combo not in r["fin"] or r["fin"][combo] <= 0:
                    continue
                out.append({"rk": r["rk"], "sport": r["sport"], "combo": combo,
                            "hit": 1 if combo == r["top2"] else 0,
                            "imp": (1.0 / r["fin"][combo]) / r["isum"],
                            "odds": odds, "drop": drop, "po": r["po"]})
    return out


def _boot_edge(v):
    random.seed(SEED)
    n = len(v)
    es = []
    for _ in range(BOOT_N):
        s = [v[random.randrange(n)] for _ in range(n)]
        h = sum(x["hit"] for x in s)
        m = sum(x["imp"] for x in s)
        es.append((h / m) if m else 0.0)
    es.sort()
    return es[int(BOOT_N * 0.025)], es[int(BOOT_N * 0.975)]


def _ex(v, k):
    return sum(sorted(v, reverse=True)[k:])


def show(lbl, v, races_n):
    n = len(v)
    if n == 0:
        print("   %-22s 표본 없음" % lbl)
        return
    hit = sum(x["hit"] for x in v)
    imp = sum(x["imp"] for x in v)
    edge = (hit / imp) if imp else 0.0
    lo, hi = _boot_edge(v) if n >= 10 else (0.0, 0.0)
    od = [x["po"] for x in v if x["hit"] and x["po"] > 0]
    r0 = 100.0 * sum(od) / n
    r1 = 100.0 * _ex(od, 1) / n
    r3 = 100.0 * _ex(od, 3) / n
    # 🔴 판정: CI 하한 > 1.0 · 적중 30건+ · 3제외 100%+
    ok = "🟢" if (lo > 1.0 and hit >= 30 and r3 >= 100) else "🔴"
    print("   %-22s 신호%5d 적중%4d · 엣지 **%.3f** CI[%.3f,%.3f] · 회수%7.1f%% · 1제외%7.1f%% · **3제외%7.1f%%** %s%s"
          % (lbl, n, hit, edge, lo, hi, r0, r1, r3, ok,
             "  ⚠적중<30" if hit < 30 else ""))


def period3(lbl, v, key=lambda x: x["rk"]):
    v = sorted(v, key=key)
    n3 = len(v) // 3
    for i, t in enumerate(("기간1(앞)", "기간2(중)", "기간3(뒤)")):
        seg = v[i * n3:(i + 1) * n3] if i < 2 else v[2 * n3:]
        show("  %s %s" % (lbl, t), seg, len({x["rk"] for x in seg}))


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    vary = "--vary" in sys.argv
    pat = args[0] if args else "2026_0*"
    rc = races(pat)
    print("▣ 마감 직전 진성 급락")
    print("   🔴 발동은 `tools/late_drop.picks` **그대로** — 문턱만 모듈 상수로 바꿔 부른다(재구현 없음)")
    print("   현행: 급락 -%.0f%%+ · 반등 %.0f%% 미만 · 배당 %.0f~%.0f배 · T-%.0f 이내 · 경주당 %d"
          % (CUR["drop"], CUR["reb"], CUR["lo"], CUR["hi"], CUR["mb"], CUR["cap"]))
    print("   ⚠ 표본: %s · 확정배당 보유 **%d경주**\n" % (pat, len(rc)))

    base = signals(rc)
    if not vary:
        show("전체", base, len(rc))
        for sp, lbl in (("horse", "경마(현행 ON)"), ("cycle", "🔴 경륜(현행 OFF)")):
            sub = [x for x in base if x["sport"] == sp]
            show(lbl, sub, len({x["rk"] for x in sub}))
        print()
        print("   ── 경륜 기간 3분할 ──")
        period3("경륜", [x for x in base if x["sport"] == "cycle"])
        print()
        print("   🔴 판정: CI 하한 > 1.0 · 적중 30건+ · 3제외 100%+ 를 **모두** 넘어야 근거가 된다.")
        return

    # ── 확장안 비교 (경마) ──────────────────────────────────────────
    PRESETS = (("① 현행", {}),
               ("② 경주당 3개", {"cap": 3}),
               ("③ 배당 5~80배", {"lo": 5.0}),
               ("④ 둘 다(3개+5배)", {"cap": 3, "lo": 5.0}),
               ("⑤ T-3 이내", {"mb": 3.0}),
               ("⑥ 반등 <20%", {"reb": 20.0}))
    print("■ 확장안 비교 — **경마만**(경륜은 2026-09-01 에 기각)")
    print("   🔴 목적은 「더 벌기」가 아니라 **구간별 적중 30건**을 넘겨 판정을 가능하게 하는 것이다.\n")
    for lbl, kw in PRESETS:
        v = [x for x in signals(rc, **kw) if x["sport"] == "horse"]
        show(lbl, v, len({x["rk"] for x in v}))
        period3("   " + lbl.split()[0], v)
        print()
    print("   🔴 구간별 적중이 30건을 넘어야 기간 3분할이 **판정**이 된다.")
    print("      넘기 전에는 3제외가 100%% 아래여도 「무너졌다」고 말할 수 없다(원칙 1).")


if __name__ == "__main__":
    main()
