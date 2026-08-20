# -*- coding: utf-8 -*-
"""[측정 · 읽기 전용] 축은 그대로 두고 유력마를 하나 더하면.

2026-08-19 대표 지시.
  축을 유력마로 바꾸면 배당중앙이 4.00 → 2.90 으로 떨어진다. 저배당 그림이라 안 된다.
  ⇒ 축 2두는 그대로 두고, **유력마 1위가 축에 없으면 그 말을 축과 묶어 하나 더한다.**
  ⚠ 배당 중앙값이 안 내려가는 것이 핵심 판정 기준이다.

검산 오가키 1경주 — 7번이 유력마 3위인데 복승에 없었고 4+7 이 22.8배 정답이었다.
"""
import glob, json, os, re, statistics as st, sys
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, 'tools'))
import measure_recovery as M

def load(sport):
    out = []
    for f in sorted(glob.glob(os.path.join(BASE, 'data', 'analysis_log', '2026_0*.json'))):
        b = os.path.basename(f)
        if any(k in b for k in ('서울', '부산', '제주')):
            continue
        try:
            d = json.load(open(f, encoding='utf-8'))
        except Exception:
            continue
        if (d.get('sport') or '') != sport:
            continue
        res = d.get('result') or {}
        qp = (res.get('payouts') or {}).get('quinella')
        if qp is None or res.get('1st') is None or res.get('2nd') is None:
            continue
        cp = d.get('corePicks') or {}
        dc = []
        for c in ((cp.get('displayedCombos') or {}).get('quinellas') or []):
            try:
                dc.append(tuple(sorted(int(x) for x in c)))
            except (TypeError, ValueError):
                pass
        if not dc:
            continue
        ax = [int(x) for x in (cp.get('axis') or []) if str(x).isdigit()][:2]
        kh = [int(x) for x in (d.get('keyHorses') or []) if str(x).isdigit()]
        if len(ax) < 2 or not kh:
            continue
        q = {}
        try:
            h = M._loadh(f.replace('analysis_log', 'odds_history')) or {}
            dl = h.get('deadline_epoch')
            sn = [s for s in (h.get('snapshots') or [])
                  if s.get('t') and dl and -8 <= (s['t'] - dl) / 60 <= 0 and s.get('quinella')]
            if sn:
                for k, v in max(sn, key=lambda x: x['t'])['quinella'].items():
                    try:
                        q[tuple(sorted(int(x) for x in str(k).replace('-', '+').split('+')))] = float(v)
                    except Exception:
                        pass
        except Exception:
            pass
        if not q:
            continue
        ans = tuple(sorted([res['1st'], res['2nd']]))
        mo = q.get(ans)
        if not mo or not (M.CLEAN_LO <= float(qp) / mo <= M.CLEAN_HI):
            continue
        m = re.match(r'(\d{4}_\d{2}_\d{2})', b)
        out.append({'race': b.replace('.json', ''), 'date': m.group(1) if m else '',
                    'dc': dc, 'ax': ax, 'kh': kh, 'q': q, 'qp': float(qp), 'ans': ans})
    return out

def add(r, upto):
    """유력마 상위 upto 중 축에 없는 말을 축과 묶어 **하나씩** 더한다(배당 낮은 쪽)."""
    have = set(r['dc'])
    outc = list(r['dc'])
    for k in r['kh'][:upto]:
        if k in r['ax']:
            continue
        best = None
        for a in r['ax']:
            c = tuple(sorted((a, k)))
            if c in have:
                continue
            o = r['q'].get(c)
            if not o:
                continue
            if best is None or o < best[0]:
                best = (o, c)
        if best:
            outc.append(best[1])
            have.add(best[1])
    return outc

def score(rows, tag, base=None, quiet=False):
    if not rows:
        return None
    slots = sum(len(c) for _, c in rows)
    hit = [(r, c) for r, c in rows if r['ans'] in c]
    ho = sorted([r['qp'] for r, _ in hit], reverse=True)
    o = [r['q'].get(c2) for r, c in rows for c2 in c]
    o = [x for x in o if x]
    d = {'n': len(rows), 'slots': slots, 'hits': len(hit),
         'hitRate': len(hit) / len(rows) * 100,
         'ex3': (sum(ho) - sum(ho[:3])) / slots * 100 if slots else 0,
         'med': st.median(o) if o else 0,
         'medHit': st.median([r['qp'] for r, _ in hit]) if hit else 0}
    if not quiet:
        g = "" if base is None else " 구좌%+6.1f%%" % ((slots / base - 1) * 100)
        print("  %-22s 경주%4d 구좌%5d 적중%3d(%4.1f%%) 대박뺀회수%7.1f%% 배당중앙%6.2f 적중배당%6.2f%s%s"
              % (tag, d['n'], slots, d['hits'], d['hitRate'], d['ex3'], d['med'], d['medHit'], g,
                 "" if d['hits'] >= 30 else "  판정불가"))
    return d

def run(rs, tag):
    print("=" * 126)
    miss = sum(1 for r in rs if r['kh'] and r['kh'][0] not in r['ax'])
    print("[%s] %d경주 · 유력마 1위가 축에 없는 경주 %d (%.1f%%)"
          % (tag, len(rs), miss, miss / len(rs) * 100))
    base = [(r, list(r['dc'])) for r in rs]
    b = score(base, "지금")
    ds = sorted({r['date'] for r in rs if r['date']})
    mid = ds[len(ds) // 2] if ds else ''
    for upto in (1, 2):
        rows = [(r, add(r, upto)) for r in rs]
        a = score(rows, "유력마 %d위까지 더함" % upto, b['slots'] if b else None)
        if b and a:
            ok = [("회수율", a['ex3'] > b['ex3'], "%.1f→%.1f" % (b['ex3'], a['ex3'])),
                  ("적중률", a['hitRate'] >= b['hitRate'] - 2.0, "%.1f→%.1f" % (b['hitRate'], a['hitRate'])),
                  ("배당중앙", a['med'] >= b['med'] - 0.01, "%.2f→%.2f" % (b['med'], a['med'])),
                  ("구좌30%", abs((a['slots'] / b['slots'] - 1) * 100) <= 30.0,
                   "%+.1f%%" % ((a['slots'] / b['slots'] - 1) * 100))]
            bad = [n for n, p, _ in ok if not p]
            print("    판정 " + ("통과" if not bad else "기각(" + "·".join(bad) + ")")
                  + "  [" + " · ".join("%s %s" % (n, v) for n, _, v in ok) + "]")
        for lab, f in (("전반", lambda r: r['date'] < mid), ("후반", lambda r: r['date'] >= mid)):
            x = score([(r, c) for r, c in base if f(r)], "", quiet=True)
            y = score([(r, c) for r, c in rows if f(r)], "", quiet=True)
            if x and y:
                print("      %s 회수 %.1f→%.1f %s · 배당 %.2f→%.2f %s"
                      % (lab, x['ex3'], y['ex3'], "개선" if y['ex3'] > x['ex3'] else "악화",
                         x['med'], y['med'], "개선" if y['med'] >= x['med'] else "악화"))
