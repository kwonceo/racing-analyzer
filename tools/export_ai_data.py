# -*- coding: utf-8 -*-
"""[AI 분석 Phase1 · 5번] data/ai_training/ 완전 데이터 → CSV/JSON 내보내기.

나중에 AI 모델 학습(scikit-learn/pandas 등)에 바로 쓰도록 경주별 완전 데이터를
①평탄화 CSV(경주 1행, 주요 피처+라벨)  ②원본 JSON 배열(전체 구조 보존) 로 내보낸다.

사용:
  python tools/export_ai_data.py                       # 기본: dist/ai_data.csv + dist/ai_data.json
  python tools/export_ai_data.py --out dist/my         # dist/my.csv + dist/my.json
  python tools/export_ai_data.py --min-quality 90      # 품질 90점+ 완전 데이터만
  python tools/export_ai_data.py --format csv          # csv 만 / json 만
기존 기능 삭제 없음 — 읽기 전용(원본 ai_training/ 을 건드리지 않음).
"""
import argparse
import csv
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AI_DIR = os.path.join(ROOT, "data", "ai_training")

# CSV 평탄화 컬럼(경주 1행) — 주요 피처 + 라벨. 리스트/딕트는 요약값으로.
CSV_COLUMNS = [
    "race_id", "date", "track", "round", "distance", "condition", "weather", "horse_count",
    "candidates", "eliminated", "recommend", "strategy", "confidence", "signal_quality",
    "exacta_reversal", "reversal_ratio", "quinella_mismatch", "refund_rate",
    "fake_betting", "large_scale_drop", "max_drop_rate", "max_excess_drop", "max_drop_speed",
    "result_1st", "result_2nd", "result_3rd", "result_4th",
    "quinella_odds", "exacta_odds", "hit", "hit_pattern",
    "quinella_hit", "winner", "second", "odds_range", "quality_score", "complete",
]


def _min_val(d):
    """딕트 값 중 최소(가장 강한 급락 등). 없으면 None."""
    vals = [v for v in (d or {}).values() if isinstance(v, (int, float))]
    return min(vals) if vals else None


def _flatten(rec):
    ri = rec.get("race_info") or {}
    pr = rec.get("prediction") or {}
    of = rec.get("odds_features") or {}
    rs = rec.get("result") or {}
    lb = rec.get("labels") or {}
    q = rec.get("quality") or {}
    return {
        "race_id": rec.get("race_id"),
        "date": ri.get("date"), "track": ri.get("track"), "round": ri.get("round"),
        "distance": ri.get("distance"), "condition": ri.get("condition"),
        "weather": ri.get("weather"), "horse_count": ri.get("horse_count"),
        "candidates": "|".join(map(str, pr.get("candidates") or [])),
        "eliminated": "|".join(map(str, pr.get("eliminated") or [])),
        "recommend": pr.get("recommend"), "strategy": pr.get("strategy"),
        "confidence": pr.get("confidence"), "signal_quality": pr.get("signal_quality"),
        "exacta_reversal": of.get("exacta_reversal"), "reversal_ratio": of.get("reversal_ratio"),
        "quinella_mismatch": of.get("quinella_mismatch"), "refund_rate": of.get("refund_rate"),
        "fake_betting": of.get("fake_betting"), "large_scale_drop": of.get("large_scale_drop"),
        "max_drop_rate": _min_val(of.get("drop_rates")),
        "max_excess_drop": _min_val(of.get("excess_drops")),
        "max_drop_speed": _min_val(of.get("drop_speed")),
        "result_1st": rs.get("1st"), "result_2nd": rs.get("2nd"),
        "result_3rd": rs.get("3rd"), "result_4th": rs.get("4th"),
        "quinella_odds": rs.get("quinella_odds"), "exacta_odds": rs.get("exacta_odds"),
        "hit": rs.get("hit"), "hit_pattern": rs.get("hit_pattern"),
        "quinella_hit": lb.get("quinella_hit"), "winner": lb.get("winner"),
        "second": lb.get("second"), "odds_range": lb.get("odds_range"),
        "quality_score": q.get("score"), "complete": q.get("complete"),
    }


def load_records(min_quality=0):
    out = []
    if not os.path.isdir(AI_DIR):
        return out
    for fn in sorted(os.listdir(AI_DIR)):
        if not fn.endswith(".json"):
            continue
        try:
            rec = json.load(open(os.path.join(AI_DIR, fn), encoding="utf-8"))
        except Exception as e:
            print(f"  ! 스킵 {fn}: {e}")
            continue
        if (rec.get("quality") or {}).get("score", 0) < min_quality:
            continue
        out.append(rec)
    return out


def main():
    ap = argparse.ArgumentParser(description="AI 학습 데이터 내보내기(CSV/JSON)")
    ap.add_argument("--out", default=os.path.join(ROOT, "dist", "ai_data"),
                    help="출력 경로 접두사(확장자 제외). 기본 dist/ai_data")
    ap.add_argument("--min-quality", type=int, default=0, help="이 품질점수 이상만 내보내기")
    ap.add_argument("--format", choices=["both", "csv", "json"], default="both")
    args = ap.parse_args()

    recs = load_records(args.min_quality)
    if not recs:
        print(f"내보낼 데이터가 없습니다. ({AI_DIR} 비어있거나 품질 {args.min_quality}점 미만)")
        return
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)

    if args.format in ("both", "json"):
        jpath = args.out + ".json"
        json.dump(recs, open(jpath, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"✅ JSON {len(recs)}경주 → {jpath}")
    if args.format in ("both", "csv"):
        cpath = args.out + ".csv"
        with open(cpath, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
            w.writeheader()
            for rec in recs:
                w.writerow(_flatten(rec))
        print(f"✅ CSV  {len(recs)}경주 → {cpath}")

    complete = sum(1 for r in recs if (r.get("quality") or {}).get("complete"))
    print(f"   (완전 데이터 {complete}/{len(recs)}경주 · 평균 품질 "
          f"{round(sum((r.get('quality') or {}).get('score', 0) for r in recs) / len(recs), 1)}점)")


if __name__ == "__main__":
    main()
