# -*- coding: utf-8 -*-
"""[자동 발견] 고칠 것을 어떤 기준으로 아는가 — 네 단계 문턱.

2026-08-16 대표 지시.

1단계 발견   조건에 30건 이상 · 대박 뺀 회수율이 전체 평균보다 10포인트 이상 낮음 ·
             한 방향으로 몰려 있을 것(들쭉날쭉하면 우연)
2단계 검증   기간을 반으로 갈라 앞뒤 **양쪽에서** 같은 방향일 것. 한쪽만이면 버린다
3단계 반영   🔴 대표가 보고 정한다. 이 도구는 **절대 자동으로 켜지 않는다**
4단계 관찰   켠 뒤 실전 2주를 소급값과 대조(별도)

🔴 완전 읽기 전용이다. analysis_log·race_results 를 읽고 logs/auto_finding/ 에만 쓴다.
⚠ 회수율 규칙(정제·확정배당·날짜매칭)은 measure_recovery 를 import 해 그대로 쓴다.
"""
import glob
import json
import os
import statistics as st
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "tools"))
OUT_DIR = os.path.join(BASE, "logs", "auto_finding")

# 🔴 [2026-08-16 조임] 대표 정의의 「30건 이상」을 **적중 30건**으로 읽는다.
#   경주 30건으로 하면 적중이 5~10건뿐이라 회수율이 크게 흔들린다.
#   첫 실행에서 경기장 12곳이 제안으로 올라왔고 그중 상당수가 전반 0.0(적중 0건)이었다.
#   원칙 1(적중 30건 미만은 판정 불가)과도 맞춘다.
MIN_HITS = 30       # 1단계: 그 조건의 적중 건수
MIN_N = 60          # 1단계: 경주 수 하한(적중률이 낮은 조건도 최소 표본은 있어야)
GAP_PT = 10.0       # 1단계: 전체 평균보다 이만큼 낮으면 후보
# 2단계: 앞뒤 각각 이만큼 적중이 있어야 「같은 방향」을 말할 수 있다
MIN_HALF_HITS = 10


def _load():
    import measure_recovery as M
    meta = {}
    for f in glob.glob(os.path.join(BASE, 'data', 'analysis_log', '2026_0*.json')):
        try:
            d = json.load(open(f, encoding='utf-8'))
        except Exception:
            continue
        res = d.get('result') or {}
        po = (res.get('payouts') or {}).get('quinella')
        if po is None or res.get('1st') is None or res.get('2nd') is None:
            continue
        b = os.path.basename(f)
        t = sorted({res['1st'], res['2nd']})
        sig = 0
        for a in (d.get('anomaly_history') or []):
            try:
                if float(a.get('drop') or 0) <= -20:
                    sig += 1
            except (TypeError, ValueError):
                pass
        meta[(t[0], t[1], float(po))] = {
            'venue': b.split('_')[3] if len(b.split('_')) > 3 else '?',
            'nh': len(d.get('horses') or []),
            'sig': sig,
            'date': b[:10],
            'kr': any(k in b for k in ('제주', '서울', '부산')),
        }
    out = []
    for sport in ('cycle', 'horse'):
        for r in M.load_races(sport=sport, pattern='2026_0*'):
            try:
                if not (M.CLEAN_LO <= r["po"] / r["mo"] <= M.CLEAN_HI):
                    continue
            except Exception:
                continue
            m = meta.get((r["top2"][0], r["top2"][1], r["po"]))
            if not m:
                continue
            r.update(m)
            r['sport'] = sport
            out.append(r)
    return out


def _ex3(rs):
    """대박 뺀 회수율(상위 3건 제외) · 적중률 · 배당중앙."""
    if not rs:
        return None
    slots = sum(len(r["dc"]) for r in rs)
    hit = [r for r in rs if sorted(r["top2"]) in [sorted(c) for c in r["dc"]]]
    ret = sum(r["po"] for r in hit)
    ho = sorted([r["po"] for r in hit], reverse=True)
    o = [r["q"].get(tuple(sorted(c))) for r in rs for c in r["dc"]]
    o = [x for x in o if x]
    return {'n': len(rs), 'hits': len(hit),
            'hitRate': round(len(hit) / len(rs) * 100, 1),
            'ex3': round((ret - sum(ho[:3])) / slots * 100, 1) if slots else 0,
            'medOdds': round(st.median(o), 2) if o else 0}


def _groups(rs):
    """조건 후보를 만든다. (이름, 값) → 경주 목록."""
    g = {}
    def add(kind, val, r):
        g.setdefault((kind, str(val)), []).append(r)
    for r in rs:
        add('경기장', r['venue'], r)
        add('종목', r['sport'], r)
        n = r['nh']
        add('두수', '6~7두' if 0 < n <= 7 else '8~9두' if n <= 9 else '10두+' if n > 9 else '전적없음', r)
        add('신호수', '0~2' if r['sig'] <= 2 else '3~9' if r['sig'] <= 9 else '10+', r)
        mn = min(r["q"].values()) if r["q"] else None
        if mn:
            add('시장최저', '~2배' if mn < 2 else '2~3배' if mn < 3 else '3~6배' if mn < 6 else '6배+', r)
        add('한국여부', '한국' if r['kr'] else '일본', r)
    return g


def run(save=True):
    rs = _load()
    if not rs:
        return {'error': '대상 경주 없음'}
    base = _ex3(rs)
    ds = sorted({r['date'] for r in rs if r['date']})
    mid = ds[len(ds) // 2] if ds else ''
    cands = []
    for (kind, val), sub in _groups(rs).items():
        if len(sub) < MIN_N:
            continue
        s = _ex3(sub)
        if s['hits'] < MIN_HITS:
            continue                                   # 적중이 얇으면 회수율이 우연으로 흔들린다
        gap = round(base['ex3'] - s['ex3'], 1)
        if gap < GAP_PT:
            continue                                   # 1단계 미달
        # 2단계 — 기간 반 분할에서 앞뒤 둘 다 낮은가
        a = _ex3([r for r in sub if r['date'] < mid])
        b = _ex3([r for r in sub if r['date'] >= mid])
        both = bool(a and b
                    and a['hits'] >= MIN_HALF_HITS and b['hits'] >= MIN_HALF_HITS
                    and a['ex3'] < base['ex3'] and b['ex3'] < base['ex3'])
        cands.append({
            'kind': kind, 'value': val, 'n': s['n'], 'hits': s['hits'],
            'ex3': s['ex3'], 'gap': gap, 'hitRate': s['hitRate'], 'medOdds': s['medOdds'],
            'stage1': True,
            'stage2': both,
            'firstHalf': (a or {}).get('ex3'), 'secondHalf': (b or {}).get('ex3'),
            'firstN': (a or {}).get('n'), 'secondN': (b or {}).get('n'),
        })
    cands.sort(key=lambda x: (-x['stage2'], -x['gap']))
    doc = {'at': time.strftime('%Y-%m-%d %H:%M:%S'), 'races': base['n'],
           'baseEx3': base['ex3'], 'baseHitRate': base['hitRate'],
           'splitDate': mid, 'minN': MIN_N, 'minHits': MIN_HITS, 'gapPt': GAP_PT, 'minHalfHits': MIN_HALF_HITS,
           'stage1': len(cands), 'stage2': sum(1 for c in cands if c['stage2']),
           'candidates': cands}
    if save:
        try:
            os.makedirs(OUT_DIR, exist_ok=True)
            with open(os.path.join(OUT_DIR, '%s.json' % time.strftime('%Y-%m-%d')),
                      'w', encoding='utf-8') as fp:
                json.dump(doc, fp, ensure_ascii=False, indent=1)
        except Exception as e:
            print('[자동발견] 저장 실패(무시):', str(e)[:120])
    return doc


def text(doc):
    if not doc or doc.get('error'):
        return (doc or {}).get('error', '결과 없음')
    L = ['자동 발견 — 전체 %d경주 · 대박 뺀 회수율 %.1f%% · 적중 %.1f%%'
         % (doc['races'], doc['baseEx3'], doc['baseHitRate']),
         '1단계 통과 %d개 · 2단계까지 통과 %d개 (기간 경계 %s)'
         % (doc['stage1'], doc['stage2'], doc['splitDate'])]
    for c in doc['candidates']:
        mark = '🔴 제안' if c['stage2'] else '   후보'
        L.append('  %s %s=%s  %d경주 대박뺀회수 %.1f%% (전체보다 %.1f 낮음) '
                 '적중 %.1f%% 배당 %.2f  전반 %s / 후반 %s'
                 % (mark, c['kind'], c['value'], c['n'], c['ex3'], c['gap'],
                    c['hitRate'], c['medOdds'], c['firstHalf'], c['secondHalf']))
    if not doc['candidates']:
        L.append('  1단계를 넘은 조건이 없다')
    L.append('⚠ 3단계(반영)는 대표가 보고 정한다. 이 도구는 켜지 않는다.')
    return '\n'.join(L)


if __name__ == "__main__":
    print(text(run()))
