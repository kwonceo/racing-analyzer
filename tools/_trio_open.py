# -*- coding: utf-8 -*-
"""[측정 · 읽기 전용] 경륜 삼복승 1순위 해제 — 켜기 전 소급 대조.

2026-08-20 대표 승인. 켜기 전에 아래 넷을 **직접 확인**한다(남이 낸 숫자를 믿지 않는다).
  배당중앙 4.60 · 10배 이상 16.1% · 회수 87.3% · 대박 뺀 회수 74.0%

그리고 「회원이 실제로 겪는 것」을 함께 낸다 —
  🔴 지금 회원이 카톡에서 받는 삼복승은 **1순위 1개**이고 「· 참고」 딱지가 붙는다(KAKAO_TRIO_MAX=1).
     판정 명단(displayedCombos)에는 **0개**다. 즉 성적표에 안 들어간다.
  ⇒ 해제란 「보내는 개수를 늘리는 것」이 아니라 **그 1개를 판정 명단에 넣는 것**이다.

⚠ 정렬은 건드리지 않는다. 1순위는 지금 코드가 정하는 그대로다.
🔴 배선하지 않는다. 숫자만.
"""
import glob
import json
import os
import statistics as st
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(sport, month='2026_08'):
    out = []
    for f in sorted(glob.glob(os.path.join(BASE, 'data', 'analysis_log', month + '_*.json'))):
        try:
            d = json.load(open(f, encoding='utf-8'))
        except Exception:
            continue
        if (d.get('sport') or '') != sport:
            continue
        res = d.get('result') or {}
        if res.get('1st') is None or res.get('2nd') is None:
            continue
        po = res.get('payouts') or {}
        qp = po.get('quinella')
        tp = po.get('trifecta')
        if tp is None:
            tp = po.get('trio')
        cp = d.get('corePicks') or {}
        dc = cp.get('displayedCombos') or {}
        q = [tuple(sorted(int(v) for v in x)) for x in (dc.get('quinellas') or []) if len(x) == 2]
        ft = []
        for t in (cp.get('finalTrifectas') or []):
            c = t.get('combo') or []
            if len(c) == 3:
                try:
                    ft.append(tuple(sorted(int(v) for v in c)))
                except (TypeError, ValueError):
                    pass
        a2 = tuple(sorted([res['1st'], res['2nd']]))
        a3 = (tuple(sorted([res['1st'], res['2nd'], res['3rd']]))
              if res.get('3rd') is not None else None)
        out.append({'race': os.path.basename(f)[:-5], 'q': q, 'ft': ft,
                    'qp': (float(qp) if qp is not None else None),
                    'tp': (float(tp) if tp is not None else None),
                    'a2': a2, 'a3': a3})
    return out


def band(odds):
    tab = [(2, '~2배'), (5, '2~5배'), (10, '5~10배'), (20, '10~20배'), (50, '20~50배')]
    for hi, lab in tab:
        if odds < hi:
            return lab
    return '50배+'


ORDER = ['~2배', '2~5배', '5~10배', '10~20배', '20~50배', '50배+']


def report(hits, slots, tag):
    """hits = 적중 배당 리스트"""
    if not slots:
        print('  %-22s 표본 없음' % tag)
        return None
    n = len(hits)
    ret = sum(hits)
    ho = sorted(hits, reverse=True)
    big = sum(1 for o in hits if o >= 10)
    d = {'slots': slots, 'hits': n, 'ret': ret,
         'roi': ret / slots * 100,
         'ex3': (ret - sum(ho[:3])) / slots * 100,
         'ex5': (ret - sum(ho[:5])) / slots * 100,
         'med': st.median(hits) if hits else 0,
         'big': big, 'bigPct': (big / n * 100) if n else 0}
    print('  %-22s 구좌%5d 적중%4d 회수%6.1f%% 대박뺀회수%6.1f%% 5제외%6.1f%% 배당중앙%6.2f 10배이상%4d(%4.1f%%)'
          % (tag, d['slots'], d['hits'], d['roi'], d['ex3'], d['ex5'], d['med'], d['big'], d['bigPct']))
    cnt = {k: 0 for k in ORDER}
    for o in hits:
        cnt[band(o)] += 1
    print('      배당대  ' + ' · '.join('%s %.1f%%' % (k, 100 * cnt[k] / n if n else 0) for k in ORDER))
    return d


def run(sport, tag):
    rs = load(sport)
    print('=' * 150)
    print('[%s] 8월 결과확정 %d경주' % (tag, len(rs)))
    have_tp = sum(1 for r in rs if r['tp'] is not None)
    print('  삼복승 확정배당 보유 %d (%.1f%%) · 삼복승 만든 경주 %d (%.1f%%)'
          % (have_tp, 100 * have_tp / len(rs) if rs else 0,
             sum(1 for r in rs if r['ft']), 100 * sum(1 for r in rs if r['ft']) / len(rs) if rs else 0))
    # 지금 — 복승만 (회원 판정 명단)
    q_slots = sum(len(r['q']) for r in rs)
    q_hits = [r['qp'] for r in rs if r['qp'] is not None and r['a2'] in r['q']]
    a = report(q_hits, q_slots, '지금 · 복승만')
    # 추가분 — 삼복승 1순위
    t_slots = sum(1 for r in rs if r['ft'])
    t_hits = [r['tp'] for r in rs
              if r['ft'] and r['tp'] is not None and r['a3'] and r['ft'][0] == r['a3']]
    b = report(t_hits, t_slots, '추가 · 삼복승 1순위')
    # 합산
    c = report(q_hits + t_hits, q_slots + t_slots, '합산(복승 + 삼복승1)')
    if a and b and c:
        print()
        print('  🔴 판정 — 10배 이상 적중 %d건 -> %d건 (%+.0f%%) · 배당중앙 %.2f -> %.2f · 회수 %.1f%% -> %.1f%%'
              % (a['big'], a['big'] + b['big'],
                 (b['big'] / a['big'] * 100) if a['big'] else 0,
                 a['med'], c['med'], a['roi'], c['roi']))
        print('  ⚠ 구좌 %d -> %d (투자 %.2f배). 합산 회수율 %.1f%% 로 **100%% 미만**이다 — 「사면 번다」가 아니다.'
              % (a['slots'], c['slots'], c['slots'] / a['slots'] if a['slots'] else 0, c['roi']))


if __name__ == '__main__':
    for sp, tag in (('cycle', '경륜'), ('horse', '경마')):
        run(sp, tag)
