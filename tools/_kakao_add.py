# -*- coding: utf-8 -*-
"""[측정 · 읽기 전용] 카톡에 되살린 조합·교차 짝을 넣으면.

2026-08-21 대표 지시.
  우리가 만든 규칙이 회수율 82.5%인데 회원이 못 받는다. 그게 격차의 60%다.

안
  A 지금대로            회원이 실제로 받은 것(T-5/T-7 + 마감 전 즉시변경)
  B A + 되살린 조합      displayedCombos.extra 의 src=reviveCut
  C B + 교차 짝          위 + src=crossPair (finalQuinellas 의 crossPair 플래그 포함)

🔴 판정 셋
  ① 대박 뺀 회수율이 오를 것
  ② 경주당 조합이 3개를 넘지 않을 것  ← T+1 을 끄고 줄인 소음을 다시 늘리면 안 된다
  ③ 적중배당 중앙이 안 내려갈 것
⚠ 기간 반 분할에서 앞뒤 둘 다 같은 방향이어야 한다.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔴 표본 정의 · 정제 필터 · 용어 — **밝히지 않으면 같은 이름의 수치가 두 배 달라진다**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
표본     data/kakao_sent/YYYYMMDD.json 을 시간순 재생해 만든 경주 중,
         같은 날짜의 analysis_log 파일이 있고 **결과 착순 + 확정 복승배당**이 있는 것.
         🔴 카톡을 안 보낸 경주는 표본에 없다(회원 기준 측정이므로).
정제     확정배당 ÷ 마감 직전 배당 이 0.5~2.0 배 안(measure_recovery 의 CLEAN_LO/HI).
         🔴 정제를 안 걸면 같은 데이터에서 회수율이 30%p 넘게 부풀 수 있다.
회수율   Σ(적중 조합의 확정배당) ÷ 총 구좌 × 100.  구좌 = 조합 1개 = 1구좌.
대박뺀   위에서 **적중배당 상위 3건을 뺀** 값. 극단값 의존을 걷어낸다.
적중률   경주 단위. 그 경주 명단에 정답 복승이 있으면 1건(조합 수와 무관).
경주당   총 구좌 ÷ 경주 수.
회원 명단 T-5/T-7 발송분 + 마감 전 즉시변경의 「복승 추가/제외」 누적.
         🔴 발주 후(T+1변경)는 **뺀다** — 회원이 살 수 없는 시점이다.
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


def _tup(x):
    try:
        t = tuple(sorted(int(v) for v in (x or [])))
    except (TypeError, ValueError):
        return None
    return t if len(t) == 2 else None


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
            elif ph != 'T+1변경':
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
            cp = d.get('corePicks') or {}
            dc = cp.get('displayedCombos') or {}
            D = set(t for t in (_tup(x) for x in (dc.get('quinellas') or [])) if t)
            revive, cross = set(), set()
            for e in (dc.get('extra') or []):
                t = _tup(e.get('combo'))
                if not t:
                    continue
                s = str(e.get('src') or '')
                if s == 'reviveCut':
                    revive.add(t)
                elif s == 'crossPair':
                    cross.add(t)
            for q in (cp.get('finalQuinellas') or []):
                t = _tup(q.get('combo'))
                if not t:
                    continue
                if q.get('crossPair'):
                    cross.add(t)
                if q.get('reviveCut') or q.get('revived'):
                    revive.add(t)
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
            rows.append({'ymd': ymd, 'rk': rk, 'K': K, 'D': D,
                         'revive': revive & D, 'cross': cross & D,
                         'ans': a, 'qp': float(qp),
                         'clean': bool(mo and M.CLEAN_LO <= float(qp) / mo <= M.CLEAN_HI)})
    return rows


def plan(r, name):
    if name == 'A':
        return set(r['K'])
    if name == 'B':
        return set(r['K']) | r['revive']
    if name == 'C':
        return set(r['K']) | r['revive'] | r['cross']
    if name == 'D':
        return set(r['D'])
    return set()


def score(rs, name, tag, base=None, quiet=False):
    slots = 0
    ho = []
    for r in rs:
        s = plan(r, name)
        slots += len(s)
        if r['ans'] in s:
            ho.append(r['qp'])
    if not slots:
        return None
    ho.sort(reverse=True)
    d = {'n': len(rs), 'slots': slots, 'hits': len(ho),
         'hitRate': len(ho) / len(rs) * 100,
         'roi': sum(ho) / slots * 100,
         'ex3': (sum(ho) - sum(ho[:3])) / slots * 100,
         'med': st.median(ho) if ho else 0,
         'per': slots / len(rs)}
    if not quiet:
        g = '' if base is None else ' 구좌%+6.1f%%' % ((slots / base - 1) * 100)
        print('  %-26s 경주%5d 구좌%6d 적중%5d(%4.1f%%) 회수%7.2f%% 대박뺀회수%7.2f%% 적중배당중앙%5.2f 경주당%5.2f%s'
              % (tag, d['n'], slots, d['hits'], d['hitRate'], d['roi'], d['ex3'],
                 d['med'], d['per'], g))
    return d


def verdict(a, x):
    ok = [('대박뺀회수', x['ex3'] > a['ex3'], '%.2f->%.2f' % (a['ex3'], x['ex3'])),
          ('경주당3개', x['per'] <= 3.0, '%.2f개' % x['per']),
          ('배당중앙', x['med'] >= a['med'] - 0.01, '%.2f->%.2f' % (a['med'], x['med']))]
    bad = [n for n, p, _ in ok if not p]
    return ('🟢 통과' if not bad else '기각(' + '·'.join(bad) + ')') + '  [' + \
           ' · '.join('%s %s' % (n, v) for n, _, v in ok) + ']'


NAMES = [('A', 'A 지금대로(회원 수신)'), ('B', 'B + 되살린 조합'),
         ('C', 'C + 되살린 조합·교차 짝'), ('D', '(참고) 판정 명단 전체')]


def run(rs, tag, split=None):
    print('=' * 156)
    nv = sum(1 for r in rs if r['revive'])
    nc = sum(1 for r in rs if r['cross'])
    print('[%s] %d경주 · 되살린 조합 있는 경주 %d(%.1f%%) · 교차 짝 있는 경주 %d(%.1f%%)'
          % (tag, len(rs), nv, 100 * nv / len(rs) if rs else 0,
             nc, 100 * nc / len(rs) if rs else 0))
    if len(rs) < 30:
        print('  판정 불가')
        return
    a = score(rs, 'A', NAMES[0][1])
    for k, lab in NAMES[1:]:
        x = score(rs, k, lab, a['slots'])
        if k == 'D' or not x:
            continue
        print('      판정 ' + verdict(a, x))
        if split:
            for pl, f in split:
                sub = [r for r in rs if f(r)]
                if len(sub) < 30:
                    continue
                p, q = score(sub, 'A', '', quiet=True), score(sub, k, '', quiet=True)
                if p and q:
                    print('        %s 대박뺀회수 %.2f->%.2f %s · 배당 %.2f->%.2f %s'
                          % (pl, p['ex3'], q['ex3'], '개선' if q['ex3'] > p['ex3'] else '악화',
                             p['med'], q['med'], '개선' if q['med'] >= p['med'] else '악화'))


if __name__ == '__main__':
    rs = load()
    cl = [r for r in rs if r['clean']]
    print('표본 %d경주 (정제 통과 %d · %.1f%%)  ⚠ 정제 = 확정배당 ÷ 마감배당 %.1f~%.1f배'
          % (len(rs), len(cl), 100 * len(cl) / len(rs) if rs else 0, M.CLEAN_LO, M.CLEAN_HI))
    ds = sorted({r['ymd'] for r in cl})
    mid = ds[len(ds) // 2] if ds else ''
    run(cl, '🟢 정제 적용 — 기준선',
        split=[('전반', lambda r: r['ymd'] < mid), ('후반', lambda r: r['ymd'] >= mid)])
