# -*- coding: utf-8 -*-
"""작업1 계단식 · 작업2 가중 섀도우. 실전 경로 무변경 · 완전 읽기 전용.

■ measure_recovery 에서 import: CLEAN_LO/HI · PAYBACK · BOOT_N · _loadh
■ app.py 에서 복사: _odds_score(4336) · _integrated_grades 계산부(4882~4907)
   integrated = fw*record_score + ow*oddsScore · 등급 = 순위 사분위(A25/B50/C75/D)
   ⚠ formScore 는 analysis_log horses[].record_score 를 쓴다(5번 역산으로 일치 확인).
■ 🔴 재현하지 못한 것 (원칙 3)
   `_final_picks` 는 key_horses 를 인자로 받지 않는다 — **가중을 바꿔도 실제 추천 조합을
   재현할 수 없다.** 그래서 회수율은 **「상위3 전조합(경주당 3구좌)」 대리 정책**으로 잰다.
   ⇒ 절대값이 아니라 **안 사이의 상대 비교**로만 읽을 것.
"""
import os, sys, json, glob, random, importlib.util, itertools, collections

BASE = os.path.abspath(".")
spec = importlib.util.spec_from_file_location("mr", os.path.join(BASE, "tools", "measure_recovery.py"))
MR = importlib.util.module_from_spec(spec); spec.loader.exec_module(MR)

FW_NOW, OW_NOW = 0.253, 0.747          # data/learning.json 현재 학습값


def step(o):                            # app.py:4336 복사
    if o is None or o >= 150:
        return 0
    if o >= 80:
        return 20
    if o >= 50:
        return 40
    if o >= 30:
        return 60
    return 100


SM = [(20, 100), (40, 60), (65, 40), (115, 20), (150, 0)]


def smooth(o):
    """계단의 각 구간 대표값을 유지하되 경계를 선형으로 잇는다(연속화). 순서·방향 불변."""
    if o is None or o >= 150:
        return 0
    if o <= 20:
        return 100
    for (x0, y0), (x1, y1) in zip(SM, SM[1:]):
        if o <= x1:
            return y0 + (y1 - y0) * (o - x0) / (x1 - x0)
    return 0


def grades(pairs):
    """[(no, integ)] → {no: 등급}. app.py:4903~4907 복사."""
    out = sorted(pairs, key=lambda x: -x[1])
    n = len(out)
    g = {}
    for i, (no, _) in enumerate(out):
        fr = i / n if n else 0
        g[no] = "A" if fr < 0.25 else "B" if fr < 0.50 else "C" if fr < 0.75 else "D"
    return g, [no for no, _ in out]


def load(sport, pattern="2026_0*"):
    rows = []
    for f in sorted(glob.glob(os.path.join(BASE, "data", "analysis_log", pattern + ".json"))):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        if (d.get("sport") or "") != sport:
            continue
        res = d.get("result") or {}
        po = (res.get("payouts") or {}).get("quinella")
        if not res.get("1st") or not po or res.get("2nd") is None:
            continue
        hs = [h for h in (d.get("horses") or [])
              if h.get("no") is not None and h.get("record_score") is not None]
        if len(hs) < 4:
            continue
        h_ = MR._loadh(f.replace("analysis_log", "odds_history"))
        dl = (h_ or {}).get("deadline_epoch")
        if not (h_ and dl):
            continue
        sn = [s for s in (h_.get("snapshots") or [])
              if s.get("t") and -8 <= (s["t"] - dl) / 60 <= 0 and s.get("quinella")]
        if not sn:
            continue
        q = {}
        for k, v in max(sn, key=lambda x: x["t"])["quinella"].items():
            try:
                q[tuple(sorted(int(x) for x in str(k).replace("-", "+").split("+")))] = float(v)
            except Exception:
                pass
        rep = {}
        for (a, b), o in q.items():
            for x in (a, b):
                if o > 0 and (rep.get(x) is None or o < rep[x]):
                    rep[x] = o
        top2 = frozenset(sorted({res.get("1st"), res.get("2nd")}))
        mo = q.get(tuple(sorted(top2)))
        if not mo:
            continue
        rows.append({"hs": [(int(h["no"]), float(h["record_score"] or 0)) for h in hs],
                     "rep": rep, "q": q, "po": float(po), "mo": float(mo), "top2": top2})
    return rows


def integ(r, fw, sc):
    ow = 1.0 - fw
    return [(no, fw * max(0.0, min(100.0, f)) + ow * sc(r["rep"].get(no))) for no, f in r["hs"]]


def recov(rows, fw, sc):
    inv, hits = 0, []
    for r in rows:
        _, order = grades(integ(r, fw, sc))
        top3 = order[:3]
        cs = {frozenset(c) for c in itertools.combinations(top3, 2)}
        inv += len(cs)
        if r["top2"] in cs:
            hits.append(r["po"])
    hits.sort(reverse=True)
    return inv, hits


def ci(rows, fw, sc, seed=42):
    random.seed(seed)
    v = []
    for _ in range(MR.BOOT_N):
        s = [random.choice(rows) for _ in range(len(rows))]
        i, h = recov(s, fw, sc)
        v.append(100.0 * sum(h) / max(i, 1))
    v.sort()
    return v[int(0.025 * MR.BOOT_N)], v[int(0.975 * MR.BOOT_N)]


for sport in ("cycle", "horse"):
    raw = load(sport)
    rows = [r for r in raw if MR.CLEAN_LO <= r["po"] / r["mo"] <= MR.CLEAN_HI]
    if len(rows) < 30:
        print("\n[%s] 정제 %d경주 — 판정 불가" % (sport, len(rows))); continue
    print()
    print("=" * 104)
    print("[%s]  전체 %d → 정제 %d경주   판정선 %.1f%%" % (sport, len(raw), len(rows), MR.PAYBACK))

    # ── 작업1: 계단 경계 근처 · 등급 뒤집힘 ──
    nearn = collections.Counter()
    flip_h = flip_r = 0
    for r in rows:
        gs, os_ = grades(integ(r, FW_NOW, step))
        gm, om = grades(integ(r, FW_NOW, smooth))
        if any(gs[no] != gm[no] for no, _ in r["hs"]):
            flip_r += 1
        flip_h += sum(1 for no, _ in r["hs"] if gs[no] != gm[no])
        for no, _ in r["hs"]:
            o = r["rep"].get(no)
            if o is None:
                continue
            for b in (30, 50, 80, 150):
                if abs(o - b) / b <= 0.10:
                    nearn[b] += 1
    tot_h = sum(len(r["hs"]) for r in rows)
    print("  작업1 계단식")
    print("    경계와 낙차 : 30배 100→60 (**-40점**) · 50배 60→40 (-20) · 80배 40→20 (-20) · 150배 20→0 (-20)")
    print("    경계 +-10%% 안에 있는 말 : %s   (전체 %d두)"
          % (" · ".join("%d배 %d두(%.1f%%)" % (b, nearn[b], 100.0 * nearn[b] / tot_h) for b in (30, 50, 80, 150)), tot_h))
    print("    계단→연속 시 등급이 바뀌는 말 %d두(%.1f%%) · **경주 %d건(%.1f%%)**"
          % (flip_h, 100.0 * flip_h / tot_h, flip_r, 100.0 * flip_r / len(rows)))

    # ── 회수율(대리 정책: 상위3 전조합) ──
    print("  회수율 ⚠ 대리 정책 = 상위3 전조합(경주당 3구좌) · 실제 추천과 다르다")
    print("    %-26s %6s %6s %8s %8s %8s %8s %s"
          % ("안", "구좌", "적중", "회수율", "1제외", "3제외", "배당중앙", "CI"))
    plans = [("현행 form%.3f · 계단" % FW_NOW, FW_NOW, step),
             ("현행 가중 · **연속**", FW_NOW, smooth),
             ("form 0.4 · 계단", 0.4, step),
             ("form 0.5 · 계단", 0.5, step),
             ("form 0.6 · 계단", 0.6, step),
             ("form 0.4 · 연속", 0.4, smooth),
             ("form 0.5 · 연속", 0.5, smooth),
             ("form 0.6 · 연속", 0.6, smooth)]
    for lab, fw, sc in plans:
        i, h = recov(rows, fw, sc)
        f = lambda x: 100.0 * sum(x) / max(i, 1)
        med = sorted(h)[len(h) // 2] if h else 0
        lo, hi = ci(rows, fw, sc)
        print("    %-26s %6d %6d %7.1f%% %7.1f%% %7.1f%% %7.1f배 [%.1f, %.1f]"
              % (lab, i, len(h), f(h), f(h[1:]), f(h[3:]), med, lo, hi))
