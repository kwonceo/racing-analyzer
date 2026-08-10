# -*- coding: utf-8 -*-
"""[카톡 삼복승을 몇 개로 줄일까] 상위 N개별 성적 + 줄인 뒤 회원 실제 회수율 (완전 읽기 전용).

🔴 배경: 회원 실제 회수율 57.6% · 판정 밖 구좌 93.3% · 최대 손실원이 카톡 삼복승
   (경주당 16.24구좌 · 회수율 45.9%).

🔴 **상위 기준**: `final_recommendation` 의 순서를 그대로 쓴다 —
   trifecta_main → trifecta_insurance1 → trifecta_insurance2 순이고,
   그 뒤는 `corePicks.finalTrifectas` 순서(= 서버가 정한 우선순위)로 잇는다.
   ⚠ 배당순·확률순으로 다시 정렬하지 않는다. **회원에게 나간 그 순서**가 기준이어야
     「몇 개까지 보낼까」의 답이 된다.

규칙은 `measure_recovery` 에서 import(원칙 15). 판정선 74.5% 는 낮추지 않는다.
실행: python tools/measure_trio_cut.py [--days 2026_0*]
"""
import os
import sys
import glob
import json
import argparse
from statistics import median

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from measure_recovery import PAYBACK   # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _c(v):
    try:
        return tuple(sorted(int(x) for x in v))
    except (TypeError, ValueError):
        return None


def _parse_combo(s):
    try:
        return tuple(sorted(int(x) for x in str(s).split("+")))
    except (TypeError, ValueError):
        return None


def collect(pattern):
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
            a, b, c3 = int(res["1st"]), int(res["2nd"]), int(res["3rd"])
        except (TypeError, ValueError, KeyError):
            continue
        pay = raw.get("payouts") or {}
        try:
            pq = float(pay.get("quinella"))
        except (TypeError, ValueError):
            pq = None
        try:
            pt = float(pay.get("trifecta"))
        except (TypeError, ValueError):
            pt = None
        if pq is None:
            continue                                  # 분모 통일
        cp = doc.get("corePicks") or {}
        # 🔴 회원에게 나간 순서 그대로 — final_recommendation 우선, 그 뒤 finalTrifectas
        order = []
        fr = doc.get("final_recommendation") or {}
        for k in ("trifecta_main", "trifecta_insurance1", "trifecta_insurance2"):
            it = fr.get(k) or {}
            cc = _parse_combo(it.get("combo"))
            if cc and len(cc) == 3 and cc not in order:
                order.append(cc)
        for it in (cp.get("finalTrifectas") or []):
            cc = _c(it.get("combo") or [])
            if cc and len(cc) == 3 and cc not in order:
                order.append(cc)
        q = {_c(x) for x in ((cp.get("displayedCombos") or {}).get("quinellas") or []) if len(x) == 2}
        q = {x for x in q if x}
        rows.append({"rk": rk, "top2": tuple(sorted((a, b))), "top3": tuple(sorted((a, b, c3))),
                     "pq": pq, "pt": pt, "trio": order, "q": q})
    return rows


def calc_trio(rows, n):
    seats = hits = 0
    ret = 0.0
    od = []
    for r in rows:
        cs = r["trio"][:n] if n else []
        if not cs or r["pt"] is None:
            continue
        seats += len(cs)
        if r["top3"] in cs:
            hits += 1
            ret += r["pt"]
            od.append(r["pt"])
    od.sort(reverse=True)
    return {"seats": seats, "hits": hits, "ret": ret,
            "rate": ret / seats * 100 if seats else 0,
            "med": median(od) if od else None, "odds": od}


def calc_member(rows, n):
    """복승(판정 명단) + 삼복승 상위n 합산 — 회원이 겪는 숫자."""
    seats = hits = 0
    ret = 0.0
    od = []
    for r in rows:
        if r["q"]:
            seats += len(r["q"])
            if r["top2"] in r["q"]:
                hits += 1
                ret += r["pq"]
                od.append(r["pq"])
        cs = r["trio"][:n] if n else []
        if cs and r["pt"] is not None:
            seats += len(cs)
            if r["top3"] in cs:
                hits += 1
                ret += r["pt"]
                od.append(r["pt"])
    od.sort(reverse=True)
    return {"seats": seats, "hits": hits, "ret": ret,
            "rate": ret / seats * 100 if seats else 0,
            "med": median(od) if od else None, "odds": od}


def ex(c, k):
    return ((c["ret"] - sum(c["odds"][:k])) / c["seats"] * 100.0) if c["seats"] else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", default="2026_0*")
    a = ap.parse_args()
    rows = collect(a.days)
    avg = sum(len(r["trio"]) for r in rows) / len(rows) if rows else 0
    print("판정선 %.1f%% · 분모 통일 %d경주 · 경주당 삼복승 평균 %.2f개" % (PAYBACK, len(rows), avg))
    print("🔴 상위 기준 = 회원에게 나간 순서(trifecta_main → insurance1 → insurance2 → finalTrifectas 순)")
    print()
    print("=== 작업1: 삼복승 상위 N개별 성적 ===")
    for n in (1, 2, 3, 4, 6, 99):
        c = calc_trio(rows, n)
        if not c["seats"]:
            continue
        lab = "현행(전부)" if n == 99 else "상위 %d개" % n
        print("  %-10s 구좌 %6d · 적중 %4d · 회수율 %6.1f%% (%+.1f%%p) · 1제외 %5.1f%% · 3제외 %5.1f%% · 배당중앙 %s"
              % (lab, c["seats"], c["hits"], c["rate"], c["rate"] - PAYBACK,
                 ex(c, 1), ex(c, 3), ("%.1f배" % c["med"]) if c["med"] else "-"))
    print()
    print("=== 작업2: 줄인 뒤 회원 실제(복승 판정명단 + 삼복승 상위N) ===")
    for n in (0, 1, 2, 3, 99):
        c = calc_member(rows, n)
        lab = "삼복승 없음" if n == 0 else ("현행(전부)" if n == 99 else "삼복승 %d개" % n)
        print("  %-12s 구좌 %6d · 적중 %4d · 회수율 %6.1f%% (%+.1f%%p) · 3제외 %5.1f%% · 배당중앙 %s"
              % (lab, c["seats"], c["hits"], c["rate"], c["rate"] - PAYBACK,
                 ex(c, 3), ("%.1f배" % c["med"]) if c["med"] else "-"))
    print()
    print("=== 작업3: 판정 밖 구좌 구성 ===")
    tot = {"삼복승(현행)": 0, "💎복병": 0, "보험매트릭스": 0}
    base = 0
    for r in rows:
        base += len(r["q"])
        tot["삼복승(현행)"] += len(r["trio"])
    for p in sorted(glob.glob(os.path.join(ROOT, "data", "analysis_log", a.days + ".json"))):
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        cp = d.get("corePicks") or {}
        tot["💎복병"] += len([x for x in (cp.get("bmedSpecial") or []) if len(x.get("combo") or []) == 2])
        ins = ((d.get("bmed") or {}).get("insurance") or {})
        tot["보험매트릭스"] += len(ins.get("combos") or [])
    s = base + sum(tot.values())
    print("  판정 대상(복승) %6d 구좌 (%.1f%%)" % (base, base / s * 100 if s else 0))
    for k, v in tot.items():
        print("  🔴 %-12s %6d 구좌 (%.1f%%)" % (k, v, v / s * 100 if s else 0))
    print()
    print("⚠ n<30 이면 판정 불가(원칙 1). 상위 1·3건 제외를 함께 본다(원칙 2).")


if __name__ == "__main__":
    main()
