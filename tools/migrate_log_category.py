#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""[분석기록 정비] 과거 분석 로그에 종목(category/sport) 태그 백필(1회 실행).

data/analysis_log/*.json 중 category 가 없는(구버전) 로그에 raceKey 경마장명으로
종목을 추론해 채운다.
  · KRA 경마장(서울/부산경남/부경/부산/제주/과천)     → korea
  · JRA 중앙 경마장(도쿄/나카야마/교토/한신/…·한자)   → japan_central
  · 그 외(일본 지방·불명)                             → japan_local

⚠ 한계: 과거 로그에는 경륜/경정/바이크 마커가 없어 구분 불가 → japan_local 로 남는다.
   (확장 v2.1.20+ 로 새로 쌓이는 로그는 category 가 정확히 저장됨.)
category 가 이미 있는 로그는 건너뛴다(멱등·재실행 안전).

사용법:
    python tools/migrate_log_category.py            # 실제 백필
    python tools/migrate_log_category.py --dry-run  # 변경 없이 대상만 표시
"""
import os
import re
import sys
import json
import glob

# Windows 콘솔(cp949)에서 한자·특수문자 출력 깨짐/에러 방지
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "data", "analysis_log")

# KRA(한국) 경마장 — content.js KRA_TRACK_RE 와 동일
KRA_RE = re.compile(r"(서울|부산경남|부경|부산|제주|과천)")
# JRA(일본 중앙) 경마장 — 한글/한자 표기 모두
JRA_CENTRAL = [
    "도쿄", "東京", "나카야마", "中山", "교토", "京都", "한신", "阪神",
    "추쿄", "中京", "고쿠라", "小倉", "후쿠시마", "福島", "니가타", "新潟",
    "삿포로", "札幌", "하코다테", "函館", "중앙", "中央", "JRA",
]

# 종목별(경륜/경정/바이크) 대표 트랙명 — 마커 없는 구버전 로그에서만 트랙명으로 추론.
# ⚠ 확신 가능한 트랙만 최소로 등록(경마장명과 겹치지 않게). 나머지는 japan_local 기본.
CYCLE_TRACKS = ["광명", "창원", "光明"]                       # 경륜(자전거)
BOAT_TRACKS = ["미사리", "사세보", "가라쓰", "오무라", "住之江", "戸田", "佐世保"]  # 경정(보트)
BIKE_TRACKS = ["가와구치", "이이즈카", "하마마쓰", "이즈모", "야마가타", "川口"]      # 오토레이스(바이크)


def infer_category(rk):
    """raceKey → category 추론. 확신 없으면 'japan_local'(기본)."""
    rk = rk or ""
    # 종목별 트랙 우선(경륜/경정/바이크 — 마커 없는 구버전 로그 보정)
    for t in CYCLE_TRACKS:
        if t in rk:
            return "cycle"
    for t in BOAT_TRACKS:
        if t in rk:
            return "boat"
    for t in BIKE_TRACKS:
        if t in rk:
            return "bike"
    if KRA_RE.search(rk):
        return "korea"
    for t in JRA_CENTRAL:
        if t in rk:
            return "japan_central"
    return "japan_local"


def main():
    dry = "--dry-run" in sys.argv
    if not os.path.isdir(LOG_DIR):
        print(f"[마이그레이션] 디렉토리 없음: {LOG_DIR}")
        return
    files = sorted(glob.glob(os.path.join(LOG_DIR, "*.json")))
    total = len(files)
    tagged, skipped, errors = 0, 0, 0
    counts = {"korea": 0, "japan_central": 0, "japan_local": 0,
              "cycle": 0, "boat": 0, "bike": 0}
    print(f"[마이그레이션] 대상 폴더: {LOG_DIR}")
    print(f"[마이그레이션] 총 {total}개 로그"
          + (" · (dry-run: 변경 안 함)" if dry else ""))
    for path in files:
        name = os.path.basename(path)
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            errors += 1
            print(f"  ⚠ 읽기 실패: {name} ({e})")
            continue
        if data.get("category"):   # 이미 태그됨(경륜/경정/바이크 등 정확값 보존)
            skipped += 1
            continue
        rk = data.get("raceKey") or data.get("race") or ""
        cat = infer_category(rk)
        counts[cat] = counts.get(cat, 0) + 1
        data["category"] = cat
        if not data.get("sport"):
            # category → sport 매핑(경륜/경정/바이크는 트랙명으로 추론된 경우)
            data["sport"] = {"cycle": "cycle", "boat": "boat", "bike": "bike"}.get(cat, "horse")
        if dry:
            tagged += 1
            print(f"  (dry) {name} → {cat}  [{rk}]")
            continue
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            tagged += 1
        except Exception as e:
            errors += 1
            print(f"  ⚠ 쓰기 실패: {name} ({e})")
    print("─" * 50)
    print(f"[결과] 태그 {tagged} (korea {counts['korea']} · 중앙 {counts['japan_central']} · 지방 {counts['japan_local']}"
          f" · 경륜 {counts['cycle']} · 경정 {counts['boat']} · 바이크 {counts['bike']})"
          f" · 이미태그(건너뜀) {skipped} · 오류 {errors} · 전체 {total}")
    print("⚠ 지방으로 태그된 것 중 실제 경륜/경정/바이크는 과거 로그에 마커가 없어 구분 불가")
    print("  (확장 v2.1.20+ 로 새로 쌓이는 로그는 category 가 정확히 저장됩니다).")


if __name__ == "__main__":
    main()
