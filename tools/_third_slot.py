# -*- coding: utf-8 -*-
"""[측정 · 읽기 전용] 세 번째 조합 교체 — 시장에서 가장 먼 것을 빼고 4~6위를 넣는다.

저장소  : data/analysis_log(corePicks.displayedCombos.quinellas · sport)
          data/race_results(최상위 payouts.quinella) · data/odds_history(.gz 포함)
날짜    : 파일명 날짜로 조인(원칙 16) · 표본 시작 2026-08-01
분모    : 경주 단위 · 정제 0.5~2.0(measure_recovery CLEAN_LO/HI) · approx/estimated 제외
          🔴 순위는 **T-5 배당판**(minutes_before 3~8 중 5에 가장 가까운 틱) 기준
되돌리기: (측정 전용 · 배선 없음)

규칙
  판정 명단이 3개 이상인 경주에서
    ① T-5 시장 순위가 가장 뒤인 조합 하나를 뺀다
    ② T-5 시장 4~6위 중 아직 명단에 없는 조합 하나를 넣는다
    ⚠ 넣을 것이 없으면 그냥 뺀다(하나 줄어든다)

🔴 배선하지 않는다. 숫자만. 지시서 2026-08-22 밤2 의 재현이다.
⚠ 「대박 뺀 회수율」 = 적중 배당 상위 3건을 뺀 회수율(3제외라고 쓰지 않는다).
"""
import collections
import glob
import json
import os
import statistics as st
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "tools"))
import measure_recovery as M

T5_LO, T5_HI = 3.0, 8.0        # T-5 로 인정하는 minutes_before 범위
IN_LO, IN_HI = 4, 6            # 넣을 조합의 시장 순위(1부터)


def _mb(s):
    try:
        return float(s.get("minutes_before"))
    except (TypeError, ValueError, AttributeError):
        return None


def _combo_key(k):
    return tuple(sorted(int(z) for z in str(k).replace("-", "+").split("+")))


def market_rank(h):
    """T-5 배당판 → {조합: 순위}, 그리고 그 틱의 배당 dict. 없으면 (None, None)."""
    sn = [s for s in (h.get("snapshots") or []) if s.get("quinella")]
    cand = [s for s in sn if _mb(s) is not None and T5_LO <= _mb(s) <= T5_HI]
    if not cand:
        return None, None
    pick = min(cand, key=lambda s: abs(_mb(s) - 5.0))
    od = {}
    for k, v in (pick.get("quinella") or {}).items():
        try:
            o = float(v)
        except (TypeError, ValueError):
            continue
        if o > 0:
            od[_combo_key(k)] = o
    if len(od) < 6:
        return None, None
    order = sorted(od.items(), key=lambda x: x[1])
    return {c: i + 1 for i, (c, _o) in enumerate(order)}, od


def load(sport="cycle"):
    rows = []
    for f in sorted(glob.glob(os.path.join(BASE, "data", "analysis_log", "2026_08_*.json"))):
        b = os.path.basename(f)
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        if (d.get("sport") or "") != sport:
            continue
        dc = ((d.get("corePicks") or {}).get("displayedCombos") or {}).get("quinellas") or []
        if not dc:
            continue
        # 결과·확정배당 — analysis_log 우선, 없으면 같은 파일명의 race_results(날짜 포함)
        res = d.get("result") or {}
        po = (res.get("payouts") or {}).get("quinella")
        a1, a2 = res.get("1st"), res.get("2nd")
        rr = os.path.join(BASE, "data", "race_results", b)
        if (po is None or a1 is None) and os.path.exists(rr):
            try:
                r2 = json.load(open(rr, encoding="utf-8"))
                if not r2.get("payouts_approx") and not r2.get("payouts_estimated"):
                    po = po if po is not None else (r2.get("payouts") or {}).get("quinella")
                    _r = r2.get("result") or {}
                    a1 = a1 if a1 is not None else _r.get("1st")
                    a2 = a2 if a2 is not None else _r.get("2nd")
            except Exception:
                pass
        if po is None or a1 is None or a2 is None:
            continue
        h = M._loadh(f.replace("analysis_log", "odds_history")) or {}
        rank, od = market_rank(h)
        if not rank:
            continue
        ans = tuple(sorted([int(a1), int(a2)]))
        # 정제 — 확정 ÷ 마감 직전 배당
        mo = None
        dl = h.get("deadline_epoch")
        sn = [s for s in (h.get("snapshots") or [])
              if s.get("t") and dl and -8 <= (s["t"] - dl) / 60 <= 0 and s.get("quinella")]
        if sn:
            for k, v in max(sn, key=lambda s: s["t"])["quinella"].items():
                if _combo_key(k) == ans:
                    try:
                        mo = float(v)
                    except (TypeError, ValueError):
                        pass
        rows.append({"f": b[:-5], "date": b[:10],
                     "dc": [tuple(sorted(int(x) for x in c)) for c in dc],
                     "ans": ans, "po": float(po), "rank": rank, "od": od,
                     "clean": bool(mo and M.CLEAN_LO <= float(po) / mo <= M.CLEAN_HI)})
    return rows


def swap(r):
    """교체 후 명단. 판정 명단이 3개 미만이면 그대로."""
    cur = list(r["dc"])
    if len(cur) < 3:
        return cur, False
    rank = r["rank"]
    far = max(cur, key=lambda c: rank.get(c, 9999))          # ① 시장에서 가장 먼 것
    out = [c for c in cur if c != far]
    have = set(out)
    add = [c for c, rk in sorted(rank.items(), key=lambda x: x[1])
           if IN_LO <= rk <= IN_HI and c not in have and c != far]
    if add:
        out.append(add[0])                                    # ② 4~6위 중 하나
    return out, True


def score(rows, combos_of):
    slots = 0
    ho = []
    fired = 0
    for r in rows:
        cur, did = combos_of(r)
        fired += 1 if did else 0
        slots += len(cur)
        if r["ans"] in cur:
            ho.append(r["po"])
    if not slots:
        return None
    ho.sort(reverse=True)
    return {"n": len(rows), "slots": slots, "hits": len(ho), "fired": fired,
            "hitRate": 100.0 * len(ho) / len(rows),
            "roi": 100.0 * sum(ho) / slots,
            "ex3": 100.0 * (sum(ho) - sum(ho[:3])) / slots,
            "med": st.median(ho) if ho else 0.0,
            "per": slots / len(rows)}


def block(tag, rows):
    print("=" * 122)
    print("[%s] %d경주" % (tag, len(rows)))
    if not rows:
        return
    a = score(rows, lambda r: (list(r["dc"]), False))
    b = score(rows, swap)
    for nm, d in (("현행", a), ("🟢 교체", b)):
        print("  %-8s 구좌%5d 적중%5.1f%% 회수%6.1f%% 대박뺀%6.1f%% 배당중앙%5.2f 경주당%5.2f %s"
              % (nm, d["slots"], d["hitRate"], d["roi"], d["ex3"], d["med"], d["per"],
                 "⚠판정불가(적중 %d)" % d["hits"] if d["hits"] < M.MIN_HITS else ""))
    print("  차이  대박 뺀 %+.1f%%p · 적중률 %+.1f%%p · 구좌 %+d · 발동 %d경주(%.1f%%)"
          % (b["ex3"] - a["ex3"], b["hitRate"] - a["hitRate"], b["slots"] - a["slots"],
             b["fired"], 100.0 * b["fired"] / len(rows)))


def run(sport):
    rows = load(sport)
    cl = [r for r in rows if r["clean"]]
    print()
    print("### %s — 표본 %d경주 (정제 통과 %d · %.1f%%) · 시작 2026-08-01 · 판정선 %.1f%%"
          % (sport, len(rows), len(cl), 100.0 * len(cl) / len(rows) if rows else 0, M.PAYBACK))
    block("🟢 정제 적용 · 전체", cl)
    ds = sorted({r["date"] for r in cl})
    mid = ds[len(ds) // 2] if ds else ""
    block("전반(< %s)" % mid, [r for r in cl if r["date"] < mid])
    block("후반(>= %s)" % mid, [r for r in cl if r["date"] >= mid])


if __name__ == "__main__":
    for sp in (sys.argv[1:] or ["cycle", "horse"]):
        run(sp)
