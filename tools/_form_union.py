# -*- coding: utf-8 -*-
"""[측정 · 읽기 전용] 전적과 시장을 **섞지 말고 합쳤을 때**.

2026-08-20 대표 지시.
  전적 상위3      1·2착 둘 다 포함 23.2%
  시장 상위3                      42.5%
  🔴 keyHorses(섞음)               32.9%   ← 시장 단독보다 나쁘다
  🟢 전적3 ∪ 시장3                 56.8%   ← 시장 단독보다 낫다
  ⇒ 「가중 평균으로 섞기」가 두 신호를 서로 희석시키는 것으로 보인다.
  ⚠ 그러나 포함률은 회수율이 아니다. 합집합은 두수가 늘어 구좌가 는다.

안
  A 현행(displayedCombos)          기준선
  B 시장 상위3 전조합              3구좌
  C 전적 상위3 전조합              3구좌
  D keyHorses 상위3 전조합         3구좌
  E 합집합 전조합                  최대 15구좌
  F 합집합 안에서 배당 낮은 순 3    3구좌
  G 🔴 교차만 — 전적3 과 시장3 에서 **한 쪽씩** 뽑은 짝(겹치는 말은 제외)
    (원칙 14: 두 정보원이 서로 다른 말을 지목한 자리)

🔴 판정 넷: 대박 뺀 회수율↑ · 적중률 유지(-2%p 이내) · 배당중앙 유지/↑ · 구좌 30% 이내
⚠ 기간 반 분할에서 앞뒤 둘 다 같은 방향이어야 한다.
🔴 배선하지 않는다. 숫자만 낸다.
"""
import glob
import itertools
import json
import os
import re
import statistics as st
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, 'tools'))
import measure_recovery as M


def market_rank(q):
    """말별 최저 복승배당 → 낮은 순. 🔴 단승을 안 쓴다(경륜은 단승 미수집)."""
    best = {}
    for pair, o in q.items():
        for n in pair:
            if n not in best or o < best[n]:
                best[n] = o
    return [n for n, _ in sorted(best.items(), key=lambda kv: kv[1])]


def load(sport):
    out = []
    for f in sorted(glob.glob(os.path.join(BASE, 'data', 'analysis_log', '2026_0*.json'))):
        b = os.path.basename(f)
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
        hs = [h for h in (d.get('horses') or [])
              if isinstance(h.get('record_score'), (int, float)) and str(h.get('no') or '').isdigit()]
        form3 = [int(h['no']) for h in sorted(hs, key=lambda h: -h['record_score'])[:3]]
        if len(form3) < 3:
            continue
        kh = [int(x) for x in (d.get('keyHorses') or []) if str(x).isdigit()][:3]
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
        mk3 = market_rank(q)[:3]
        if len(mk3) < 3:
            continue
        m = re.match(r'(\d{4}_\d{2}_\d{2})', b)
        out.append({'race': b[:-5], 'date': m.group(1) if m else '', 'dc': dc, 'q': q,
                    'qp': float(qp), 'ans': ans, 'form3': form3, 'mk3': mk3, 'kh': kh})
    return out


def combos(nos):
    return [tuple(sorted(c)) for c in itertools.combinations(sorted(set(nos)), 2)]


def plan(r, name):
    if name == 'A':
        return list(r['dc'])
    if name == 'B':
        return combos(r['mk3'])
    if name == 'C':
        return combos(r['form3'])
    if name == 'D':
        return combos(r['kh']) if len(r['kh']) >= 2 else list(r['dc'])
    uni = list(dict.fromkeys(r['form3'] + r['mk3']))
    if name == 'E':
        return combos(uni)
    if name == 'F':
        return sorted(combos(uni), key=lambda c: r['q'].get(c) or 9e9)[:3]
    if name == 'G':
        only_f = [n for n in r['form3'] if n not in r['mk3']]
        only_m = [n for n in r['mk3'] if n not in r['form3']]
        cr = [tuple(sorted((a, b))) for a in only_f for b in only_m]
        return cr if cr else list(r['dc'])
    return []


def score(rs, name, base=None, quiet=False, label=None):
    rows = [(r, plan(r, name)) for r in rs]
    slots = sum(len(c) for _, c in rows)
    if not slots:
        return None
    hit = [r for r, c in rows if r['ans'] in c]
    ho = sorted([r['qp'] for r in hit], reverse=True)
    od = [r['q'].get(c2) for r, c in rows for c2 in c]
    od = [x for x in od if x]
    d = {'n': len(rows), 'slots': slots, 'hits': len(hit),
         'hitRate': len(hit) / len(rows) * 100,
         'ex3': (sum(ho) - sum(ho[:3])) / slots * 100,
         'med': st.median(od) if od else 0,
         'medHit': st.median([r['qp'] for r in hit]) if hit else 0}
    if not quiet:
        g = "" if base is None else " 구좌%+7.1f%%" % ((slots / base - 1) * 100)
        print("  %-30s 경주%4d 구좌%5d 적중%4d(%4.1f%%) 대박뺀회수%7.1f%% 배당중앙%6.2f 적중배당중앙%6.2f%s%s"
              % (label or name, d['n'], slots, d['hits'], d['hitRate'], d['ex3'],
                 d['med'], d['medHit'], g, "" if d['hits'] >= 30 else "  판정불가"))
    return d


def verdict(b, a):
    if not b or not a:
        return "판정불가"
    ok = [("회수율", a['ex3'] > b['ex3'], "%.1f->%.1f" % (b['ex3'], a['ex3'])),
          ("적중률", a['hitRate'] >= b['hitRate'] - 2.0, "%.1f->%.1f" % (b['hitRate'], a['hitRate'])),
          ("배당중앙", a['med'] >= b['med'] - 0.01, "%.2f->%.2f" % (b['med'], a['med'])),
          ("구좌30%", abs((a['slots'] / b['slots'] - 1) * 100) <= 30.0,
           "%+.1f%%" % ((a['slots'] / b['slots'] - 1) * 100))]
    bad = [n for n, p, _ in ok if not p]
    return ("🟢 통과" if not bad else "기각(" + "·".join(bad) + ")") + "  [" + \
           " · ".join("%s %s" % (n, v) for n, _, v in ok) + "]"


NAMES = [('A', 'A 현행(기준선)'), ('B', 'B 시장 상위3 전조합'), ('C', 'C 전적 상위3 전조합'),
         ('D', 'D keyHorses 상위3 전조합'), ('E', 'E 합집합 전조합'),
         ('F', 'F 합집합 중 배당 낮은 순 3'), ('G', 'G 교차만(전적x시장 서로 다른 말)')]


def incl(rs, key):
    return sum(1 for r in rs if set(r['ans']) <= set(key(r))) / len(rs) * 100


def run(rs, tag):
    print("=" * 152)
    print("[%s] 정제 %d경주 · 1·2착 둘 다 포함: 전적3 %.1f%% · 시장3 %.1f%% · keyHorses %.1f%% · 합집합 %.1f%%"
          % (tag, len(rs), incl(rs, lambda r: r['form3']), incl(rs, lambda r: r['mk3']),
             incl(rs, lambda r: r['kh']), incl(rs, lambda r: r['form3'] + r['mk3'])))
    b = score(rs, 'A', label='A 현행(기준선)')
    ds = sorted({r['date'] for r in rs if r['date']})
    mid = ds[len(ds) // 2] if ds else ''
    for nm, lab in NAMES[1:]:
        a = score(rs, nm, b['slots'] if b else None, label=lab)
        if not a:
            continue
        print("      판정 " + verdict(b, a))
        for pl, fn in (("전반", lambda r: r['date'] < mid), ("후반", lambda r: r['date'] >= mid)):
            sub = [r for r in rs if fn(r)]
            if len(sub) < 20:
                continue
            x, y = score(sub, 'A', quiet=True), score(sub, nm, quiet=True)
            if x and y:
                print("        %s 회수 %.1f->%.1f %s · 배당 %.2f->%.2f %s"
                      % (pl, x['ex3'], y['ex3'], "개선" if y['ex3'] > x['ex3'] else "악화",
                         x['med'], y['med'], "개선" if y['med'] >= x['med'] else "악화"))


if __name__ == "__main__":
    for sp, tag in (("horse", "경마"), ("cycle", "경륜")):
        rs = load(sp)
        if len(rs) < 30:
            print("[%s] 표본 %d — 판정 불가" % (tag, len(rs)))
            continue
        run(rs, tag)
