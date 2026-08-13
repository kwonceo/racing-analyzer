import json, io, glob, sys, os, collections

sys.path.insert(0, os.path.join(os.getcwd(), "tools"))
import measure_recovery as MR   # noqa: E402

MIN_HITS = getattr(MR, "MIN_HITS", 30)
B1, B2, B3 = 3.0, 6.0, 10.0


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
    if not fq:
        continue
    items = []
    for q in fq:
        try:
            c = tuple(sorted(int(x) for x in (q.get("combo") or [])))
            o = float(q.get("odds"))
        except (TypeError, ValueError):
            continue
        if len(c) != 2 or o <= 0:
            continue
        items.append({"c": c, "o": o, "t5": bool(q.get("t5Restored")),
                      "late": q.get("pickTier") == "late",
                      "ovr": "Override" in str(q.get("reason") or "")})
    if not items:
        continue
    rows.append({"it": items, "ans": ans, "pay": float(payq),
                 "mn": min(x["o"] for x in items)})

print("대상 %d경주 · 판정선: 3제외 · 적중 %d건 미만 판정불가" % (len(rows), MIN_HITS))
print()
print("== 작업3 · T-5 확정을 상한보다 우선하는 안 ==")
print("  안                        경주   구좌  적중  적중률  회수율   3제외")


def run(order, label):
    seats = 0
    pays = []
    for x in rows:
        cap = cap_of(x["mn"])
        cs = [i["c"] for i in order(x["it"])][:cap]
        seats += len(cs)
        if x["ans"] in cs:
            pays.append(x["pay"])
    rr = sum(pays) / max(1, seats) * 100
    ex3 = (sum(sorted(pays)[:-3]) / max(1, seats) * 100) if len(pays) > 3 else 0.0
    print("   %-24s %5d %6d %5d %6.1f%% %6.1f%% %6.1f%%" % (
        label, len(rows), seats, len(pays), len(pays) / max(1, len(rows)) * 100, rr, ex3))


run(lambda it: it, "현행 순서 그대로")
run(lambda it: sorted(it, key=lambda z: (not z["t5"],)), "🔴 T-5 복원 우선")
run(lambda it: sorted(it, key=lambda z: (z["ovr"],)), "Override 뒤로")
run(lambda it: sorted(it, key=lambda z: (not z["t5"], z["ovr"])), "T-5 우선 + Override 뒤로")
run(lambda it: sorted(it, key=lambda z: z["o"]), "배당 낮은 순")
run(lambda it: sorted(it, key=lambda z: -z["o"]), "배당 높은 순")

print()
print("== 작업4 · Override 표본과 성적 ==")
ov_n = ov_hit = 0
ov_pays = []
t5_n = t5_hit = 0
t5_pays = []
for x in rows:
    for i in x["it"]:
        if i["ovr"]:
            ov_n += 1
            if i["c"] == x["ans"]:
                ov_hit += 1
                ov_pays.append(x["pay"])
        if i["t5"]:
            t5_n += 1
            if i["c"] == x["ans"]:
                t5_hit += 1
                t5_pays.append(x["pay"])


def show(nm, n, hit, pays):
    rr = sum(pays) / max(1, n) * 100
    ex3 = (sum(sorted(pays)[:-3]) / max(1, n) * 100) if len(pays) > 3 else 0.0
    print("   %-16s 조합 %5d · 적중 %4d (%5.1f%%) · 회수율 %6.1f%% · 3제외 %6.1f%%  %s"
          % (nm, n, hit, hit / max(1, n) * 100, rr, ex3,
             "🔴 판정불가(%d)" % hit if hit < MIN_HITS else ""))


show("막판급락 Override", ov_n, ov_hit, ov_pays)
show("T-5 복원", t5_n, t5_hit, t5_pays)
allc = sum(len(x["it"]) for x in rows)
allh = sum(1 for x in rows for i in x["it"] if i["c"] == x["ans"])
allp = [x["pay"] for x in rows for i in x["it"] if i["c"] == x["ans"]]
show("전체(대조군)", allc, allh, allp)
