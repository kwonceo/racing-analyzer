"""조각별 회수율·3제외 — 어디서 이기고 있는가 (읽기 전용 · 배선 없음).

🔴 판정 규칙(대표 지시):
  · **3제외 기준**으로 본다. 회수율은 대박 몇 건에 흔들린다.
  · 적중 30건 미만은 판정 불가.
  · 🔴 **3제외가 100% 를 넘는 조각만 이긴 것**이다. 74.5% 는 현행 대비 판정선이지 이긴 게 아니다.
  · 건수를 반드시 함께 적는다(잘게 자르면 우연이 나온다).
"""
import json, io, glob, sys, os, collections

sys.path.insert(0, os.path.join(os.getcwd(), "tools"))
import measure_recovery as MR   # noqa: E402

MIN_HITS = getattr(MR, "MIN_HITS", 30)
WIN_LINE = 100.0          # 🔴 이긴 것의 기준
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
    mn = min(odds) if odds else None
    rg = cp.get("raceGrade") or {}
    ss = d.get("strong_signals") or {}
    types = set(ss.get("types") or [])
    rows.append({
        "sport": d.get("sport"), "cat": d.get("category"),
        "n": len(dc), "hit": any(set(c) == top2 for c in dc), "pay": float(payq),
        "grade": rg.get("tier"), "minOdds": mn,
        "horses": cp.get("raceHorseCount") or len(d.get("horses") or []),
        "compress": bool((d.get("compression_pattern") or {}).get("detected")),
        "dia": bool(cp.get("bmedSpecial")),
        "dark": bool(cp.get("darkHorsePicks")),
        "types": types,
        "mismatch": bool((d.get("signal_quality_full") or {}).get("quinellaMismatch")),
    })

print("판정: 3제외 **%.0f%% 초과**만 이긴 것 · 적중 %d건 미만은 판정불가" % (WIN_LINE, MIN_HITS))
print("대상: %d경주" % len(rows))
print()

winners = []


def seg(title, keyfn, order=None):
    g = collections.defaultdict(list)
    for x in rows:
        k = keyfn(x)
        if k is not None:
            g[k].append(x)
    print("== %s ==" % title)
    print("   조각                경주   구좌  적중  적중률  회수율   3제외   판정")
    ks = order or sorted(g.keys(), key=lambda z: str(z))
    for k in ks:
        arr = g.get(k) or []
        if not arr:
            continue
        seats = sum(x["n"] for x in arr)
        pays = [x["pay"] for x in arr if x["hit"]]
        hits = len(pays)
        rr = sum(pays) / max(1, seats) * 100
        ex3 = (sum(sorted(pays)[:-3]) / max(1, seats) * 100) if hits > 3 else 0.0
        if hits < MIN_HITS:
            v = "판정불가(%d)" % hits
        elif ex3 > WIN_LINE:
            v = "🟢🟢 이겼다"
            winners.append((title, k, len(arr), hits, ex3))
        else:
            v = "짐"
        print("    %-18s %4d %6d %5d %5.1f%% %6.1f%% %6.1f%%  %s" % (
            str(k)[:18], len(arr), seats, hits,
            hits / max(1, len(arr)) * 100, rr, ex3, v))
    print()


seg("종목별", lambda x: "%s/%s" % (x["sport"], x["cat"]))
seg("등급별", lambda x: x["grade"] or "(없음)")
seg("조합 수별", lambda x: ("1개" if x["n"] == 1 else "2개" if x["n"] == 2 else
                         "3개" if x["n"] == 3 else "4개+"),
    order=["1개", "2개", "3개", "4개+"])
seg("최저배당대별", lambda x: (None if x["minOdds"] is None else
                          "~3배" if x["minOdds"] < 3 else
                          "3~6배" if x["minOdds"] < 6 else
                          "6~10배" if x["minOdds"] < 10 else "10배+"),
    order=["~3배", "3~6배", "6~10배", "10배+"])
seg("두수별", lambda x: (None if not x["horses"] else
                      "7두↓" if x["horses"] <= 7 else
                      "8~10두" if x["horses"] <= 10 else "11두+"),
    order=["7두↓", "8~10두", "11두+"])
seg("압축(축 명확)", lambda x: "압축O" if x["compress"] else "압축X")
seg("💎 보유", lambda x: "💎O" if x["dia"] else "💎X")
seg("복승불일치", lambda x: "불일치O" if x["mismatch"] else "불일치X")
seg("신호 종류(마감직전 대급락 t3)", lambda x: "t3O" if 3 in x["types"] else "t3X")
seg("신호 종류(복수동시급락 t5)", lambda x: "t5O" if 5 in x["types"] else "t5X")
seg("신호 종류(스마트머니 t8)", lambda x: "t8O" if 8 in x["types"] else "t8X")

# 겹쳐 자르기 — 유망 조각 조합
print("== 겹쳐 자르기 (조합 1개 × 배당대) ==")
print("   조각                경주   구좌  적중  적중률  회수율   3제외   판정")
for nk in ("1개", "2개"):
    for ok_ in ("~3배", "3~6배", "6~10배", "10배+"):
        arr = [x for x in rows
               if (("1개" if x["n"] == 1 else "2개" if x["n"] == 2 else "3개+") == nk)
               and x["minOdds"] is not None
               and (("~3배" if x["minOdds"] < 3 else "3~6배" if x["minOdds"] < 6
                     else "6~10배" if x["minOdds"] < 10 else "10배+") == ok_)]
        if not arr:
            continue
        seats = sum(x["n"] for x in arr)
        pays = [x["pay"] for x in arr if x["hit"]]
        hits = len(pays)
        rr = sum(pays) / max(1, seats) * 100
        ex3 = (sum(sorted(pays)[:-3]) / max(1, seats) * 100) if hits > 3 else 0.0
        if hits < MIN_HITS:
            v = "판정불가(%d)" % hits
        elif ex3 > WIN_LINE:
            v = "🟢🟢 이겼다"
            winners.append(("겹침", "%s×%s" % (nk, ok_), len(arr), hits, ex3))
        else:
            v = "짐"
        print("    %-18s %4d %6d %5d %5.1f%% %6.1f%% %6.1f%%  %s" % (
            "%s × %s" % (nk, ok_), len(arr), seats, hits,
            hits / max(1, len(arr)) * 100, rr, ex3, v))

print()
print("=" * 72)
if winners:
    print("🟢 3제외 100%% 를 넘는 조각: %d개" % len(winners))
    for w in winners:
        print("   [%s] %s · %d경주(전체의 %.1f%%) · 적중 %d · 3제외 %.1f%%"
              % (w[0], w[1], w[2], w[2] / max(1, len(rows)) * 100, w[3], w[4]))
else:
    print("🔴 **3제외 100%% 를 넘는 조각은 없다.**")
    print("   판정 가능한(적중 30건+) 조각 어디에서도 이기지 못한다.")
    print("   억지로 찾지 않는다 — 없다.")
