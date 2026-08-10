# -*- coding: utf-8 -*-
"""[삼복승 메인 상위2] 복승과 **같은 분모**로 나란히 잰다 (완전 읽기 전용).

🔴 왜: 8/9 「회원 발송은 복승만」 결정의 근거는 7/30 「손실 전액이 삼복승」이었다.
   그런데 그 측정이 **보조·보험까지 포함한 값**이면 메인 상위2만으로는 다를 수 있다.
   오늘 도야마 7R 에서 삼복승 2+4+5 가 48배로 적중했고 그 경주는 복승이 미적중이었다.
⚠ 판정 대상은 `displayedCombos.trifectas`(= finalTrifectas 상위 2)다. 보조는 판정 밖이다.
⚠ 🔴 **분모 집합을 같게 한다** — 복승·삼복승 둘 다 확정배당이 있는 경주만 센다.
   7/30 측정이 분모가 달라 틀렸을 수 있다는 것이 이번 재측정의 출발점이다.

규칙은 `measure_recovery` 에서 import(원칙 15). 판정선 74.5% 는 낮추지 않는다.
실행: python tools/measure_trio_main.py [--days 2026_0*]
"""
import os
import sys
import glob
import json
import random
import argparse
from statistics import median

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from measure_recovery import PAYBACK, CLEAN_LO, CLEAN_HI, BOOT_N   # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def collect(pattern, both_only=True):
    rows = []
    for p in sorted(glob.glob(os.path.join(ROOT, "data", "analysis_log", pattern + ".json"))):
        rk = os.path.basename(p)[:-5]
        rp = os.path.join(ROOT, "data", "race_results", rk + ".json")
        if not os.path.exists(rp):
            continue
        try:
            doc = json.load(open(p, encoding="utf-8"))
            raw = json.load(open(rp, encoding="utf-8")) or {}
        except Exception:
            continue
        res = raw.get("result") or {}
        try:
            a, b, c = int(res["1st"]), int(res["2nd"]), int(res["3rd"])
        except (TypeError, ValueError, KeyError):
            continue
        top2, top3 = tuple(sorted((a, b))), tuple(sorted((a, b, c)))
        pay = raw.get("payouts") or {}
        pq, pt = pay.get("quinella"), pay.get("trifecta")
        if pq is None:
            pq = ((res.get("payouts") or {}).get("quinella"))
        if pt is None:
            pt = ((res.get("payouts") or {}).get("trifecta"))
        try:
            pq = float(pq)
        except (TypeError, ValueError):
            pq = None
        try:
            pt = float(pt)
        except (TypeError, ValueError):
            pt = None
        # 🔴 분모 통일 — 둘 다 확정배당이 있어야 같은 집합에서 비교된다
        if both_only and (pq is None or pt is None):
            continue
        if not both_only and pq is None and pt is None:
            continue
        cp = doc.get("corePicks") or {}
        dc = cp.get("displayedCombos") or {}
        q = {tuple(sorted(int(x) for x in cb)) for cb in (dc.get("quinellas") or []) if len(cb) == 2}
        # 🔴 판정 대상 = displayedCombos.trifectas(상위2). trioShadow 로 비었으면 finalTrifectas[:2]
        t = {tuple(sorted(int(x) for x in cb)) for cb in (dc.get("trifectas") or []) if len(cb) == 3}
        shadow = False
        if not t:
            shadow = True
            t = {tuple(sorted(int(x) for x in (it.get("combo") or [])))
                 for it in (cp.get("finalTrifectas") or [])[:2] if len(it.get("combo") or []) == 3}
        if not q and not t:
            continue
        rows.append({"rk": rk, "q": q, "t": t, "top2": top2, "top3": top3,
                     "pq": pq, "pt": pt, "shadow": shadow})
    return rows


def _calc(rows, mode):
    seats = hits = 0
    ret = 0.0
    od = []
    for r in rows:
        if mode in ("q", "both"):
            if r["q"] and r["pq"] is not None:
                seats += len(r["q"])
                if r["top2"] in r["q"]:
                    hits += 1
                    ret += r["pq"]
                    od.append(r["pq"])
        if mode in ("t", "both"):
            if r["t"] and r["pt"] is not None:
                seats += len(r["t"])
                if r["top3"] in r["t"]:
                    hits += 1
                    ret += r["pt"]
                    od.append(r["pt"])
    return {"seats": seats, "hits": hits, "ret": ret,
            "rate": (ret / seats * 100.0) if seats else 0.0,
            "med": median(od) if od else None, "odds": sorted(od, reverse=True)}


def _ex(c, k):
    return ((c["ret"] - sum(c["odds"][:k])) / c["seats"] * 100.0) if c["seats"] else 0.0


def _ci(rows, mode):
    random.seed(20260810)
    out = []
    for _ in range(BOOT_N):
        smp = [rows[random.randrange(len(rows))] for _ in range(len(rows))]
        out.append(_calc(smp, mode)["rate"])
    out.sort()
    return out[int(BOOT_N * 0.025)], out[int(BOOT_N * 0.975)]


def report(rows, label):
    print("[%s] 경주 %d" % (label, len(rows)))
    for name, mode in [("복승만", "q"), ("삼복승 메인 상위2만", "t"), ("둘 다", "both")]:
        c = _calc(rows, mode)
        if not c["seats"]:
            print("  %-18s 구좌 0 — 판정 불가" % name)
            continue
        lo, hi = _ci(rows, mode)
        print("  %-18s 구좌 %4d · 적중 %3d · 회수율 %6.1f%% (%+.1f%%p) · 1제외 %5.1f%% · 3제외 %5.1f%%"
              " · 배당중앙 %s · CI[%.1f, %.1f]"
              % (name, c["seats"], c["hits"], c["rate"], c["rate"] - PAYBACK,
                 _ex(c, 1), _ex(c, 3), ("%.1f배" % c["med"]) if c["med"] else "-", lo, hi))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", default="2026_0*")
    a = ap.parse_args()
    print("판정선 %.1f%% (import · 낮추지 않는다)" % PAYBACK)
    rows = collect(a.days, both_only=True)
    ns = sum(1 for r in rows if r["shadow"])
    print("🔴 분모 통일(복승·삼복승 확정배당 **둘 다** 보유):", len(rows), "경주",
          "| 그중 trioShadow 로 표시명단이 비어 finalTrifectas[:2] 로 대체한 경주:", ns)
    print()
    report(rows, "분모 통일")
    print()
    rows2 = collect(a.days, both_only=False)
    print("참고 — 분모 미통일(각자 있는 것만 · 7/30 방식과 유사):", len(rows2), "경주")
    report(rows2, "분모 미통일")
    print()
    print("⚠ 두 표를 직접 비교하지 말 것 — 분모가 다르다(원칙 8·8-C).")
    print("⚠ n<30 이면 판정 불가(원칙 1). 상위 1·3건 제외를 함께 본다(원칙 2).")


if __name__ == "__main__":
    main()
