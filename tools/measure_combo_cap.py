"""조합 수가 배당보다 많으면 적중이 아니다 — 소급 측정 (읽기 전용 · 배선 없음).

🔴 대표 산수: 균등 매수면 필요 배당 = 조합 수. 6조합에 2.3배 적중은 회수 38%다.
  화면에는 적중이라 뜨지만 회원은 62% 를 잃었다.
"""
import json, io, glob, sys, os, collections

sys.path.insert(0, os.path.join(os.getcwd(), "tools"))
import measure_recovery as MR   # noqa: E402

MIN_HITS = getattr(MR, "MIN_HITS", 30)
rows = []

for p in sorted(glob.glob("data/analysis_log/2026_08_*.json")):
    try:
        d = json.load(io.open(p, encoding="utf-8"))
    except Exception:
        continue
    r = d.get("result") or {}
    payq = (r.get("payouts") or {}).get("quinella")
    if payq is None:
        continue
    try:
        top2 = {int(r["1st"]), int(r["2nd"])}
    except (KeyError, TypeError, ValueError):
        continue
    cp = d.get("corePicks") or {}
    dc = [tuple(sorted(int(x) for x in c))
          for c in ((cp.get("displayedCombos") or {}).get("quinellas") or [])]
    if not dc:
        continue
    om = {}
    for q in (cp.get("finalQuinellas") or []):
        try:
            om[tuple(sorted(int(x) for x in (q.get("combo") or [])))] = float(q.get("odds"))
        except (TypeError, ValueError):
            pass
    odds = [om.get(c) for c in dc if om.get(c)]
    rows.append({
        "rk": d.get("raceKey"), "n": len(dc), "hit": any(set(c) == top2 for c in dc),
        "pay": float(payq), "minOdds": (min(odds) if odds else None),
        "sport": d.get("sport"), "grade": (cp.get("raceGrade") or {}).get("tier"),
        "combos": dc, "om": om,
    })

hits = [x for x in rows if x["hit"]]
print("== 작업1 · 적중 판정된 경주에서 배당 ÷ 조합 수 ==")
print("   대상 %d경주 · 적중 %d경주" % (len(rows), len(hits)))
print()
loss = [x for x in hits if x["pay"] / x["n"] * 100 < 100]
print("   🔴 적중인데 **회수 100%% 미만**: %d / %d = **%.1f%%**"
      % (len(loss), len(hits), len(loss) / max(1, len(hits)) * 100))
print("   ⚠ 화면에는 전부 '적중'으로 뜨지만 이만큼은 손해다")
print()
b = collections.Counter()
for x in hits:
    v = x["pay"] / x["n"] * 100
    b["~50%" if v < 50 else "50~100%" if v < 100 else
      "100~200%" if v < 200 else "200%+"] += 1
print("   적중 경주의 실제 회수 분포:")
for k in ("~50%", "50~100%", "100~200%", "200%+"):
    print("     %-10s %4d (%.1f%%)" % (k, b[k], b[k] / max(1, len(hits)) * 100))

print()
print("   조합 수별 (적중 경주만):")
g = collections.defaultdict(list)
for x in hits:
    g[x["n"] if x["n"] <= 5 else 6].append(x)
for k in sorted(g):
    arr = g[k]
    lo = sum(1 for x in arr if x["pay"] / x["n"] * 100 < 100)
    med = sorted(x["pay"] for x in arr)[len(arr) // 2]
    print("     %s조합 적중 %4d · 손해 %4d (%.1f%%) · 배당중앙 %5.1f · 본전선 %d배"
          % ("6+" if k == 6 else k, len(arr), lo, lo / max(1, len(arr)) * 100, med, k))

print()
for title, keyfn in (("종목별", lambda x: x["sport"]),
                     ("등급별", lambda x: x["grade"] or "(없음)")):
    print("   %s (적중 경주 중 손해 비율):" % title)
    g2 = collections.defaultdict(list)
    for x in hits:
        g2[keyfn(x)].append(x)
    for k, arr in sorted(g2.items(), key=lambda kv: -len(kv[1])):
        lo = sum(1 for x in arr if x["pay"] / x["n"] * 100 < 100)
        print("     %-16s 적중 %4d · 손해 %4d (%5.1f%%)%s"
              % (k, len(arr), lo, lo / max(1, len(arr)) * 100,
                 "" if len(arr) >= MIN_HITS else "  판정불가"))
    print()

# ── 작업2: 조합 수 상한을 배당으로 ──
print("== 작업2 · 시장 최저 배당으로 조합 수 상한 (소급) ==")


def cap_of(mn):
    if mn is None:
        return None
    return 1 if mn < 3 else 2 if mn < 6 else 3 if mn < 10 else 4


print()
print("")
rows2 = []
for p in sorted(glob.glob("data/analysis_log/2026_08_*.json")):
    try:
        d = json.load(io.open(p, encoding="utf-8"))
    except Exception:
        continue
    r = d.get("result") or {}
    payq = (r.get("payouts") or {}).get("quinella")
    if payq is None:
        continue
    try:
        top2 = tuple(sorted({int(r["1st"]), int(r["2nd"])}))
    except (KeyError, TypeError, ValueError):
        continue
    cp = d.get("corePicks") or {}
    dc = [tuple(sorted(int(x) for x in c))
          for c in ((cp.get("displayedCombos") or {}).get("quinellas") or [])]
    if not dc:
        continue
    om = {}
    for q in (cp.get("finalQuinellas") or []):
        try:
            om[tuple(sorted(int(x) for x in (q.get("combo") or [])))] = float(q.get("odds"))
        except (TypeError, ValueError):
            pass
    odds = [om.get(c) for c in dc if om.get(c)]
    rows2.append({"dc": dc, "ans": top2, "pay": float(payq),
                  "mn": (min(odds) if odds else None)})


def run2(cap_fn, label):
    seats = 0
    pays = []
    used = 0
    for x in rows2:
        cap = cap_fn(x["mn"], len(x["dc"]))
        if cap is None:
            continue
        used += 1
        cs = x["dc"][:cap]
        seats += len(cs)
        if x["ans"] in cs:
            pays.append(x["pay"])
    rr = sum(pays) / max(1, seats) * 100
    ex3 = (sum(sorted(pays)[:-3]) / max(1, seats) * 100) if len(pays) > 3 else 0.0
    lo = sum(1 for i, x in enumerate(rows2)
             if cap_fn(x["mn"], len(x["dc"])) and x["ans"] in x["dc"][:cap_fn(x["mn"], len(x["dc"]))]
             and x["pay"] / max(1, min(cap_fn(x["mn"], len(x["dc"])), len(x["dc"]))) * 100 < 100)
    print("   %-22s 경주 %4d · 구좌 %5d · 적중 %4d(%5.1f%%) · 회수율 %6.1f%% · 3제외 %6.1f%% · 손해적중 %d%s"
          % (label, used, seats, len(pays), len(pays) / max(1, used) * 100, rr, ex3, lo,
             "" if len(pays) >= MIN_HITS else "  판정불가"))


run2(lambda mn, n: n, "현행(상한 없음)")
run2(lambda mn, n: cap_of(mn), "대표안(배당별 상한)")
run2(lambda mn, n: 1, "무조건 1개")
run2(lambda mn, n: 2, "무조건 2개")
run2(lambda mn, n: 3, "무조건 3개")
