# -*- coding: utf-8 -*-
"""[측정 · 읽기 전용] 축을 유력마 목록 안에서만 뽑으면.

2026-08-19 대표 지시. 실물 도요하시 7경주(결과 1-4-5).
  축이 5·7 인데 유력마는 1·4·5·2 였다. 축 2두 중 7번이 유력마에 없다.
  ⇒ 축이 틀리면 3착을 뭘 고르든 못 맞힌다.

🔴 축은 `_core_picks`(app.py 8346)가 정한다. 우선순위가 이렇다.
  tier4 역배열+급락 > tier3 스마트머니 > tier2 집중급락 > **tier1 유력마**
  즉 **유력마가 가장 낮다.** 도요하시는 스마트머니 5·7 이 축이 되고 유력마 1번이 밀렸다.

측정: 축 2두를 그대로 복승 한 자리로 보고, 지금 축 ↔ 유력마 상위2 를 비교한다.
⚠ 조합 생성 전체를 재현하지 않는다. 축 자체의 적중을 보는 대리 정책이다.
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
        ax = [int(x) for x in (cp.get('axis') or []) if str(x).isdigit()][:2]
        kh = [int(x) for x in (d.get('keyHorses') or []) if str(x).isdigit()]
        if len(ax) < 2 or len(kh) < 2:
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
                    'ax': ax, 'kh': kh, 'q': q, 'qp': float(qp), 'ans': ans,
                    'picks': cp.get('picks') or []})
    return out

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
         'med': st.median(o) if o else 0}
    if not quiet:
        g = "" if base is None else " 구좌%+6.1f%%" % ((slots / base - 1) * 100)
        print("  %-24s 경주%4d 구좌%5d 적중%3d(%4.1f%%) 대박뺀회수%7.1f%% 배당중앙%6.2f%s%s"
              % (tag, d['n'], slots, d['hits'], d['hitRate'], d['ex3'], d['med'], g,
                 "" if d['hits'] >= 30 else "  판정불가"))
    return d

def run(rs, tag):
    print("=" * 118)
    out = sum(1 for r in rs if any(a not in r['kh'] for a in r['ax']))
    print("[%s] %d경주 · 🔴 축 2두 중 유력마 밖이 있는 경주 %d (%.1f%%)"
          % (tag, len(rs), out, out / len(rs) * 100))
    base = [(r, [tuple(sorted(r['ax']))]) for r in rs]
    b = score(base, "지금 축 2두")
    rows = [(r, [tuple(sorted(r['kh'][:2]))]) for r in rs]
    a = score(rows, "유력마 상위2", b['slots'] if b else None)
    if b and a:
        ok = [("회수율", a['ex3'] > b['ex3'], "%.1f→%.1f" % (b['ex3'], a['ex3'])),
              ("적중률", a['hitRate'] >= b['hitRate'] - 2.0, "%.1f→%.1f" % (b['hitRate'], a['hitRate'])),
              ("배당중앙", a['med'] >= b['med'] - 0.01, "%.2f→%.2f" % (b['med'], a['med'])),
              ("구좌30%", abs((a['slots'] / b['slots'] - 1) * 100) <= 30.0,
               "%+.1f%%" % ((a['slots'] / b['slots'] - 1) * 100))]
        bad = [n for n, p, _ in ok if not p]
        print("    판정 " + ("통과" if not bad else "기각(" + "·".join(bad) + ")")
              + "  [" + " · ".join("%s %s" % (n, v) for n, _, v in ok) + "]")
    ds = sorted({r['date'] for r in rs if r['date']})
    mid = ds[len(ds) // 2] if ds else ''
    for lab, f in (("전반", lambda r: r['date'] < mid), ("후반", lambda r: r['date'] >= mid)):
        x = score([(r, c) for r, c in base if f(r)], "", quiet=True)
        y = score([(r, c) for r, c in rows if f(r)], "", quiet=True)
        if x and y:
            print("      %s 회수 %.1f→%.1f %s · 배당 %.2f→%.2f"
                  % (lab, x['ex3'], y['ex3'], "개선" if y['ex3'] > x['ex3'] else "악화",
                     x['med'], y['med']))
    # 축이 정답 2두를 맞힌 비율
    a1 = sum(1 for r in rs if tuple(sorted(r['ax'])) == r['ans'])
    a2 = sum(1 for r in rs if tuple(sorted(r['kh'][:2])) == r['ans'])
    print("  축이 정답 복승과 정확히 같은 경주 — 지금 %d(%.1f%%) · 유력마상위2 %d(%.1f%%)"
          % (a1, a1 / len(rs) * 100, a2, a2 / len(rs) * 100))
