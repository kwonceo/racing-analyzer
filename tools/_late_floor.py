# -*- coding: utf-8 -*-
"""[측정 · 읽기 전용] 뒤에 붙는 규칙에 배당 하한을 걸면.

2026-08-17 대표 지시.
  실물 욧카이치 7경주 — 3+5 가 4.5배로 적중인데 회수가 92%다. **맞고도 손해다.**
  같이 든 1+5 가 **1.2배**로 비중의 77%를 먹었다. 그 하나만 없었으면 392%였다.
  🔴 그 1+5 는 T-6 에 빠졌다가 T-3 에 **되살리기 규칙**으로 다시 들어왔다.

대상(뒤에 붙는 규칙 전부)
  reviveCut(되살린 조합) · crossPair(교차 짝) · trioPair(삼복승 짝) · evRescue(중고배당 복원)

하한을 1.5 / 2.0 / 2.5 / 3.0 배로 나눠 잰다.
🔴 판정 넷: 대박 뺀 회수율↑ · 적중률 유지 · 배당중앙↑ · 구좌 30% 이내.
"""
import glob
import json
import os
import re
import statistics as st
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, 'tools'))
import measure_recovery as M

FLOORS = (1.5, 2.0, 2.5, 3.0)
LATE = ("reviveCut", "crossPair", "trioPair", "dark")


def load(sport=None):
    out = []
    for f in sorted(glob.glob(os.path.join(BASE, 'data', 'analysis_log', '2026_0*.json'))):
        b = os.path.basename(f)
        try:
            d = json.load(open(f, encoding='utf-8'))
        except Exception:
            continue
        if sport and (d.get('sport') or '') != sport:
            continue
        res = d.get('result') or {}
        qp = (res.get('payouts') or {}).get('quinella')
        if qp is None or res.get('1st') is None or res.get('2nd') is None:
            continue
        cp = d.get('corePicks') or {}
        dcx = cp.get('displayedCombos') or {}
        dc = []
        for c in (dcx.get('quinellas') or []):
            try:
                dc.append(tuple(sorted(int(x) for x in c)))
            except (TypeError, ValueError):
                pass
        if not dc:
            continue
        late = {}
        for e in (dcx.get('extra') or []):
            s = str(e.get('src') or '')
            if s in LATE:
                try:
                    late[tuple(sorted(int(x) for x in (e.get('combo') or [])))] = s
                except (TypeError, ValueError):
                    pass
        for q in (cp.get('finalQuinellas') or []):
            if q.get('evRescue'):
                try:
                    late[tuple(sorted(int(x) for x in (q.get('combo') or [])))] = 'evRescue'
                except (TypeError, ValueError):
                    pass
        if not late:
            continue                       # 뒤에 붙은 것이 없으면 이 안과 무관
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
                    'sport': d.get('sport') or '?', 'dc': dc, 'late': late, 'q': q,
                    'qp': float(qp), 'ans': ans})
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
        print("  %-24s 경주%4d 구좌%5d 적중%3d(%4.1f%%) 대박뺀회수%6.1f%% 배당중앙%6.2f%s%s"
              % (tag, d['n'], slots, d['hits'], d['hitRate'], d['ex3'], d['med'], g,
                 "" if d['hits'] >= 30 else "  판정불가"))
    return d


def verdict(b, a):
    if not b or not a:
        return "판정불가"
    ok = [("회수율", a['ex3'] > b['ex3'], "%.1f→%.1f" % (b['ex3'], a['ex3'])),
          ("적중률", a['hitRate'] >= b['hitRate'] - 2.0, "%.1f→%.1f" % (b['hitRate'], a['hitRate'])),
          ("배당중앙", a['med'] >= b['med'] - 0.01, "%.2f→%.2f" % (b['med'], a['med'])),
          ("구좌30%", abs((a['slots'] / b['slots'] - 1) * 100) <= 30.0,
           "%+.1f%%" % ((a['slots'] / b['slots'] - 1) * 100))]
    bad = [n for n, p, _ in ok if not p]
    return ("통과" if not bad else "기각(" + "·".join(bad) + ")") + "  [" + \
           " · ".join("%s %s" % (n, v) for n, _, v in ok) + "]"


def run(rs, tag):
    print("=" * 124)
    nlate = sum(len(r['late']) for r in rs)
    print("[%s] 뒤에 붙은 조합이 있는 경주 %d · 그 조합 %d개" % (tag, len(rs), nlate))
    base = [(r, list(r['dc'])) for r in rs]
    b = score(base, "지금(하한 없음)")
    ds = sorted({r['date'] for r in rs if r['date']})
    mid = ds[len(ds) // 2] if ds else ''
    for fl in FLOORS:
        rows, cut, cuthit = [], 0, 0
        for r in rs:
            c = []
            for k in r['dc']:
                if k in r['late']:
                    o = r['q'].get(k)
                    if o is not None and o < fl:
                        cut += 1
                        if k == r['ans']:
                            cuthit += 1
                        continue
                c.append(k)
            rows.append((r, c))
        a = score(rows, "하한 %.1f배" % fl, b['slots'] if b else None)
        print("    판정 " + verdict(b, a))
        print("    잘라낸 조합 %d개 · 그중 정답이던 것 %d개" % (cut, cuthit))
        for lab, f in (("전반", lambda r: r['date'] < mid), ("후반", lambda r: r['date'] >= mid)):
            x = score([(r, c) for r, c in base if f(r)], "", quiet=True)
            y = score([(r, c) for r, c in rows if f(r)], "", quiet=True)
            if x and y:
                print("      %s 회수 %.1f→%.1f %s · 배당 %.2f→%.2f %s"
                      % (lab, x['ex3'], y['ex3'], "개선" if y['ex3'] > x['ex3'] else "악화",
                         x['med'], y['med'], "개선" if y['med'] >= x['med'] else "악화"))


def check(rs):
    print("=" * 124)
    print("[검산]")
    for want in ('욧카이치_7경주', '오가키_1경주'):
        hit = [r for r in rs if want in r['race']]
        if not hit:
            print("  %s — 대상에 없음(결과 미저장이거나 뒤에 붙은 조합 없음)" % want)
            continue
        r = hit[-1]
        print("  %s 정답 %s(%.1f배)" % (r['race'], list(r['ans']), r['qp']))
        for k in r['dc']:
            o = r['q'].get(k)
            src = r['late'].get(k, '본선')
            mark = " ← 정답" if k == r['ans'] else ""
            cuts = [fl for fl in FLOORS if k in r['late'] and o is not None and o < fl]
            print("     %s %-7s %-10s %s%s"
                  % (list(k), ("%.1f배" % o) if o else "?", src,
                     ("하한 %s에서 잘림" % cuts[0]) if cuts else "", mark))


if __name__ == "__main__":
    allr = load()
    check(allr)
    for sp, tag in (("cycle", "경륜"), ("horse", "경마")):
        rs = [r for r in allr if r['sport'] == sp]
        if len(rs) >= 20:
            run(rs, tag)
        else:
            print("=" * 124)
            print("[%s] 표본 %d — 판정 불가" % (tag, len(rs)))
    if len(allr) >= 20:
        run(allr, "전체")
