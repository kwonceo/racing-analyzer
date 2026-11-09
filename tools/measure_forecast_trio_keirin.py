"""경륜 예측 삼복승(경주당 2장) ↔ 같은 경주 시장 삼복승 — 읽기 전용.
시장: logs/keirin_trio/<날짜>.jsonl(3連複 관찰 틱) 중 발주 60초 전보다 앞선 마지막 틱의 가장 싼 1·2·3장
회수: 예측 기록 result.trifecta(공식 3連複 확정 배당) · 구좌=조합1 · 정제 없음 · 같은 경주 집합만
"""
import json, glob, os, re, time, collections, random, gzip
ROOT = r"C:\Users\Administrator\Desktop\경마분석서버"

obs = collections.defaultdict(list)          # (ymd, rk) -> [(t, [(odds, combo)])]
for p in glob.glob(os.path.join(ROOT, "logs", "keirin_trio", "*.jsonl")):
    ymd = os.path.basename(p)[:8]
    for ln in open(p, encoding="utf-8", errors="replace"):
        try:
            e = json.loads(ln)
        except Exception:
            continue
        tr = []
        for c in e.get("trio") or []:
            try:
                tr.append((float(c["odds"]), frozenset(int(x) for x in c["combo"])))
            except Exception:
                pass
        if tr and e.get("rk") and e.get("t"):
            obs[(ymd, e["rk"].strip())].append((float(e["t"]), sorted(tr, key=lambda x: x[0])))

rows = []
for p in sorted(glob.glob(os.path.join(ROOT, "logs", "keirin_forecast", "2026*", "*.json"))):
    try:
        d = json.load(open(p, encoding="utf-8"))
    except Exception:
        continue
    ymd = os.path.basename(os.path.dirname(p))
    res = d.get("result") or {}
    order = res.get("order") or []
    pr = d.get("prediction") or {}
    tp = res.get("trifecta")
    if len(order) < 3 or not tp:
        continue
    w3 = frozenset(int(x) for x in order[:3])
    ts = [frozenset(int(x) for x in c) for c in (pr.get("trios") or []) if len(c) == 3]
    if not ts:
        continue
    post = None
    mm = re.match(r"(.+?)\s*(\d+)경주", d.get("race") or "")
    if mm:
        fn = os.path.join(ROOT, "data", "odds_history", "%s_%s_%s_%s_%s경주.json" % (ymd[:4], ymd[4:6], ymd[6:], mm.group(1), mm.group(2)))
        try:
            oh = json.load(open(fn, encoding="utf-8")) if os.path.exists(fn) else json.load(gzip.open(fn + ".gz", "rt", encoding="utf-8"))
            post = oh.get("deadline_epoch")
        except Exception:
            post = None
    if not post:
        continue
    ticks = [x for x in obs.get((ymd, (d.get("race") or "").strip()), []) if x[0] <= post - 60]
    if not ticks:
        continue
    mk = max(ticks, key=lambda x: x[0])[1]
    mk = [c for o, c in mk if o < 9000]
    if len(mk) < 3:
        continue
    rows.append({"ymd": ymd, "pay": float(tp), "w3": w3, "fc": ts, "mk": mk})

def stat(name, pick):
    tk = hit = 0; pays = []
    for r in rows:
        cs = pick(r)
        tk += len(cs)
        if r["w3"] in cs:
            hit += 1; pays.append(r["pay"])
    pays.sort()
    n = len(rows)
    print("  %-26s %d경주 · %.1f장/경주 · 적중 %d(%.1f%%) · 회수 %.1f%% · 3제외 %.1f%% · 적중 배당 중앙 %.1f" % (
        name, n, tk / n, hit, 100 * hit / n, 100 * sum(pays) / tk, 100 * sum(pays[:-3]) / tk,
        pays[len(pays) // 2] if pays else 0))
    return tk, hit, pays

rows.sort(key=lambda r: r["ymd"])
print("같은 경주 집합: %d경주 (%s~%s) · 예측 기록 중 시장 삼복승 틱이 발주 1분 전에 있는 경주" % (len(rows), rows[0]["ymd"], rows[-1]["ymd"]))
stat("예측 삼복승", lambda r: r["fc"])
stat("시장 최저 1장", lambda r: r["mk"][:1])
stat("시장 최저 2장", lambda r: r["mk"][:2])
stat("시장 최저 3장", lambda r: r["mk"][:3])
# 예측 2장이 시장 최저 2장과 얼마나 겹치나
same = sum(1 for r in rows if set(r["fc"]) == set(r["mk"][:2]))
inter = sum(len(set(r["fc"]) & set(r["mk"][:2])) for r in rows)
print("  예측 2장 = 시장 최저 2장 그대로 %d경주(%.0f%%) · 장 단위 겹침 %.0f%%" % (same, 100 * same / len(rows), 100 * inter / sum(len(r["fc"]) for r in rows)))
# 시장 순위로 본 예측 삼복승 위치
rk = collections.Counter()
for r in rows:
    for c in r["fc"]:
        i = r["mk"].index(c) + 1 if c in r["mk"] else 99
        rk["1~2위" if i <= 2 else "3~5위" if i <= 5 else "6~10위" if i <= 10 else "11위+"] += 1
print("  예측 삼복승의 시장 순위:", dict(rk))
# 무작위 대조: 시장 순위가 예측과 같은 자리(경주별)인 다른 조합 — 시장 3~10위 중 무작위 2장
random.seed(3); R = []
for it in range(300):
    tk = 0; pays = []
    for r in rows:
        pool = r["mk"][:10]
        cs = random.sample(pool, 2)
        tk += 2
        if r["w3"] in cs:
            pays.append(r["pay"])
    pays.sort(); R.append(100 * sum(pays[:-3]) / tk)
R.sort()
print("  무작위(시장 상위 10장 중 2장) 300회 3제외 회수 중앙 %.1f%% · 95%% %.1f%%" % (R[150], R[284]))
# 기간 3분할
n = len(rows); t = n // 3
for i, seg in enumerate((rows[:t], rows[t:2 * t], rows[2 * t:])):
    def s2(pick):
        tk = 0; pays = []
        for r in seg:
            cs = pick(r); tk += len(cs)
            if r["w3"] in cs: pays.append(r["pay"])
        pays.sort(); return 100 * sum(pays) / tk, 100 * sum(pays[:-3]) / tk
    a = s2(lambda r: r["fc"]); b = s2(lambda r: r["mk"][:2])
    print("   구간%d %s~%s %d경주 · 예측 회수 %.1f(3제외 %.1f) ↔ 시장 2장 %.1f(%.1f)" % (i + 1, seg[0]["ymd"], seg[-1]["ymd"], len(seg), a[0], a[1], b[0], b[1]))
