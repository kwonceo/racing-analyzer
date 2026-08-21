# -*- coding: utf-8 -*-
"""[측정 · 읽기 전용] 판형(선행 마릿수)별로 성적이 갈리는가.

2026-08-21 대표 요청 — 「전적표를 보고 경기별 유형·상황을 예측하는 별도 분석기」.
  ⚠ 이 프로젝트에서 전개 기반 **조합 예측**은 여러 번 기각됐다
    (_scenario_plan 부스팅 z=-2.80 · flow_table 생존 셀 0 · 라인 축 엣지 하한 미달).
  🔴 그래서 먼저 답해야 하는 것은 **「유형 분류」가 「조합 예측」과 다른가**이다.
    조합을 바꾸지 않고 **판형에 이름만 붙이는** 것이라면 기각 이력에 걸리지 않는다.
    다만 그러려면 **판형별로 성적이 실제로 갈려야** 한다. 그것을 잰다.

⚠ 표본·정제·구좌·명단 (원칙 26)
  표본   analysis_log 8월 · sport=cycle(경륜만 — 각질이 98% 채워지는 종목이다)
         결과 착순 + 확정 복승배당 + 표시 명단(displayedCombos.quinellas)이 있어야 한다
  정제   확정배당 ÷ 마감 직전 배당 0.5~2.0배(measure_recovery CLEAN_LO/HI)
  구좌   조합 1개 = 1구좌
  명단   판정 명단(displayedCombos) — 회원 수신 명단이 아니다
  적중률 경주 단위
🔴 배선하지 않는다. 숫자만.
"""
import collections
import glob
import json
import os
import re
import statistics as st
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, 'tools'))
import measure_recovery as M

LEAD = ('선행', '逃げ', 'nige')


def shape(n_lead, n_horse):
    """판형 이름 — 선행 마릿수로만 가른다(라인은 horses[] 에 없다)."""
    if n_lead <= 1:
        return '단독선행(0~1)'
    if n_lead == 2:
        return '정석(2)'
    if n_lead == 3:
        return '경합(3)'
    return '난전(4+)'


def load(sport='cycle'):
    out = []
    for f in sorted(glob.glob(os.path.join(BASE, 'data', 'analysis_log', '2026_08_*.json'))):
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
        hs = d.get('horses') or []
        gaits = [str(h.get('gait') or h.get('styleType') or '') for h in hs]
        if not any(gaits):
            continue
        n_lead = sum(1 for g in gaits if any(k in g for k in LEAD))
        dc = (d.get('corePicks') or {}).get('displayedCombos') or {}
        q = [tuple(sorted(int(v) for v in x)) for x in (dc.get('quinellas') or []) if len(x) == 2]
        if not q:
            continue
        a = tuple(sorted([res['1st'], res['2nd']]))
        mo = None
        try:
            h = M._loadh(f.replace('analysis_log', 'odds_history')) or {}
            dl = h.get('deadline_epoch')
            sn = [s for s in (h.get('snapshots') or [])
                  if s.get('t') and dl and -8 <= (s['t'] - dl) / 60 <= 0 and s.get('quinella')]
            if sn:
                for k, v in max(sn, key=lambda s: s['t'])['quinella'].items():
                    if tuple(sorted(int(z) for z in str(k).replace('-', '+').split('+'))) == a:
                        mo = float(v)
        except Exception:
            pass
        m = re.match(r'(\d{4}_\d{2}_\d{2})', os.path.basename(f))
        out.append({'date': m.group(1) if m else '', 'shape': shape(n_lead, len(hs)),
                    'lead': n_lead, 'n': len(hs), 'q': q, 'ans': a, 'qp': float(qp),
                    'clean': bool(mo and M.CLEAN_LO <= float(qp) / mo <= M.CLEAN_HI)})
    return out


def score(rs):
    slots = sum(len(r['q']) for r in rs)
    ho = sorted([r['qp'] for r in rs if r['ans'] in r['q']], reverse=True)
    if not slots:
        return None
    return {'n': len(rs), 'slots': slots, 'hits': len(ho),
            'hitRate': len(ho) / len(rs) * 100,
            'roi': sum(ho) / slots * 100,
            'ex3': (sum(ho) - sum(ho[:3])) / slots * 100,
            'med': st.median(ho) if ho else 0,
            'per': slots / len(rs)}


ORDER = ['단독선행(0~1)', '정석(2)', '경합(3)', '난전(4+)']


def block(rs, tag):
    print('=' * 140)
    print('[%s] %d경주' % (tag, len(rs)))
    g = collections.defaultdict(list)
    for r in rs:
        g[r['shape']].append(r)
    for k in ORDER:
        v = g.get(k) or []
        if len(v) < 30:
            print('  %-14s %4d경주 — 판정 불가(30 미만)' % (k, len(v)))
            continue
        d = score(v)
        print('  %-14s 경주%5d 구좌%5d 적중%5.1f%% 회수%6.1f%% 대박뺀회수%6.1f%% 적중배당중앙%5.2f 경주당%5.2f'
              % (k, d['n'], d['slots'], d['hitRate'], d['roi'], d['ex3'], d['med'], d['per']))


if __name__ == '__main__':
    rs = load()
    cl = [r for r in rs if r['clean']]
    print('표본 %d경주 (정제 통과 %d · %.1f%%) · 경륜만 · 판정 명단 기준'
          % (len(rs), len(cl), 100 * len(cl) / len(rs) if rs else 0))
    print('선행 마릿수 분포:', dict(sorted(collections.Counter(r['lead'] for r in cl).items())))
    block(cl, '🟢 정제 적용 — 기준선')
    ds = sorted({r['date'] for r in cl})
    mid = ds[len(ds) // 2] if ds else ''
    block([r for r in cl if r['date'] < mid], '전반(정제)')
    block([r for r in cl if r['date'] >= mid], '후반(정제)')
