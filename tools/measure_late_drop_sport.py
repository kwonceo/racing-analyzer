# -*- coding: utf-8 -*-
"""[읽기 전용] 마감 직전 진성 급락 — **경륜에도 켤 수 있는가**.

🔴 2026-08-28 에 이 축을 켤 때 경마만 켰다:
     ④ 종목별  경마 엣지 1.471(하한 **1.227**) 🟢 · 경륜 1.241(하한 **0.874**) 🟡
   재검토 트리거는 **경륜 진성 급락 적중 30건 이상**(그때 24건)이었다.
   ⇒ 표본이 쌓였는지 보고, 쌓였으면 **판정 4단계를 그대로** 건다.

🔴 원칙 3 — 발동 조건을 **재현하지 않는다.** `tools/late_drop.picks` 를 **그대로 import** 해
   실전과 같은 필터(급락 -25%+ · 반등 10% 미만 · 배당 10~80배 · T-2 이내 · 경주당 2개)를 쓴다.
   ⚠ 재현 못 한 것: 실전은 `_late_drop_ctx` 로 **그 시점 저장분**의 finalQuinellas 를 제외 집합으로
     쓴다. 여기서는 **저장된 최종 finalQuinellas** 를 쓴다 — 마감 직전이라 대개 같지만 동일 보장은 없다.

⚠ 원칙 1(적중 30) · 원칙 2(대박 1·3건 제외 병기) · 원칙 15(확정배당) · 원칙 16(날짜 매칭)
⚠ 엣지 = 실측 적중률 ÷ 시장암시확률(최종 배당 기준 · Σ정규화). 1.0 을 넘어야 시장 위의 정보다.

실행: python tools/measure_late_drop_sport.py [패턴]
"""
import io
import os
import sys
import json
import gzip
import glob
import math
import random
import statistics as st

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "tools"))
import late_drop as LD                      # 🔴 실전과 **같은 함수**를 쓴다

BAD = ("odds_suspect", "baseline_reset", "next_race_blocked")
BOOT_N = 2000
SEED = 20260901


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


def rows(pattern):
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
        ps = LD.picks(tk, ex)                       # 🔴 실전 함수 그대로
        if not ps:
            continue
        fin = LD._qmap((tk[-1] or {}).get("quinella"))
        s = sum(1.0 / o for o in fin.values() if o > 0)
        if s <= 0:
            continue
        for combo, odds, drop, mb in ps:
            imp = (1.0 / fin[combo]) / s if combo in fin and fin[combo] > 0 else None
            if imp is None:
                continue
            out.append({"rk": name, "sport": d.get("sport"), "combo": combo,
                        "hit": 1 if combo == top2 else 0, "imp": imp,
                        "odds": odds, "drop": drop,
                        "po": float((rr.get("payouts") or {}).get("quinella") or 0)})
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


def show(lbl, v, races):
    n = len(v)
    if n == 0:
        print("   %-18s 표본 없음" % lbl)
        return
    hit = sum(x["hit"] for x in v)
    imp = sum(x["imp"] for x in v)
    edge = (hit / imp) if imp else 0.0
    lo, hi = _boot_edge(v) if n >= 10 else (0.0, 0.0)
    od = [x["po"] for x in v if x["hit"] and x["po"] > 0]
    rec = sum(od)
    r0 = 100.0 * rec / n
    r1 = 100.0 * _ex(od, 1) / n
    r3 = 100.0 * _ex(od, 3) / n
    ok = "🟢" if (lo > 1.0 and hit >= 30) else ("🔴" if lo <= 1.0 else "⚠")
    print("   %-18s 신호%5d 적중%4d · 엣지 **%.3f** CI[%.3f,%.3f] %s · 회수 %.1f%% · 1제외 %.1f%% · **3제외 %.1f%%** · 경주당 %.2f%s"
          % (lbl, n, hit, edge, lo, hi, ok, r0, r1, r3, n / float(races) if races else 0,
             "" if hit >= 30 else "  ⚠적중<30"))


def main():
    pat = sys.argv[1] if len(sys.argv) > 1 else "2026_0*"
    v = rows(pat)
    nr = len({x["rk"] for x in v})
    print("▣ 마감 직전 진성 급락 — 경륜에도 켤 수 있는가")
    print("   🔴 발동 조건은 `tools/late_drop.picks` **그대로**"
          " (급락 -%.0f%%+ · 반등 %.0f%% 미만 · 배당 %.0f~%.0f배 · T-%.0f 이내 · 경주당 %d)"
          % (LD.DROP_MIN, LD.REBOUND_MAX, LD.ODDS_LO, LD.ODDS_HI, LD.MB_MAX, LD.MAX_PICKS))
    print("   ⚠ 표본: %s · 확정배당 보유 · 신호 발생 **%d경주 / 신호 %d건**\n" % (pat, nr, len(v)))
    if not v:
        print("   신호 없음")
        return
    show("전체", v, nr)
    for sp, lbl in (("horse", "경마(현행 ON)"), ("cycle", "🔴 경륜(현행 OFF)")):
        sub = [x for x in v if x["sport"] == sp]
        show(lbl, sub, len({x["rk"] for x in sub}))
    print()
    print("   ── 경륜 기간 3분할(판정 4단계 ③) ──")
    cy = sorted([x for x in v if x["sport"] == "cycle"], key=lambda x: x["rk"])
    n3 = len(cy) // 3
    for i, t in enumerate(("기간1(앞)", "기간2(중)", "기간3(뒤)")):
        seg = cy[i * n3:(i + 1) * n3] if i < 2 else cy[2 * n3:]
        show("  " + t, seg, len({x["rk"] for x in seg}))
    print()
    print("   🔴 판정: 엣지 **CI 하한 > 1.0** 이고 **적중 30건 이상**이며")
    print("      **대박 3건 제외 회수율이 100%% 를 넘어야** 켤 근거가 된다. 판정선을 낮추지 않는다.")


if __name__ == "__main__":
    main()
