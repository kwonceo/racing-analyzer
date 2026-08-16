# -*- coding: utf-8 -*-
"""[측정 · 읽기 전용] 한국 성적 — 확정배당은 race_results 에 있었다.

2026-08-16.

🔴 정정: 오늘 여러 번 「한국은 확정배당 0경주라 측정 불가」로 보고했다. **틀렸다.**
   analysis_log 의 result.payouts 에는 0건이지만,
   **race_results/<날짜>_<경주>.json 의 payouts.quinella 에는 109경주(49.3%)가 있다.**
   measure_recovery 가 analysis_log 만 읽어서 못 본 것이다(원칙 8-E — 조회식을 의심한다).

이 도구는 두 파일을 **같은 파일명으로 조인**해 한국 성적을 낸다(원칙 16 — 날짜 포함 매칭).
🔴 배선하지 않는다.
"""
import glob
import json
import os
import re
import statistics as st
from collections import Counter

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KR = ('서울', '부산', '제주')
CLEAN_LO, CLEAN_HI, PAYBACK = 0.5, 2.0, 74.5


def load():
    out = []
    for f in sorted(glob.glob(os.path.join(BASE, 'data', 'analysis_log', '2026_*.json'))):
        b = os.path.basename(f)
        if not any(k in b for k in KR):
            continue
        rr = f.replace('analysis_log', 'race_results')       # 🔴 같은 파일명(날짜 포함)
        if not os.path.exists(rr):
            continue
        try:
            d = json.load(open(f, encoding='utf-8'))
            r = json.load(open(rr, encoding='utf-8'))
        except Exception:
            continue
        po = (r.get('payouts') or {}).get('quinella')
        res = r.get('result') or d.get('result') or {}
        if po is None or res.get('1st') is None or res.get('2nd') is None:
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
        # 마감 직전 배당(정제·배당중앙에 쓴다)
        q = {}
        try:
            h = json.load(open(f.replace('analysis_log', 'odds_history'), encoding='utf-8'))
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
        m = re.match(r'(\d{4}_\d{2}_\d{2})', b)
        ans = tuple(sorted([res['1st'], res['2nd']]))
        out.append({'race': b.replace('.json', ''), 'date': m.group(1) if m else '',
                    'ans': ans, 'po': float(po), 'dc': dc, 'q': q,
                    'mo': q.get(ans), 'venue': b.split('_')[3] if len(b.split('_')) > 3 else '?',
                    'ref': [tuple(sorted(int(v) for v in (x.get('combo') or [])))
                            for x in (cp.get('quinellaRef') or []) if len(x.get('combo') or []) == 2],
                    'refReason': [(tuple(sorted(int(v) for v in (x.get('combo') or []))),
                                   str(x.get('refReason') or ''))
                                  for x in (cp.get('quinellaRef') or []) if len(x.get('combo') or []) == 2],
                    'kh': [int(x) for x in (d.get('keyHorses') or []) if str(x).isdigit()]})
    return out


def score(rows, tag, base=None):
    if not rows:
        print("  %-24s 경주 0" % tag)
        return None
    slots = sum(len(c) for _, c in rows)
    hit = [(r, c) for r, c in rows if r['ans'] in c]
    ho = sorted([r['po'] for r, _ in hit], reverse=True)
    o = [r['q'].get(c2) for r, c in rows for c2 in c]
    o = [x for x in o if x]
    d = {'n': len(rows), 'slots': slots, 'hits': len(hit),
         'hitRate': len(hit) / len(rows) * 100,
         'rec': sum(r['po'] for r, _ in hit) / slots * 100 if slots else 0,
         'ex3': (sum(ho) - sum(ho[:3])) / slots * 100 if slots else 0,
         'med': st.median(o) if o else 0,
         'medHit': st.median([r['po'] for r, _ in hit]) if hit else 0}
    g = "" if base is None else " 구좌%+6.1f%%" % ((slots / base - 1) * 100)
    print("  %-24s 경주%4d 구좌%4d 적중%3d(%4.1f%%) 회수%6.1f%% 대박뺀회수%6.1f%% 배당중앙%5.2f 적중배당중앙%6.2f%s%s"
          % (tag, d['n'], slots, d['hits'], d['hitRate'], d['rec'], d['ex3'], d['med'],
             d['medHit'], g, "" if d['hits'] >= 30 else "  판정불가"))
    return d


def main():
    rs = load()
    print("한국 확정배당 보유 %d경주 (race_results 기준 · analysis_log 에는 0건)" % len(rs))
    if not rs:
        return
    clean = [r for r in rs if r['mo'] and CLEAN_LO <= r['po'] / r['mo'] <= CLEAN_HI]
    print("  정제(확정과 마감배당 괴리 0.5~2.0배) %d경주 (%.1f%%)"
          % (len(clean), len(clean) / len(rs) * 100))
    by = Counter(r['venue'] for r in rs)
    print("  경마장: " + " · ".join("%s %d" % (k, v) for k, v in by.most_common()))
    print("  날짜: %s ~ %s" % (min(r['date'] for r in rs), max(r['date'] for r in rs)))
    print("=" * 124)
    use = clean if len(clean) >= 30 else rs
    print("[현행] 판정선 %.1f%%" % PAYBACK)
    b = score([(r, r['dc']) for r in use], "지금")
    # 유력마 넷의 짝 전부
    for topn in (3, 4):
        rows = []
        for r in use:
            c = list(r['dc'])
            have = set(c)
            kh = r['kh'][:topn]
            for i in range(len(kh)):
                for j in range(i + 1, len(kh)):
                    k = tuple(sorted((kh[i], kh[j])))
                    if k not in have and r['q'].get(k):
                        c.append(k)
                        have.add(k)
            rows.append((r, c))
        score(rows, "유력마 상위%d 짝 전부" % topn, b['slots'] if b else None)
    # 조합 수 상한에 잘린 것 되살리기
    rows = []
    for r in use:
        c = list(r['dc'])
        have = set(c)
        for k, why in r['refReason']:
            if '조합 수 상한' in why and k not in have:
                c.append(k)
                have.add(k)
        rows.append((r, c))
    score(rows, "조합 수 상한 해제", b['slots'] if b else None)
    # 기간 반 분할
    ds = sorted({r['date'] for r in use})
    mid = ds[len(ds) // 2] if ds else ''
    print("  기간 경계 %s" % mid)
    for lab, f in (("전반", lambda r: r['date'] < mid), ("후반", lambda r: r['date'] >= mid)):
        score([(r, r['dc']) for r in use if f(r)], "  %s 지금" % lab)


if __name__ == "__main__":
    main()
