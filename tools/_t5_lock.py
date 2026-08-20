# -*- coding: utf-8 -*-
"""[측정 · 읽기 전용] T-5 고정 — 켜기 전 소급 대조.

2026-08-20 대표 승인. 켜기 전에 **내가 직접** 확인한다.
  회수 100.3 · 대박 뺀 78.7 · 배당중앙 2.40 · 경주당 1.77

세 안 (전부 회원이 실제로 받은 것 기준 · kakao_sent 재생)
  A T-5 고정      T-5/T-7 명단 그대로. 그 뒤 변경 없음
  B 마감 전 변경   T-5 + 즉시변경(마감 전)          ← 지금 회원이 겪는 것
  C 발주 후 포함   B + T+1변경                      ← 지금 우리가 세던 것

🔴 정제 필터를 **적용한 값과 안 한 값을 함께** 낸다.
   measure_recovery 의 CLEAN_LO/HI(확정배당 ÷ 마감배당 0.5~2.0)를 그대로 쓴다.
   ⚠ 정제 없는 100%대 수치를 밖에 내면 「사면 번다」로 읽힌다.
⚠ 기간 반 분할에서 앞뒤 둘 다 같은 방향인지 본다.
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
    """(ymd, rk) -> {A,B,C 명단, 정답, 확정배당, 마감배당, 정제여부}"""
    races = {}
    for p in sorted(glob.glob(os.path.join(BASE, 'data', 'kakao_sent', '2026*.json'))):
        ymd = os.path.basename(p)[:-5]
        if not (len(ymd) == 8 and ymd.isdigit()):
            continue
        ds = '%s_%s_%s' % (ymd[:4], ymd[4:6], ymd[6:])
        rows = json.load(open(p, encoding='utf-8'))
        if isinstance(rows, dict):
            rows = list(rows.values())
        rows = [r for r in rows if isinstance(r, dict) and r.get('raceKey')]
        rows.sort(key=lambda r: float(r.get('sentEpoch') or 0))
        cur = collections.defaultdict(lambda: {'A': set(), 'B': set(), 'C': set()})
        for r in rows:
            rk, ph = r.get('raceKey'), r.get('phase')
            s = cur[rk]
            if ph in ('T-5', 'T-7'):
                base = set(_field(r.get('quinellas')))
                s['A'], s['B'], s['C'] = set(base), set(base), set(base)
            else:
                for m in RE_DIFF.finditer(str(r.get('text') or '')):
                    for c in m.group(2).split('·'):
                        nn = [int(x) for x in re.findall(r'\d+', c)]
                        if len(nn) != 2:
                            continue
                        k = tuple(sorted(nn))
                        tgts = [s['C']] if ph == 'T+1변경' else [s['B'], s['C']]
                        for t in tgts:
                            t.add(k) if m.group(1) == '추가' else t.discard(k)
        for rk, s in cur.items():
            if not (s['A'] or s['B'] or s['C']):
                continue
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
            clean = bool(mo and M.CLEAN_LO <= float(qp) / mo <= M.CLEAN_HI)
            races[(ymd, rk)] = {'A': s['A'], 'B': s['B'], 'C': s['C'],
                                'ans': a, 'qp': float(qp), 'clean': clean}
    return races


def score(rs, key, tag, base=None):
    slots = sum(len(r[key]) for r in rs)
    if not slots:
        return None
    ho = sorted([r['qp'] for r in rs if r['ans'] in r[key]], reverse=True)
    d = {'n': len(rs), 'slots': slots, 'hits': len(ho),
         'hitRate': len(ho) / len(rs) * 100,
         'roi': sum(ho) / slots * 100,
         'ex3': (sum(ho) - sum(ho[:3])) / slots * 100,
         'med': st.median(ho) if ho else 0,
         'per': slots / len(rs)}
    g = '' if base is None else ' 구좌%+6.1f%%' % ((slots / base - 1) * 100)
    print('  %-22s 경주%5d 구좌%6d 적중%5d(%4.1f%%) 회수%7.1f%% 대박뺀회수%7.1f%% 적중배당중앙%6.2f 경주당%5.2f%s'
          % (tag, d['n'], slots, d['hits'], d['hitRate'], d['roi'], d['ex3'], d['med'], d['per'], g))
    return d


NAMES = [('A', 'A T-5 고정'), ('B', 'B 마감 전 변경(현행)'), ('C', 'C 발주 후 포함')]


def block(rs, tag):
    print('=' * 154)
    print('[%s] %d경주' % (tag, len(rs)))
    if len(rs) < 30:
        print('  판정 불가')
        return
    b = None
    for k, lab in NAMES:
        d = score(rs, k, lab, b)
        if k == 'A':
            b = d['slots']
    canc = sum(1 for r in rs if r['A'] - r['B'])
    print('  🔴 추천이 취소당한 경주 %d (%.1f%%)' % (canc, 100 * canc / len(rs)))


if __name__ == '__main__':
    races = load()
    rs = list(races.values())
    print('kakao_sent 재생 · 결과·확정배당 확보 %d경주 (정제 통과 %d · %.1f%%)'
          % (len(rs), sum(1 for r in rs if r['clean']),
             100 * sum(1 for r in rs if r['clean']) / len(rs) if rs else 0))
    block(rs, '전체 · 정제 안 함  ⚠ 이 값을 밖에 내지 말 것')
    cl = [r for r in rs if r['clean']]
    block(cl, '🟢 정제 적용 — 이 값이 기준선이다')
    ds = sorted({k[0] for k in races})
    mid = ds[len(ds) // 2] if ds else ''
    for lab, f in (('전반(정제)', lambda k: k[0] < mid), ('후반(정제)', lambda k: k[0] >= mid)):
        block([v for k, v in races.items() if f(k) and v['clean']], lab)
