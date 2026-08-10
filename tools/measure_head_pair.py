# -*- coding: utf-8 -*-
"""[머리로 팔리는 말의 짝] 소급 측정 — 현행 / 유력마짝 1개 / 머리+후보 전체 (완전 읽기 전용).

🔴 대표 지시: 「11번이 머리로 팔리는 말이기에 11-2번은 필수야」
   실물(모리오카 1R · 착순 11-2-5 · 정답 11+2 = 27.8배): 11이 낀 조합을 5개 만들고도 11+2 만 없었다.
⚠ 구좌 증가는 기각 근거가 아니다(대표 결정). 회수율·적중배당 중앙을 **함께** 낸다.

규칙은 `measure_recovery` 에서 import 한다(즉석 코드 금지 · 원칙 15). 판정선 74.5% 는 낮추지 않는다.
실행: python tools/measure_head_pair.py [--days 2026_0*] [--max-odds 2.5]
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


def collect(pattern, max_odds):
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
            top2 = tuple(sorted([int(res.get("1st")), int(res.get("2nd"))]))
        except (TypeError, ValueError):
            continue
        pay = (raw.get("payouts") or {}).get("quinella")
        if pay is None:
            pay = (res.get("payouts") or {}).get("quinella")
        try:
            pay = float(pay)
        except (TypeError, ValueError):
            continue
        cp = doc.get("corePicks") or {}
        cur = {tuple(sorted(int(v) for v in c))
               for c in ((cp.get("displayedCombos") or {}).get("quinellas") or []) if len(c) == 2}
        if not cur:
            continue
        # 단승(머리) — 경륜은 없다(수집 대상이 아님) → 그 경주는 발동 자체가 없다
        win = {}
        for k, v in (cp.get("single") or {}).items():
            try:
                w = float(v)
                if w > 0:
                    win[int(k)] = w
            except (TypeError, ValueError):
                continue
        head = min(win, key=lambda n: win[n]) if win else None
        fired = head is not None and win.get(head, 99) <= max_odds
        # 유력마 — 저장분이 없으면 그 경주는 '유력마짝' 안에서 제외(정직하게 미적용)
        kh = []
        for x in (cp.get("keyHorses") or []):
            try:
                kh.append(int(x))
            except (TypeError, ValueError):
                pass
        cands = []
        for x in ((doc.get("elimination") or {}).get("candidates") or []):
            try:
                cands.append(int(x))
            except (TypeError, ValueError):
                pass
        rows.append({"rk": rk, "cur": cur, "top2": top2, "pay": pay,
                     "head": head, "fired": fired, "win": win,
                     "kh": [n for n in kh if n != head],
                     "cands": [n for n in cands if n != head]})
    return rows


def _pair1(r):
    """유력마 짝 하나 — 단승 낮은 순(머리로 팔리는 말 우선)."""
    out = set(r["cur"])
    if not r["fired"]:
        return out
    pool = r["kh"] or r["cands"]
    pool = sorted(pool, key=lambda n: r["win"].get(n, 9e9))
    for p in pool:
        k = tuple(sorted((r["head"], p)))
        if k not in out:
            out.add(k)
            break
    return out


def _wide(r):
    """머리 + 후보 전체 — 구좌는 늘지만 정답을 놓치지 않는다."""
    out = set(r["cur"])
    if not r["fired"]:
        return out
    for p in r["cands"]:
        out.add(tuple(sorted((r["head"], p))))
    return out


def _calc(rows, pick):
    seats = hits = 0
    ret = 0.0
    od = []
    for r in rows:
        cs = pick(r)
        if not cs:
            continue
        seats += len(cs)
        if r["top2"] in cs:
            hits += 1
            ret += r["pay"]
            od.append(r["pay"])
    return {"seats": seats, "hits": hits, "ret": ret,
            "rate": (ret / seats * 100.0) if seats else 0.0,
            "med": median(od) if od else None, "odds": sorted(od, reverse=True)}


def _ex(c, k):
    return ((c["ret"] - sum(c["odds"][:k])) / c["seats"] * 100.0) if c["seats"] else 0.0


def _ci(rows, pick):
    random.seed(20260810)
    out = []
    for _ in range(BOOT_N):
        smp = [rows[random.randrange(len(rows))] for _ in range(len(rows))]
        out.append(_calc(smp, pick)["rate"])
    out.sort()
    return out[int(BOOT_N * 0.025)], out[int(BOOT_N * 0.975)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", default="2026_0*")
    ap.add_argument("--max-odds", type=float, default=2.5)
    a = ap.parse_args()
    rows = collect(a.days, a.max_odds)
    fired = [r for r in rows if r["fired"]]
    print("판정선 %.1f%% (import · 낮추지 않는다) · 머리 임계 단승 %.1f배 이하" % (PAYBACK, a.max_odds))
    print("대상 경주:", len(rows), "| 🔴 머리 발동:", len(fired),
          ("(%.1f%%)" % (len(fired) / len(rows) * 100)) if rows else "")
    khn = sum(1 for r in fired if r["kh"])
    print("  그중 keyHorses 저장 보유:", khn, "— 없으면 후보(candidates)로 폴백한다")
    if not fired:
        print("🔴 발동 0 — 판정 불가")
        return
    print()
    for name, f in [("현행", lambda r: r["cur"]),
                    ("유력마 짝 1개(지시안)", _pair1),
                    ("머리+후보 전체", _wide)]:
        c = _calc(rows, f)
        lo, hi = _ci(rows, f)
        print("[%s]" % name)
        print("  구좌 %d · 적중 %d · 회수율 %.1f%% (%+.1f%%p)"
              % (c["seats"], c["hits"], c["rate"], c["rate"] - PAYBACK))
        print("  1건제외 %.1f%% · 3건제외 %.1f%% · 적중배당 중앙 %s · CI[%.1f, %.1f]"
              % (_ex(c, 1), _ex(c, 3), ("%.1f배" % c["med"]) if c["med"] else "-", lo, hi))
    base = _calc(rows, lambda r: r["cur"])
    for name, f in [("유력마 짝 1개(지시안)", _pair1), ("머리+후보 전체", _wide)]:
        c = _calc(rows, f)
        ds, dr = c["seats"] - base["seats"], c["ret"] - base["ret"]
        print("  [한계] %s — 추가 구좌 %d · 추가 회수 %.1f · 한계 회수율 %s"
              % (name, ds, dr, ("%.1f%%" % (dr / ds * 100)) if ds else "-"))
    print()
    print("⚠ n<30 이면 판정 불가(원칙 1). 상위 1·3건 제외를 함께 본다(원칙 2).")


if __name__ == "__main__":
    main()
