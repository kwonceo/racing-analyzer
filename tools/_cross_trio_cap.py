# -*- coding: utf-8 -*-
"""[측정 · 읽기 전용] 작업4 교차 짝에 삼복승 마번 넣기 · 작업5 조합 수 상한.

2026-08-16 대표 지시.

작업4  지금 교차 짝 후보는 **복승 표시 조합에 나온 말**뿐이다.
       삿포로 1경주(2026-08-16 · 결과 10-3-14 · 정답 3+10 = 22.6배)에서
       3번은 삼복승 관찰 목록 [3,5,10] 에만 있어 후보가 못 됐다.
       🔴 삼복승은 trioShadow 로 판정에서 빠져 displayedCombos.trifectas 가 비어 있고,
          실제 화면 목록은 corePicks.finalTrifectas(=shadowTrifectas) 다.

작업5  니가타 1경주에서 4+8 을 자른 것은 **조합 수 상한**이었다.
       상한을 하나씩 늘렸을 때를 잰다.

판정 기준 넷(대표) — 넷을 다 넘어야 한다
  대박 뺀 회수율 오를 것 · 적중률 크게 안 떨어질 것 ·
  배당 중앙값 안 내려갈 것 · 구좌 증가 30% 이내
그리고 기간을 반으로 갈라 앞뒤 방향이 같아야 한다.

⚠ 배선하지 않는다.
"""
import glob
import itertools
import json
import os
import re
import statistics as st
import sys
from collections import Counter

sys.path.insert(0, 'tools')
import measure_recovery as M

LO, HI, TOPN = 6.0, 30.0, 4


def clean(r):
    try:
        return M.CLEAN_LO <= r["po"] / r["mo"] <= M.CLEAN_HI
    except Exception:
        return False


def load(sport):
    """analysis_log 에서 추가 정보(삼복승 관찰목록·잘린 조합·두수·날짜)를 붙인다."""
    extra = {}
    for f in glob.glob(os.path.join('data', 'analysis_log', '2026_0*.json')):
        try:
            d = json.load(open(f, encoding='utf-8'))
        except Exception:
            continue
        res = d.get('result') or {}
        po = (res.get('payouts') or {}).get('quinella')
        if po is None or res.get('1st') is None or res.get('2nd') is None:
            continue
        cp = d.get('corePicks') or {}
        tri = []
        for src in ('finalTrifectas', 'shadowTrifectas'):
            for x in (cp.get(src) or []):
                cb = x.get('combo') or []
                if len(cb) == 3:
                    tri.append([int(v) for v in cb])
        capcut, evcut = [], []
        for x in (cp.get('quinellaRef') or []):
            cb = x.get('combo') or []
            if len(cb) != 2:
                continue
            rs = str(x.get('refReason') or '')
            (capcut if '조합 수 상한' in rs else evcut).append(sorted(int(v) for v in cb))
        t = sorted({res['1st'], res['2nd']})
        m = re.match(r'(\d{4}_\d{2}_\d{2})', os.path.basename(f))
        extra[(t[0], t[1], float(po))] = {
            'tri': tri, 'capcut': capcut, 'evcut': evcut,
            'nh': len(d.get('horses') or []),
            'date': m.group(1) if m else '',
            'kr': any(k in os.path.basename(f) for k in ('제주', '서울', '부산')),
        }
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


def pick(cand, already, q):
    """상위 후보들 사이의 짝 중 LO~HI 최저 1개."""
    have = {tuple(sorted(c)) for c in already}
    best = None
    for a, b in itertools.combinations(sorted(set(cand)), 2):
        k = (a, b)
        if k in have:
            continue
        o = q.get(k)
        if not o or not (LO <= o <= HI):
            continue
        if best is None or o < best[0]:
            best = (o, [a, b])
    return best


def cross_now(r):
    """지금 방식 — 복승 표시 조합에 나온 말만."""
    base = [sorted(c) for c in r["dc"]]
    cnt = Counter(h for c in base for h in c)
    return base, [h for h, _ in cnt.most_common()][:TOPN]


def cross_trio(r):
    """작업4 — 삼복승 관찰 목록 마번도 후보에 넣는다(빈도에 합산)."""
    base = [sorted(c) for c in r["dc"]]
    cnt = Counter(h for c in base for h in c)
    for c in (r.get('tri') or []):
        for h in c:
            cnt[int(h)] += 1
    return base, [h for h, _ in cnt.most_common()][:TOPN]


def score(rows, tag, base_slots=None, quiet=False):
    if not rows:
        if not quiet:
            print("  %-26s 경주 0" % tag)
        return None
    slots = sum(len(c) for _, c in rows)
    hit = [(r, c) for r, c in rows if sorted(r["top2"]) in [sorted(x) for x in c]]
    ret = sum(r["po"] for r, _ in hit)
    ho = sorted([r["po"] for r, _ in hit], reverse=True)
    o = [r["q"].get(tuple(sorted(x))) for r, c in rows for x in c]
    o = [x for x in o if x]
    d = {'n': len(rows), 'slots': slots, 'hits': len(hit),
         'hitRate': len(hit) / len(rows) * 100,
         'ex3': (ret - sum(ho[:3])) / slots * 100 if slots else 0,
         'med': st.median(o) if o else 0}
    if not quiet:
        g = "" if base_slots is None else " 구좌%+6.1f%%" % ((slots / base_slots - 1) * 100)
        print("  %-26s 경주%4d 구좌%5d 적중%3d(%4.1f%%) 대박뺀회수%6.1f%% 배당중앙%6.2f%s%s"
              % (tag, d['n'], slots, d['hits'], d['hitRate'], d['ex3'], d['med'], g,
                 "" if d['hits'] >= 30 else "  판정불가"))
    return d


def verdict(b, a):
    """판정 기준 넷."""
    if not b or not a:
        return "판정불가"
    ok = []
    ok.append(("회수율", a['ex3'] > b['ex3'], "%.1f→%.1f" % (b['ex3'], a['ex3'])))
    ok.append(("적중률", a['hitRate'] >= b['hitRate'] - 2.0,
               "%.1f→%.1f" % (b['hitRate'], a['hitRate'])))
    ok.append(("배당중앙", a['med'] >= b['med'] - 0.01, "%.2f→%.2f" % (b['med'], a['med'])))
    g = (a['slots'] / b['slots'] - 1) * 100 if b['slots'] else 999
    ok.append(("구좌30%이내", g <= 30.0, "%+.1f%%" % g))
    bad = [n for n, p, _ in ok if not p]
    return ("통과" if not bad else "기각(" + "·".join(bad) + ")") + "  [" + \
           " · ".join("%s %s" % (n, v) for n, _, v in ok) + "]"


def halves(rs):
    ds = sorted({r['date'] for r in rs if r['date']})
    return ds[len(ds) // 2] if ds else ''


def task4(rs, tag):
    print("=" * 118)
    print("[작업4] %s %d경주 — 교차 짝 후보에 삼복승 마번을 넣으면" % (tag, len(rs)))
    rows_n, rows_t = [], []
    for r in rs:
        b, c = cross_now(r)
        p = pick(c, b, r["q"])
        rows_n.append((r, b + [p[1]] if p else b))
        b2, c2 = cross_trio(r)
        p2 = pick(c2, b2, r["q"])
        rows_t.append((r, b2 + [p2[1]] if p2 else b2))
    bn = score(rows_n, "지금(복승 마번만)")
    bt = score(rows_t, "삼복승 마번도 후보", bn['slots'] if bn else None)
    print("  판정 " + verdict(bn, bt))
    mid = halves(rs)
    for lab, sel in (("전반", lambda r: r['date'] < mid), ("후반", lambda r: r['date'] >= mid)):
        a = score([(r, c) for r, c in rows_n if sel(r)], "  %s 지금" % lab)
        b = score([(r, c) for r, c in rows_t if sel(r)], "  %s 삼복승포함" % lab,
                  a['slots'] if a else None)
        print("    %s 방향 %s" % (lab, "개선" if a and b and b['ex3'] > a['ex3'] else "악화"))


def task5(rs, tag):
    print("=" * 118)
    print("[작업5] %s %d경주 — 조합 수 상한에 잘린 조합" % (tag, len(rs)))
    tot, ans_hit, byband, bynh = 0, 0, Counter(), Counter()
    hit_band, hit_nh = Counter(), Counter()
    for r in rs:
        ans = tuple(sorted(r["top2"]))
        nhk = '6~7두' if 0 < r['nh'] <= 7 else '8~9두' if r['nh'] <= 9 else '10두+' if r['nh'] > 9 else '전적없음'
        for c in (r.get('capcut') or []):
            o = r["q"].get(tuple(c))
            bd = ('~3배' if o and o < 3 else '3~6배' if o and o < 6 else '6~10배' if o and o < 10
                  else '10~20배' if o and o < 20 else '20~50배' if o and o < 50
                  else '50배+' if o else '배당없음')
            tot += 1
            byband[bd] += 1
            bynh[nhk] += 1
            if tuple(c) == ans:
                ans_hit += 1
                hit_band[bd] += 1
                hit_nh[nhk] += 1
    print("  잘린 조합 %d개 · 그중 정답 %d개 (%.2f%%)"
          % (tot, ans_hit, ans_hit / tot * 100 if tot else 0))
    if tot:
        print("  배당대별 잘림/정답: " + " · ".join(
            "%s %d/%d(%.1f%%)" % (k, byband[k], hit_band.get(k, 0),
                                  hit_band.get(k, 0) / byband[k] * 100)
            for k in sorted(byband, key=lambda x: -byband[x])))
        print("  두수별   잘림/정답: " + " · ".join(
            "%s %d/%d(%.1f%%)" % (k, bynh[k], hit_nh.get(k, 0),
                                  hit_nh.get(k, 0) / bynh[k] * 100)
            for k in sorted(bynh, key=lambda x: -bynh[x])))
    # 상한을 하나씩 늘리면 — 잘린 것 중 배당 낮은 순으로 k개 되살린다
    base = [(r, [sorted(c) for c in r["dc"]]) for r in rs]
    b = score(base, "지금 상한")
    mid = halves(rs)
    for k in (1, 2, 3):
        rows = []
        for r in rs:
            c = [sorted(x) for x in r["dc"]]
            have = {tuple(x) for x in c}
            add = sorted([x for x in (r.get('capcut') or []) if tuple(x) not in have],
                         key=lambda x: r["q"].get(tuple(x)) or 9e9)[:k]
            rows.append((r, c + add))
        a = score(rows, "상한 +%d" % k, b['slots'] if b else None)
        print("    판정 " + verdict(b, a))
        if k == 1:
            for lab, sel in (("전반", lambda r: r['date'] < mid), ("후반", lambda r: r['date'] >= mid)):
                x = score([(r, cc) for r, cc in base if sel(r)], "", quiet=True)
                y = score([(r, cc) for r, cc in rows if sel(r)], "", quiet=True)
                if x and y:
                    print("      %s %.1f→%.1f %s" % (lab, x['ex3'], y['ex3'],
                                                     "개선" if y['ex3'] > x['ex3'] else "악화"))


def check_sapporo():
    print("=" * 118)
    print("[검산] 삿포로 1경주 2026-08-16 · 결과 10-3-14 · 정답 3+10")
    d = json.load(open('data/analysis_log/2026_08_16_삿포로_1경주.json', encoding='utf-8'))
    cp = d.get('corePicks') or {}
    q = {}
    for c in (cp.get('quinella') or []):
        cb = c.get('combo') or []
        if len(cb) == 2:
            try:
                q[tuple(sorted(int(x) for x in cb))] = float(c.get('odds') or 0)
            except (TypeError, ValueError):
                pass
    base = [sorted(c) for c in ((cp.get('displayedCombos') or {}).get('quinellas') or [])]
    tri = [[int(v) for v in (x.get('combo') or [])]
           for x in (cp.get('finalTrifectas') or []) if len(x.get('combo') or []) == 3]
    print("  복승 표시 %s · 삼복승 관찰 %s" % (base, tri))
    print("  3+10 배당 %s (범위 %s~%s)" % (q.get((3, 10)), LO, HI))
    for nm, cand in (("지금", cross_now({'dc': base})[1]),
                     ("삼복승 포함", cross_trio({'dc': base, 'tri': tri})[1])):
        p = pick(cand, base, q)
        print("  %-10s 후보 %s → 교차 짝 %s" % (nm, cand, p))


if __name__ == "__main__":
    for sport, tag in (("cycle", "경륜"), ("horse", "경마")):
        rs = load(sport)
        if not rs:
            print("%s 데이터 없음" % tag)
            continue
        jp = [r for r in rs if not r.get('kr')]
        kr = [r for r in rs if r.get('kr')]
        task4(jp or rs, tag)
        task5(jp or rs, tag)
        if kr:
            print("  ※ %s 한국 %d경주" % (tag, len(kr)))
    print("=" * 118)
    print("한국: 확정배당 보유 0경주 — 측정 불가(원칙 1)")
    check_sapporo()
