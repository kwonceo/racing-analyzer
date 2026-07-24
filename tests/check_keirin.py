# -*- coding: utf-8 -*-
"""[복기 도구] 3일치 경륜 sweep 유형별 집계 (읽기 전용 · 판정/추천 무영향).
   review_engine.classify_race 를 재사용해 경륜(cycle)만 필터 → type 카운트 + 상세.
   실행: py check_keirin.py [날짜1 날짜2 ...]  (기본 최근 3일 7/23·22·21)"""
import os
import sys
import io

# 콘솔 한글 깨짐(cp949) 방어 — UTF-8 강제
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import review_engine as re

RRD = re.RACE_RESULTS_DIR
ALD = re.ANALYSIS_LOG_DIR
_KRE = getattr(re, "_KEIRIN_ONLY_RE", None)

# 분류 유형(review_engine.REVIEW_TYPES 순서 반영)
TYPES = ("hit", "caught_then_lost", "composition_miss", "near_structure",
         "no_signal_forced", "pure_upset", "data_tainted")


def classify_day(date):
    """하루치 경주 파일을 분류해 경륜(cycle) 케이스 리스트 반환."""
    prefix = date.replace("-", "_")
    cases = []
    for fn in sorted(os.listdir(RRD) if os.path.isdir(RRD) else []):
        if not fn.startswith(prefix) or not fn.endswith(".json"):
            continue
        doc = re._load(os.path.join(RRD, fn))
        if not doc:
            continue
        log_doc = re._load(os.path.join(ALD, fn))
        c = re.classify_race(doc, log_doc)
        if not c:
            continue
        rk = str(doc.get("raceKey") or "")
        c["raceKey"] = rk
        c["sport"] = re._sport_of(doc, rk, _KRE)
        cases.append(c)
    return [c for c in cases if c.get("sport") == "cycle"]


def main(dates):
    grand = {t: 0 for t in TYPES}
    g_total = 0
    for d in dates:
        keirin = classify_day(d)
        by = {t: 0 for t in TYPES}
        for c in keirin:
            t = c.get("type") or "?"
            by[t] = by.get(t, 0) + 1
        g_total += len(keirin)
        for t in TYPES:
            grand[t] += by[t]
        hits = by["hit"]
        rate = round(hits / len(keirin) * 100) if keirin else 0
        print(f"■ {d}  경륜 {len(keirin)}경주  적중 {hits}({rate}%)")
        for t in TYPES:
            if by[t]:
                print(f"    {t:18s}: {by[t]}")
        # 편성 개선 재료 2종 상세(정답 1·2착)
        for label, tkey in (("MISS(조합미편성)", "composition_miss"),
                            ("CAUGHT_LOST(잡았다놓침)", "caught_then_lost")):
            for c in keirin:
                if c.get("type") == tkey:
                    top3 = c.get("top3", [])
                    q12 = sorted(frozenset(top3[:2]))
                    print(f"      {label}: {c['raceKey']} 정답1-2착 {q12}"
                          + (f" (1·2·3착 {top3})" if tkey == "caught_then_lost" else ""))
    print("=" * 56)
    grate = round(grand["hit"] / g_total * 100) if g_total else 0
    print(f"■ 합계  경륜 {g_total}경주  적중 {grand['hit']}({grate}%)")
    for t in TYPES:
        if grand[t]:
            print(f"    {t:18s}: {grand[t]}")


if __name__ == "__main__":
    args = sys.argv[1:]
    dates = args if args else ["2026-07-23", "2026-07-22", "2026-07-21"]
    main(dates)
