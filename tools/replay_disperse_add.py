import json, glob, os, gzip, random, re, collections
ROOT = r"C:\Users\Administrator\Desktop\경마분석서버\data"
def load(p):
    if os.path.exists(p):
        return json.load(open(p, encoding="utf-8"))
    if os.path.exists(p + ".gz"):
        return json.load(gzip.open(p + ".gz", "rt", encoding="utf-8"))
    return None
def hm(t):
    try: h, m, *s = [int(x) for x in str(t).split(":")]; return h*3600 + m*60 + (s[0] if s else 0)
    except Exception: return None
races = []; nosnap = 0; same = 0; cand_n = []
for f in sorted(glob.glob(os.path.join(ROOT, "analysis_log", "2026_*.json"))):
    try: d = json.load(open(f, encoding="utf-8"))
    except Exception: continue
    if d.get("sport") != "horse" or d.get("category") == "korea": continue
    dc = (d.get("corePicks") or {}).get("displayedCombos") or {}
    da = dc.get("disperseAdd")
    if not da: continue
    r = d.get("result") or {}
    try: win = tuple(sorted((int(r["1st"]), int(r["2nd"]))))
    except Exception: continue
    pay = (r.get("payouts") or {}).get("quinella")
    if not pay: continue
    L = {tuple(sorted(int(x) for x in c)) for c in (dc.get("quinellas") or [])}
    dac = tuple(sorted(int(x) for x in da[0]))
    oh = load(os.path.join(ROOT, "odds_history", os.path.basename(f)))
    snaps = (oh or {}).get("archive_snapshots") or (oh or {}).get("snapshots") or []
    at = hm(dc.get("at"))
    pre = [s for s in snaps if s.get("quinella") and (at is None or (hm(s.get("time")) or 0) <= at)]
    if not pre: nosnap += 1; continue
    q = pre[-1]["quinella"]
    band = []
    for k, v in q.items():
        try:
            a, b = [int(x) for x in re.split(r"[+\-]", k)]; o = float(v)
        except Exception: continue
        pk = tuple(sorted((a, b)))
        if 8.0 <= o < 20.0 and pk not in L: band.append((o, pk))
    band.sort()
    if not band: nosnap += 1; continue
    if band[0][1] == dac: same += 1
    cand_n.append(len(band))
    races.append({"date": os.path.basename(f)[:10], "win": win, "pay": float(pay), "da": dac,
                  "band": [pk for _o, pk in band], "L": L, "hitL": win in L})
races.sort(key=lambda x: x["date"])
n = len(races)
print("경주", n, "· 스냅샷 없음/구간 비어 제외", nosnap, "· 규칙 조합 = 그 스냅샷 구간 최저", same, "(%.0f%%)" % (100*same/max(1,n)), "· 구간 후보 중앙", sorted(cand_n)[len(cand_n)//2])
def stat(picks):
    ps = sorted(r["pay"] for r, c in picks if c == r["win"])
    k = len(picks)
    return len(ps), 100*sum(ps)/k, 100*sum(ps[:-3])/k
h, rec, ex3 = stat([(r, r["da"]) for r in races])
print("규칙(구간 최저 1개)  구좌 %d 적중 %d(%.1f%%) 회수 %.1f 3제외 %.1f" % (n, h, 100*h/n, rec, ex3))
t = n // 3
for i, seg in enumerate((races[:t], races[t:2*t], races[2*t:])):
    hh, rr, ee = stat([(r, r["da"]) for r in seg]); print("  구간%d %s~%s 구좌 %d 적중 %d 회수 %.1f 3제외 %.1f" % (i+1, seg[0]["date"], seg[-1]["date"], len(seg), hh, rr, ee))
random.seed(7); R = []; RS = [[], [], []]
for it in range(300):
    picks = [(r, random.choice(r["band"])) for r in races]
    R.append(stat(picks))
    for i, seg in enumerate((picks[:t], picks[t:2*t], picks[2*t:])): RS[i].append(stat(seg)[2])
def q(v, p): v = sorted(v); return v[int(p*(len(v)-1))]
hits = [x[0] for x in R]; recs = [x[1] for x in R]; exs = [x[2] for x in R]
print("무작위(같은 경주·같은 구간 1개) 300회  적중 중앙 %d · 회수 중앙 %.1f [95%% %.1f] · 3제외 중앙 %.1f [95%% %.1f]" % (q(hits,.5), q(recs,.5), q(recs,.95), q(exs,.5), q(exs,.95)))
print("  무작위 ≥ 규칙(3제외)", sum(1 for e in exs if e >= ex3), "/300")
for i in range(3): print("  구간%d 무작위 3제외 중앙 %.1f [95%% %.1f]" % (i+1, q(RS[i],.5), q(RS[i],.95)))
# 명단(판정·회원 맞춤 후) 기준선 · 같은 경주
Lk = sum(len(r["L"]) for r in races); Lp = sorted(r["pay"] for r in races if r["hitL"])
print("같은 경주 현행 명단  구좌 %d 적중 %d 회수 %.1f 3제외 %.1f" % (Lk, len(Lp), 100*sum(Lp)/Lk, 100*sum(Lp[:-3])/Lk))
both = sum(1 for r in races if r["da"] == r["win"] and r["hitL"])
print("규칙 적중 중 현행 명단도 맞힌 경주", both)
