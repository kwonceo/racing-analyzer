# -*- coding: utf-8 -*-
"""[읽기 전용] 2,500경주 데이터 활용 — **교차 조건 탐색**(다변량).

🔴 왜 이것인가 (2026-09-01 대표 화두)
  "우리 문제는 2,500경주 데이터를 활용을 잘 못하는 거야. 어떻게 써야 될지 몰라."
  지금까지 우리는 **축을 하나씩** 손으로 쟀다(단변량). 오늘만 6축을 쟀고 **생존 0개**였다.
  🔴 그런데 `ml_training_data.jsonl` 에 **feature 18개 + label(입상·배당·ROI)** 이
    6,069행 쌓여 있는데 **모델도 교차 탐색도 한 번도 안 돌렸다.**
  ⇒ 「하나씩」으로 못 찾은 것을 「둘이 엮인 조건」에서 찾는다.

🔴 블랙박스 모델을 쓰지 않는 이유
  이 프로젝트는 모든 채택에 **판정 4단계**(적중 30건 · 대박3제외 · 기간분할 · 종목별)를 건다.
  설명되지 않는 예측은 그 판정을 걸 수 없다. 그래서 **셀(조건 조합) 단위**로 본다.
  ⇒ 경륜 `build_flow_table` 과 같은 틀을 **2-way 로 확장**한 것이다.

🔴 다중비교 방어 — 이게 이 도구의 핵심이다
  조합을 수백 개 훑으면 **우연히 좋은 셀이 반드시 나온다.**
  ⓐ 전반기(train)에서 후보를 뽑고 **후반기(test)에서 재현되는지**만 본다(OOS)
  ⓑ 양쪽 모두 n>=MIN_N · 양쪽 모두 기준 통과라야 「생존」
  ⓒ 훑은 셀 수를 **함께 출력**한다 — 몇 개 중 몇 개가 살았는지 알아야 우연을 판단한다

⚠ label 의 `roi` 는 **입상(place) 기준**이다. 복승 회수율과 다르다 — 절대값을 인용하지 말 것.
⚠ 원칙 1(n<30) · 2(극단값) · 8-C(분모) · 30(결손이 신호처럼 보인다)

실행: python tools/mine_cross_cells.py [--min N] [--split YYYY-MM-DD]
"""
import io
import os
import sys
import json
import math
import collections

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(BASE, "data", "ml_training_data.jsonl")
MIN_N = 40                      # 셀당 최소 표본(양쪽 기간 각각)
SPLIT = "2026-08-15"            # 전반기/후반기 경계

# 수치형은 구간으로 자른다(경계는 실측 분포에서 정한 것이 아니라 **의미 단위**로 고정 — 소급 최적화 방지)
NUM_BINS = {
    "max_drop_pct":    [(-999, -40, "급락40%+"), (-40, -25, "급락25~40"), (-25, -10, "급락10~25"),
                        (-10, 10, "횡보"), (10, 999, "급등")],
    "drop_timing_min": [(-999, -10, "T-10이전"), (-10, -3, "T-10~3"), (-3, 999, "T-3이내")],
    "form_score":      [(-999, 40, "전적하"), (40, 80, "전적중"), (80, 999, "전적상")],
    "horse_count":     [(0, 8, "8두↓"), (8, 11, "9~10두"), (11, 99, "11두↑")],
    "gate_no":         [(0, 4, "안쪽"), (4, 8, "중간"), (8, 99, "바깥")],
}
CAT = ("gait_type", "pace_type", "sport", "drop_source", "t1_drop", "reversal", "smart_money",
       "jockey_change", "track_condition")


def binof(k, v):
    if k in NUM_BINS:
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        for lo, hi, lbl in NUM_BINS[k]:
            if lo <= f < hi:
                return lbl
        return None
    if v is None or v == "":
        return None
    return str(v)


def wilson(h, n):
    if n <= 0:
        return 0.0, 0.0
    z = 1.96
    p = h / float(n)
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    m = z * math.sqrt(max(p * (1 - p) / n + z * z / (4 * n * n), 0.0)) / d
    return c - m, c + m


def main():
    args = sys.argv[1:]
    split = SPLIT
    minn = MIN_N
    if "--split" in args:
        split = args[args.index("--split") + 1]
    if "--min" in args:
        minn = int(args[args.index("--min") + 1])

    rows = []
    for l in io.open(SRC, encoding="utf-8", errors="replace"):
        l = l.strip()
        if not l:
            continue
        try:
            r = json.loads(l)
        except Exception:
            continue
        L = r.get("label") or {}
        if not isinstance(L, dict) or L.get("hit_place") is None:
            continue
        f = dict(r.get("features") or {})
        f["sport"] = r.get("sport")
        rows.append({"d": str(r.get("date") or "")[:10], "f": f,
                     "hit": 1 if L.get("hit_place") else 0,
                     "od": L.get("odds_place")})
    tr = [x for x in rows if x["d"] and x["d"] < split]
    te = [x for x in rows if x["d"] and x["d"] >= split]
    base_tr = sum(x["hit"] for x in tr) / float(len(tr)) if tr else 0
    base_te = sum(x["hit"] for x in te) / float(len(te)) if te else 0
    print("[교차 조건 탐색] 2,500경주 데이터 다변량 활용")
    print("   표본 %d행 · 전반기(<%s) %d · 후반기 %d" % (len(rows), split, len(tr), len(te)))
    print("   기준 입상률: 전반기 %.1f%% · 후반기 %.1f%%" % (100 * base_tr, 100 * base_te))
    print("   🔴 전반기에서 뽑고 **후반기에서 재현되는 것만** 생존(다중비교 방어)")
    print("")

    keys = [k for k in NUM_BINS] + list(CAT)
    cells_tr = collections.defaultdict(lambda: [0, 0, []])
    cells_te = collections.defaultdict(lambda: [0, 0, []])
    for src, dst in ((tr, cells_tr), (te, cells_te)):
        for x in src:
            tags = []
            for k in keys:
                b = binof(k, x["f"].get(k))
                if b is not None:
                    tags.append("%s=%s" % (k, b))
            for i in range(len(tags)):
                for j in range(i + 1, len(tags)):
                    c = dst[(tags[i], tags[j])]
                    c[0] += 1
                    c[1] += x["hit"]
                    if x["hit"] and x["od"]:
                        c[2].append(float(x["od"]))

    scanned = 0
    cands = []
    for key, (n, h, od) in cells_tr.items():
        if n < minn:
            continue
        scanned += 1
        lo, _hi = wilson(h, n)
        if lo <= base_tr:                       # 전반기 CI 하한이 기준선을 못 넘으면 후보 아님
            continue
        cands.append((key, n, h, lo))
    cands.sort(key=lambda x: -x[3])

    print("   전반기에서 훑은 셀 %d개(n>=%d) · 그중 후보 %d개" % (scanned, minn, len(cands)))
    print("")
    survived = 0
    print("   %-46s %14s %14s" % ("조건 조합", "전반기", "후반기(OOS)"))
    for key, n, h, lo in cands[:25]:
        n2, h2, od2 = cells_te.get(key, [0, 0, []])
        if n2 < minn:
            mark = "n<%d" % minn
            ok = False
        else:
            lo2, _ = wilson(h2, n2)
            ok = lo2 > base_te
            mark = "%.1f%% ci≥%.1f%%" % (100.0 * h2 / n2, 100 * lo2)
        if ok:
            survived += 1
        print("   %-46s %5d %5.1f%%  %s %s"
              % (" + ".join(key)[:46], n, 100.0 * h / n, mark, "🟢 생존" if ok else ""))
    print("")
    print("   🔴 생존 %d / 후보 %d (훑은 셀 %d)" % (survived, len(cands[:25]), scanned))
    if survived == 0:
        print("      → 전반기 우위가 후반기에 재현되지 않았다. **우연이었다**는 뜻이다.")
    else:
        print("      ⚠ 생존해도 **입상 기준**이다. 복승 회수율·판정 4단계는 별도로 걸어야 한다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
