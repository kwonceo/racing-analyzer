# -*- coding: utf-8 -*-
"""[측정 · 읽기 전용] T-5 확정 뒤 삭제된 조합 중 정답이 몇 건인가.

2026-08-16 대표 지시(서울 3경주에서 일곱 개가 잘렸다).

logs/t5_freeze/<날짜>.jsonl 의 lost 이벤트를 쓴다.
  lost          그 틱에 사라진 조합
  withoutFreeze 동결을 안 했을 때의 명단
  withFreeze    동결했을 때의 명단(= 되살린 것)
경주당 **마지막 lost 이벤트**를 그 경주의 최종 상태로 본다.

⚠ 실제 판정 명단(displayedCombos)과 대조해 어느 쪽이 실전인지 먼저 확인한다.
🔴 배선하지 않는다.
"""
import glob
import json
import os
import re
import statistics as st
import sys
from collections import Counter

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, 'tools'))
import measure_recovery as M


def key(c):
    try:
        return tuple(sorted(int(x) for x in str(c).replace('-', '+').split('+')))
    except (TypeError, ValueError):
        return None


def load_lost():
    """경주키 → 마지막 lost 이벤트(날짜 포함)."""
    last = {}
    for f in sorted(glob.glob(os.path.join(BASE, 'logs', 't5_freeze', '2026*.jsonl'))):
        ymd = os.path.basename(f)[:8]
        d8 = '%s_%s_%s' % (ymd[:4], ymd[4:6], ymd[6:8])
        for ln in open(f, encoding='utf-8'):
            try:
                e = json.loads(ln)
            except Exception:
                continue
            if e.get('ev') != 'lost' or not e.get('rk'):
                continue
            e['_date'] = d8
            last['%s|%s' % (d8, e['rk'])] = e
    return last


def load_races():
    """확정배당 있는 경주 — analysis_log 기준(파일명에 날짜)."""
    out = {}
    for f in sorted(glob.glob(os.path.join(BASE, 'data', 'analysis_log', '2026_0*.json'))):
        try:
            d = json.load(open(f, encoding='utf-8'))
        except Exception:
            continue
        res = d.get('result') or {}
        po = (res.get('payouts') or {}).get('quinella')
        if po is None or res.get('1st') is None or res.get('2nd') is None:
            continue
        b = os.path.basename(f).replace('.json', '')
        m = re.match(r'(\d{4}_\d{2}_\d{2})_(.+)$', b)
        if not m:
            continue
        d8, rk = m.group(1), m.group(2).replace('_', ' ')
        cp = d.get('corePicks') or {}
        dc = [key('+'.join(str(x) for x in c))
              for c in ((cp.get('displayedCombos') or {}).get('quinellas') or [])]
        # 🔴 배당 맵은 odds_history 마감 직전 스냅샷에서 가져온다.
        #   corePicks.quinella 는 경주마다 형식이 달라(마번 리스트인 경우가 있다) 못 쓴다.
        q = {}
        try:
            h = M._loadh(f.replace('analysis_log', 'odds_history')) or {}
            dl = h.get('deadline_epoch')
            sn = [s2 for s2 in (h.get('snapshots') or [])
                  if s2.get('t') and dl and -8 <= (s2['t'] - dl) / 60 <= 0 and s2.get('quinella')]
            if sn:
                for kk, vv in max(sn, key=lambda x: x['t'])['quinella'].items():
                    k2 = key(kk)
                    if k2:
                        try:
                            q[k2] = float(vv)
                        except (TypeError, ValueError):
                            pass
        except Exception:
            pass
        out['%s|%s' % (d8, rk)] = {
            'ans': tuple(sorted([res['1st'], res['2nd']])), 'po': float(po),
            'dc': [x for x in dc if x], 'q': q, 'date': d8,
            'sport': d.get('sport') or '?', 'race': b}
    return out


def score(rows, tag, base=None):
    if not rows:
        print("  %-24s 경주 0" % tag)
        return None
    slots = sum(len(c) for _, c in rows)
    hit = [(r, c) for r, c in rows if r['ans'] in [tuple(x) for x in c]]
    ho = sorted([r['po'] for r, _ in hit], reverse=True)
    o = [r['q'].get(tuple(x)) for r, c in rows for x in c]
    o = [x for x in o if x]
    d = {'n': len(rows), 'slots': slots, 'hits': len(hit),
         'hitRate': len(hit) / len(rows) * 100,
         'ex3': (sum(ho) - sum(ho[:3])) / slots * 100 if slots else 0,
         'med': st.median(o) if o else 0}
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
          ("구좌30%", (a['slots'] / b['slots'] - 1) * 100 <= 30.0,
           "%+.1f%%" % ((a['slots'] / b['slots'] - 1) * 100))]
    bad = [n for n, p, _ in ok if not p]
    return ("통과" if not bad else "기각(" + "·".join(bad) + ")") + "  [" + \
           " · ".join("%s %s" % (n, v) for n, _, v in ok) + "]"


def band(o):
    if not o:
        return '배당없음'
    return ('~3배' if o < 3 else '3~6배' if o < 6 else '6~10배' if o < 10
            else '10~20배' if o < 20 else '20~50배' if o < 50 else '50배+')


def main():
    lost, races = load_lost(), load_races()
    common = [k for k in lost if k in races]
    print("lost 이벤트 보유 경주 %d · 확정배당 보유 %d · 겹치는 것 %d"
          % (len(lost), len(races), len(common)))
    # 실제 판정 명단이 어느 쪽인지 대조
    agree_wo, agree_wi = 0, 0
    for k in common:
        e, r = lost[k], races[k]
        dc = set(r['dc'])
        if dc == {key(x) for x in (e.get('withoutFreeze') or [])}:
            agree_wo += 1
        if dc == {key(x) for x in (e.get('withFreeze') or [])}:
            agree_wi += 1
    print("  실제 명단과 일치: 동결 안 한 쪽 %d · 동결한 쪽 %d (나머지는 그 뒤 편입으로 달라짐)"
          % (agree_wo, agree_wi))

    for sp, tag in (('cycle', '경륜'), ('horse', '경마')):
        sub = [k for k in common if races[k]['sport'] == sp]
        if len(sub) < 20:
            print("=" * 116)
            print("[%s] 표본 %d — 판정 불가" % (tag, len(sub)))
            continue
        print("=" * 116)
        print("[%s] T-5 확정 뒤 삭제분 %d경주" % (tag, len(sub)))
        tot, hit = Counter(), Counter()
        for k in sub:
            e, r = lost[k], races[k]
            for c in (e.get('lost') or []):
                kk = key(c)
                if not kk:
                    continue
                bd = band(r['q'].get(kk))
                tot[bd] += 1
                if kk == r['ans']:
                    hit[bd] += 1
        N = sum(tot.values())
        print("  삭제된 조합 %d개 · 그중 정답 %d개 (%.2f%%)"
              % (N, sum(hit.values()), sum(hit.values()) / N * 100 if N else 0))
        for bd in ('~3배', '3~6배', '6~10배', '10~20배', '20~50배', '50배+', '배당없음'):
            if tot.get(bd):
                print("    %-8s 삭제%4d · 정답%3d (%.1f%%)"
                      % (bd, tot[bd], hit.get(bd, 0), hit.get(bd, 0) / tot[bd] * 100))
        base = [(races[k], races[k]['dc']) for k in sub]
        b = score(base, "지금(실제 명단)")
        rows = []
        for k in sub:
            e, r = lost[k], races[k]
            c = list(r['dc'])
            have = set(c)
            for x in (e.get('lost') or []):
                kk = key(x)
                if kk and kk not in have:
                    c.append(kk)
                    have.add(kk)
            rows.append((r, c))
        a = score(rows, "삭제분을 되살리면", b['slots'] if b else None)
        print("    판정 " + verdict(b, a))
        ds = sorted({races[k]['date'] for k in sub})
        mid = ds[len(ds) // 2] if ds else ''
        for lab, f in (("전반", lambda r: r['date'] < mid), ("후반", lambda r: r['date'] >= mid)):
            x = score([(r, c) for r, c in base if f(r)], "  %s 지금" % lab)
            y = score([(r, c) for r, c in rows if f(r)], "  %s 되살림" % lab,
                      x['slots'] if x else None)
            if x and y:
                print("      %s %s" % (lab, "개선" if y['ex3'] > x['ex3'] else "악화"))


if __name__ == "__main__":
    main()
