# -*- coding: utf-8 -*-
"""[측정 · 읽기 전용] 최저 배당이 낮으면 복승을 접고 삼복승으로.

2026-08-17 대표 지시. 실물 오가키 1경주(결과 3-5-6) —
복승 3+5 가 **1.3배**로 적중인데 회수가 106%뿐이다. 그 한 조합이 비중을 82% 먹는다.
빼면 회수 578%인데 나머지 둘은 미적중. **사면 안 남고 빼면 못 맞힌다.**

안 A 지금대로 복승 전부
안 B 시장 최저 < 임계면 **복승을 안 내고 삼복승만**
안 C 복승은 최저 하나만 + 삼복승

🔴 판정 기준(대표): 대박 뺀 회수율이 오를 것 · 배당 중앙값이 오를 것.
   ⚠ **적중률은 떨어져도 된다** — 1.3배 적중은 값어치가 없다.

⚠ 삼복승 확정배당 키가 경로마다 다르다: `trifecta` 1697건 ↔ `trio` 95건.
  `_keiba_result_payouts` 는 三連複을 `trifecta` 에 넣고 JRA 파서는 `trio` 에 넣는다.
  🔴 **오늘 네 번째 같은 유형**(같은 값이 두 이름으로 저장된다). 여기서는 둘 다 받는다.
🔴 배선하지 않는다.
"""
import glob
import json
import os
import re
import statistics as st
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, 'tools'))
import measure_recovery as M

THRESH = (1.5, 2.0, 2.5)


def load(sport):
    out = []
    for f in sorted(glob.glob(os.path.join(BASE, 'data', 'analysis_log', '2026_0*.json'))):
        b = os.path.basename(f)
        if any(k in b for k in ('서울', '부산', '제주')):
            continue
        try:
            d = json.load(open(f, encoding='utf-8'))
        except Exception:
            continue
        if (d.get('sport') or '') != sport:
            continue
        res = d.get('result') or {}
        po = res.get('payouts') or {}
        qp, tp = po.get('quinella'), (po.get('trifecta') or po.get('trio'))
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
        ft = []
        for x in (cp.get('finalTrifectas') or []):
            cb = x.get('combo') or []
            if len(cb) == 3:
                try:
                    ft.append(tuple(sorted(int(v) for v in cb)))
                except (TypeError, ValueError):
                    pass
        # 마감 직전 배당
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
        ans2 = tuple(sorted([res['1st'], res['2nd']]))
        mo = q.get(ans2)
        if not mo or not (M.CLEAN_LO <= float(qp) / mo <= M.CLEAN_HI):
            continue                                  # 정제
        m = re.match(r'(\d{4}_\d{2}_\d{2})', b)
        ans3 = None
        if res.get('3rd') is not None:
            ans3 = tuple(sorted([res['1st'], res['2nd'], res['3rd']]))
        out.append({'race': b.replace('.json', ''), 'date': m.group(1) if m else '',
                    'dc': dc, 'ft': ft, 'q': q, 'qp': float(qp),
                    'tp': (float(tp) if tp is not None else None),
                    'ans2': ans2, 'ans3': ans3,
                    'minq': min(q.values()) if q else None,
                    'kh': [int(x) for x in (d.get('keyHorses') or []) if str(x).isdigit()],
                    'dia': [tuple(sorted(int(v) for v in (x.get('combo') or [])))
                            for x in (cp.get('bmedSpecial') or []) if len(x.get('combo') or []) == 2],
                    'cand': [int(x) for x in ((d.get('elimination') or {}).get('candidates') or [])
                             if str(x).isdigit()],
                    'drops': d.get('anomaly_history') or []})
    return out


def score(rows, tag, base=None):
    """rows = [(r, 복승목록, 삼복승목록)] · 구좌는 둘을 합친다."""
    if not rows:
        print("  %-26s 경주 0" % tag)
        return None
    slots = sum(len(a) + len(b) for _, a, b in rows)
    hits, ret, hodds, odds = 0, 0.0, [], []
    for r, qa, ta in rows:
        for c in qa:
            o = r['q'].get(c)
            if o:
                odds.append(o)
        if r['ans2'] in qa:
            hits += 1
            ret += r['qp']
            hodds.append(r['qp'])
        if r['ans3'] and r['tp'] is not None and r['ans3'] in ta:
            hits += 1
            ret += r['tp']
            hodds.append(r['tp'])
            odds.append(r['tp'])
    hodds.sort(reverse=True)
    d = {'n': len(rows), 'slots': slots, 'hits': hits,
         'hitRate': hits / len(rows) * 100,
         'ex3': (ret - sum(hodds[:3])) / slots * 100 if slots else 0,
         'med': st.median(odds) if odds else 0,
         'medHit': st.median(hodds) if hodds else 0}
    g = "" if base is None else " 구좌%+6.1f%%" % ((slots / base - 1) * 100)
    print("  %-26s 경주%4d 구좌%5d 적중%3d(%4.1f%%) 대박뺀회수%6.1f%% 배당중앙%6.2f 적중배당중앙%6.2f%s%s"
          % (tag, d['n'], slots, d['hits'], d['hitRate'], d['ex3'], d['med'], d['medHit'], g,
             "" if d['hits'] >= 30 else "  판정불가"))
    return d


def run(rs, tag, trion=2):
    print("=" * 132)
    n_tp = sum(1 for r in rs if r['tp'] is not None)
    print("[%s] %d경주 · 삼복승 확정배당 보유 %d (%.1f%%) · 삼복승 상위%d 사용"
          % (tag, len(rs), n_tp, n_tp / len(rs) * 100 if rs else 0, trion))
    A = [(r, list(r['dc']), []) for r in rs]
    b = score(A, "A 지금대로 복승만")
    for th in THRESH:
        low = [r for r in rs if (r['minq'] or 9e9) < th]
        print("  ── 시장 최저 %.1f배 미만 %d경주(%.1f%%) ──"
              % (th, len(low), len(low) / len(rs) * 100 if rs else 0))
        B = [(r, [] if (r['minq'] or 9e9) < th else list(r['dc']),
              list(r['ft'][:trion]) if (r['minq'] or 9e9) < th else []) for r in rs]
        score(B, "  B 낮으면 삼복승만", b['slots'] if b else None)
        C = [(r, (sorted(r['dc'], key=lambda c: r['q'].get(c) or 9e9)[:1]
                  if (r['minq'] or 9e9) < th else list(r['dc'])),
              list(r['ft'][:trion]) if (r['minq'] or 9e9) < th else []) for r in rs]
        score(C, "  C 최저 하나 + 삼복승", b['slots'] if b else None)


def task2(rs, tag):
    """3착이 어디서 나오나."""
    print("=" * 132)
    tot = 0
    src = {'유력마': 0, '다이아': 0, '급락': 0, '후보': 0, '어디에도없음': 0}
    for r in rs:
        if not r['ans3']:
            continue
        third = [x for x in r['ans3'] if x not in r['ans2']]
        if not third:
            continue
        t = third[0]
        tot += 1
        sig = set()
        for a in (r['drops'] or []):
            try:
                if float(a.get('drop') or 0) > -20:
                    continue
            except (TypeError, ValueError):
                continue
            for tok in str(a.get('combo') or '').replace('-', '+').split('+'):
                if tok.strip().isdigit():
                    sig.add(int(tok))
        if t in (r['kh'] or []):
            src['유력마'] += 1
        elif any(t in c for c in (r['dia'] or [])):
            src['다이아'] += 1
        elif t in sig:
            src['급락'] += 1
        elif t in (r['cand'] or []):
            src['후보'] += 1
        else:
            src['어디에도없음'] += 1
    print("[%s] 3착 자리가 어디서 나오나 — %d경주" % (tag, tot))
    for k, v in sorted(src.items(), key=lambda kv: -kv[1]):
        print("    %-12s %4d (%.1f%%)" % (k, v, v / tot * 100 if tot else 0))


if __name__ == "__main__":
    for sport, tag in (("cycle", "경륜"), ("horse", "경마")):
        rs = load(sport)
        if not rs:
            print("%s 데이터 없음" % tag)
            continue
        run(rs, tag)
        task2(rs, tag)
