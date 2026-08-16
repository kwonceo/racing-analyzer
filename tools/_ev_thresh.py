# -*- coding: utf-8 -*-
"""[측정 · 읽기 전용] 기대값 필터 문턱.

2026-08-16 대표 지시.

필터가 하는 일 (app.py 10261~10279)
  EV = 배당 x 추정적중률.  EV < 1.0 이면 메인에서 강등.
  단 면제 토큰(유력마 1·2위 · 시장 최저복승 · 급락 · 역배열 · 확신도 …)이 있으면 통과.

추정적중률은 배당대 밴드의 **실측 누적**이다(data/ev_bands.json).
  0~1.8배 197/368 = 53.5% · 1.8~2.5 196/585 = 33.5% · 2.5~5 327/1898 = 17.2%
  5~15배 221/2511 = **8.8%** · 15배+ 26/666 = 3.9%

🔴 그래서 5~15배 밴드에서 EV 1.0 을 넘으려면 배당이 11.4배 이상이어야 한다.
   8~11.4배는 **구조적으로 전부 잘린다.** 서울 1경주 8.5·8.6배가 정확히 그 자리다.

⚠ 배선하지 않는다.
"""
import glob
import json
import os
import re
import statistics as st
import sys
from collections import Counter

sys.path.insert(0, 'tools')
import measure_recovery as M

BANDS = [(0.0, 1.8), (1.8, 2.5), (2.5, 5.0), (5.0, 15.0), (15.0, 9e9)]
ANCH = [(1.4, 0.50), (2.15, 0.40), (3.75, 0.30), (10.0, 0.15), (32.0, 0.05)]


def band_p(o, learned):
    for lo, hi in BANDS:
        if lo <= o < hi:
            b = learned.get("%s-%s" % (lo, hi)) or {}
            if (b.get("n") or 0) >= 50 and b.get("hit") is not None:
                return max(0.01, min(0.95, b["hit"] / b["n"]))
            break
    if o <= ANCH[0][0]:
        return ANCH[0][1]
    for (x1, p1), (x2, p2) in zip(ANCH, ANCH[1:]):
        if o <= x2:
            return round(p1 + (p2 - p1) * (o - x1) / (x2 - x1), 4)
    return ANCH[-1][1]


def clean(r):
    try:
        return M.CLEAN_LO <= r["po"] / r["mo"] <= M.CLEAN_HI
    except Exception:
        return False


def load(sport):
    extra = {}
    for f in glob.glob(os.path.join('data', 'analysis_log', '2026_0*.json')):
        try:
            d = json.load(open(f, encoding='utf-8'))
        except Exception:
            continue
        res = d.get('result') or {}
        po = (res.get('payouts') or {}).get('quinella')
        if po is None or res.get('1st') is None or res.get('2nd') is None:
            continue
        cp = d.get('corePicks') or {}
        ev = []
        for x in (cp.get('quinellaRef') or []):
            cb = x.get('combo') or []
            rs = str(x.get('refReason') or '')
            if len(cb) != 2 or '기대값' not in rs:
                continue
            m2 = re.search(r'기대값 ([\d.]+) 미달', rs)
            ev.append((sorted(int(v) for v in cb), float(m2.group(1)) if m2 else None))
        t = sorted({res['1st'], res['2nd']})
        m = re.match(r'(\d{4}_\d{2}_\d{2})', os.path.basename(f))
        extra[(t[0], t[1], float(po))] = {'evcut': ev, 'date': m.group(1) if m else '',
                                          'kr': any(k in os.path.basename(f)
                                                    for k in ('제주', '서울', '부산'))}
    out = []
    for r in M.load_races(sport=sport, pattern='2026_0*'):
        if not clean(r):
            continue
        e = extra.get((r["top2"][0], r["top2"][1], r["po"]))
        if not e:
            continue
        r.update(e)
        out.append(r)
    return out


def bandname(o):
    if o is None:
        return '배당없음'
    return ('~5배' if o < 5 else '5~8배' if o < 8 else '8~11.4배' if o < 11.4
            else '11.4~15배' if o < 15 else '15~26배' if o < 26 else '26~50배' if o < 50 else '50배+')


def score(rows, tag, base=None, quiet=False):
    if not rows:
        return None
    slots = sum(len(c) for _, c in rows)
    hit = [(r, c) for r, c in rows if sorted(r["top2"]) in [sorted(x) for x in c]]
    ret = sum(r["po"] for r, _ in hit)
    ho = sorted([r["po"] for r, _ in hit], reverse=True)
    o = [r["q"].get(tuple(sorted(x))) for r, c in rows for x in c]
    o = [x for x in o if x]
    d = {'n': len(rows), 'slots': slots, 'hits': len(hit),
         'hitRate': len(hit) / len(rows) * 100,
         'ex3': (ret - sum(ho[:3])) / slots * 100 if slots else 0,
         'med': st.median(o) if o else 0}
    if not quiet:
        g = "" if base is None else " 구좌%+6.1f%%" % ((slots / base - 1) * 100)
        print("  %-22s 경주%4d 구좌%5d 적중%3d(%4.1f%%) 대박뺀회수%6.1f%% 배당중앙%6.2f%s%s"
              % (tag, d['n'], slots, d['hits'], d['hitRate'], d['ex3'], d['med'], g,
                 "" if d['hits'] >= 30 else "  판정불가"))
    return d


def verdict(b, a):
    if not b or not a:
        return "판정불가"
    ok = [("회수율", a['ex3'] > b['ex3'], "%.1f→%.1f" % (b['ex3'], a['ex3'])),
          ("적중률", a['hitRate'] >= b['hitRate'] - 2.0, "%.1f→%.1f" % (b['hitRate'], a['hitRate'])),
          ("배당중앙", a['med'] >= b['med'] - 0.01, "%.2f→%.2f" % (b['med'], a['med'])),
          ("구좌30%", (a['slots'] / b['slots'] - 1) * 100 <= 30.0,
           "%+.1f%%" % ((a['slots'] / b['slots'] - 1) * 100))]
    bad = [n for n, p, _ in ok if not p]
    return ("통과" if not bad else "기각(" + "·".join(bad) + ")") + "  [" + \
           " · ".join("%s %s" % (n, v) for n, _, v in ok) + "]"


def run(rs, tag):
    print("=" * 116)
    print("[기대값 필터] %s %d경주" % (tag, len(rs)))
    tot, hitn = Counter(), Counter()
    for r in rs:
        ans = tuple(sorted(r["top2"]))
        for c, _ev in (r.get('evcut') or []):
            o = r["q"].get(tuple(c))
            bd = bandname(o)
            tot[bd] += 1
            if tuple(c) == ans:
                hitn[bd] += 1
    N = sum(tot.values())
    print("  기대값으로 잘린 조합 %d개 · 그중 정답 %d개 (%.2f%%)"
          % (N, sum(hitn.values()), sum(hitn.values()) / N * 100 if N else 0))
    order = ['~5배', '5~8배', '8~11.4배', '11.4~15배', '15~26배', '26~50배', '50배+', '배당없음']
    for k in order:
        if tot.get(k):
            print("    %-10s 잘림%4d · 정답%3d (%.1f%%)"
                  % (k, tot[k], hitn.get(k, 0), hitn.get(k, 0) / tot[k] * 100))
    base = [(r, [sorted(c) for c in r["dc"]]) for r in rs]
    b = score(base, "지금(문턱 1.0)")
    ds = sorted({r['date'] for r in rs if r['date']})
    mid = ds[len(ds) // 2] if ds else ''
    for th in (0.9, 0.8, 0.7, 0.6):
        rows = []
        for r in rs:
            c = [sorted(x) for x in r["dc"]]
            have = {tuple(x) for x in c}
            add = [x for x, ev in (r.get('evcut') or [])
                   if ev is not None and ev >= th and tuple(x) not in have]
            rows.append((r, c + add))
        a = score(rows, "문턱 %.1f 로 낮추면" % th, b['slots'] if b else None)
        print("    판정 " + verdict(b, a))
        x = score([(r, cc) for r, cc in base if r['date'] < mid], "", quiet=True)
        y = score([(r, cc) for r, cc in rows if r['date'] < mid], "", quiet=True)
        x2 = score([(r, cc) for r, cc in base if r['date'] >= mid], "", quiet=True)
        y2 = score([(r, cc) for r, cc in rows if r['date'] >= mid], "", quiet=True)
        if x and y and x2 and y2:
            print("      전반 %.1f→%.1f %s · 후반 %.1f→%.1f %s"
                  % (x['ex3'], y['ex3'], "개선" if y['ex3'] > x['ex3'] else "악화",
                     x2['ex3'], y2['ex3'], "개선" if y2['ex3'] > x2['ex3'] else "악화"))


if __name__ == "__main__":
    lr = {}
    try:
        lr = json.load(open('data/ev_bands.json', encoding='utf-8'))
    except Exception:
        pass
    print("[실측 적중률] " + " · ".join(
        "%s %d/%d=%.1f%%" % (k, v['hit'], v['n'], v['hit'] / v['n'] * 100)
        for k, v in sorted(lr.items(), key=lambda kv: float(kv[0].split('-')[0]))))
    print("[서울 1경주 계산] 8.5배 x %.4f = %.2f → 1.0 미만이라 강등"
          % (band_p(8.5, lr), 8.5 * band_p(8.5, lr)))
    print("  5~15배 밴드에서 EV 1.0 을 넘으려면 배당 %.1f배 이상이어야 한다"
          % (1.0 / band_p(8.5, lr)))
    print("  15배+ 밴드에서는 %.1f배 이상" % (1.0 / band_p(20.0, lr)))
    for sport, tag in (("cycle", "경륜"), ("horse", "경마")):
        rs = [r for r in load(sport) if not r.get('kr')]
        if rs:
            run(rs, tag)
