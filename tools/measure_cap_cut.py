"""조합 수 상한이 자른 조합 중 정답이 몇 %인가 (읽기 전용 · 배선 없음).

🔴 아침 측정의 구멍: **남은 조합만** 봤고 잘린 쪽을 안 셌다(대표 지적).
  소노다 1R 정답 7+10(13.5배)이 상한으로 잘렸다.
"""
import json, io, glob, sys, os, collections

sys.path.insert(0, os.path.join(os.getcwd(), "tools"))
import measure_recovery as MR   # noqa: E402

MIN_HITS = getattr(MR, "MIN_HITS", 30)
B1, B2, B3 = 3.0, 6.0, 10.0


def band(mn):
    return ("~3배" if mn < B1 else "3~6배" if mn < B2
            else "6~10배" if mn < B3 else "10배+")


def cap_of(mn):
    return 1 if mn < B1 else 2 if mn < B2 else 3 if mn < B3 else 4


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
        ans = tuple(sorted({int(r["1st"]), int(r["2nd"])}))
    except (KeyError, TypeError, ValueError):
        continue
    cp = d.get("corePicks") or {}
    fq = cp.get("finalQuinellas") or []
    dc = [tuple(sorted(int(x) for x in c))
          for c in ((cp.get("displayedCombos") or {}).get("quinellas") or [])]
    if not dc:
        continue
    om = {}
    for q in fq:
        try:
            om[tuple(sorted(int(x) for x in (q.get("combo") or [])))] = float(q.get("odds"))
        except (TypeError, ValueError):
            pass
    odds = [om.get(c) for c in dc if om.get(c)]
    if not odds:
        continue
    mn = min(odds)
    rows.append({"dc": dc, "ans": ans, "pay": float(payq), "mn": mn,
                 "sport": d.get("sport"), "rk": d.get("raceKey")})

print("판정선: 3제외 · 적중 %d건 미만은 판정불가" % MIN_HITS)
print("대상 %d경주" % len(rows))
print()
print("== 작업1 · 상한이 자른 조합 중 정답이 있었나 ==")
print("  배당대     경주   잘린조합  🔴잘린것이정답  비율    남은것이정답")
tot_cut = tot_cut_hit = 0
for bd in ("~3배", "3~6배", "6~10배", "10배+"):
    arr = [x for x in rows if band(x["mn"]) == bd]
    if not arr:
        continue
    cut = cuthit = keephit = 0
    for x in arr:
        cap = cap_of(x["mn"])
        kept, dropped = x["dc"][:cap], x["dc"][cap:]
        cut += len(dropped)
        if x["ans"] in dropped:
            cuthit += 1
        if x["ans"] in kept:
            keephit += 1
    tot_cut += cut
    tot_cut_hit += cuthit
    print("   %-8s %5d %8d %10d %7.1f%% %10d" % (
        bd, len(arr), cut, cuthit, cuthit / max(1, len(arr)) * 100, keephit))
print("   %-8s %5d %8d %10d %7.1f%%" % (
    "합계", len(rows), tot_cut, tot_cut_hit, tot_cut_hit / max(1, len(rows)) * 100))

print()
print("== 작업2 · 3배 미만: 1개로 줄이기 ↔ 아예 안 사기 ==")
print("  안                     경주   구좌  적중  적중률  회수율   3제외   판정")


def run(fn, label):
    seats = 0
    pays = []
    used = 0
    for x in rows:
        cap = fn(x)
        if cap is None or cap <= 0:
            continue
        used += 1
        cs = x["dc"][:cap]
        seats += len(cs)
        if x["ans"] in cs:
            pays.append(x["pay"])
    rr = sum(pays) / max(1, seats) * 100
    ex3 = (sum(sorted(pays)[:-3]) / max(1, seats) * 100) if len(pays) > 3 else 0.0
    print("   %-20s %5d %6d %5d %6.1f%% %6.1f%% %6.1f%%  %s" % (
        label, used, seats, len(pays), len(pays) / max(1, used) * 100, rr, ex3,
        "" if len(pays) >= MIN_HITS else "판정불가(%d)" % len(pays)))


run(lambda x: len(x["dc"]), "현행(상한 없음)")
run(lambda x: cap_of(x["mn"]), "상한(3배미만 1개)")
run(lambda x: (None if x["mn"] < B1 else cap_of(x["mn"])), "3배미만 **안 사기**")
run(lambda x: (2 if x["mn"] < B1 else cap_of(x["mn"])), "3배미만 2개로 완화")

print()
print("== 3배 미만 구간만 따로 ==")
sub = [x for x in rows if x["mn"] < B1]
for cap, lbl in ((1, "1개"), (2, "2개"), (3, "3개"), (99, "전부")):
    seats = 0
    pays = []
    for x in sub:
        cs = x["dc"][:cap]
        seats += len(cs)
        if x["ans"] in cs:
            pays.append(x["pay"])
    rr = sum(pays) / max(1, seats) * 100
    ex3 = (sum(sorted(pays)[:-3]) / max(1, seats) * 100) if len(pays) > 3 else 0.0
    print("   %-6s 경주 %4d · 구좌 %5d · 적중 %4d(%5.1f%%) · 회수율 %6.1f%% · 3제외 %6.1f%%"
          % (lbl, len(sub), seats, len(pays), len(pays) / max(1, len(sub)) * 100, rr, ex3))

print()
print("== 작업3 · 상한 두 자리를 무엇이 차지하나 (reason 분포) ==")
cnt = collections.Counter()
cut_cnt = collections.Counter()
for p in sorted(glob.glob("data/analysis_log/2026_08_*.json")):
    try:
        d = json.load(io.open(p, encoding="utf-8"))
    except Exception:
        continue
    cp = d.get("corePicks") or {}
    fq = cp.get("finalQuinellas") or []
    if len(fq) < 2:
        continue
    om = {}
    for q in fq:
        try:
            om[tuple(sorted(int(x) for x in (q.get("combo") or [])))] = float(q.get("odds"))
        except (TypeError, ValueError):
            pass
    if not om:
        continue
    mn = min(om.values())
    cap = cap_of(mn)
    for i, q in enumerate(fq):
        tag = str(q.get("reason") or "")[:24]
        if q.get("pickTier") == "late":
            tag = "[마감신호] " + tag
        if q.get("t5Restored"):
            tag = "[T5복원] " + tag
        (cnt if i < cap else cut_cnt)[tag] += 1
print("   남는 자리 상위:")
for k, v in cnt.most_common(10):
    print("     %-34s %d" % (k, v))
print("   🔴 잘리는 쪽 상위:")
for k, v in cut_cnt.most_common(10):
    print("     %-34s %d" % (k, v))
