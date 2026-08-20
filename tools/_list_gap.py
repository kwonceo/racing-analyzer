# -*- coding: utf-8 -*-
"""[측정 · 읽기 전용] 회원이 받는 명단 ↔ 우리가 재는 명단.

2026-08-21 대표 지시 — 공식 71.8% ↔ 회원 체감 68.9%. 그 3포인트가 어디서 나오나.
  회원이 받는 것: kakao_sent 재생(T-5/T-7 + 마감 전 즉시변경). 🔴 발주 후(T+1)는 뺀다.
  우리가 재는 것: corePicks.displayedCombos.quinellas (마감 시점 동결)

낼 것
  ① 같은 경주에서 두 명단이 실제로 얼마나 다른가
  ② 다르면 어느 쪽이 더 많은가 — 카톡이 더 많나 적나
  ③ 3포인트가 어디서 나오나 — 한쪽에만 있는 조합의 적중·회수 기여로 분해

🔴 정제(measure_recovery CLEAN_LO/HI)를 적용한 값을 기준선으로 낸다.
🔴 배선하지 않는다.
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

RE_COMBO = re.compile(r"'combo':\s*\[([0-9,\s]+)\]")
RE_DIFF = re.compile(r'복승 (추가|제외):\s*([^\n]+)')


def _field(v):
    out = []
    if isinstance(v, list):
        for x in v:
            c = (x or {}).get('combo') if isinstance(x, dict) else x
            try:
                c = tuple(sorted(int(i) for i in (c or [])))
            except (TypeError, ValueError):
                continue
            if len(c) == 2:
                out.append(c)
        return out
    for m in RE_COMBO.finditer(str(v or '')):
        nn = [int(x) for x in re.findall(r'\d+', m.group(1))]
        if len(nn) == 2:
            out.append(tuple(sorted(nn)))
    return out


def load():
    rows = []
    for p in sorted(glob.glob(os.path.join(BASE, 'data', 'kakao_sent', '2026*.json'))):
        ymd = os.path.basename(p)[:-5]
        if not (len(ymd) == 8 and ymd.isdigit()):
            continue
        ds = '%s_%s_%s' % (ymd[:4], ymd[4:6], ymd[6:])
        try:
            js = json.load(open(p, encoding='utf-8'))
        except Exception:
            continue
        if isinstance(js, dict):
            js = list(js.values())
        js = [r for r in js if isinstance(r, dict) and r.get('raceKey')]
        js.sort(key=lambda r: float(r.get('sentEpoch') or 0))
        cur = collections.defaultdict(set)
        for r in js:
            rk, ph = r.get('raceKey'), r.get('phase')
            if ph in ('T-5', 'T-7'):
                cur[rk] = set(_field(r.get('quinellas')))
            elif ph != 'T+1변경':          # 🔴 발주 후는 뺀다 — 회원이 못 산다
                for m in RE_DIFF.finditer(str(r.get('text') or '')):
                    for c in m.group(2).split('·'):
                        nn = [int(x) for x in re.findall(r'\d+', c)]
                        if len(nn) != 2:
                            continue
                        k = tuple(sorted(nn))
                        cur[rk].add(k) if m.group(1) == '추가' else cur[rk].discard(k)
        for rk, K in cur.items():
            f = os.path.join(BASE, 'data', 'analysis_log',
                             re.sub(r'[^\w가-힣]+', '_', '%s_%s' % (ds, rk)).strip('_') + '.json')
            if not os.path.exists(f):
                continue
            try:
                d = json.load(open(f, encoding='utf-8'))
            except Exception:
                continue
            res = d.get('result') or {}
            qp = (res.get('payouts') or {}).get('quinella')
            if qp is None or res.get('1st') is None or res.get('2nd') is None:
                continue
            dc = (d.get('corePicks') or {}).get('displayedCombos') or {}
            D = set(tuple(sorted(int(v) for v in x))
                    for x in (dc.get('quinellas') or []) if len(x) == 2)
            a = tuple(sorted([res['1st'], res['2nd']]))
            mo = None
            try:
                h = M._loadh(f.replace('analysis_log', 'odds_history')) or {}
                dl = h.get('deadline_epoch')
                sn = [x for x in (h.get('snapshots') or [])
                      if x.get('t') and dl and -8 <= (x['t'] - dl) / 60 <= 0 and x.get('quinella')]
                if sn:
                    for k, v in max(sn, key=lambda x: x['t'])['quinella'].items():
                        kk = tuple(sorted(int(z) for z in str(k).replace('-', '+').split('+')))
                        if kk == a:
                            mo = float(v)
            except Exception:
                pass
            rows.append({'ymd': ymd, 'rk': rk, 'K': K, 'D': D, 'ans': a, 'qp': float(qp),
                         'clean': bool(mo and M.CLEAN_LO <= float(qp) / mo <= M.CLEAN_HI)})
    return rows


def roi(rs, key):
    slots = sum(len(r[key]) for r in rs)
    ho = sorted([r['qp'] for r in rs if r['ans'] in r[key]], reverse=True)
    if not slots:
        return None
    return {'slots': slots, 'hits': len(ho), 'ret': sum(ho),
            'roi': sum(ho) / slots * 100,
            'ex3': (sum(ho) - sum(ho[:3])) / slots * 100,
            'med': st.median(ho) if ho else 0}


def run(rs, tag):
    print('=' * 140)
    print('[%s] %d경주' % (tag, len(rs)))
    if len(rs) < 30:
        print('  판정 불가')
        return
    same = konly = donly = both = 0
    for r in rs:
        if r['K'] == r['D']:
            same += 1
        elif r['K'] < r['D']:
            donly += 1          # 화면이 더 많다
        elif r['D'] < r['K']:
            konly += 1          # 카톡이 더 많다
        else:
            both += 1
    n = len(rs)
    print('  ① 두 명단이 같은 경주            %5d (%.1f%%)' % (same, 100 * same / n))
    print('  ② 화면이 더 많다(카톡이 부분집합) %5d (%.1f%%)' % (donly, 100 * donly / n))
    print('     카톡이 더 많다               %5d (%.1f%%)' % (konly, 100 * konly / n))
    print('     서로 다름                    %5d (%.1f%%)' % (both, 100 * both / n))
    k, d = roi(rs, 'K'), roi(rs, 'D')
    print('  회원(카톡·마감전)  구좌%6d 적중%5d 회수%7.2f%% 대박뺀회수%7.2f%% 배당중앙%5.2f 경주당%5.2f'
          % (k['slots'], k['hits'], k['roi'], k['ex3'], k['med'], k['slots'] / n))
    print('  우리(판정 명단)    구좌%6d 적중%5d 회수%7.2f%% 대박뺀회수%7.2f%% 배당중앙%5.2f 경주당%5.2f'
          % (d['slots'], d['hits'], d['roi'], d['ex3'], d['med'], d['slots'] / n))
    print('  🔴 격차 회수 %+.2f%%p · 대박뺀회수 %+.2f%%p' % (d['roi'] - k['roi'], d['ex3'] - k['ex3']))
    # ③ 분해 — 한쪽에만 있는 조합
    d_only_slots = sum(len(r['D'] - r['K']) for r in rs)
    k_only_slots = sum(len(r['K'] - r['D']) for r in rs)
    d_only_hit = [r['qp'] for r in rs if r['ans'] in (r['D'] - r['K'])]
    k_only_hit = [r['qp'] for r in rs if r['ans'] in (r['K'] - r['D'])]
    print()
    print('  ③ 어디서 나오나')
    print('     화면에만 있는 조합 %5d개 · 그중 적중 %3d건 · 회수 %8.1f (배당중앙 %.2f)'
          % (d_only_slots, len(d_only_hit), sum(d_only_hit),
             st.median(d_only_hit) if d_only_hit else 0))
    print('     카톡에만 있는 조합 %5d개 · 그중 적중 %3d건 · 회수 %8.1f (배당중앙 %.2f)'
          % (k_only_slots, len(k_only_hit), sum(k_only_hit),
             st.median(k_only_hit) if k_only_hit else 0))
    if d['slots'] and k['slots']:
        print('     🔴 화면에만 있는 조합만 떼면 회수 %.2f%% — 전체 %.2f%% 를 %s'
              % (sum(d_only_hit) / d_only_slots * 100 if d_only_slots else 0, d['roi'],
                 '끌어올린다' if (sum(d_only_hit) / d_only_slots * 100 if d_only_slots else 0) > d['roi'] else '끌어내린다'))


if __name__ == '__main__':
    rs = load()
    print('kakao_sent 재생 · 결과·확정배당 확보 %d경주 (정제 통과 %d · %.1f%%)'
          % (len(rs), sum(1 for r in rs if r['clean']),
             100 * sum(1 for r in rs if r['clean']) / len(rs) if rs else 0))
    run([r for r in rs if r['clean']], '🟢 정제 적용 — 기준선')
    ds = sorted({r['ymd'] for r in rs})
    mid = ds[len(ds) // 2] if ds else ''
    run([r for r in rs if r['clean'] and r['ymd'] < mid], '전반(정제)')
    run([r for r in rs if r['clean'] and r['ymd'] >= mid], '후반(정제)')
