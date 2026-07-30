# -*- coding: utf-8 -*-
"""[적중왕전개 Phase 1·2] 전개 확률 테이블 생성기 — 경륜 전용 · 완전 읽기 전용.

설계 6원칙(2026-07-30 권대표 지시)을 코드로 고정한다:
  ① paceBonus·record_score 를 입력으로 쓰지 않는다 — 원본 gait_lists·pace 에서 직접 계산.
     (`paceBonus` 는 매핑 방향이 반대임이 실측됐다. 입력으로 쓰면 그 오류가 엔진에 전파된다.)
  ② 셀은 최소로 — 페이스(3) × 두수(7·9) = 6셀. 구장·등급·거리는 넣지 않는다(소표본 착시 방지).
  ③ 모든 출력에 n·신뢰구간. n<MIN_N 이면 prob=null + reason.
  ④ 채택 기준은 엣지 점추정 1.0 이 아니라 **엣지 신뢰구간 하한 > 1.0**.
  ⑤ out-of-sample 필수 — 테이블 생성 기간과 검증 기간을 날짜로 분리(--split).
  ⑥ 경륜 전용(sport=cycle). 경마는 표본 부족 + 각질이 역산값이라 제외.

입력: data/simulation_db/keirin_profiles.jsonl (pace·gait_lists·result.top3)
      data/odds_history/<race_id>.json        (복승 전체 배당판 — 시장암시확률 분모)
출력: data/simulation_db/flow_table.json

⚠ 운영 데이터는 읽기만 한다. 추천·판정·학습 경로에 개입하지 않는다.

실행:
  python tools/build_flow_table.py                      # 전기간 테이블
  python tools/build_flow_table.py --split 2026-07-26   # 전반기 생성 + 후반기 OOS 검증
  python tools/build_flow_table.py --stats              # 요약만
"""
import json
import os
import re
import sys
import glob
import math
import random
import argparse
import collections
import itertools
import statistics
import time

# [콘솔 인코딩] 이 프로젝트 콘솔은 cp949 라 em-dash 등에서 UnicodeEncodeError 로 죽는다
#   (app.py 기동 로그에서 이미 겪은 함정). 출력 스트림을 UTF-8 로 고정한다.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILES = os.path.join(ROOT, "data", "simulation_db", "keirin_profiles.jsonl")
ODDS_DIR = os.path.join(ROOT, "data", "odds_history")
OUT = os.path.join(ROOT, "data", "simulation_db", "flow_table.json")

SCHEMA_VERSION = 1
MIN_N = 30                 # ③ 원칙 1: n<30 은 판정 불가
FIELD_SIZES = (7, 9)       # ② 두수 축은 이 둘만(나머지는 표본 부족)
PACES = ("빠른", "보통", "느린")
BOOT_ITERS = 2000          # ④ 엣지 신뢰구간 = 부트스트랩(비율의 비율이라 해석해가 없음)
SUM_INV_LO, SUM_INV_HI = 1.25, 1.45   # 배당판 건전성 게이트(실측 중앙 1.348)
RANDOM_SEED = 20260730     # 재현성 — 부트스트랩 고정 시드


# ───────────────────────── 입력 로드 ─────────────────────────
def load_profiles():
    out = []
    with open(PROFILES, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


def _qmap(sn):
    """스냅샷 → {(a,b): 배당}. 리스트/딕셔너리 두 형식 모두 정규화."""
    q = sn.get("quinella")
    m = {}
    if isinstance(q, dict):
        for k, v in q.items():
            nn = [int(x) for x in re.findall(r"\d+", str(k))]
            if len(nn) == 2 and v:
                try:
                    m[tuple(sorted(nn))] = float(v)
                except (TypeError, ValueError):
                    pass
    elif isinstance(q, list):
        for it in q:
            if not isinstance(it, dict):
                continue
            c, o = it.get("combo"), it.get("odds")
            if c and o and len(c) == 2:
                try:
                    m[tuple(sorted(int(x) for x in c))] = float(o)
                except (TypeError, ValueError):
                    pass
    return m


def full_board(race_id, need):
    """마감 전 마지막 '완전' 배당판(조합 수 == need) + 그 스냅샷 반환. 없으면 (None, None).

    ⚠ opening(>=100배) 을 제외하지 않는다 — 실측상 경륜 7두의 100배+ 는 껍데기가 아니라
      진짜 고배당이다(완전 배당판 330경주의 Σ(1/배당) 중앙 1.348 = 환급률 0.742 유지 ·
      100배+ 가 Σ 에서 차지하는 비중 중앙 2.66%). 제외하면 배당판이 불완전해져
      정규화 분모가 작아지고 시장암시확률이 과대추정된다.
    ⚠ 오염 스냅샷(odds_suspect·baseline_reset·next_race_blocked·after_close)은 건너뛴다.
    """
    p = os.path.join(ODDS_DIR, race_id + ".json")
    if not os.path.exists(p):
        return None, None
    try:
        doc = json.load(open(p, encoding="utf-8"))
    except Exception:
        return None, None
    for sn in reversed(doc.get("snapshots") or []):
        if sn.get("after_close") or sn.get("odds_suspect") \
           or sn.get("baseline_reset") or sn.get("next_race_blocked"):
            continue
        m = _qmap(sn)
        if len(m) >= need and all(v > 0 for v in m.values()):
            return m, sn
    return None, None


def gait_map(gait_lists):
    """gait_lists {'선행':[7,2],...} → {마번: 각질}. ① 원본에서 직접 — paceBonus 미사용."""
    m = {}
    for style, nos in (gait_lists or {}).items():
        for n in (nos or []):
            try:
                m[int(n)] = style
            except (TypeError, ValueError):
                pass
    return m


# ───────────────────────── 통계 ─────────────────────────
def wilson(hits, n, z=1.96):
    """③ 비율 신뢰구간 — Wilson score interval(소표본에서 정규근사보다 안정)."""
    if n <= 0:
        return [None, None]
    p = hits / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return [round(max(0.0, (c - half) / d), 4), round(min(1.0, (c + half) / d), 4)]


def bootstrap_edge_ci(samples, iters=BOOT_ITERS, lo=2.5, hi=97.5, seed=RANDOM_SEED):
    """④ 엣지 신뢰구간 — 부트스트랩.

    samples = [(hit:0|1, implied:float), ...]. 각 리샘플에서
      edge = (Σhit / n) / (Σimplied / n) 를 재계산해 분포의 백분위를 취한다.
    비율의 비율이라 해석적 분산식이 없어 부트스트랩이 맞다(권대표 지시 권장안).
    """
    n = len(samples)
    if n < 2:
        return [None, None]
    rnd = random.Random(seed)
    vals = []
    for _ in range(iters):
        h = 0.0
        s = 0.0
        for _ in range(n):
            a, b = samples[rnd.randrange(n)]
            h += a
            s += b
        if s > 0:
            vals.append((h / n) / (s / n))
    if not vals:
        return [None, None]
    vals.sort()

    def pct(q):
        k = (len(vals) - 1) * q / 100.0
        f = math.floor(k)
        c = min(f + 1, len(vals) - 1)
        return vals[f] + (vals[c] - vals[f]) * (k - f)
    return [round(pct(lo), 4), round(pct(hi), 4)]


# ───────────────────────── 집계 ─────────────────────────
def collect(profiles, date_from=None, date_to=None):
    """조합 단위 표본 수집. 모집단 = 시장 전판(추천 선택 편향 제거).

    반환 (cells, diag) — cells[cell][pair] = [(hit, implied, odds, race_id), ...]
    """
    cells = collections.defaultdict(lambda: collections.defaultdict(list))
    diag = collections.Counter()
    races = collections.defaultdict(set)
    for r in profiles:
        if r.get("sport") != "cycle":            # ⑥ 경륜 전용
            diag["제외:비경륜"] += 1
            continue
        d = r.get("date") or ""
        if date_from and d < date_from:
            diag["제외:기간밖"] += 1
            continue
        if date_to and d >= date_to:
            diag["제외:기간밖"] += 1
            continue
        pace = r.get("pace")
        gl = r.get("gait_lists")
        top3 = [x for x in ((r.get("result") or {}).get("top3") or []) if x]
        if not gl:
            diag["제외:각질없음"] += 1
            continue
        if pace not in PACES:
            diag["제외:페이스없음"] += 1
            continue
        if len(top3) < 2:
            diag["제외:결과없음"] += 1
            continue
        gm = gait_map(gl)
        n = len(gm)
        if n not in FIELD_SIZES:
            diag["제외:두수 %s" % n] += 1
            continue
        need = n * (n - 1) // 2
        board, _sn = full_board(r["race_id"], need)
        if not board:
            diag["제외:완전배당판없음"] += 1
            continue
        s_inv = sum(1.0 / v for v in board.values())
        if not (SUM_INV_LO <= s_inv <= SUM_INV_HI):
            diag["제외:배당판건전성(Σ=%.2f)" % s_inv] += 1
            continue
        top2 = set(top3[:2])
        cell = "%s|%d두" % (pace, n)
        races[cell].add(r["race_id"])
        diag["채택경주"] += 1
        for a, b in itertools.combinations(sorted(gm.keys()), 2):
            o = board.get((a, b))
            if not o or o <= 0:
                continue
            # ② 시장암시확률 = 정규화 (1/배당)/Σ(1/배당) — 경주별 자기교정.
            #    고정상수 0.75/배당 과 실측 차이는 중앙 1.1% 로 미미하나, 정규화는
            #    경주별 환급률 편차를 흡수하고 Σ(암시)=1 이 보장돼 엣지가 제로섬이 된다.
            implied = (1.0 / o) / s_inv
            hit = 1 if {a, b} <= top2 else 0
            pair = "+".join(sorted([gm[a], gm[b]]))
            cells[cell][pair].append((hit, implied, o, r["race_id"]))
    return cells, diag, races


def build_cells(cells, races):
    """③④ 셀×각질쌍 → prob·ci·market_implied·edge·edge_ci. n<MIN_N 은 prob=null."""
    out = {}
    for cell in sorted(cells.keys()):
        block = {}
        for pair, sm in sorted(cells[cell].items(), key=lambda x: -len(x[1])):
            n = len(sm)
            hits = sum(x[0] for x in sm)
            imp = statistics.fmean(x[1] for x in sm) if sm else None
            if n < MIN_N:
                block[pair] = {"prob": None, "n": n, "hits": hits,
                               "market_implied": round(imp, 4) if imp else None,
                               "reason": "표본 부족(n<%d)" % MIN_N}
                continue
            prob = hits / n
            edge = (prob / imp) if imp else None
            block[pair] = {
                "prob": round(prob, 4),
                "n": n, "hits": hits,
                "ci": wilson(hits, n),
                "market_implied": round(imp, 4),
                "edge": round(edge, 4) if edge else None,
                "edge_ci": bootstrap_edge_ci([(x[0], x[1]) for x in sm]),
                "median_odds": round(statistics.median(x[2] for x in sm), 1),
            }
        out[cell] = block
        out[cell]["_meta"] = {"races": len(races.get(cell, ())),
                              "combos": sum(len(v) for v in cells[cell].values())}
    return out


def survivors(table):
    """④ 엣지 신뢰구간 하한 > 1.0 인 칸만 = 채택 후보."""
    got = []
    for cell, block in table.items():
        for pair, v in block.items():
            if pair == "_meta" or not isinstance(v, dict):
                continue
            lo = (v.get("edge_ci") or [None])[0]
            if v.get("prob") is not None and lo is not None and lo > 1.0:
                got.append((cell, pair, v))
    return sorted(got, key=lambda x: -(x[2]["edge_ci"][0]))


def trimmed_edges(cells, cell, pair, drop):
    """⚠ 원칙 2: 상위 k건(배당 큰 적중) 제외 시 엣지. 극단값 의존도 확인."""
    sm = cells[cell][pair]
    if not sm:
        return None
    order = sorted(sm, key=lambda x: -(x[2] if x[0] else 0))
    kept = order[drop:]
    if not kept:
        return None
    p = sum(x[0] for x in kept) / len(kept)
    i = statistics.fmean(x[1] for x in kept)
    return round(p / i, 4) if i else None


def main():
    global MIN_N
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default=None,
                    help="OOS 분할 기준일(YYYY-MM-DD). 이 날짜 '전'=생성기간, '이후'=검증기간")
    ap.add_argument("--stats", action="store_true", help="요약만 출력(파일 저장 생략)")
    ap.add_argument("--min-n", type=int, default=MIN_N)
    a = ap.parse_args()
    MIN_N = a.min_n

    profs = load_profiles()
    print("keirin_profiles 로드: %d행" % len(profs))

    cells, diag, races = collect(profs)
    table = build_cells(cells, races)
    dates = sorted({r.get("date") for r in profs if r.get("sport") == "cycle" and r.get("date")})

    print("\n[모집단 필터]")
    for k, v in diag.most_common():
        print("  %-28s%d" % (k, v))

    print("\n[셀 요약]  (* = n<%d 판정불가)" % MIN_N)
    for cell in sorted(table):
        meta = table[cell]["_meta"]
        print("  %-10s 경주 %3d · 조합 %5d" % (cell, meta["races"], meta["combos"]))
        for pair, v in table[cell].items():
            if pair == "_meta":
                continue
            if v.get("prob") is None:
                print("      %-10s n=%-5d %s" % (pair, v["n"], v.get("reason")))
            else:
                print("      %-10s n=%-5d 적중률 %.3f ci[%.3f~%.3f] 시장 %.4f "
                      "엣지 %.3f ci[%.3f~%.3f] 중앙배당 %.1f"
                      % (pair, v["n"], v["prob"], v["ci"][0], v["ci"][1],
                         v["market_implied"], v["edge"], v["edge_ci"][0], v["edge_ci"][1],
                         v["median_odds"]))

    surv = survivors(table)
    print("\n[전기간 · 엣지 신뢰구간 하한 > 1.0 인 칸]  → %d개" % len(surv))
    if not surv:
        print("  없음 — 현 표본에서 시장을 이기는 각질쌍 셀을 찾지 못했다(정직한 결과).")
    for cell, pair, v in surv:
        t1 = trimmed_edges(cells, cell, pair, 1)
        t3 = trimmed_edges(cells, cell, pair, 3)
        print("  %-10s %-10s n=%-5d 엣지 %.3f ci[%.3f~%.3f] · 상위1제외 %s · 상위3제외 %s"
              % (cell, pair, v["n"], v["edge"], v["edge_ci"][0], v["edge_ci"][1], t1, t3))

    doc = {
        "schema_version": SCHEMA_VERSION,
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "sport": "cycle",
        "min_n": MIN_N,
        "method": {
            "prob": "복승 적중률 = 조합 2두가 1·2착 | 모집단=시장 전판(추천 편향 제거)",
            "market_implied": "정규화 (1/배당)/Σ(1/배당) · 완전 배당판만 · opening 미제외",
            "prob_ci": "Wilson score interval (z=1.96)",
            "edge_ci": "부트스트랩 %d회 · 시드 %d · 2.5/97.5 백분위" % (BOOT_ITERS, RANDOM_SEED),
            "adopt_rule": "엣지 신뢰구간 하한 > 1.0 (점추정 1.0 아님)",
            "excluded_inputs": ["paceBonus", "record_score", "comp_score",
                                "구장", "등급", "거리"],
            "board_health_gate": "Σ(1/배당) ∈ [%.2f, %.2f]" % (SUM_INV_LO, SUM_INV_HI),
        },
        "source_period": {"from": dates[0] if dates else None,
                          "to": dates[-1] if dates else None},
        "cells": table,
        "survivors": [{"cell": c, "pair": p, "edge": v["edge"], "edge_ci": v["edge_ci"],
                       "n": v["n"]} for c, p, v in surv],
    }

    # ⑤ out-of-sample — 날짜로 분리
    if a.split:
        c1, d1, r1 = collect(profs, date_to=a.split)
        c2, d2, r2 = collect(profs, date_from=a.split)
        t1, t2 = build_cells(c1, r1), build_cells(c2, r2)
        s1, s2 = survivors(t1), survivors(t2)
        s2map = {(c, p) for c, p, _ in s2}
        alive = [(c, p, v) for c, p, v in s1 if (c, p) in s2map]
        print("\n" + "=" * 74)
        print("[⑤ out-of-sample 검증]  분할 기준 %s" % a.split)
        print("  전반기(< %s) 채택경주 %d · 생존칸 %d" % (a.split, d1["채택경주"], len(s1)))
        print("  후반기(>=%s) 채택경주 %d · 생존칸 %d" % (a.split, d2["채택경주"], len(s2)))
        print("  → 양 기간 모두 엣지 하한>1.0 인 칸(=생존): %d개" % len(alive))
        if not alive:
            print("     없음 — 전반기 신호가 후반기에 재현되지 않았다.")
        for c, p, v in alive:
            w = dict(t2[c][p])
            print("     %-10s %-10s 전반 엣지 %.3f ci[%.3f~%.3f] n=%d "
                  "→ 후반 엣지 %.3f ci[%.3f~%.3f] n=%d"
                  % (c, p, v["edge"], v["edge_ci"][0], v["edge_ci"][1], v["n"],
                     w["edge"], w["edge_ci"][0], w["edge_ci"][1], w["n"]))
        for c, p, _v in s1:
            if (c, p) not in s2map:
                w = t2.get(c, {}).get(p)
                if isinstance(w, dict):
                    if w.get("prob") is None:
                        print("     [탈락] %-10s %-10s 후반 n=%d(%s)"
                              % (c, p, w.get("n", 0), w.get("reason")))
                    else:
                        print("     [탈락] %-10s %-10s 후반 엣지 %.3f ci[%.3f~%.3f] n=%d"
                              % (c, p, w["edge"], w["edge_ci"][0], w["edge_ci"][1], w["n"]))
                else:
                    print("     [탈락] %-10s %-10s 후반 표본 없음" % (c, p))
        doc["oos"] = {
            "split": a.split,
            "build_period": {"races": d1["채택경주"], "survivors": len(s1)},
            "test_period": {"races": d2["채택경주"], "survivors": len(s2)},
            "survived_both": [{"cell": c, "pair": p,
                               "build_edge": v["edge"], "build_edge_ci": v["edge_ci"],
                               "test_edge": t2[c][p]["edge"],
                               "test_edge_ci": t2[c][p]["edge_ci"]} for c, p, v in alive],
            "build_table": t1, "test_table": t2,
        }

    if not a.stats:
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        tmp = OUT + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=1)
        os.replace(tmp, OUT)
        print("\n저장: %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
