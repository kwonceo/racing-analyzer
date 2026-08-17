# -*- coding: utf-8 -*-
"""[측정 · 읽기 전용] 저배당 경주에서 조합을 정말 1~2개로 · 그리고 단승.

2026-08-17 대표 지시.
  실물 오가키 1경주 — 최저 1.3배인데 표시 조합이 셋이었다.
  조합 수 상한이 1개로 잘랐는데 그 **뒤에** 교차 짝·되살린 조합이 다시 붙는다.

작업1 저배당 경주에서 **뒤에 붙는 규칙까지 막았을 때**
  임계 2.0 / 2.5 / 3.0 배 × 남기는 개수 1 / 2
작업2 단승 — 저배당 경주에서 단승 하나만 샀을 때

🔴 단승 실태: 경륜 **0%**(수집 안 함) · 경마 마감배당 72.6% · 확정 단승 5.5%(JRA만)
  ⇒ 경마만 잴 수 있고 **마감 배당으로 근사**한다(확정이 아니다).
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

TH = (2.0, 2.5, 3.0)
LATE_SRC = ("crossPair", "reviveCut", "trioPair", "dark")   # 뒤에 붙는 규칙


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
        qp = (res.get('payouts') or {}).get('quinella')
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
        late = set()
        for e in ((cp.get('displayedCombos') or {}).get('extra') or []):
            if str(e.get('src') or '') in LATE_SRC:
                try:
                    late.add(tuple(sorted(int(x) for x in (e.get('combo') or []))))
                except (TypeError, ValueError):
                    pass
        q, win = {}, {}
        try:
            h = M._loadh(f.replace('analysis_log', 'odds_history')) or {}
            dl = h.get('deadline_epoch')
            sn = [s for s in (h.get('snapshots') or [])
                  if s.get('t') and dl and -8 <= (s['t'] - dl) / 60 <= 0 and s.get('quinella')]
            if sn:
                last = max(sn, key=lambda x: x['t'])
                for k, v in (last.get('quinella') or {}).items():
                    try:
                        q[tuple(sorted(int(x) for x in str(k).replace('-', '+').split('+')))] = float(v)
                    except Exception:
                        pass
                for k, v in (last.get('win') or {}).items():
                    try:
                        win[int(str(k).strip())] = float(v)
                    except Exception:
                        pass
        except Exception:
            pass
        if not q:
            continue
        ans = tuple(sorted([res['1st'], res['2nd']]))
        mo = q.get(ans)
        if not mo or not (M.CLEAN_LO <= float(qp) / mo <= M.CLEAN_HI):
            continue
        m = re.match(r'(\d{4}_\d{2}_\d{2})', b)
        out.append({'race': b.replace('.json', ''), 'date': m.group(1) if m else '',
                    'dc': dc, 'late': late, 'q': q, 'win': win, 'qp': float(qp),
                    'ans': ans, 'first': res['1st'],
                    'minq': min(q.values()) if q else None})
    return out


def score(rows, tag, base=None, quiet=False):
    if not rows:
        return None
    slots = sum(len(c) for _, c in rows)
    hit = [(r, c) for r, c in rows if r['ans'] in c]
    ho = sorted([r['qp'] for r, _ in hit], reverse=True)
    o = [r['q'].get(c2) for r, c in rows for c2 in c]
    o = [x for x in o if x]
    d = {'n': len(rows), 'slots': slots, 'hits': len(hit),
         'hitRate': len(hit) / len(rows) * 100,
         'ex3': (sum(ho) - sum(ho[:3])) / slots * 100 if slots else 0,
         'med': st.median(o) if o else 0,
         'medHit': st.median([r['qp'] for r, _ in hit]) if hit else 0}
    if not quiet:
        g = "" if base is None else " 구좌%+6.1f%%" % ((slots / base - 1) * 100)
        print("  %-30s 경주%4d 구좌%5d 적중%3d(%4.1f%%) 대박뺀회수%6.1f%% 배당중앙%6.2f 적중배당중앙%6.2f%s%s"
              % (tag, d['n'], slots, d['hits'], d['hitRate'], d['ex3'], d['med'], d['medHit'], g,
                 "" if d['hits'] >= 30 else "  판정불가"))
    return d


def task1(rs, tag):
    print("=" * 138)
    print("[작업1] %s %d경주 — 저배당에서 뒤에 붙는 규칙까지 막으면" % (tag, len(rs)))
    base = [(r, list(r['dc'])) for r in rs]
    b = score(base, "지금")
    ds = sorted({r['date'] for r in rs if r['date']})
    mid = ds[len(ds) // 2] if ds else ''
    for th in TH:
        low = [r for r in rs if (r['minq'] or 9e9) < th]
        print("  ── %.1f배 미만 %d경주(%.1f%%) ──" % (th, len(low), len(low) / len(rs) * 100 if rs else 0))
        for keep in (1, 2):
            rows = []
            for r in rs:
                if (r['minq'] or 9e9) < th:
                    c = [x for x in r['dc'] if x not in r['late']]      # 🔴 뒤에 붙은 것 제거
                    c = sorted(c, key=lambda k: r['q'].get(k) or 9e9)[:keep]
                    if not c:
                        c = sorted(r['dc'], key=lambda k: r['q'].get(k) or 9e9)[:keep]
                else:
                    c = list(r['dc'])
                rows.append((r, c))
            a = score(rows, "    %d개만 남기면" % keep, b['slots'] if b else None)
            for lab, f in (("전반", lambda r: r['date'] < mid), ("후반", lambda r: r['date'] >= mid)):
                x = score([(r, c) for r, c in base if f(r)], "", quiet=True)
                y = score([(r, c) for r, c in rows if f(r)], "", quiet=True)
                if x and y:
                    print("        %s %.1f→%.1f %s · 배당 %.2f→%.2f %s"
                          % (lab, x['ex3'], y['ex3'], "개선" if y['ex3'] > x['ex3'] else "악화",
                             x['med'], y['med'], "개선" if y['med'] >= x['med'] else "악화"))


def task2(rs, tag):
    """단승 — 저배당 경주에서 1착 단승 하나."""
    print("=" * 138)
    hw = [r for r in rs if r['win']]
    print("[작업2] %s — 마감 단승배당 보유 %d/%d (%.1f%%) · ⚠ 확정이 아니라 마감 근사"
          % (tag, len(hw), len(rs), len(hw) / len(rs) * 100 if rs else 0))
    if not hw:
        print("  단승 배당 없음 — 측정 불가")
        return
    for th in (None,) + TH:
        sub = hw if th is None else [r for r in hw if (r['minq'] or 9e9) < th]
        if not sub:
            continue
        # 단승 최저 1두를 산다
        slots, hits, ret, odds, hodds = 0, 0, 0.0, [], []
        for r in sub:
            if not r['win']:
                continue
            no = min(r['win'], key=lambda k: r['win'][k])
            o = r['win'][no]
            slots += 1
            odds.append(o)
            if no == r['first']:
                hits += 1
                ret += o
                hodds.append(o)
        hodds.sort(reverse=True)
        lab = "전체" if th is None else "%.1f배 미만" % th
        print("  %-14s 경주%4d 적중%3d(%4.1f%%) 회수%6.1f%% 대박뺀회수%6.1f%% 단승중앙%5.2f 적중단승중앙%5.2f%s"
              % (lab, slots, hits, hits / slots * 100 if slots else 0,
                 ret / slots * 100 if slots else 0,
                 (ret - sum(hodds[:3])) / slots * 100 if slots else 0,
                 st.median(odds) if odds else 0, st.median(hodds) if hodds else 0,
                 "" if hits >= 30 else "  판정불가"))


if __name__ == "__main__":
    for sport, tag in (("cycle", "경륜"), ("horse", "경마")):
        rs = load(sport)
        if not rs:
            print("%s 데이터 없음" % tag)
            continue
        task1(rs, tag)
        task2(rs, tag)
