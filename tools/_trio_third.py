# -*- coding: utf-8 -*-
"""[측정 · 읽기 전용] 삼복승 3착 자리를 유력마 안에서만 고르면.

2026-08-19 대표 지시. 실물 둘.
  08-18 다치카와 9경주 결과 2-4-5 · 시스템 2+4+6 · 6번은 유력마 밖
  08-19 도요하시 7경주 결과 1-4-5 · 첫 줄 1+3+4 · 3번은 유력마 밖(3착도 아님)
  정답 5번은 둘 다 유력마 안에 있었다.

지금 3착 자리는 `darkHorsePicks`(복병) + BMED 특별 조합에서 고른다(app.py 9653).
🔴 그 목록은 화면 「복병」과 같은 것을 쓰는지, 그리고 실제 3착률이 유력마보다 나은지를 잰다.
"""
import glob, json, os, re, statistics as st, sys
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, 'tools'))
import measure_recovery as M

def trio_po(res):
    po = (res or {}).get('payouts') or {}
    v = po.get('trio')
    return v if v is not None else po.get('trifecta')

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
        tp = trio_po(res)
        if tp is None or res.get('3rd') is None:
            continue
        cp = d.get('corePicks') or {}
        ft = []
        for x in (cp.get('finalTrifectas') or []):
            cb = x.get('combo') or []
            if len(cb) == 3:
                try:
                    ft.append((tuple(sorted(int(v) for v in cb)), str(x.get('reason') or '')))
                except (TypeError, ValueError):
                    pass
        if not ft:
            continue
        kh = [int(x) for x in (d.get('keyHorses') or []) if str(x).isdigit()]
        dk = []
        for x in (cp.get('darkHorsePicks') or []):
            n = x.get('no') if isinstance(x, dict) else x
            try:
                dk.append(int(n))
            except (TypeError, ValueError):
                pass
        ax = [int(x) for x in (cp.get('axis') or []) if str(x).isdigit()][:2]
        m = re.match(r'(\d{4}_\d{2}_\d{2})', b)
        out.append({'race': b.replace('.json', ''), 'date': m.group(1) if m else '',
                    'ft': ft, 'kh': kh, 'dk': dk, 'axis': ax, 'tp': float(tp),
                    'ans': tuple(sorted([res['1st'], res['2nd'], res['3rd']])),
                    'top2': tuple(sorted([res['1st'], res['2nd']])),
                    'third_actual': res['3rd']})
    return out

def score(rows, tag, base=None, quiet=False):
    if not rows:
        return None
    slots = sum(len(c) for _, c in rows)
    hit = [(r, c) for r, c in rows if r['ans'] in c]
    ho = sorted([r['tp'] for r, _ in hit], reverse=True)
    d = {'n': len(rows), 'slots': slots, 'hits': len(hit),
         'hitRate': len(hit) / len(rows) * 100,
         'ex3': (sum(ho) - sum(ho[:3])) / slots * 100 if slots else 0,
         'medHit': st.median([r['tp'] for r, _ in hit]) if hit else 0}
    if not quiet:
        g = "" if base is None else " 구좌%+6.1f%%" % ((slots / base - 1) * 100)
        print("  %-26s 경주%4d 구좌%5d 적중%3d(%4.1f%%) 대박뺀회수%7.1f%% 적중배당중앙%7.2f%s%s"
              % (tag, d['n'], slots, d['hits'], d['hitRate'], d['ex3'], d['medHit'], g,
                 "" if d['hits'] >= 30 else "  판정불가"))
    return d

def thirds(r, mode, n=2):
    """축 2두 + 3착 후보 n명."""
    ax = r['axis'] if len(r['axis']) >= 2 else r['kh'][:2]
    if len(ax) < 2:
        return []
    pool = r['dk'] if mode == 'dark' else [x for x in r['kh'] if x not in ax]
    out = []
    for t in pool:
        if t in ax:
            continue
        c = tuple(sorted(ax + [t]))
        if len(set(c)) == 3 and c not in out:
            out.append(c)
        if len(out) >= n:
            break
    return out

def run(rs, tag):
    print("=" * 120)
    print("[%s] 삼복승 3착 자리 — %d경주" % (tag, len(rs)))
    base = [(r, [c for c, _ in r['ft']][:2]) for r in rs]
    b = score(base, "지금(finalTrifectas 상위2)")
    for mode, lab in (('dark', '복병에서 3착'), ('key', '유력마에서 3착')):
        rows = [(r, thirds(r, mode, 2)) for r in rs]
        rows = [(r, c) for r, c in rows if c]
        score(rows, lab, b['slots'] if b else None)
    ds = sorted({r['date'] for r in rs if r['date']})
    mid = ds[len(ds) // 2] if ds else ''
    for lab, f in (("전반", lambda r: r['date'] < mid), ("후반", lambda r: r['date'] >= mid)):
        x = score([(r, c) for r, c in base if f(r)], "", quiet=True)
        rr = [(r, thirds(r, 'key', 2)) for r in rs if f(r)]
        rr = [(r, c) for r, c in rr if c]
        y = score(rr, "", quiet=True)
        if x and y:
            print("    %s 회수 %.1f→%.1f %s · 배당 %.2f→%.2f"
                  % (lab, x['ex3'], y['ex3'], "개선" if y['ex3'] > x['ex3'] else "악화",
                     x['medHit'], y['medHit']))
    # 3착 실물이 어디서 나오나
    ink = ind = neither = 0
    for r in rs:
        t = r['third_actual']
        if t in r['kh']:
            ink += 1
        elif t in r['dk']:
            ind += 1
        else:
            neither += 1
    n = len(rs)
    print("  실제 3착마 위치 — 유력마 %d(%.1f%%) · 복병 %d(%.1f%%) · 둘 다 아님 %d(%.1f%%)"
          % (ink, ink / n * 100, ind, ind / n * 100, neither, neither / n * 100))
