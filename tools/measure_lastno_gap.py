import json, io, glob, os, collections, re

# ── 2·3번 이미 처리됐는지 확인 ──
s = io.open("app.py", encoding="utf-8").read()
print("== 2·3번 배선 상태 확인 ==")
for nm, pat in (("2번 실적화면 구좌", "SCOREBOARD_SEAT_COUNT"),
                ("3번 매트릭스 교정", "MATRIX_SHRINK_GUARD"),
                ("3번 신호 종/건", "신호 %d종(%d건)")):
    print("   %-18s %s" % (nm, "🟢 배선됨" if pat in s else "🔴 없음"))
aj = io.open("static/js/app.js", encoding="utf-8").read()
print("   %-18s %s" % ("3번 흐름약함 라벨",
                       "🟢 배선됨" if "🔻 흐름 약함" in aj else "🔴 없음"))

# ── 1번: 마지막 마번 누락 8월 전수 ──
print()
print("== 1번 · 마지막 마번 누락 (8월 전수) ==")


def nos_of(q):
    out = set()
    if isinstance(q, dict):
        for k in q:
            for x in re.split(r"[^0-9]+", str(k)):
                if x:
                    out.add(int(x))
    elif isinstance(q, list):
        for e in q:
            for x in (e.get("combo") or []):
                try:
                    out.add(int(x))
                except (TypeError, ValueError):
                    pass
    return out


# 기준 두수: analysis_log 의 raceHorseCount / rosterNos / horses 수
meta = {}
for p in glob.glob("data/analysis_log/2026_08_*.json"):
    try:
        d = json.load(io.open(p, encoding="utf-8"))
    except Exception:
        continue
    cp = d.get("corePicks") or {}
    ros = cp.get("rosterNos") or []
    hs = d.get("horses") or []
    meta[os.path.basename(p)] = {
        "cnt": cp.get("raceHorseCount"),
        "roster": sorted(int(x) for x in ros if str(x).isdigit()),
        "hn": sorted(int(h["no"]) for h in hs if h.get("no") is not None),
        "sport": "%s/%s" % (d.get("sport"), d.get("category")),
    }

bad = []
by_src = collections.Counter()
tot = 0
for p in sorted(glob.glob("data/odds_history/2026_08_*.json")):
    b = os.path.basename(p)
    mm = meta.get(b)
    if not mm:
        continue
    ref = mm["roster"] or mm["hn"]
    if len(ref) < 3:
        continue
    try:
        d = json.load(io.open(p, encoding="utf-8"))
    except Exception:
        continue
    sn = [x for x in (d.get("snapshots") or []) if x.get("quinella")]
    if not sn:
        continue
    tot += 1
    last = sn[-1]
    got = nos_of(last.get("quinella"))
    if not got:
        continue
    miss = sorted(set(ref) - got)
    # 🔴 "마지막 하나만" 유형 = 빠진 것이 최대 마번 1개
    if len(miss) == 1 and miss[0] == max(ref):
        src = str(last.get("src") or "?")
        fam = ("oddspark" if ("oddspark" in src or "netkeiba" in src)
               else ("private" if "private" in src else "?"))
        by_src[fam] += 1
        bad.append((b[:-5], len(ref), miss[0], fam, mm["sport"]))

print("   대상(명단+배당 보유): %d경주" % tot)
print("   🔴 마지막 마번 하나만 빠진 경주: %d (%.1f%%)"
      % (len(bad), len(bad) / max(1, tot) * 100))
print("   소스별:", dict(by_src))
print()
sp = collections.Counter(x[4] for x in bad)
print("   종목별:", dict(sp))
v = collections.Counter(x[0][11:].rsplit("_", 1)[0] for x in bad)
print("   경기장 상위:", dict(v.most_common(8)))
print()
print("   표본:")
for x in bad[:12]:
    print("     %-28s %2d두인데 %2d번 없음 · %s · %s" % (x[0], x[1], x[2], x[3], x[4]))
