# -*- coding: utf-8 -*-
"""[읽기 전용] 집중도(상위3두 내재확률 비중)로 경주를 **선별**하면 — 판정 4단계.

🔴 복기 6일치(633경주)에서 집중도가 압도적 판별자로 나왔다(t=+10.5).
   그러나 적중배당이 5.0 → 1.4배로 떨어졌다(원칙 14).
   ⇒ 큰 표본 · 종목별 · 확정배당으로 다시 재고 **판정 4단계**를 건다.
⚠ 2026-08-27 에 같은 축을 쟀고 「기간 분할에서 무너졌다 · 소급 최적화」로 기각된 이력이 있다.
   그때는 8개 축 중 최고를 고른 것이었다. 이번엔 **하나만** 재고 기간 3분할을 함께 본다.
"""
import io, json, glob, os, gzip, collections, statistics as st, importlib.util

_sp = importlib.util.spec_from_file_location("mr", os.path.join("tools", "measure_recovery.py"))
_mr = importlib.util.module_from_spec(_sp)
_sp.loader.exec_module(_mr)
CLEAN_LO, CLEAN_HI, PAYBACK = _mr.CLEAN_LO, _mr.CLEAN_HI, _mr.PAYBACK
BAD = ("odds_suspect", "baseline_reset", "next_race_blocked")


def load(p):
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


def pair(k):
    for s in ("+", "-", "_"):
        if s in str(k):
            a, b = str(k).split(s)[:2]
            try:
                return tuple(sorted((int(a), int(b))))
            except ValueError:
                return None
    return None


def qmap(sn):
    q = (sn or {}).get("quinella")
    if isinstance(q, list):
        q = {"+".join(str(z) for z in (it.get("combo") or [])): it.get("odds")
             for it in q if isinstance(it, dict)}
    o = {}
    for k, v in (q or {}).items():
        t = pair(k)
        if not t:
            continue
        try:
            x = float(v.get("odds") if isinstance(v, dict) else v)
        except Exception:
            continue
        if x > 0:
            o[t] = x
    return o


rows = []
for f in sorted(glob.glob("data/analysis_log/2026_0*.json")):
    name = os.path.basename(f)[:-5]
    d = load(f)
    if not isinstance(d, dict):
        continue
    cp = d.get("corePicks") or {}
    judged = [tuple(sorted(int(x) for x in c))
              for c in ((cp.get("displayedCombos") or {}).get("quinellas") or [])
              if c and len(c) == 2]
    if not judged:
        continue
    rr = load(os.path.join("data", "race_results", name + ".json"))
    if not isinstance(rr, dict) or rr.get("payouts_approx"):
        continue
    r = rr.get("result") or {}
    po = (rr.get("payouts") or {}).get("quinella")
    try:
        top2 = tuple(sorted((int(r["1st"]), int(r["2nd"]))))
        po = float(po)
    except Exception:
        continue
    oh = load(os.path.join("data", "odds_history", name + ".json")) or {}
    sn = [s for s in (oh.get("snapshots") or [])
          if s.get("quinella") and not any(s.get(b) for b in BAD) and not s.get("after_close")]
    qm = qmap(sn[-1] if sn else {})
    if len(qm) < 6:
        continue
    mo = qm.get(top2)
    if not mo or not (CLEAN_LO <= po / mo <= CLEAN_HI):
        continue
    # 마별 1·2착 진입 시장암시확률 → 상위3두 비중 = 집중도
    inv = {c: 1.0 / o for c, o in qm.items()}
    s = sum(inv.values())
    P = collections.defaultdict(float)
    for (a, b), v in inv.items():
        P[a] += v / s
        P[b] += v / s
    ps = sorted(P.values(), reverse=True)
    conc = sum(ps[:3]) / 2.0                # Σ=2 이므로 2로 나눠 비중
    rows.append({"rk": name, "sport": d.get("sport"), "cat": d.get("category"),
                 "judged": judged, "top2": top2, "po": po, "conc": conc})
rows.sort(key=lambda r: r["rk"])
print("표본 %d경주 · 정제 %.1f~%.1f · 판정선 %.1f%%\n" % (len(rows), CLEAN_LO, CLEAN_HI, PAYBACK))


def _ex(v, k):
    return sum(sorted(v, reverse=True)[k:])


def calc(lbl, sub):
    if len(sub) < 30:
        print("   %-16s ⚠ n=%d < 30 판정불가" % (lbl, len(sub)))
        return
    seats = sum(len(r["judged"]) for r in sub)
    od = [r["po"] for r in sub if r["top2"] in r["judged"]]
    print("   %-16s %4d경주 구좌%5d · 적중 **%.1f%%** · 회수 **%.1f%%** · 1제외 %.1f%% · **3제외 %.1f%%** · 적중배당중앙 %5.1f배 %s"
          % (lbl, len(sub), seats, 100.0 * len(od) / len(sub),
             100.0 * sum(od) / seats, 100.0 * _ex(od, 1) / seats, 100.0 * _ex(od, 3) / seats,
             st.median(od) if od else 0,
             "🟢" if 100.0 * _ex(od, 3) / seats >= PAYBACK else "🔴"))


BK = (("~0.55", 0, 0.55), ("0.55~0.65", 0.55, 0.65), ("0.65~0.75", 0.65, 0.75), ("0.75+", 0.75, 9))
for sp, lbl in ((None, "전체"), ("cycle", "경륜"), ("horse", "경마")):
    sub0 = rows if sp is None else [r for r in rows if r["sport"] == sp]
    if len(sub0) < 100:
        continue
    print("■ %s (%d경주)" % (lbl, len(sub0)))
    calc("  현행 전체", sub0)
    for nm, lo, hi in BK:
        calc("  " + nm, [r for r in sub0 if lo <= r["conc"] < hi])
    print()

print("■ 후보: **집중도 0.65 미만 제외** — 판정 4단계")
for sp, lbl in ((None, "전체"), ("cycle", "경륜"), ("horse", "경마")):
    sub0 = rows if sp is None else [r for r in rows if r["sport"] == sp]
    if len(sub0) < 100:
        continue
    keep = [r for r in sub0 if r["conc"] >= 0.65]
    calc("  %s 유지분" % lbl, keep)
    n3 = len(keep) // 3
    for i, t in enumerate(("기간1", "기간2", "기간3")):
        calc("    %s %s" % (lbl, t), keep[i * n3:(i + 1) * n3] if i < 2 else keep[2 * n3:])
