import json, io, glob, sys, os, collections

sys.path.insert(0, os.path.join(os.getcwd(), "tools"))
import measure_recovery as MR   # noqa: E402

PAYBACK = MR.PAYBACK
MIN_HITS = getattr(MR, "MIN_HITS", 30)

# ① 계수기 현황
g = json.load(io.open("data/_gate_hits.json", encoding="utf-8"))
print("== ① 💎 판정 편입 계수기 (2026-08-11 배선분) ==")
for k in ("judge_extra_dark", "judge_extra_linePair", "dia_judge_reach"):
    v = g.get(k)
    print("   %-22s %s" % (k, ("도달 %s · 발동 %s · last=%s" % (
        v.get("reach"), v.get("fire"),
        json.dumps(v.get("last"), ensure_ascii=False)[:70])) if isinstance(v, dict) else "없음"))

# ② 대표가 든 3경주
print()
print("== ② 대표가 든 3경주 실물 ==")
for pat in ("*기후_4경주*", "*우라와_10경주*", "*몬베츠_10경주*"):
    for p in sorted(glob.glob("data/analysis_log/2026_08_1[12]_" + pat.strip("*")))[-1:]:
        d = json.load(io.open(p, encoding="utf-8"))
        cp = d.get("corePicks") or {}
        r = d.get("result") or {}
        dc = (cp.get("displayedCombos") or {})
        bm = cp.get("bmedSpecial") or []
        try:
            top2 = {int(r["1st"]), int(r["2nd"])}
        except (KeyError, TypeError, ValueError):
            top2 = set()
        print("   %s · 착순 %s%s · 복승배당 %s" % (
            os.path.basename(p)[:-5], [r.get("1st"), r.get("2nd"), r.get("3rd")],
            "", (r.get("payouts") or {}).get("quinella")))
        print("      💎: %s" % json.dumps(
            [{"c": x.get("combo"), "o": x.get("odds")} for x in bm], ensure_ascii=False)[:150])
        print("      판정명단: %s · extra: %s" % (
            dc.get("quinellas"), json.dumps(dc.get("extra"), ensure_ascii=False)[:80]))
        hit_dia = any(set(int(v) for v in (x.get("combo") or [])) == top2 for x in bm)
        in_dc = any(set(c) == top2 for c in (dc.get("quinellas") or []))
        print("      -> 💎가 정답인가 %s · 판정명단에 있나 %s" % (hit_dia, in_dc))

# ③ 소급: 💎 상위1을 판정에 더했을 때
print()
print("== ③ 소급 · 💎 상위 1개를 판정 명단에 **더했을** 때 ==")
cur = {"seats": 0, "hits": 0, "pays": []}
add = {"seats": 0, "hits": 0, "pays": []}
dia_only = {"seats": 0, "hits": 0, "pays": []}
fired = dup = 0
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
    for c in dc:
        cur["seats"] += 1
        if set(c) == top2:
            cur["hits"] += 1
            cur["pays"].append(float(payq))
    combos = list(dc)
    bm = cp.get("bmedSpecial") or []
    d1 = None
    for x in bm:
        try:
            k = tuple(sorted(int(v) for v in (x.get("combo") or [])))
        except (TypeError, ValueError):
            continue
        if len(k) == 2:
            d1 = k
            break
    if d1:
        fired += 1
        if d1 in combos:
            dup += 1
        else:
            combos.append(d1)
            dia_only["seats"] += 1
            if set(d1) == top2:
                dia_only["hits"] += 1
                dia_only["pays"].append(float(payq))
    for c in combos:
        add["seats"] += 1
        if set(c) == top2:
            add["hits"] += 1
            add["pays"].append(float(payq))


def show(nm, a):
    rr = sum(a["pays"]) / max(1, a["seats"]) * 100
    ex3 = (sum(sorted(a["pays"])[:-3]) / max(1, a["seats"]) * 100) if len(a["pays"]) > 3 else 0.0
    med = sorted(a["pays"])[len(a["pays"]) // 2] if a["pays"] else 0
    v = ("판정불가(적중%d)" % a["hits"]) if a["hits"] < MIN_HITS else (
        "🟢 통과" if rr >= PAYBACK else "미달")
    print("   %-14s 구좌 %5d · 적중 %4d · 회수율 %6.1f%% · 3제외 %6.1f%% · 배당중앙 %5.1f  %s"
          % (nm, a["seats"], a["hits"], rr, ex3, med, v))


show("현행", cur)
show("현행+💎1", add)
show("💎 추가분만", dia_only)
print()
print("   💎 보유 경주 %d · 그중 이미 판정에 있던 것 %d (%.1f%%) · 새로 더해진 것 %d"
      % (fired, dup, dup / max(1, fired) * 100, fired - dup))
if dia_only["seats"]:
    print("   🔴 한계 회수율(추가분) = %.1f%%"
          % (sum(dia_only["pays"]) / dia_only["seats"] * 100))
