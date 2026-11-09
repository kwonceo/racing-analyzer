"""[리플레이] 조합 수 상한 「시장 최저 3배 미만 → 1조합」 — 잘린 조합을 되살리면? (읽기 전용)
표본: analysis_log 2026-09-01~ · 결과(1·2착)+공식 복승 배당(result.payouts.quinella) · corePicks.quinellaRef 에
      refReason 「조합 수 상한(시장 최저 X배 → 1조합)」 이 있는 경주(= 상한이 실제로 자른 경주)
현행: displayedCombos.quinellas(판정 명단)
안  : +1 = 잘린 조합 중 배당 낮은 순 1개 더 · +2 · 전부 / 「잘린 것 중 15~80배만」
무작위: 같은 경주 마감 전 배당판에서 명단 밖 · 잘린 조합과 배당 ±30% 인 조합 1개(300회)
구좌=조합1 · 정제 없음 · 경륜/경마 분리 · 기간 3분할
"""
import json, glob, os, re, gzip, random, collections
ROOT = r"C:\Users\Administrator\Desktop\경마분석서버"

def load(p):
    if os.path.exists(p):
        return json.load(open(p, encoding="utf-8"))
    if os.path.exists(p + ".gz"):
        return json.load(gzip.open(p + ".gz", "rt", encoding="utf-8"))
    return None

def pk(c):
    return tuple(sorted(int(x) for x in c))

races = []
for f in sorted(glob.glob(os.path.join(ROOT, "data", "analysis_log", "2026_*.json"))):
    b = os.path.basename(f)
    if b < "2026_09_01":
        continue
    try:
        d = json.load(open(f, encoding="utf-8"))
    except Exception:
        continue
    cp = d.get("corePicks") or {}
    ref = cp.get("quinellaRef") or []
    cut = [x for x in ref if "조합 수 상한" in str(x.get("refReason") or "") and "1조합" in str(x.get("refReason") or "")]
    if not cut:
        continue
    r = d.get("result") or {}
    try:
        win = pk((r["1st"], r["2nd"]))
    except Exception:
        continue
    pay = (r.get("payouts") or {}).get("quinella")
    if not pay:
        continue
    disp = [pk(c) for c in ((cp.get("displayedCombos") or {}).get("quinellas") or [])]
    if not disp:
        continue
    cuts = []
    for x in cut:
        try:
            c = pk(x["combo"]); o = float(x.get("odds") or 0)
        except Exception:
            continue
        if c not in disp and c not in [y for y, _ in cuts]:
            cuts.append((c, o))
    if not cuts:
        continue
    cuts.sort(key=lambda t: t[1])
    m = re.search(r"최저 ([\d.]+)배", str(cut[0].get("refReason")))
    sp = "cycle" if d.get("sport") == "cycle" else ("korea" if d.get("category") == "korea" else "horse")
    oh = load(os.path.join(ROOT, "data", "odds_history", b))
    mkt = []
    if oh:
        sn = [s for s in (oh.get("archive_snapshots") or oh.get("snapshots") or [])
              if s.get("quinella") and isinstance(s.get("minutes_before"), (int, float)) and s["minutes_before"] >= 1]
        if sn:
            for k, v in sn[-1]["quinella"].items():
                try:
                    a, bb = [int(z) for z in re.split(r"[+\-]", k)]; o = float(v)
                except Exception:
                    continue
                if o < 100:
                    mkt.append((pk((a, bb)), o))
    races.append({"date": b[:10], "sp": sp, "win": win, "pay": float(pay), "disp": disp, "cuts": cuts,
                  "low": float(m.group(1)) if m else None, "mkt": mkt})

def stat(rs, pick):
    tk = 0; pays = []
    for r in rs:
        cs = pick(r); tk += len(cs)
        if r["win"] in cs:
            pays.append(r["pay"])
    pays.sort()
    if not tk:
        return "구좌 0"
    return "구좌 %d · 적중 %d · 회수 %.1f%% · 3제외 %.1f%%" % (tk, len(pays), 100 * sum(pays) / tk, 100 * sum(pays[:-3]) / tk)

def rnd_extra(r, rng):
    out = []
    for c, o in r["cuts"][:1]:
        pool = [k for k, v in r["mkt"] if k not in r["disp"] and k != c and o > 0 and abs(v - o) / o <= 0.3]
        if pool:
            out.append(rng.choice(pool))
    return out

for sp in ("cycle", "horse", "korea"):
    rs = [r for r in races if r["sp"] == sp]
    if not rs:
        continue
    rs.sort(key=lambda r: r["date"])
    print("=" * 6, {"cycle": "경륜", "horse": "경마(일본)", "korea": "한국"}[sp], "· 상한이 자른 경주 %d (%s~%s)" % (len(rs), rs[0]["date"], rs[-1]["date"]))
    lows = [r["low"] for r in rs if r["low"]]
    print("  시장 최저 배당 중앙 %.1f · 잘린 조합 수 중앙 %d" % (sorted(lows)[len(lows) // 2], sorted(len(r["cuts"]) for r in rs)[len(rs) // 2]))
    print("  현행(판정 명단)        ", stat(rs, lambda r: r["disp"]))
    print("  잘린 것만(1개·가장 쌈)  ", stat(rs, lambda r: [c for c, o in r["cuts"][:1]]))
    print("  잘린 것만(전부)         ", stat(rs, lambda r: [c for c, o in r["cuts"]]))
    print("  잘린 것 중 15~80배만    ", stat(rs, lambda r: [c for c, o in r["cuts"] if 15 <= o < 80]))
    print("  현행 + 1개              ", stat(rs, lambda r: r["disp"] + [c for c, o in r["cuts"][:1]]))
    print("  현행 + 2개              ", stat(rs, lambda r: r["disp"] + [c for c, o in r["cuts"][:2]]))
    print("  현행 + 전부             ", stat(rs, lambda r: r["disp"] + [c for c, o in r["cuts"]]))
    # 무작위 대조(잘린 1개 자리 · 배당 ±30%)
    rng = random.Random(5); R = []
    for it in range(300):
        tk = 0; pays = []
        for r in rs:
            cs = rnd_extra(r, rng); tk += len(cs)
            if r["win"] in cs:
                pays.append(r["pay"])
        pays.sort()
        if tk:
            R.append(100 * sum(pays[:-3]) / tk)
    if R:
        R.sort()
        print("  무작위(잘린 1개와 배당 ±30%%·명단 밖) 3제외 중앙 %.1f%% · 95%% %.1f%%" % (R[len(R) // 2], R[int(len(R) * 0.95)]))
    n = len(rs); t = n // 3
    for i, seg in enumerate((rs[:t], rs[t:2 * t], rs[2 * t:])):
        if seg:
            print("   구간%d %s~%s · 잘린 1개 %s" % (i + 1, seg[0]["date"], seg[-1]["date"], stat(seg, lambda r: [c for c, o in r["cuts"][:1]])))
    # 잘린 것 중 정답이 있던 경주의 배당
    hits = sorted(r["pay"] for r in rs if r["win"] in [c for c, o in r["cuts"]])
    print("  정답이 잘린 쪽에 있던 경주 %d(%.1f%%) · 그 배당 중앙 %.1f · 최대 %.1f" % (
        len(hits), 100 * len(hits) / n, hits[len(hits) // 2] if hits else 0, hits[-1] if hits else 0))

print()
print("=== 시장 최저 배당 구간별(잘린 1개 · 현행 · 현행+전부) ===")
for sp in ("cycle", "horse"):
    for lo, hi in ((0, 2.0), (2.0, 3.0)):
        rs = [r for r in races if r["sp"] == sp and r["low"] is not None and lo <= r["low"] < hi]
        if not rs:
            continue
        print(" ", {"cycle": "경륜", "horse": "경마"}[sp], "최저 %.1f~%.1f배 %d경주" % (lo, hi, len(rs)))
        print("     현행      ", stat(rs, lambda r: r["disp"]))
        print("     잘린 1개  ", stat(rs, lambda r: [c for c, o in r["cuts"][:1]]))
        print("     현행+전부 ", stat(rs, lambda r: r["disp"] + [c for c, o in r["cuts"]]))
