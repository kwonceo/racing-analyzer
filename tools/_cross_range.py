# -*- coding: utf-8 -*-
"""[측정 · 읽기 전용] 교차 짝 상한을 넓히면 · 몰림 경주.

2026-08-16 대표 지시. 니가타 1경주(결과 1-4-8 · 정답 1+4 = 34.3배)에서
교차 짝이 1+9(21.8배)를 만들고 1+4 는 안 만들었다.

작업1  상한 6~30 / 6~40 / 6~50 / 6~100 을 각각 잰다
작업2  상한 때문에 못 만든 짝 중 정답이 몇 건이고 배당이 어디에 몰렸나
작업3  추천 전부가 한 마리를 끼는 경주가 몇 %이고 성적은 어떤가

⚠ 배선하지 않는다. 읽기만 한다.
"""
import glob
import itertools
import json
import os
import statistics as st
import sys
from collections import Counter

sys.path.insert(0, 'tools')
import measure_recovery as M

LO = 6.0
HIS = [30.0, 40.0, 50.0, 100.0]
TOPN = 4


def clean(r):
    try:
        return M.CLEAN_LO <= r["po"] / r["mo"] <= M.CLEAN_HI
    except Exception:
        return False


def load(sport, kr=None):
    """kr=True 한국만 · False 일본만 · None 전부."""
    out = []
    for r in M.load_races(sport=sport, pattern="2026_0*"):
        if not clean(r):
            continue
        nm = str(r.get("file") or r.get("race") or "")
        out.append(r)
    return out


def base_combos(r):
    """교차 짝을 빼기 전의 표시 조합. 소급이라 crossPair 표식이 없으면 그대로 쓴다."""
    return [sorted(c) for c in r["dc"]]


def pick(already, q, hi):
    """교차 짝 재현 — 상위 TOPN 빈출마 사이의 짝 중 LO~hi 최저 1개."""
    cnt = Counter()
    for c in already:
        for h in c:
            cnt[int(h)] += 1
    cand = [h for h, _ in cnt.most_common()][:TOPN]
    if len(cand) < 2:
        return None
    have = {tuple(sorted(c)) for c in already}
    best = None
    for a, b in itertools.combinations(sorted(cand), 2):
        k = (a, b)
        if k in have:
            continue
        o = q.get(k)
        if not o or not (LO <= o <= hi):
            continue
        if best is None or o < best[0]:
            best = (o, [a, b])
    return best


def score(rows, tag, base_slots=None):
    if not rows:
        print("  %-22s 경주 0" % tag)
        return None
    slots = sum(len(c) for _, c in rows)
    hit = [(r, c) for r, c in rows if sorted(r["top2"]) in [sorted(x) for x in c]]
    ret = sum(r["po"] for r, _ in hit)
    ho = sorted([r["po"] for r, _ in hit], reverse=True)
    o = [r["q"].get(tuple(sorted(x))) for r, c in rows for x in c]
    o = [x for x in o if x]
    g = "" if base_slots is None else " 구좌%+6.1f%%" % ((slots / base_slots - 1) * 100)
    print("  %-22s 경주%4d 구좌%5d 적중%3d(%4.1f%%) 대박뺀회수%6.1f%% 배당중앙%6.2f%s%s"
          % (tag, len(rows), slots, len(hit), len(hit) / len(rows) * 100,
             (ret - sum(ho[:3])) / slots * 100 if slots else 0,
             st.median(o) if o else 0, g, "" if len(hit) >= 30 else "  판정불가"))
    return slots


def task1(rs, tag):
    print("=" * 112)
    print("[작업1] %s %d경주 — 교차 짝 상한을 넓히면" % (tag, len(rs)))
    b = score([(r, base_combos(r)) for r in rs], "교차 짝 없이")
    cur = None
    for hi in HIS:
        rows = []
        for r in rs:
            c = base_combos(r)
            p = pick(c, r["q"], hi)
            if p:
                c = c + [p[1]]
            rows.append((r, c))
        s = score(rows, "6~%d배" % hi, b)
        if hi == 30.0:
            cur = s
    if cur and b:
        print("  ※ 지금(6~30배) 구좌는 교차 짝 없이 대비 %+.1f%%" % ((cur / b - 1) * 100))


def task2(rs, tag):
    """상한 때문에 못 만든 짝 중 정답이 몇 건인가."""
    lost, lost_hit, odds_hit = 0, 0, []
    band = Counter()
    for r in rs:
        c = base_combos(r)
        cnt = Counter()
        for x in c:
            for h in x:
                cnt[int(h)] += 1
        cand = [h for h, _ in cnt.most_common()][:TOPN]
        have = {tuple(sorted(x)) for x in c}
        ans = tuple(sorted(r["top2"]))
        for a, b in itertools.combinations(sorted(cand), 2):
            k = (a, b)
            if k in have:
                continue
            o = r["q"].get(k)
            if not o or o <= 30.0:
                continue                       # 지금 상한 안이면 만들어졌다
            lost += 1
            band['30~40배' if o <= 40 else '40~50배' if o <= 50
                 else '50~100배' if o <= 100 else '100배+'] += 1
            if k == ans:
                lost_hit += 1
                odds_hit.append(o)
    print("=" * 112)
    print("[작업2] %s — 상한 30배 때문에 못 만든 짝 %d개 · 그중 정답 %d개 (%.2f%%)"
          % (tag, lost, lost_hit, lost_hit / lost * 100 if lost else 0))
    print("  못 만든 짝의 배당대: " + " · ".join("%s %d(%.1f%%)" % (k, v, v / lost * 100)
                                          for k, v in sorted(band.items())) if lost else "  없음")
    if odds_hit:
        hb = Counter('30~40배' if x <= 40 else '40~50배' if x <= 50
                     else '50~100배' if x <= 100 else '100배+' for x in odds_hit)
        print("  정답이던 것의 배당: " + " · ".join("%s %d" % (k, v) for k, v in sorted(hb.items()))
              + " · 중앙 %.1f배" % st.median(odds_hit))


def task3(rs, tag):
    """추천 전부가 한 마리를 끼는 경주."""
    con, non = [], []
    for r in rs:
        c = base_combos(r)
        if len(c) < 2:
            continue
        common = set(c[0])
        for x in c[1:]:
            common &= set(x)
        (con if common else non).append(r)
    print("=" * 112)
    print("[작업3] %s — 추천 전부가 한 마리를 끼는 경주 %d / %d = %.1f%%"
          % (tag, len(con), len(con) + len(non),
             len(con) / (len(con) + len(non)) * 100 if (con or non) else 0))
    score([(r, base_combos(r)) for r in con], "몰림 경주")
    score([(r, base_combos(r)) for r in non], "안 몰린 경주")
    # 그 말을 안 낀 조합 하나를 강제로 넣으면
    rows = []
    for r in con:
        c = base_combos(r)
        common = set(c[0])
        for x in c[1:]:
            common &= set(x)
        have = {tuple(sorted(x)) for x in c}
        best = None
        for k, o in (r["q"] or {}).items():
            if len(k) != 2 or k in have or (set(k) & common):
                continue
            if not o or o <= 0:
                continue
            if best is None or o < best[0]:
                best = (o, list(k))
        rows.append((r, c + [best[1]] if best else c))
    b = sum(len(base_combos(r)) for r in con)
    score(rows, "몰림 + 안 낀 조합 1", b)


if __name__ == "__main__":
    for sport, tag in (("cycle", "경륜"), ("horse", "경마")):
        rs = load(sport)
        if not rs:
            print("%s 데이터 없음" % tag)
            continue
        task1(rs, tag)
        task2(rs, tag)
        task3(rs, tag)
