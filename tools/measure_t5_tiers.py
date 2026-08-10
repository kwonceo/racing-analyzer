# -*- coding: utf-8 -*-
"""[T-5 등급별 회수율] 본선만 · 본선+추가 · 전체 를 각각 잰다 (완전 읽기 전용).

🔴 왜 셋을 나누나: 우리가 구좌를 정하지 않는다. 회원이 등급을 보고 고른다.
   그러므로 **하나의 회수율은 의미가 없다** — 회원이 고를 수 있는 조합마다 따로 재야 한다.

등급 정의(소급 재현):
  ★ 본선   = mb<=5 가 되는 **첫 시점**의 복승 집합
  ⚡ 추가   = 마감 시점에 있으나 본선에 없던 것
  전체     = 본선 ∪ 추가 (= T5_FREEZE 를 켠 상태의 명단)
  ⚠ 현행(동결 없음)도 함께 낸다 — 마감 시점 명단 그대로다.

규칙은 `measure_recovery` 것을 **import 해서 쓴다**(즉석 코드 금지 · 원칙 15).
  · PAYBACK(74.5) 판정선 · CLEAN_LO/HI 정제 범위 · BOOT_N 부트스트랩 횟수
  ⚠ 판정선은 낮추지 않는다.

실행: python tools/measure_t5_tiers.py [--days 2026_08_*]
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


def _combos_of(row):
    """이력 1행 → 복승 조합 집합."""
    out = set()
    for q in (row.get("quinellas") or []):
        c = q.get("combo") or []
        if len(c) == 2:
            try:
                out.add(tuple(sorted(int(x) for x in c)))
            except (TypeError, ValueError):
                pass
    if not out and row.get("quinella_main"):
        try:
            out.add(tuple(sorted(int(x) for x in str(row["quinella_main"]).split("+"))))
        except (TypeError, ValueError):
            pass
    return out


def _final_odds(doc):
    """마감 직전 스냅샷의 복승 배당맵(정제용 대조값)."""
    rk = doc.get("_rk")
    p = os.path.join(ROOT, "data", "odds_history", rk + ".json")
    if not os.path.exists(p):
        return {}
    try:
        d = json.load(open(p, encoding="utf-8"))
    except Exception:
        return {}
    best = {}
    for s in (d.get("snapshots") or []):
        q = s.get("quinella")
        if isinstance(q, dict) and q:
            best = q
    out = {}
    for k, v in best.items():
        ps = [x for x in str(k).replace("-", "+").split("+") if x.isdigit()]
        if len(ps) != 2:
            continue
        try:
            out[tuple(sorted(int(x) for x in ps))] = float(v[0] if isinstance(v, list) else v)
        except (TypeError, ValueError):
            pass
    return out


def collect(pattern):
    """경주별로 (본선, 추가, 마감명단, 정답, 확정배당) 을 모은다."""
    rows = []
    for p in sorted(glob.glob(os.path.join(ROOT, "data", "analysis_log", pattern + ".json"))):
        rk = os.path.basename(p)[:-5]
        rp = os.path.join(ROOT, "data", "race_results", rk + ".json")
        if not os.path.exists(rp):
            continue
        try:
            doc = json.load(open(p, encoding="utf-8"))
            raw = json.load(open(rp, encoding="utf-8")) or {}
            res = raw.get("result") or {}
        except Exception:
            continue
        try:
            top2 = tuple(sorted([int(res.get("1st")), int(res.get("2nd"))]))
        except (TypeError, ValueError):
            continue
        # 🔴 payouts 는 **최상위**다. `result` 하위에서 찾으면 전건이 "없음"으로 나온다 —
        #   2026-08-09 오진 ①과 같은 실수를 이 도구에서 한 번 더 냈다(원칙 8-E).
        #   실측: 2,466건이 전부 결손으로 잡혔고, 최상위로 고치니 정상이었다.
        pay = (raw.get("payouts") or {}).get("quinella")
        if pay is None:
            pay = (res.get("payouts") or {}).get("quinella")   # 구데이터 호환(무삭제)
        if pay is None:
            continue                                  # 🔴 확정배당 없으면 회수율을 못 잰다
        try:
            pay = float(pay)
        except (TypeError, ValueError):
            continue
        rh = doc.get("recommendation_history") or []
        first = None
        for r in rh:
            mb = r.get("minutes_before")
            if isinstance(mb, (int, float)) and mb <= 5:
                first = r
                break
        if first is None or not rh:
            continue
        main = _combos_of(first)
        last = _combos_of(rh[-1])
        if not main and not last:
            continue
        doc["_rk"] = rk
        fo = _final_odds(doc)
        mk = fo.get(top2)
        if mk:                                        # 정제: 확정 ↔ 배당판 괴리
            r = pay / mk if mk else None
            if r is not None and not (CLEAN_LO <= r <= CLEAN_HI):
                continue
        rows.append({"rk": rk, "main": main, "late": last - main,
                     "last": last, "top2": top2, "pay": pay})
    return rows


def _calc(rows, pick):
    """pick(row)->조합집합 으로 구좌·적중·회수율을 낸다. 총투자 = 구좌당 1."""
    seats = hits = 0
    ret = 0.0
    odds_hit = []
    for r in rows:
        cs = pick(r)
        if not cs:
            continue
        seats += len(cs)
        if r["top2"] in cs:
            hits += 1
            ret += r["pay"]
            odds_hit.append(r["pay"])
    rate = (ret / seats * 100.0) if seats else 0.0
    return {"races": sum(1 for r in rows if pick(r)), "seats": seats, "hits": hits,
            "ret": ret, "rate": rate,
            "med": median(odds_hit) if odds_hit else None,
            "odds": sorted(odds_hit, reverse=True)}


def _ex(c, k):
    """상위 k건 제외 회수율."""
    if c["seats"] <= 0:
        return 0.0
    return (c["ret"] - sum(c["odds"][:k])) / c["seats"] * 100.0


def _ci(rows, pick):
    random.seed(20260810)
    if not rows:
        return (0.0, 0.0)
    out = []
    for _ in range(BOOT_N):
        smp = [rows[random.randrange(len(rows))] for _ in range(len(rows))]
        out.append(_calc(smp, pick)["rate"])
    out.sort()
    return (out[int(BOOT_N * 0.025)], out[int(BOOT_N * 0.975)])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", default="2026_08_*")
    a = ap.parse_args()
    rows = collect(a.days)
    print("판정선 %.1f%% (measure_recovery 에서 import · 낮추지 않는다)" % PAYBACK)
    print("정제 범위 %.1f~%.1f · 부트스트랩 %d회" % (CLEAN_LO, CLEAN_HI, BOOT_N))
    print("대상 경주:", len(rows), "(결과 + 확정배당 + T-5 시점 보유)")
    if not rows:
        print("🔴 표본 0 — 판정 불가")
        return
    plans = [
        ("현행(동결 없음·마감 명단)", lambda r: r["last"]),
        ("★ 본선만", lambda r: r["main"]),
        ("⚡ 추가만", lambda r: r["late"]),
        ("★+⚡ 전체(동결 적용)", lambda r: r["main"] | r["late"]),
    ]
    print()
    for name, f in plans:
        c = _calc(rows, f)
        lo, hi = _ci(rows, f)
        print("[%s]" % name)
        print("  경주 %d · 구좌 %d · 적중 %d · 회수율 %.1f%% (%+.1f%%p)"
              % (c["races"], c["seats"], c["hits"], c["rate"], c["rate"] - PAYBACK))
        print("  1건제외 %.1f%% · 3건제외 %.1f%% · 적중배당 중앙 %s · CI[%.1f, %.1f]"
              % (_ex(c, 1), _ex(c, 3),
                 ("%.1f배" % c["med"]) if c["med"] else "-", lo, hi))
    print()
    print("⚠ n<30 이면 판정 불가로 읽는다(원칙 1). 상위 1·3건 제외를 반드시 함께 본다(원칙 2).")


if __name__ == "__main__":
    main()
