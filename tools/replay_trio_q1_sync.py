# -*- coding: utf-8 -*-
"""삼복승 메인을 복승① 쌍과 맞추는 규칙 리플레이 (읽기 전용).

규칙(app.py _sync_trio_to_q1 과 같은 정의):
  판정 삼복승 = finalTrifectas 앞 2개.  복승① 쌍(finalQuinellas[0])이 그 2개 어디에도 없으면
  → [복승①두 말 + 셋째] 를 첫자리에 넣고 원래 1번째를 2번째로(원래 2번째는 보조로 밀림)
  셋째 = 확신도1위(confTop1) → 유력마(keyHorses) → 원래 메인1의 쌍 밖 말 순, 복승① 두 말 제외
표본  analysis_log 결과 1~3착 + 삼복승 확정배당(result.payouts.trifecta) · 근사 제외 · 구좌=조합 1
대조  같은 경주에서 바뀐 자리(= 빠지는 원래 메인2 ↔ 들어가는 동기화 조합)만 비교 + 3분할
"""
import os, io, sys, json, glob, collections

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAT = sys.argv[1] if len(sys.argv) > 1 else "2026_0[789]*"


def load(p):
    try:
        return json.load(io.open(p, encoding="utf-8"))
    except Exception:
        return None


def ints(c):
    try:
        return [int(x) for x in c]
    except (TypeError, ValueError):
        return []


def third_of(cp, pair, main0):
    cands = []
    for v in [cp.get("confTop1")] + list(cp.get("keyHorses") or []) + [x for x in (main0 or []) if x not in pair]:
        try:
            v = int(v)
        except (TypeError, ValueError):
            continue
        if v not in pair and v not in cands:
            cands.append(v)
    return cands[0] if cands else None


rows = []
for p in sorted(glob.glob(os.path.join(BASE, "data", "analysis_log", PAT + "_*.json"))):
    d = load(p)
    if not d:
        continue
    cp = d.get("corePicks") or {}
    fq = [ints(q.get("combo")) for q in (cp.get("finalQuinellas") or []) if isinstance(q, dict)]
    fq = [q for q in fq if len(q) == 2]
    ft = [ints(t.get("combo")) for t in (cp.get("finalTrifectas") or []) if isinstance(t, dict)]
    ft = [t for t in ft if len(t) == 3]
    if not fq or not ft:
        continue
    r = d.get("result") or {}
    tri = (r.get("payouts") or {}).get("trifecta")
    if not (r.get("1st") and r.get("2nd") and r.get("3rd")) or not tri or r.get("payouts_approx"):
        continue
    try:
        tri = float(tri)
    except (TypeError, ValueError):
        continue
    w3 = {int(r["1st"]), int(r["2nd"]), int(r["3rd"])}
    pair = set(fq[0])
    main = ft[:2]
    changed = not any(pair <= set(t) for t in main)
    new = main
    ins = drop = None
    if changed:
        t3 = third_of(cp, pair, main[0])
        if t3 is not None:
            ins = sorted(pair | {t3})
            drop = main[1] if len(main) > 1 else None
            new = [ins, main[0]]
        else:
            changed = False
    hit = lambda ms: next((tri for m in ms if set(m) == w3), 0.0)
    rows.append({"date": os.path.basename(p)[:10], "sport": "경륜" if d.get("sport") == "cycle" else "경마",
                 "changed": changed, "old": hit(main), "new": hit(new), "n_old": len(main), "n_new": len(new),
                 "ins_hit": (tri if ins and set(ins) == w3 else 0.0), "drop_hit": (tri if drop and set(drop) == w3 else 0.0)})


def summ(rs, key, nkey):
    stake = sum(x[nkey] for x in rs)
    pays = sorted([x[key] for x in rs if x[key] > 0], reverse=True)
    t = sum(pays)
    return len(pays), (100.0 * t / stake if stake else 0), (100.0 * (t - sum(pays[:3])) / max(1, stake - 3) if stake else 0), stake


def split3(rs):
    ds = sorted(set(x["date"] for x in rs))
    if len(ds) < 3:
        return [rs, [], []]
    a, b = ds[len(ds) // 3], ds[2 * len(ds) // 3]
    return [[x for x in rs if x["date"] < a], [x for x in rs if a <= x["date"] < b], [x for x in rs if x["date"] >= b]]


print("표본 %d경주 (삼복승 확정배당 · 근사 제외) · 구좌=조합 1 · 판정 삼복승=앞 2개" % len(rows))
for sp in ("경륜", "경마"):
    rs = [x for x in rows if x["sport"] == sp]
    if not rs:
        continue
    ch = [x for x in rs if x["changed"]]
    print("\n[%s] %d경주 · 규칙 발동 %d (%.1f%%)" % (sp, len(rs), len(ch), 100.0 * len(ch) / len(rs)))
    for lab, k, nk in (("현행 메인 2", "old", "n_old"), ("동기화 후 메인 2", "new", "n_new")):
        h, ret, ex3, st = summ(rs, k, nk)
        print("   %-14s 전체  적중 %4d  회수 %6.1f  3제외 %6.1f  (구좌 %d)" % (lab, h, ret, ex3, st))
    ins_h = sum(1 for x in ch if x["ins_hit"] > 0); ins_p = sum(x["ins_hit"] for x in ch)
    dr_h = sum(1 for x in ch if x["drop_hit"] > 0); dr_p = sum(x["drop_hit"] for x in ch)
    print("   바뀐 자리만(발동 %d경주 · 각 1구좌): 들어온 동기화 조합 적중 %d · 배당합 %.1f  ↔  빠진 원래 메인2 적중 %d · 배당합 %.1f" % (len(ch), ins_h, ins_p, dr_h, dr_p))
    print("   → 순증 적중 %+d · 순증 배당 %+.1f구좌" % (ins_h - dr_h, ins_p - dr_p))
    parts = split3(ch)
    print("   3분할(바뀐 자리 순증 배당): %s" % " / ".join("%+.1f(%d경주)" % (sum(x["ins_hit"] for x in pt) - sum(x["drop_hit"] for x in pt), len(pt)) for pt in parts))
