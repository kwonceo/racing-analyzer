# -*- coding: utf-8 -*-
"""[측정 · 읽기 전용] 신호 가중을 올리면.

2026-08-15 대표 지시.

🔴 「1000배 가중」이 무엇인가 (app.py 11507~11510)
    정렬 키 = combinedProb * 1000 + total + formScoreAdj/100 + 50*dualConverge
      combinedProb  0~100  (시장 70% + 전적 30%)
      total         0~150  (_elim_score · 여기에만 신호가 들어간다)
                           급락 30%+ → +30 · 쌍승 상위 → +20
    combinedProb 에 1000 을 곱하므로 **0.1 차이가 100점**이다.
    신호가 줄 수 있는 최대 +50 은 그 아래로 묻힌다.
    ⇒ 신호가 순위를 바꾸는 길은 있으나 실질적으로 안 바뀐다.

이 측정은 그 균형을 K 배로 바꿔가며 본다.
  점수 = 기존 순위 점수 + K * 신호 크기
⚠ 조합 생성 전체를 재현하지 않는다. 상위3 전조합 대리 정책이다.
"""
import sys, glob, json, os, itertools, collections, statistics as st
sys.path.insert(0, 'tools')
import measure_recovery as M


def clean(r):
    try:
        return M.CLEAN_LO <= r["po"] / r["mo"] <= M.CLEAN_HI
    except Exception:
        return False


def build():
    meta = {}
    for f in glob.glob('data/analysis_log/2026_0*.json'):
        try:
            d = json.load(open(f, encoding='utf-8'))
        except Exception:
            continue
        res = d.get('result') or {}
        po = (res.get('payouts') or {}).get('quinella')
        if po is None or res.get('1st') is None or res.get('2nd') is None:
            continue
        sig = collections.Counter()
        for a in (d.get('anomaly_history') or []):
            try:
                dp = float(a.get('drop') or 0)
            except (TypeError, ValueError):
                continue
            if dp > -20:
                continue
            for tok in str(a.get('combo') or '').replace('-', '+').split('+'):
                if tok.strip().isdigit():
                    sig[int(tok)] += abs(dp)
        t = sorted({res['1st'], res['2nd']})
        meta[(t[0], t[1], float(po))] = {
            'kh': [int(x) for x in (d.get('keyHorses') or []) if str(x).isdigit()],
            'sig': dict(sig), 'po': float(po)}
    return meta


def line(nm, rows, base=None):
    if not rows:
        print("  %-24s 경주 0"); return None
    slots = sum(len(c) for _, c in rows)
    hit = [(r, c) for r, c in rows if sorted(r["top2"]) in [sorted(x) for x in c]]
    ret = sum(r["po"] for r, _ in hit)
    ho = sorted([r["po"] for r, _ in hit], reverse=True)
    o = [r["q"].get(tuple(sorted(x))) for r, c in rows for x in c]
    o = [x for x in o if x]
    g = "" if base is None else " 구좌%+5.1f%%" % ((slots / base - 1) * 100)
    print("  %-24s 경주%4d 적중%3d(%4.1f%%) 3제외%6.1f%% 배당중앙%5.2f%s %s"
          % (nm, len(rows), len(hit), len(hit) / len(rows) * 100,
             (ret - sum(ho[:3])) / slots * 100 if slots else 0,
             st.median(o) if o else 0, g, "" if len(hit) >= 30 else "⚠판정불가"))
    return slots


def run(sport, tag, hi_only=False):
    meta = build()
    rs = []
    for r in M.load_races(sport=sport, pattern="2026_0*"):
        if not clean(r):
            continue
        m = meta.get((r["top2"][0], r["top2"][1], r["po"]))
        if not m or not m['kh']:
            continue
        if hi_only and r["po"] < 20:
            continue
        r.update(m); rs.append(r)
    if not rs:
        print("%s 데이터 없음" % tag); return
    print("=" * 108)
    print("%s %d경주 — 신호 가중을 올리면" % (tag, len(rs)))

    def combos(order, r):
        v = [h for h in order if h in {x for k in r["q"] for x in k}][:3]
        return [list(c) for c in itertools.combinations(sorted(v), 2)] if len(v) >= 2 else []

    def order_k(r, k):
        """기존 유력마 순서에 신호를 k 배로 얹는다."""
        base = {}
        allh = {x for kk in r["q"] for x in kk}
        for i, h in enumerate(r['kh']):
            base[h] = 100 - i * 10           # 1위 100 · 2위 90 …
        for h in allh:
            base.setdefault(h, 0)
        sig = r['sig'] or {}
        mx = max(sig.values()) if sig else 1.0
        sc = {h: base[h] + k * (sig.get(h, 0) / mx * 100) for h in allh}
        return [h for h, _ in sorted(sc.items(), key=lambda kv: -kv[1])]

    b = line("K=0 지금(신호 무시)", [(r, combos(order_k(r, 0), r)) for r in rs])
    for k in (0.2, 0.5, 1.0, 2.0, 5.0):
        line("K=%.1f" % k, [(r, combos(order_k(r, k), r)) for r in rs], b)
    n_sig = sum(1 for r in rs if r['sig'])
    print("  ※ 신호 있는 경주 %d / %d (%.1f%%)" % (n_sig, len(rs), n_sig / len(rs) * 100))


if __name__ == "__main__":
    run("cycle", "경륜")
    run("cycle", "경륜 · 정답 20배 이상만", hi_only=True)
    run("horse", "경마")
    run("horse", "경마 · 정답 20배 이상만", hi_only=True)
