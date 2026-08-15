# -*- coding: utf-8 -*-
"""[일회성 측정 · 읽기 전용] 짚은 말끼리 짝 만들기.

2026-08-14 대표 지시. 나흘간 여섯 번 같은 이유로 놓쳤다.
두 말이 **각각** 우리 조합 안에 있었는데 그 둘을 묶은 짝만 없어서 놓친 건이다.
  소노다 2R 7+8 117배 · 야히코 10R 4+7 41.6배
  소노다 9R 3+8 6.2배 · 세이부엔 3R 4+6 15.2배

지금 규칙의 구멍: 자체검사가 **2회 이상 등장한 말끼리만** 본다.
위 네 건은 전부 **1회만 등장한 말**이라 대상에서 빠졌다.

⚠ 회수율 규칙(정제·확정배당·날짜매칭)은 measure_recovery 를 import 해 그대로 쓴다.
⚠ 배당판에 없는 조합(q 에 없음)은 살 수 없으므로 추가 대상에서 제외한다.
"""
import sys, itertools, statistics as st, collections
sys.path.insert(0, 'tools')
import measure_recovery as M


def clean(r):
    try:
        return M.CLEAN_LO <= r["po"] / r["mo"] <= M.CLEAN_HI
    except Exception:
        return False


def horses(r):
    """추천 조합에 등장한 말 — 등장 횟수 많은 순."""
    c = collections.Counter()
    for combo in r["dc"]:
        for h in combo:
            c[h] += 1
    return [h for h, _ in c.most_common()]


def add_pairs(r, topn=None, min_cnt=1):
    """후보 말 사이의 짝 중 아직 없는 것. topn=상위 몇 마리까지 볼지."""
    c = collections.Counter()
    for combo in r["dc"]:
        for h in combo:
            c[h] += 1
    cand = [h for h, n in c.most_common() if n >= min_cnt]
    if topn:
        cand = cand[:topn]
    have = {tuple(sorted(x)) for x in r["dc"]}
    out = []
    for a, b in itertools.combinations(sorted(cand), 2):
        k = (a, b)
        if k in have:
            continue
        if r["q"].get(k) is None:       # 배당판에 없는 조합은 살 수 없다
            continue
        out.append([a, b])
    return out


def line(nm, rows, base_slots=None):
    if not rows:
        print("  %-30s 경주 0" % nm)
        return None
    slots = sum(len(c) for _, c in rows)
    hit = [(r, c) for r, c in rows if sorted(r["top2"]) in [sorted(x) for x in c]]
    ret = sum(r["po"] for r, _ in hit)
    ho = sorted([r["po"] for r, _ in hit], reverse=True)
    o = [r["q"].get(tuple(sorted(x))) for r, c in rows for x in c]
    o = [x for x in o if x]
    grow = ("" if base_slots is None else " 구좌%+5.1f%%" % ((slots / base_slots - 1) * 100))
    print("  %-30s 경주%4d 구좌%5d 적중%3d(%4.1f%%) 3제외%6.1f%% 배당중앙%5.2f%s %s"
          % (nm, len(rows), slots, len(hit), len(hit) / len(rows) * 100,
             (ret - sum(ho[:3])) / slots * 100 if slots else 0,
             st.median(o) if o else 0, grow,
             "" if len(hit) >= 30 else "⚠판정불가"))
    return slots


def main(sport="cycle"):
    rs = [r for r in M.load_races(sport=sport, pattern="2026_0*") if clean(r)]
    print("=" * 116)
    print("%s %d경주 — 짚은 말끼리 짝 만들기" % (sport, len(rs)))
    base = line("현행(대조군)", [(r, r["dc"]) for r in rs])

    # 놓친 실물이 실제로 이 방법으로 잡히는지 먼저 센다
    caught = 0
    for r in rs:
        if sorted(r["top2"]) in [sorted(c) for c in r["dc"]]:
            continue                                   # 이미 맞춘 것은 제외
        if sorted(r["top2"]) in [sorted(c) for c in add_pairs(r)]:
            caught += 1
    miss = len([r for r in rs if sorted(r["top2"]) not in [sorted(c) for c in r["dc"]]])
    print("  ※ 놓친 %d경주 중 짝 추가로 잡히는 것 %d경주 (%.1f%%)"
          % (miss, caught, caught / miss * 100 if miss else 0))

    print(" -- 후보 상위 N마리 사이의 짝을 더한다 --")
    for n in (3, 4, 5, None):
        nm = ("상위%d마리" % n) if n else "전체 후보"
        line("  " + nm, [(r, r["dc"] + add_pairs(r, topn=n)) for r in rs], base)
    print(" -- 2회 이상 등장한 말끼리만(지금 규칙) --")
    line("  2회+ 전체", [(r, r["dc"] + add_pairs(r, min_cnt=2)) for r in rs], base)
    print(" -- 짝만 따로(추가분 성적) --")
    line("  추가된 짝만", [(r, add_pairs(r, topn=4)) for r in rs if add_pairs(r, topn=4)])


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "cycle")


def limited(sport="cycle"):
    """[추가] 구좌 증가를 30% 이내로 묶는 안 — 짝을 골라서 조금만 넣는다."""
    rs = [r for r in M.load_races(sport=sport, pattern="2026_0*") if clean(r)]
    print("=" * 116)
    print("%s %d경주 — 짝을 골라 넣기(구좌 30%% 이내를 노린다)" % (sport, len(rs)))
    base = line("현행(대조군)", [(r, r["dc"]) for r in rs])

    def pick(r, topn, mode, lo=None, hi=None):
        ps = add_pairs(r, topn=topn)
        if not ps:
            return r["dc"]
        vs = [(r["q"][tuple(sorted(p))], p) for p in ps]
        if mode == "band":
            vs = [(o, p) for o, p in vs if lo <= o <= hi]
            if not vs:
                return r["dc"]
        vs.sort()
        one = vs[0][1] if mode in ("low", "band") else vs[-1][1]
        return r["dc"] + [one]

    line("  상위3 짝 중 최저 1개", [(r, pick(r, 3, "low")) for r in rs], base)
    line("  상위3 짝 중 최고 1개", [(r, pick(r, 3, "high")) for r in rs], base)
    line("  상위3 짝 6~30배 1개", [(r, pick(r, 3, "band", 6, 30)) for r in rs], base)
    line("  상위3 짝 10~50배 1개", [(r, pick(r, 3, "band", 10, 50)) for r in rs], base)
    line("  상위4 짝 6~30배 1개", [(r, pick(r, 4, "band", 6, 30)) for r in rs], base)
    line("  상위4 짝 10~50배 1개", [(r, pick(r, 4, "band", 10, 50)) for r in rs], base)


def verify(sport="cycle"):
    """[검증] 채택 후보를 기간 반으로 갈라 앞뒤 둘 다에서 같은 방향인지 본다."""
    import os, json, glob as _g
    dt = {}
    for f in _g.glob('data/analysis_log/2026_0*.json'):
        try:
            d = json.load(open(f, encoding='utf-8'))
        except Exception:
            continue
        res = d.get('result') or {}
        po = (res.get('payouts') or {}).get('quinella')
        if po is None or res.get('1st') is None or res.get('2nd') is None:
            continue
        t = sorted({res['1st'], res['2nd']})
        dt[(t[0], t[1], float(po))] = os.path.basename(f)[:10]
    rs = [r for r in M.load_races(sport=sport, pattern="2026_0*") if clean(r)]
    for r in rs:
        r["d"] = dt.get((r["top2"][0], r["top2"][1], r["po"]), "")

    def pick(r, topn, lo, hi):
        ps = add_pairs(r, topn=topn)
        vs = [(r["q"][tuple(sorted(p))], p) for p in ps
              if lo <= (r["q"].get(tuple(sorted(p))) or 0) <= hi]
        if not vs:
            return r["dc"]
        vs.sort()
        return r["dc"] + [vs[0][1]]

    ds = sorted({r["d"] for r in rs if r["d"]})
    mid = ds[len(ds) // 2] if ds else ""
    print("=" * 116)
    print("%s — 채택 후보 기간 검증 (경계 %s)" % (sport, mid))
    for tag, sub in (("전반", [r for r in rs if r["d"] < mid]),
                     ("후반", [r for r in rs if r["d"] >= mid])):
        b = line("  %s 현행" % tag, [(r, r["dc"]) for r in sub])
        line("  %s 상위4 6~30배 1개" % tag, [(r, pick(r, 4, 6, 30)) for r in sub], b)
        line("  %s 상위4 10~50배 1개" % tag, [(r, pick(r, 4, 10, 50)) for r in sub], b)
