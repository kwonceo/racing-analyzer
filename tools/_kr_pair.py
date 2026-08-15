# -*- coding: utf-8 -*-
"""[측정 · 읽기 전용] 한국만 — 교차 짝 후보를 넓히면.

2026-08-15 대표 지시. 오늘 한국 복기에서 미적중 10건 중 5건이
「두 말을 이미 짚었는데 그 짝만 없었다」였다.
지금 교차 짝은 **이미 낸 조합에 등장한 말**만 후보로 본다.
유력마·화면별표까지 넓히면 그 다섯 건을 잡는지 본다.

🔴 한국은 확정배당이 거의 없다 → **마감 직전 배당판 값으로 근사**한다(원칙 15).
   확정배당 기준이 아니므로 절대값은 신뢰하지 않는다.
⚠ 경주가 15건뿐이다. **판정 불가**. 방향만 본다(원칙 1).
"""
import sys, glob, json, os, itertools, collections, statistics as st


def load(date="2026_08_15"):
    out = []
    for f in sorted(glob.glob('data/analysis_log/%s_*.json' % date)):
        b = os.path.basename(f)
        if not any(k in b for k in ('서울', '부산', '제주')):
            continue
        try:
            d = json.load(open(f, encoding='utf-8'))
        except Exception:
            continue
        res = d.get('result') or {}
        if res.get('1st') is None or res.get('2nd') is None:
            continue
        # 마감 직전 배당판(odds_history 마지막 스냅샷)
        hp = f.replace('analysis_log', 'odds_history')
        q = {}
        try:
            h = json.load(open(hp, encoding='utf-8'))
            sn = [s for s in (h.get('snapshots') or []) if s.get('quinella')]
            if sn:
                last = max(sn, key=lambda x: x.get('t') or 0)
                for k, v in (last.get('quinella') or {}).items():
                    try:
                        key = tuple(sorted(int(x) for x in str(k).replace('-', '+').split('+')))
                        o = float(v)
                        if len(key) == 2 and o > 0 and (q.get(key) is None or o < q[key]):
                            q[key] = o
                    except Exception:
                        pass
        except Exception:
            pass
        if not q:
            continue
        cp = d.get('corePicks') or {}
        dc = [tuple(sorted(c)) for c in ((cp.get('displayedCombos') or {}).get('quinellas') or [])]
        if not dc:
            continue
        out.append({
            'nm': b.replace('.json', '').replace(date + '_', ''),
            'top2': sorted([res['1st'], res['2nd']]),
            'q': q, 'dc': [list(c) for c in dc],
            'kh': [int(x) for x in (d.get('keyHorses') or []) if str(x).isdigit()],
            'star': [int(x) for x in (cp.get('starHorses') or []) if str(x).isdigit()]})
    return out


def pool_now(r, n=4):
    c = collections.Counter()
    for combo in r['dc']:
        for h in combo:
            c[h] += 1
    return [h for h, _ in c.most_common()][:n]


def add_one(r, pool, lo=6.0, hi=30.0):
    have = {tuple(sorted(c)) for c in r['dc']}
    best = None
    for a, b in itertools.combinations(sorted(set(pool)), 2):
        k = (a, b)
        if k in have:
            continue
        o = r['q'].get(k)
        if o is None or not (lo <= o <= hi):
            continue
        if best is None or o < best[0]:
            best = (o, [a, b])
    return r['dc'] + ([best[1]] if best else [])


def line(nm, rows, base=None):
    slots = sum(len(c) for _, c in rows)
    hit = [(r, c) for r, c in rows if r['top2'] in [sorted(x) for x in c]]
    ret = sum(r['q'].get(tuple(r['top2']), 0) for r, _ in hit)
    ho = sorted([r['q'].get(tuple(r['top2']), 0) for r, _ in hit], reverse=True)
    o = [r['q'].get(tuple(sorted(x))) for r, c in rows for x in c]
    o = [x for x in o if x]
    g = "" if base is None else " 구좌%+5.1f%%" % ((slots / base - 1) * 100)
    print("  %-26s 경주%3d 구좌%3d 적중%2d(%4.1f%%) 3제외%6.1f%% 배당중앙%5.2f%s"
          % (nm, len(rows), slots, len(hit), len(hit) / len(rows) * 100,
             (ret - sum(ho[:3])) / slots * 100 if slots else 0,
             st.median(o) if o else 0, g))
    return slots


def main(date="2026_08_15"):
    rs = load(date)
    print("=" * 104)
    print("한국 %d경주 (%s) — 교차 짝 후보를 넓히면" % (len(rs), date))
    print("⚠ 확정배당이 없어 마감 배당판으로 근사했다. 15경주라 판정 불가 · 방향만.")
    base = line("현행(짝 없음)", [(r, r['dc']) for r in rs])
    line("지금 방식(낸 조합 상위4)", [(r, add_one(r, pool_now(r))) for r in rs], base)
    line("+ 유력마", [(r, add_one(r, pool_now(r) + r['kh'][:4])) for r in rs], base)
    line("+ 화면 별표", [(r, add_one(r, pool_now(r) + r['star'][:4])) for r in rs], base)
    line("+ 둘 다", [(r, add_one(r, pool_now(r) + r['kh'][:4] + r['star'][:4])) for r in rs], base)
    line("+ 둘 다 · 배당 제한 없음", [(r, add_one(r, pool_now(r) + r['kh'][:4] + r['star'][:4],
                                             lo=0, hi=9e9)) for r in rs], base)
    # 놓친 다섯 건이 실제로 잡히는지
    print("  -- 미적중 경주에서 정답이 잡히나 --")
    for r in rs:
        if r['top2'] in [sorted(c) for c in r['dc']]:
            continue
        got = []
        for tag, pool in (("지금", pool_now(r)),
                          ("유력마추가", pool_now(r) + r['kh'][:4]),
                          ("별표추가", pool_now(r) + r['star'][:4]),
                          ("둘다", pool_now(r) + r['kh'][:4] + r['star'][:4])):
            if r['top2'] in [sorted(c) for c in add_one(r, pool)]:
                got.append(tag)
        print("    %-12s 정답 %d+%d %s"
              % (r['nm'], r['top2'][0], r['top2'][1], ("🟢 " + "·".join(got)) if got else "못 잡음"))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "2026_08_15")
