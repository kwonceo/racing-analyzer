# -*- coding: utf-8 -*-
"""[측정 · 읽기 전용] 삼복승 메인 세 마리의 짝 셋을 복승에도 내면.

2026-08-17 대표 지시. 이틀 연속 같은 자리에서 놓쳤다.
  08-16 삿포로 1경주 삼복승 3+5+10 · 정답 3+10
  08-17 벳푸 5경주   삼복승 1+2+7  · 정답 1+2

⚠ 어제 기각한 「삼복승 마번을 교차 짝 후보에」와는 **다른 안**이다.
  그건 후보 풀을 넓히는 것이었고, 이건 **삼복승 조합 자체의 짝**을 복승에 넣는 것이다.

🔴 이미 같은 규칙이 있다 — `fix_trio_coherence`(app.py 13125).
  삼복승 **배열 첫 번째** 하나의 부분 페어 3개를 만들고 50배 이하만, 그리고 `_mainmax` 로 자른다.
  ⇒ 안 나온 이유가 셋 중 하나다: ⓐ메인이 그 조합이 아니다 ⓑ배당 50배 초과 ⓒ개수 상한에 잘렸다.

측정: 표시 복승에 **없는** 짝만 추가했을 때의 성적.
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

MAXO = 50.0          # fix_trio_coherence 와 같은 상한


def clean(r):
    try:
        return M.CLEAN_LO <= r["po"] / r["mo"] <= M.CLEAN_HI
    except Exception:
        return False


def load(sport):
    extra = {}
    for f in glob.glob(os.path.join(BASE, 'data', 'analysis_log', '2026_0*.json')):
        try:
            d = json.load(open(f, encoding='utf-8'))
        except Exception:
            continue
        res = d.get('result') or {}
        po = (res.get('payouts') or {}).get('quinella')
        if po is None or res.get('1st') is None or res.get('2nd') is None:
            continue
        cp = d.get('corePicks') or {}
        ft = []
        for x in (cp.get('finalTrifectas') or []):
            cb = x.get('combo') or []
            if len(cb) == 3:
                try:
                    ft.append(sorted(int(v) for v in cb))
                except (TypeError, ValueError):
                    pass
        t = sorted({res['1st'], res['2nd']})
        m = re.match(r'(\d{4}_\d{2}_\d{2})', os.path.basename(f))
        extra[(t[0], t[1], float(po))] = {
            'ft': ft, 'date': m.group(1) if m else '',
            'kr': any(k in os.path.basename(f) for k in ('제주', '서울', '부산'))}
    out = []
    for r in M.load_races(sport=sport, pattern='2026_0*'):
        if not clean(r):
            continue
        e = extra.get((r["top2"][0], r["top2"][1], r["po"]))
        if not e:
            continue
        r.update(e)
        out.append(r)
    return out


def add_pairs(r, topn=1):
    """삼복승 앞 topn 개의 짝 중 표시 복승에 없고 배당 50배 이하인 것."""
    base = [tuple(sorted(c)) for c in r["dc"]]
    have = set(base)
    add = []
    for t in (r.get('ft') or [])[:topn]:
        for i in range(3):
            for j in range(i + 1, 3):
                k = (t[i], t[j])
                if k in have:
                    continue
                o = r["q"].get(k)
                if not o or o > MAXO:
                    continue
                add.append(k)
                have.add(k)
    return base, add


def score(rows, tag, base=None, quiet=False):
    if not rows:
        return None
    slots = sum(len(c) for _, c in rows)
    hit = [(r, c) for r, c in rows if tuple(sorted(r["top2"])) in [tuple(x) for x in c]]
    ho = sorted([r["po"] for r, _ in hit], reverse=True)
    o = [r["q"].get(tuple(x)) for r, c in rows for x in c]
    o = [x for x in o if x]
    d = {'n': len(rows), 'slots': slots, 'hits': len(hit),
         'hitRate': len(hit) / len(rows) * 100,
         'ex3': (sum(ho) - sum(ho[:3])) / slots * 100 if slots else 0,
         'med': st.median(o) if o else 0}
    if not quiet:
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


def run(rs, tag):
    print("=" * 118)
    print("[%s] %d경주 — 삼복승 짝을 복승에도" % (tag, len(rs)))
    base = [(r, [tuple(sorted(c)) for c in r["dc"]]) for r in rs]
    b = score(base, "지금")
    ds = sorted({r['date'] for r in rs if r['date']})
    mid = ds[len(ds) // 2] if ds else ''
    for topn in (1, 2):
        rows = []
        addn, hitadd = 0, 0
        for r in rs:
            bs, ad = add_pairs(r, topn)
            addn += len(ad)
            if tuple(sorted(r["top2"])) in ad:
                hitadd += 1
            rows.append((r, bs + ad))
        a = score(rows, "삼복승 상위%d 의 짝" % topn, b['slots'] if b else None)
        print("    판정 " + verdict(b, a))
        print("    추가된 짝 %d개 · 그중 정답 %d개 (%.2f%%)"
              % (addn, hitadd, hitadd / addn * 100 if addn else 0))
        for lab, f in (("전반", lambda r: r['date'] < mid), ("후반", lambda r: r['date'] >= mid)):
            x = score([(r, c) for r, c in base if f(r)], "", quiet=True)
            y = score([(r, c) for r, c in rows if f(r)], "", quiet=True)
            if x and y:
                print("      %s %.1f→%.1f %s" % (lab, x['ex3'], y['ex3'],
                                                 "개선" if y['ex3'] > x['ex3'] else "악화"))


def check():
    """두 경주 검산 — 이 안으로 실제로 잡히나."""
    print("=" * 118)
    print("[검산]")
    for f, ans in (('data/analysis_log/2026_08_16_삿포로_1경주.json', (3, 10)),
                   ('data/analysis_log/2026_08_17_벳푸_5경주.json', (1, 2))):
        p = os.path.join(BASE, f)
        if not os.path.exists(p):
            print("  %s 없음" % os.path.basename(f))
            continue
        d = json.load(open(p, encoding='utf-8'))
        cp = d.get('corePicks') or {}
        dc = [tuple(sorted(int(v) for v in c))
              for c in ((cp.get('displayedCombos') or {}).get('quinellas') or [])]
        ft = [sorted(int(v) for v in (x.get('combo') or []))
              for x in (cp.get('finalTrifectas') or []) if len(x.get('combo') or []) == 3]
        rsn = [(x.get('combo'), (x.get('reason') or '')[:22])
               for x in (cp.get('finalTrifectas') or [])][:3]
        # 마감 직전 배당
        q = {}
        try:
            h = json.load(open(p.replace('analysis_log', 'odds_history'), encoding='utf-8'))
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
        r = {'dc': dc, 'ft': ft, 'q': q}
        for topn in (1, 2, 3):
            bs, ad = add_pairs(r, topn)
            got = tuple(sorted(ans)) in ad
            print("  %-22s 상위%d → 추가 %s %s"
                  % (os.path.basename(f).replace('.json', ''), topn,
                     [list(x) for x in ad], "🟢 정답 포함" if got else ""))
        print("      삼복승 앞 3개: %s" % rsn)
        print("      정답 %s 배당 %s · 표시복승 %s"
              % (list(ans), q.get(tuple(sorted(ans))), [list(x) for x in dc]))


if __name__ == "__main__":
    check()
    for sport, tag in (("cycle", "경륜"), ("horse", "경마")):
        rs = [r for r in load(sport) if not r.get('kr')]
        if rs:
            run(rs, tag)
