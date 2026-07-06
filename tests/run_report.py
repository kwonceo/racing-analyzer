# -*- coding: utf-8 -*-
"""[신규] 고배당 적중 상세 분석 리포트 시스템 격리 테스트.
   app.py 를 importlib 로 로드 → 저장소 상수를 임시경로로 monkeypatch → 함수 직접 호출.
   프로덕션 데이터 오염 없이 _build_race_report / _signal_win_tags / win_tag_stats 검증."""
import importlib.util
import io
import json
import os
import sys
import tempfile

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
spec = importlib.util.spec_from_file_location("appmod", os.path.join(ROOT, "app.py"))
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

PASS = FAIL = 0


def ok(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ✅", msg)
    else:
        FAIL += 1
        print("  ❌", msg)


# 임시 저장소로 격리
tmp = tempfile.mkdtemp(prefix="report_test_")
app.RACE_REPORT_DIR = os.path.join(tmp, "race_report")
app.HIGHLIGHT_FILE = os.path.join(tmp, "highlight_wins.json")

# ── 합성 시나리오: 모리오카 2경주, 결과 7-6-10, 7번 초과급락 -12.3 + 쌍승역전 7↔6 0.82, 전적 68 ──
an = {
    "keyHorses": [7, 6, 10],
    "form": [{"no": 7, "name": "가", "totalScore": 68},
             {"no": 6, "name": "나", "totalScore": 55},
             {"no": 10, "name": "다", "totalScore": 40}],
    "drops": [{"combo": [7, 6], "pct": -25}, {"combo": [7, 10], "pct": -18},
              {"combo": [6, 10], "pct": -12}],
    "betRecommend": [
        {"kind": "복승", "label": "복승 메인", "combo": [7, 6], "alloc": 43, "expOdds": 5.8},
        {"kind": "삼복승", "label": "삼복승 메인", "combo": [7, 6, 10], "alloc": 29, "expOdds": 22.1},
    ],
    "bmed": {"strategy": "보험형"},
    "signalQuality": {
        "excess": {"overall": -13.0, "concentrated": [7],
                   "horses": {7: {"avg": -21.5, "excess": -12.3, "grade": "🔴", "combos": 2},
                              6: {"avg": -18.5, "excess": -5.5, "grade": "🔴", "combos": 2}}},
        "winExactaReversals": [{"challenger": 7, "favorite": 6, "ratio": 0.82, "level": "🔴",
                                "reverseExacta": 32.1, "favoredExacta": 58.4}],
        "quinellaMismatch": {"focusHorses": [7]},
        "signalConfidence": {"horses": {
            7: {"excessScore": 80.0, "reversalScore": 60.0, "mismatchScore": 40.0, "confidence": 72.0, "grade": "🔴"},
        }},
    },
}
record = {
    "top3": [7, 6, 10], "top4": 3, "was_hit": True,
    "quinella_hit": True, "trifecta_hit": True,
    "payouts": {"quinella": 45.2, "trifecta": 121.0},
    "hit_basis": {"reason": "초과급락 + 쌍승역전"},
    "pnl": 120000, "stake": 1000,
}
doc = {"snapshots": [
    {"time": "10:15", "minutes_before": 12, "quinella": {"7+6": 45.2}},
    {"time": "10:20", "minutes_before": 7, "quinella": {"7+6": 38.1}},
    {"time": "10:25", "minutes_before": 2, "quinella": {"7+6": 28.5}},
]}

rk = "2026-07-06 모리오카 2경주"

print("[1] _signal_win_tags")
wt = app._signal_win_tags(an, [7, 6, 10])
ok("초과급락_적중" in wt["tags"], "초과급락_적중 태깅(집중급락 7번 입상)")
ok("쌍승역전_적중" in wt["tags"], "쌍승역전_적중 태깅(challenger 7번 입상)")
ok("전적보조_적중" in wt["tags"], "전적보조_적중 태깅(전적 최고 7번 입상)")
ok(wt["combo"] is True, "동시(2개+) 플래그")

print("[2] _build_race_report")
rep = app._build_race_report(rk, an, record, {"1st": 7, "2nd": 6, "3rd": 10, "4th": 3}, doc)
ok(rep["result"]["1st"] == 7 and rep["result"]["4th"] == 3, "결과 1착=7 4착=3")
ok(rep["hit"] is True and rep["hit_type"] == "복승 메인", "적중 + hit_type=복승 메인")
ok(rep["odds"] == "고배당", "고배당 밴드(삼복승 121배)")
s7 = rep["why_recommended"].get("signal_7")
ok(s7 and s7["excess_drop"] == -12.3, "signal_7 초과급락 -12.3")
ok(s7 and s7["exacta_reversal"] is True and s7["reversal_ratio"] == 0.82, "signal_7 쌍승역전 0.82")
ok(s7 and s7["record_score"] == 68, "signal_7 전적 68")
ok(len(s7["drop_timeline"]) == 3 and s7["drop_timeline"][0]["odds"] == 45.2, "7+6 타임라인 3건 45.2배부터")
ok(s7["drop_timeline"][1]["change"] is not None and s7["drop_timeline"][1]["change"] < 0, "타임라인 변화율(급락) 계산")
cb = rep["confidence_breakdown"]
ok(cb["total"] == 72.0 and cb["grade"] == "상", "신뢰도 분해 total=72 grade=상")
ok(cb["excess_drop_score"] == 32 and cb["exacta_reversal_score"] == 21, "가중 기여(40%×80=32 / 35%×60=21)")
ok(cb["record_score"] == 68, "신뢰도 분해 record_score=68")
ok(len(rep["recommendation_process"]) >= 5, "추천 스토리 단계 5+")
ok(any("BMED" in s for s in rep["recommendation_process"]), "스토리에 BMED 전략 포함")
ok("초과급락_적중" in (rep["win_tags"] or []), "리포트 win_tags")
# 파일 저장 확인
saved = os.path.join(app.RACE_REPORT_DIR, rep["_slug"] + ".json")
ok(os.path.isfile(saved), "리포트 파일 저장됨")

print("[3] _combo_timeline 방어(역순 키)")
tl = app._combo_timeline({"snapshots": [{"time": "a", "quinella": {"6+7": 10.0}}]}, [7, 6])
ok(len(tl) == 1 and tl[0]["odds"] == 10.0, "정렬 키와 역순 키 모두 처리")

print("[4] win_tag_stats 누적(고배당 적중률)")
records = [
    {"win_tags": ["초과급락_적중", "쌍승역전_적중"], "was_hit": True,
     "quinella_hit": False, "trifecta_hit": True, "payouts": {"trifecta": 121.0}},
    {"win_tags": ["초과급락_적중", "쌍승역전_적중"], "was_hit": True,
     "quinella_hit": True, "trifecta_hit": False, "payouts": {"quinella": 12.0}},
    {"win_tags": ["초과급락_적중"], "was_hit": False, "payouts": {}},
]
st = app._recompute_learning_stats(records)
wts = st["win_tag_stats"]
ok(wts["초과급락_적중"]["n"] == 3, "초과급락_적중 n=3")
combo_key = "+".join(sorted(["초과급락_적중", "쌍승역전_적중"]))
ok(wts[combo_key]["n"] == 2, "동시 조합 버킷 n=2")
ok(wts[combo_key]["high_rate"] == 50.0, "동시 조합 고배당 적중률 50%(1/2: 삼복승 121배만)")
ok("동시(2개+)" in wts, "동시(2개+) 버킷 존재")

print("=" * 56)
print(f"결과: 통과 {PASS} / 실패 {FAIL}")
print("=" * 56)
sys.exit(1 if FAIL else 0)
