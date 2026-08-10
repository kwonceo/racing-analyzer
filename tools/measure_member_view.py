# -*- coding: utf-8 -*-
"""[회원이 실제로 사는 것] 다섯 갈래를 한 분모로 잰다 (완전 읽기 전용).

🔴 문제: 회원 눈에 「사라」로 보이는 목록이 다섯인데 **판정하는 것은 하나**다.
   ① displayedCombos      판정 대상(복승) — 유일하게 성적이 잡힌다
   ② finalQuinellas       화면 「🎯 최종 추천」 상위2
   ③ bmedSpecial          화면 「💎 복병」
   🔴 ④ bmed.insurance     화면 하단 **보험 매트릭스** — 금액·원금보전까지 계산해 보여준다
   🔴 ⑤ 카카오 발송분      실제로 회원 폰에 간 것(복승·삼복승)
⚠ 분모를 같게 한다 — 확정배당이 있는 경주만. 갈래마다 분모가 다르면 비교가 성립하지 않는다.
규칙은 `measure_recovery` 에서 import(원칙 15). 판정선 74.5% 는 낮추지 않는다.

실행: python tools/measure_member_view.py [--days 2026_0*]
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


def _c2(v):
    try:
        return tuple(sorted(int(x) for x in v))
    except (TypeError, ValueError):
        return None


def kakao_index():
    """카카오 발송 이력 → {raceKey: {'q': set, 't': set}} (마지막 발송 기준)."""
    idx = {}
    for p in glob.glob(os.path.join(ROOT, "data", "kakao_sent", "*.json")):
        b = os.path.basename(p)
        if not b[:8].isdigit():
            continue
        try:
            arr = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(arr, list):
            continue
        for x in arr:
            rk = x.get("raceKey")
            if not rk:
                continue
            d = idx.setdefault(rk, {"q": set(), "t": set()})
            for it in (x.get("quinellas") or []):
                c = _c2(it.get("combo") if isinstance(it, dict) else it)
                if c and len(c) == 2:
                    d["q"].add(c)
            for it in (x.get("trifectas") or []):
                c = _c2(it.get("combo") if isinstance(it, dict) else it)
                if c and len(c) == 3:
                    d["t"].add(c)
    return idx


def collect(pattern):
    kak = kakao_index()
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
        pay = raw.get("payouts") or {}
        pq, pt = pay.get("quinella"), pay.get("trifecta")
        try:
            pq = float(pq)
        except (TypeError, ValueError):
            pq = None
        try:
            pt = float(pt)
        except (TypeError, ValueError):
            pt = None
        if pq is None:
            continue                                   # 분모 통일 — 복승 확정배당 필수
        cp = doc.get("corePicks") or {}
        dc = cp.get("displayedCombos") or {}
        g = {}
        g["판정(displayed)"] = {_c2(x) for x in (dc.get("quinellas") or []) if len(x) == 2}
        g["최종추천 상위2"] = {_c2(q.get("combo")) for q in (cp.get("finalQuinellas") or [])[:2]
                           if len(q.get("combo") or []) == 2}
        g["💎복병"] = {_c2(q.get("combo")) for q in (cp.get("bmedSpecial") or [])
                     if len(q.get("combo") or []) == 2}
        ins = ((doc.get("bmed") or {}).get("insurance") or {})
        g["보험매트릭스"] = {_c2(x.get("combo")) for x in (ins.get("combos") or [])
                        if len(x.get("combo") or []) == 2}
        kk = kak.get(rk.split("_", 3)[-1].replace("_", " "), {"q": set(), "t": set()})
        g["카톡복승"] = set(kk["q"])
        for k in list(g):
            g[k] = {x for x in g[k] if x}
        rows.append({"rk": rk, "top2": tuple(sorted((a, b))), "top3": tuple(sorted((a, b, c))),
                     "pq": pq, "pt": pt, "g": g, "kt": set(kk["t"]),
                     "insActive": bool(ins.get("active")),
                     "insRows": ins.get("combos") or []})
    return rows


def calc(rows, key, trio=False):
    seats = hits = 0
    ret = 0.0
    od = []
    n = 0
    for r in rows:
        cs = r["kt"] if trio else r["g"].get(key, set())
        if not cs:
            continue
        n += 1
        seats += len(cs)
        want = r["top3"] if trio else r["top2"]
        payv = r["pt"] if trio else r["pq"]
        if want in cs and payv is not None:
            hits += 1
            ret += payv
            od.append(payv)
    od.sort(reverse=True)
    return {"races": n, "seats": seats, "hits": hits, "ret": ret,
            "rate": (ret / seats * 100.0) if seats else 0.0,
            "med": median(od) if od else None, "odds": od}


def ex(c, k):
    return ((c["ret"] - sum(c["odds"][:k])) / c["seats"] * 100.0) if c["seats"] else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", default="2026_0*")
    a = ap.parse_args()
    rows = collect(a.days)
    print("판정선 %.1f%% · 분모 통일(복승 확정배당 보유): %d경주" % (PAYBACK, len(rows)))
    print()
    print("=== 작업1: 회원이 살 수 있는 목록 · 경주당 구좌 · 판정 여부 ===")
    order = ["판정(displayed)", "최종추천 상위2", "💎복병", "보험매트릭스", "카톡복승"]
    tot_seats = 0
    for k in order:
        c = calc(rows, k)
        per = c["seats"] / c["races"] if c["races"] else 0
        tot_seats += c["seats"]
        print("  %-16s 경주 %4d · 구좌 %5d · 경주당 %.2f · 판정대상 %s"
              % (k, c["races"], c["seats"], per, "예" if k == "판정(displayed)" else "🔴 아니오"))
    ct = calc(rows, None, trio=True)
    tot_seats += ct["seats"]
    print("  %-16s 경주 %4d · 구좌 %5d · 경주당 %.2f · 판정대상 🔴 아니오"
          % ("카톡삼복승", ct["races"], ct["seats"], ct["seats"] / ct["races"] if ct["races"] else 0))
    base = calc(rows, "판정(displayed)")
    print()
    print("  🔴 판정 밖 구좌: %d / %d (%.1f%%)"
          % (tot_seats - base["seats"], tot_seats,
             (tot_seats - base["seats"]) / tot_seats * 100 if tot_seats else 0))
    print()
    print("=== 작업2·3: 갈래별 성적 ===")
    for k in order:
        c = calc(rows, k)
        if not c["seats"]:
            print("  %-16s 구좌 0 — 판정 불가" % k)
            continue
        print("  %-16s 구좌 %5d · 적중 %3d · 회수율 %6.1f%% (%+.1f%%p) · 1제외 %5.1f%% · 3제외 %5.1f%% · 배당중앙 %s"
              % (k, c["seats"], c["hits"], c["rate"], c["rate"] - PAYBACK,
                 ex(c, 1), ex(c, 3), ("%.1f배" % c["med"]) if c["med"] else "-"))
    print("  %-16s 구좌 %5d · 적중 %3d · 회수율 %6.1f%% (%+.1f%%p) · 1제외 %5.1f%% · 3제외 %5.1f%% · 배당중앙 %s"
          % ("카톡삼복승", ct["seats"], ct["hits"], ct["rate"], ct["rate"] - PAYBACK,
             ex(ct, 1), ex(ct, 3), ("%.1f배" % ct["med"]) if ct["med"] else "-"))

    print()
    print("=== 작업4: 🔴 다섯 갈래를 다 사면(회원이 겪는 진짜 숫자) ===")
    seats = hits = 0
    ret = 0.0
    od = []
    for r in rows:
        allq = set()
        for k in order:
            allq |= r["g"].get(k, set())
        if allq:
            seats += len(allq)
            if r["top2"] in allq:
                hits += 1
                ret += r["pq"]
                od.append(r["pq"])
        if r["kt"] and r["pt"] is not None:
            seats += len(r["kt"])
            if r["top3"] in r["kt"]:
                hits += 1
                ret += r["pt"]
                od.append(r["pt"])
    od.sort(reverse=True)
    cc = {"seats": seats, "hits": hits, "ret": ret,
          "rate": ret / seats * 100 if seats else 0, "odds": od,
          "med": median(od) if od else None}
    print("  전부 구좌 %d · 적중 %d · 회수율 %.1f%% (%+.1f%%p) · 1제외 %.1f%% · 3제외 %.1f%% · 배당중앙 %s"
          % (cc["seats"], cc["hits"], cc["rate"], cc["rate"] - PAYBACK,
             ex(cc, 1), ex(cc, 3), ("%.1f배" % cc["med"]) if cc["med"] else "-"))
    print("  🔴 발표값(판정만) %.1f%% ↔ 회원 실제 %.1f%% · 차이 %+.1f%%p"
          % (base["rate"], cc["rate"], cc["rate"] - base["rate"]))

    print()
    print("=== 보험 매트릭스 '원금보전 ✅' 표시가 맞았나 ===")
    ok = bad = 0
    for r in rows:
        for it in r["insRows"]:
            c = _c2(it.get("combo"))
            if not c or len(c) != 2:
                continue
            pres = it.get("preserved")
            if pres is None:
                pres = (it.get("payoutRatio") or 0) >= 1
            if c == r["top2"]:
                if pres:
                    ok += 1
                else:
                    bad += 1
    print("  적중한 보험 조합 중 원금보전 표시 있음 %d · 없음 %d" % (ok, bad))
    print()
    print("⚠ n<30 이면 판정 불가(원칙 1). 상위 1·3건 제외를 함께 본다(원칙 2).")


if __name__ == "__main__":
    main()
