# -*- coding: utf-8 -*-
"""경주 선택(관망) 측정 — 「어느 말」이 아니라 「어느 경주를 안 사는가」로 시장을 이기는가.

대표(2026-09-25): 「터지는 경주만 예상하더라도 엄청 유리한 거야. 저배당은 안 사는 게 시장을 이기는 거니까」
논리: 공제 25% 라 아무 경주나 사면 ~75% 로 수렴. 경주마다 우리 회수율이 다르면 낮은 경주를 안 사는 것만으로 전체가 오른다.

원칙
  · 원칙 27: 선택 재료는 **발주 전에 있던 값만** — 마감 5분 이상 전 틱의 최저 복승배당(시장 집중도) · 두수 · 선행형 수 · 종목
  · 원칙 14·30: 「터진 경주」를 결과로 고르지 않는다. 같은 수의 경주를 무작위로 고른 분포(300회)와 대조한다
  · 원칙 26: 표본 = analysis_log 결과+확정배당(근사 제외 · 한국 제외) · 구좌=판정 명단 조합 1 · 정제 없음 · 3분할 = 날짜 3등분
  · 판정선 74.5 는 무리 크기의 함수(2026-09-22) — 같은 크기 무작위 무리의 3제외 중앙값·95%ile 을 함께 낸다
사용  python tools/measure_race_select.py [2026_0*]
"""
import os, io, sys, json, glob, gzip, random, statistics as st, collections

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAT = sys.argv[1] if len(sys.argv) > 1 else "2026_0*"
SEEDS = 300


def load(p):
    """⚠ odds_history 는 대부분 .json.gz 다(9,594 중 8,580) — .json 만 읽으면 표본이 9.6% 로 준다(원칙 8-E · 2026-09-25 실측)"""
    try:
        if os.path.exists(p):
            return json.load(io.open(p, encoding="utf-8"))
        if os.path.exists(p + ".gz"):
            with gzip.open(p + ".gz", "rt", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        return None
    return None


def pre_min_odds(fn):
    """마감 5분 이상 전 마지막 틱의 최저 복승배당 (발주 전 재료)."""
    d = load(os.path.join(BASE, "data", "odds_history", fn)) or {}
    best = None
    for s in d.get("snapshots") or []:
        mb = s.get("minutes_before")
        q = s.get("quinella")
        if mb is None or mb < 5 or not q:
            continue
        vals = []
        items = q.items() if isinstance(q, dict) else [(None, x.get("odds")) for x in q]
        for _, o in items:
            try:
                o = float(o)
            except (TypeError, ValueError):
                continue
            if o > 1.0:
                vals.append(o)
        if vals:
            best = min(vals)          # 뒤 틱이 앞 틱을 덮는다 = T-5 직전 값
    return best


rows = []
for p in sorted(glob.glob(os.path.join(BASE, "data", "analysis_log", PAT + "_*.json"))):
    d = load(p)
    if not d:
        continue
    fn = os.path.basename(p)
    if (d.get("category") or "") == "korea":
        continue
    r = d.get("result") or {}
    po = r.get("payouts") or {}
    q = po.get("quinella")
    if r.get("1st") is None or r.get("2nd") is None or not q or r.get("payouts_approx"):
        continue
    cp = d.get("corePicks") or {}
    dc = cp.get("displayedCombos")
    ql = (dc or {}).get("quinellas") if isinstance(dc, dict) else dc
    if not ql:
        continue
    combos = [tuple(sorted(int(x) for x in (c if isinstance(c, list) else c.get("combo") or []))) for c in ql]
    combos = [c for c in combos if len(c) == 2]
    if not combos:
        continue
    win = tuple(sorted((int(r["1st"]), int(r["2nd"]))))
    mo = pre_min_odds(fn)
    hs = d.get("horses") or []
    lead = sum(1 for h in hs if isinstance(h, dict) and str(h.get("styleType") or h.get("gait") or "").startswith(("선행", "逃")))
    rows.append({
        "fn": fn, "date": fn[:10], "sport": "경륜" if d.get("sport") == "cycle" else "경마",
        "n_combo": len(combos), "hit": win in combos, "pay": float(q) if win in combos else 0.0,
        "min_odds": mo, "n_horse": len(cp.get("rosterNos") or hs), "lead": lead,
    })

print("표본 %d경주 (결과+확정배당 · 근사·한국 제외 · 명단 보유) · 발주 전 최저배당 보유 %d" % (len(rows), sum(1 for x in rows if x["min_odds"])))


def score(rs):
    """구좌=조합 1 · 회수 / 대박 3제외 · 적중 수"""
    if not rs:
        return None
    stake = sum(x["n_combo"] for x in rs)
    pays = sorted([x["pay"] for x in rs if x["hit"]], reverse=True)
    tot = sum(pays)
    ex3 = tot - sum(pays[:3])
    return {"races": len(rs), "stake": stake, "hits": len(pays), "ret": 100.0 * tot / stake, "ex3": 100.0 * ex3 / max(1, stake - 3)}


def fmt(s):
    return "경주 %5d 구좌 %6d 적중 %4d 회수 %6.1f 3제외 %6.1f" % (s["races"], s["stake"], s["hits"], s["ret"], s["ex3"]) if s else "-"


def split3(rs):
    ds = sorted(set(x["date"] for x in rs))
    if len(ds) < 3:
        return [None, None, None]
    cut = [ds[len(ds) // 3], ds[2 * len(ds) // 3]]
    parts = [[x for x in rs if x["date"] < cut[0]], [x for x in rs if cut[0] <= x["date"] < cut[1]], [x for x in rs if x["date"] >= cut[1]]]
    return [score(p) for p in parts]


def random_ctrl(pool, k):
    vals = []
    for s in range(SEEDS):
        random.seed(s)
        vals.append(score(random.sample(pool, k))["ex3"])
    vals.sort()
    return vals[len(vals) // 2], vals[int(len(vals) * 0.95)], vals


for sport in ("경마", "경륜"):
    pool = [x for x in rows if x["sport"] == sport and x["min_odds"]]
    if len(pool) < 100:
        continue
    base = score(pool)
    print("\n" + "=" * 78)
    print("[%s] 전체(=아무 경주나 산다)   %s" % (sport, fmt(base)))
    print("   3분할 3제외: %s" % " / ".join("%.1f" % s["ex3"] if s else "-" for s in split3(pool)))
    # 발주 전 재료별 구간
    bands = [
        ("최저복승 <3배 (시장이 확신)", lambda x: x["min_odds"] < 3),
        ("최저복승 3~6배", lambda x: 3 <= x["min_odds"] < 6),
        ("최저복승 6~10배", lambda x: 6 <= x["min_odds"] < 10),
        ("최저복승 10배+ (시장 흩어짐)", lambda x: x["min_odds"] >= 10),
        ("두수 ≤7", lambda x: x["n_horse"] <= 7),
        ("두수 8~10", lambda x: 8 <= x["n_horse"] <= 10),
        ("두수 11+", lambda x: x["n_horse"] >= 11),
        ("선행형 ≤2", lambda x: x["lead"] <= 2),
        ("선행형 3", lambda x: x["lead"] == 3),
        ("선행형 4+ (난전)", lambda x: x["lead"] >= 4),
    ]
    print("   %-30s %s   3분할          무작위 같은크기(중앙/95%%)" % ("발주 전 재료 구간", "경주  구좌  적중  회수  3제외"))
    for name, f in bands:
        sub = [x for x in pool if f(x)]
        if len(sub) < 30:
            continue
        s = score(sub)
        med, p95, _ = random_ctrl(pool, len(sub))
        s3 = split3(sub)
        flag = "🟢" if (s["ex3"] > p95 and all(v and v["ex3"] > base["ex3"] for v in s3)) else ("🟡" if s["ex3"] > med else "  ")
        print("   %s %-28s %5d %6d %4d %6.1f %6.1f   %s   %5.1f / %5.1f" % (
            flag, name, s["races"], s["stake"], s["hits"], s["ret"], s["ex3"],
            "/".join("%5.1f" % v["ex3"] if v else "  -  " for v in s3), med, p95))
    # 관망 정책: 시장이 확신하는 경주(<3배)를 안 산다
    for th in (3, 4, 6):
        keep = [x for x in pool if x["min_odds"] >= th]
        s = score(keep)
        med, p95, _ = random_ctrl(pool, len(keep))
        s3 = split3(keep)
        print("   관망 정책: 최저복승 <%d배 경주는 안 산다 → %s   3분할 %s   무작위 %.1f/%.1f  (안 사는 경주 %d = %.0f%%)" % (
            th, fmt(s), "/".join("%.1f" % v["ex3"] if v else "-" for v in s3), med, p95, len(pool) - len(keep), 100.0 * (len(pool) - len(keep)) / len(pool)))
print("\n🟢 = 3제외가 같은 크기 무작위 95%ile 위 + 3분할 세 구간 모두 전체보다 위   🟡 = 무작위 중앙 위(판정 아님)")
