"""8012/자동 예측(form_forecast·keirin_forecast) 성적 — 읽기 전용.
예측: 복승 장수·적중·회수(결과 기록의 공식 복승 배당) · 삼복승 · 축 1·2착
대조: 같은 경주 시장 — 발주 전 마지막 배당판 틱(odds_history · mb>=1)의 최저 복승 1장 · 최저 3장(같은 배당으로 회수)
"""
import json, glob, os, re, gzip, collections, sys
ROOT = r"C:\Users\Administrator\Desktop\경마분석서버"

def load(p):
    if os.path.exists(p):
        return json.load(open(p, encoding="utf-8"))
    if os.path.exists(p + ".gz"):
        return json.load(gzip.open(p + ".gz", "rt", encoding="utf-8"))
    return None

def market_pairs(date8, race):
    m = re.match(r"(.+?)\s*(\d+)경주", race)
    if not m:
        return None
    fn = "%s_%s_%s_%s_%s경주.json" % (date8[:4], date8[4:6], date8[6:], m.group(1), m.group(2))
    oh = load(os.path.join(ROOT, "data", "odds_history", fn))
    if not oh:
        return None
    sn = [s for s in (oh.get("archive_snapshots") or oh.get("snapshots") or [])
          if s.get("quinella") and isinstance(s.get("minutes_before"), (int, float)) and s["minutes_before"] >= 1]
    if not sn:
        return None
    q = sn[-1]["quinella"]
    out = []
    for k, v in q.items():
        try:
            a, b = [int(x) for x in re.split(r"[+\-]", k)]
            o = float(v)
        except Exception:
            continue
        if o >= 100:      # 배당판 표시 상한 칸 제외
            continue
        out.append((o, tuple(sorted((a, b)))))
    out.sort()
    return [pk for _o, pk in out]

def stat(rows):
    n = len(rows)
    if not n:
        return "0경주"
    tk = sum(r[0] for r in rows); hit = sum(1 for r in rows if r[1]); pays = sorted(r[2] for r in rows if r[1])
    ex3 = sum(pays[:-3]) if len(pays) > 3 else 0.0
    return "%d경주 · %.1f장/경주 · 적중 %d(%.1f%%) · 회수 %.1f%% · 3제외 %.1f%%" % (
        n, tk / n, hit, 100 * hit / n, 100 * sum(pays) / tk if tk else 0, 100 * ex3 / tk if tk else 0)

for kind in ("form_forecast", "keirin_forecast"):
    F, T, M1, M3, AX = [], [], [], [], []
    for p in sorted(glob.glob(os.path.join(ROOT, "logs", kind, "2026*", "*.json"))):
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        res = d.get("result") or {}
        order = res.get("order") or []
        pr = d.get("prediction") or {}
        if len(order) < 2 or not pr:
            continue
        win = tuple(sorted(int(x) for x in order[:2]))
        w3 = set(int(x) for x in order[:3])
        qp = float(res.get("quinella") or 0)
        tp = float(res.get("trifecta") or 0)
        qs = [tuple(sorted(int(x) for x in c)) for c in (pr.get("quinellas") or []) if len(c) == 2]
        ts = [set(int(x) for x in c) for c in (pr.get("trios") or []) if len(c) == 3]
        if not qs:
            continue
        date8 = os.path.basename(os.path.dirname(p))
        F.append((len(qs), win in qs, qp, date8))
        if ts and len(order) >= 3:
            T.append((len(ts), any(t == w3 for t in ts), tp, date8))
        if pr.get("axis") is not None:
            AX.append(int(pr["axis"]) in win)
        mk = market_pairs(date8, d.get("race") or "")
        if mk:
            M1.append((1, win in mk[:1], qp, date8))
            M3.append((min(3, len(mk)), win in mk[:3], qp, date8))
    print("=" * 8, "경마" if kind == "form_forecast" else "경륜", "(%s~%s)" % (F[0][3] if F else "-", F[-1][3] if F else "-"))
    print("  예측 복승   ", stat(F))
    print("  예측 삼복승 ", stat(T))
    print("  예측 축 1·2착 %.1f%% (%d경주)" % (100 * sum(AX) / len(AX), len(AX)) if AX else "")
    print("  시장 최저 1장", stat(M1))
    print("  시장 최저 3장", stat(M3))
    # 기간 3분할(예측 복승)
    n = len(F); t = n // 3
    for i, seg in enumerate((F[:t], F[t:2 * t], F[2 * t:])):
        if seg:
            print("   구간%d %s~%s %s" % (i + 1, seg[0][3], seg[-1][3], stat(seg)))
