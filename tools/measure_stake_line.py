"""적중 시 회수 임계별 소급 측정 (읽기 전용 · 배선 없음).

🔴 배경: 배분 계획의 「적중 시 회수」 = 1 / Σ(1/배당). 지금 권장선은 150%.
  대표 지적: 손익분기는 1/적중률이다. 적중률 57.8% 면 173% 가 본전이므로
  150% 는 손익분기 **아래**다.

측정: 임계(150·175·200·250) 미만 경주를 뺐을 때
  ① 남는 경주 수 ② 회수율 ③ 상위 3건 제외 ④ 적중률
규칙 상수는 measure_recovery 에서 import(원칙 15).
"""
import json, io, glob, sys, os

sys.path.insert(0, os.path.join(os.getcwd(), "tools"))
import measure_recovery as MR   # noqa: E402

PAYBACK = MR.PAYBACK
MIN_HITS = getattr(MR, "MIN_HITS", 30)
LO, HI = getattr(MR, "CLEAN_LO", 0.5), getattr(MR, "CLEAN_HI", 2.0)

rows = []
for p in sorted(glob.glob("data/analysis_log/2026_08_*.json")):
    try:
        d = json.load(io.open(p, encoding="utf-8"))
    except Exception:
        continue
    r = d.get("result") or {}
    try:
        top2 = {int(r["1st"]), int(r["2nd"])}
    except (KeyError, TypeError, ValueError):
        continue
    payq = (r.get("payouts") or {}).get("quinella")
    if payq is None:
        continue
    cp = d.get("corePicks") or {}
    dc = (cp.get("displayedCombos") or {}).get("quinellas") or []
    if not dc:
        continue
    # 조합별 배당 — finalQuinellas 에서 끌어온다
    om = {}
    for q in (cp.get("finalQuinellas") or []):
        try:
            om[tuple(sorted(int(x) for x in (q.get("combo") or [])))] = float(q.get("odds"))
        except (TypeError, ValueError):
            pass
    combos = []
    for c in dc:
        try:
            k = tuple(sorted(int(x) for x in c))
        except (TypeError, ValueError):
            continue
        if om.get(k):
            combos.append((k, om[k]))
    if not combos:
        continue
    inv = sum(1.0 / o for _, o in combos if o > 0)
    if inv <= 0:
        continue
    ret_pct = 100.0 / inv                      # 🔴 적중 시 회수(%)
    hit = any(set(k) == top2 for k, _ in combos)
    rows.append({"rk": d.get("raceKey"), "n": len(combos), "ret": ret_pct,
                 "hit": hit, "pay": float(payq), "sport": d.get("sport")})

print("판정선(환급률) %.1f%% · 최소 적중 %d건  [measure_recovery import]" % (PAYBACK, MIN_HITS))
print("대상(결과+확정배당+표시조합+배당 보유): %d경주" % len(rows))
print()

# 전체 적중률 -> 손익분기 확인
allhit = sum(1 for x in rows if x["hit"])
hr = allhit / max(1, len(rows)) * 100
print("== 손익분기 확인 ==")
print("   전체 적중률 %.1f%%  ->  손익분기 = 1/적중률 = **%.0f%%**"
      % (hr, 100.0 / max(0.0001, hr / 100)))
print("   ⚠ 대표 지적대로 지금 권장선 150%% 는 손익분기 아래다")
print()

print("== 임계별 (적중 시 회수 N%% 미만 경주 제외) ==")
print("  임계   남는경주  비율    적중  적중률   회수율   3제외   판정")
for th in (0, 150, 175, 200, 250):
    sub = [x for x in rows if x["ret"] >= th]
    if not sub:
        print("   %4d%%      0" % th)
        continue
    seats = sum(x["n"] for x in sub)
    hits = sum(1 for x in sub if x["hit"])
    pays = [x["pay"] for x in sub if x["hit"]]
    ret = sum(pays)
    rr = ret / max(1, seats) * 100
    ex3 = (sum(sorted(pays)[:-3]) / max(1, seats) * 100) if len(pays) > 3 else 0.0
    verdict = ("판정불가(적중%d)" % hits) if hits < MIN_HITS else (
        "🟢 통과" if rr >= PAYBACK else "미달")
    print("   %4d%%  %6d  %5.1f%%  %5d  %5.1f%%  %6.1f%%  %6.1f%%  %s" % (
        th if th else 0, len(sub), len(sub) / max(1, len(rows)) * 100,
        hits, hits / max(1, len(sub)) * 100, rr, ex3, verdict))

print()
print("   ⚠ 임계 0 = 현행(거르지 않음)")
print()
print("== 종목별 (임계 175%%) ==")
import collections   # noqa: E402
bysp = collections.defaultdict(list)
for x in rows:
    bysp[x["sport"]].append(x)
for sp, arr in sorted(bysp.items(), key=lambda kv: -len(kv[1])):
    sub = [x for x in arr if x["ret"] >= 175]
    if not sub:
        print("   %-10s 남는 경주 0 / %d" % (sp, len(arr)))
        continue
    seats = sum(x["n"] for x in sub)
    hits = sum(1 for x in sub if x["hit"])
    pays = [x["pay"] for x in sub if x["hit"]]
    rr = sum(pays) / max(1, seats) * 100
    print("   %-10s 남는 경주 %4d / %4d (%.1f%%) · 적중 %3d · 회수율 %6.1f%%  %s" % (
        sp, len(sub), len(arr), len(sub) / max(1, len(arr)) * 100, hits, rr,
        "판정불가" if hits < MIN_HITS else ""))
