# -*- coding: utf-8 -*-
"""[상품 분리 성적] 본선과 한방을 **따로** 잰다.

2026-08-16 대표 승인. docs/상품분리_설계안.md 의 ① 단계.

🔴 잣대가 다르다. 섞어서 하나의 회수율로 보고하면 이 분리가 무의미해진다.
   본선  대박 뺀 회수율(상위 3건 제외)      판정선 74.5%
   한방  적중배당 중앙 · 적중 건수          🔴 회수율로 기각하지 않는다
        최소선 — 30경주에 적중 1건(적중률 3.33%). 미달이면 그 자리를 접는다.

가르는 규칙(app.py 와 같다 · 한방은 셋뿐)
   ① 교차 짝(crossPair)  ② 기대값 복원 12~30배(evRescue)  ③ 💎 복병 편입분(extra src=dark)

⚠ 저장 표식(displayedCombos.productSplit)은 2026-08-16 부터 쌓인다.
  그 이전은 같은 규칙을 **소급으로 재현**해 기준선을 낸다(표식이 없어도 crossPair 등은 저장돼 있다).
🔴 완전 읽기 전용이다.
"""
import glob
import json
import os
import re
import statistics as st
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "tools"))
OUT_DIR = os.path.join(BASE, "logs", "product_split")
MIN_HIT_PER = 30          # 한방 최소선 — 이만큼의 경주에 적중 1건
PAYBACK = 74.5            # 본선 판정선


def split_of(d):
    """(본선, 한방) 조합 목록. 저장 표식이 있으면 그것을, 없으면 소급 재현."""
    cp = d.get('corePicks') or {}
    dc = cp.get('displayedCombos') or {}
    ps = dc.get('productSplit')
    if isinstance(ps, dict) and (ps.get('main') or ps.get('bomb')):
        return ([sorted(c) for c in (ps.get('main') or [])],
                [sorted(c) for c in (ps.get('bomb') or [])], True)
    bomb = set()
    for e in (dc.get('extra') or []):
        if str(e.get('src') or '') in ('crossPair', 'dark'):
            try:
                bomb.add(tuple(sorted(int(x) for x in (e.get('combo') or []))))
            except (TypeError, ValueError):
                pass
    for q in (cp.get('finalQuinellas') or []):
        if q.get('crossPair') or q.get('evRescue'):
            try:
                bomb.add(tuple(sorted(int(x) for x in (q.get('combo') or []))))
            except (TypeError, ValueError):
                pass
    m, b = [], []
    for c in (dc.get('quinellas') or []):
        try:
            k = tuple(sorted(int(x) for x in c))
        except (TypeError, ValueError):
            continue
        (b if k in bomb else m).append(list(k))
    return m, b, False


def collect(pattern='2026_0*'):
    """(정답, 확정배당, 본선, 한방, 날짜, 종목, 표식여부) 목록."""
    out = []
    for f in sorted(glob.glob(os.path.join(BASE, 'data', 'analysis_log', pattern + '.json'))):
        try:
            d = json.load(open(f, encoding='utf-8'))
        except Exception:
            continue
        res = d.get('result') or {}
        po = (res.get('payouts') or {}).get('quinella')
        if po is None or res.get('1st') is None or res.get('2nd') is None:
            continue
        m, b, tagged = split_of(d)
        if not m and not b:
            continue
        mt = re.match(r'(\d{4}_\d{2}_\d{2})', os.path.basename(f))
        out.append({'ans': sorted([res['1st'], res['2nd']]), 'po': float(po),
                    'main': m, 'bomb': b, 'date': mt.group(1) if mt else '',
                    'sport': d.get('sport') or '?', 'tagged': tagged,
                    'race': os.path.basename(f).replace('.json', '')})
    return out


def stat_main(rows):
    """본선 — 대박 뺀 회수율."""
    rs = [r for r in rows if r['main']]
    if not rs:
        return None
    slots = sum(len(r['main']) for r in rs)
    hit = [r for r in rs if r['ans'] in r['main']]
    ho = sorted([r['po'] for r in hit], reverse=True)
    return {'n': len(rs), 'slots': slots, 'hits': len(hit),
            'hitRate': round(len(hit) / len(rs) * 100, 1),
            'ex3': round((sum(ho) - sum(ho[:3])) / slots * 100, 1) if slots else 0,
            'medHit': round(st.median([r['po'] for r in hit]), 2) if hit else 0}


def stat_bomb(rows):
    """한방 — 적중배당 중앙과 적중 건수. 🔴 회수율은 참고로만 낸다."""
    rs = [r for r in rows if r['bomb']]
    if not rs:
        return None
    slots = sum(len(r['bomb']) for r in rs)
    hit = [r for r in rs if r['ans'] in r['bomb']]
    hp = [r['po'] for r in hit]
    need = len(rs) / MIN_HIT_PER          # 최소선이 요구하는 적중 건수
    return {'n': len(rs), 'slots': slots, 'hits': len(hit),
            'hitRate': round(len(hit) / len(rs) * 100, 1),
            'medHit': round(st.median(hp), 2) if hp else 0,
            'maxHit': round(max(hp), 1) if hp else 0,
            'over10': sum(1 for x in hp if x >= 10),
            'over20': sum(1 for x in hp if x >= 20),
            'refRecovery': round(sum(hp) / slots * 100, 1) if slots else 0,
            'needHits': round(need, 1),
            'passMin': len(hit) >= need}


def run(save=True):
    rows = collect()
    doc = {'at': time.strftime('%Y-%m-%d %H:%M:%S'), 'races': len(rows),
           'tagged': sum(1 for r in rows if r['tagged']),
           'minHitPer': MIN_HIT_PER, 'payback': PAYBACK, 'bySport': {}}
    for sp in ('cycle', 'horse'):
        sub = [r for r in rows if r['sport'] == sp]
        if sub:
            doc['bySport'][sp] = {'main': stat_main(sub), 'bomb': stat_bomb(sub)}
    doc['all'] = {'main': stat_main(rows), 'bomb': stat_bomb(rows)}
    if save:
        try:
            os.makedirs(OUT_DIR, exist_ok=True)
            with open(os.path.join(OUT_DIR, '%s.json' % time.strftime('%Y-%m-%d')),
                      'w', encoding='utf-8') as fp:
                json.dump(doc, fp, ensure_ascii=False, indent=1)
        except Exception as e:
            print('[상품분리] 저장 실패(무시):', str(e)[:120])
    return doc


def text(doc):
    if not doc or not doc.get('races'):
        return '대상 경주 없음'
    L = ['상품 분리 성적 — %d경주 (그중 표식 저장분 %d · 나머지는 소급 재현)'
         % (doc['races'], doc['tagged']),
         '  잣대가 다르다. 본선은 대박 뺀 회수율 · 한방은 적중배당 중앙과 적중 건수.']
    nm = {'cycle': '경륜', 'horse': '경마', 'all': '전체'}
    for k in ('cycle', 'horse', 'all'):
        d = doc['bySport'].get(k) if k != 'all' else doc.get('all')
        if not d:
            continue
        m, b = d.get('main'), d.get('bomb')
        L.append('  [%s]' % nm[k])
        if m:
            L.append('    본선  %d경주 구좌%d 적중%d(%.1f%%) 대박뺀회수 %.1f%% (판정선 %.1f) 적중배당중앙 %.2f'
                     % (m['n'], m['slots'], m['hits'], m['hitRate'], m['ex3'], PAYBACK, m['medHit']))
        if b:
            L.append('    한방  %d경주 구좌%d 적중%d(%.1f%%) 적중배당중앙 %.2f 최고 %.1f · 10배+ %d · 20배+ %d'
                     % (b['n'], b['slots'], b['hits'], b['hitRate'], b['medHit'],
                        b['maxHit'], b['over10'], b['over20']))
            L.append('      최소선 %d경주에 적중 1건 → 필요 %.1f건 · 실제 %d건 → %s'
                     % (MIN_HIT_PER, b['needHits'], b['hits'],
                        '통과' if b['passMin'] else '🔴 미달(자리를 접거나 구성을 바꾼다)'))
            L.append('      (참고) 한방만의 회수율 %.1f%% — 🔴 이 값으로 기각하지 않는다'
                     % b['refRecovery'])
    L.append('  ⚠ 합산 회수율은 내지 않는다. 섞으면 이 분리가 무의미해진다.')
    return '\n'.join(L)


if __name__ == "__main__":
    print(text(run()))
