# -*- coding: utf-8 -*-
"""[강한 신호 8유형 자동 감지 + 유형별 적중률 학습] 단위 검증.
_strong_signals(순수 함수) + _smart_money_combos + _learn_strong_signals/_signal_type_hitrates."""
import importlib.util
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("app", os.path.join(ROOT, "app.py"))
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

_p = _f = 0


def ok(cond, msg):
    global _p, _f
    if cond:
        _p += 1
        print("  ✅", msg)
    else:
        _f += 1
        print("  ❌", msg)


def types_of(ss):
    return set(s["type"] for s in ss.get("signals", []))


print("[1] 유형1 연속하락 3회+")
adv = {"horseStreaks": {3: {"no": 3, "count": 3, "rebounded": False},
                        9: {"no": 9, "count": 2, "rebounded": False}}}
ss = app._strong_signals(adv, {}, [], None, [], {}, False)
ok(1 in types_of(ss), "3연속 하락(3번) → 유형1 감지")
ok(all(s["type"] != 1 for s in ss["signals"] if 9 in s["horses"]), "2연속(9번)은 유형1 미감지")

print("[2] 유형2 역배열 10%+")
inv = {"detected": True, "invLead": {"no": 7, "diffPct": 35, "level": "\U0001f534 강한 역배열", "tag": "강한 역배열"}}
ss = app._strong_signals({}, inv, [], None, [], {}, False)
ok(2 in types_of(ss), "역배열 35%(7번) → 유형2 감지")
inv_lo = {"detected": True, "invLead": {"no": 7, "diffPct": 8, "level": "", "tag": "약한"}}
ss_lo = app._strong_signals({}, inv_lo, [], None, [], {}, False)
ok(2 not in types_of(ss_lo), "역배열 8%(10% 미만)는 유형2 미감지")

print("[3] 유형3 마감직전 대급락(T-2분 30%+)")
drops = [{"combo": [3, 9], "pct": -56}, {"combo": [2, 9], "pct": -42}]
ss = app._strong_signals({}, {}, drops, 1, [], {}, False)
ok(3 in types_of(ss), "T-1분 -56% 급락 → 유형3 감지")
ss_far = app._strong_signals({}, {}, drops, 5, [], {}, False)
ok(3 not in types_of(ss_far), "T-5분(2분 초과)은 유형3 미감지")
ss_after = app._strong_signals({}, {}, drops, 1, [], {}, True)
ok(3 not in types_of(ss_after), "마감 후(after_close)는 유형3 미감지")

print("[4] 유형4 재급락(recrash)")
adv4 = {"rebounds": [{"pattern": "recrash", "combo": [3, 9]}, {"pattern": "valid", "combo": [1, 2]}]}
ss = app._strong_signals(adv4, {}, [], None, [], {}, False)
ok(4 in types_of(ss), "급락→반등→재급락 → 유형4 감지")

print("[5] 유형5 복수조합 동시급락 2개+")
drops5 = [{"combo": [3, 9], "pct": -50}, {"combo": [3, 6], "pct": -40}, {"combo": [2, 9], "pct": -35}]
ss = app._strong_signals({}, {}, drops5, None, [], {}, False)
ok(5 in types_of(ss), "3번(2조합)·9번(2조합) 동시급락 → 유형5 감지")
sig5 = next(s for s in ss["signals"] if s["type"] == 5)
ok(3 in sig5["horses"] and 9 in sig5["horses"], "유형5 말에 3·9번 포함")

print("[6] 유형6 역배열+급락 동시(이중수렴) — 단독 강력")
inv6 = {"detected": True, "invLead": {"no": 3, "diffPct": 30, "level": "\U0001f534", "tag": "역배열"}}
drops6 = [{"combo": [3, 9], "pct": -42}]
ss = app._strong_signals({}, inv6, drops6, None, [], {}, False)
ok(6 in types_of(ss), "3번 역배열+급락 동시 → 유형6 감지")
ok(ss["dualConverge"] is True, "dualConverge=True")
ok(ss["recommendLevel"] == "강력", "이중수렴 → 단독으로도 강력 추천")

print("[7] 유형7 환수율 90%+ 집중")
adv7 = {"overround": {"invSum": 1.2, "top3Share": 0.93, "concentrated": True}}
ss = app._strong_signals(adv7, {}, [], None, [], {}, False)
ok(7 in types_of(ss), "상위3조합 93% 집중 → 유형7 감지")

print("[8] 유형8 상승후급락(스마트머니)")
hist8 = [{"quinella": {"3+9": 8}}, {"quinella": {"3+9": 14}}, {"quinella": {"3+9": 15}}, {"quinella": {"3+9": 7}}]
sm = app._smart_money_combos(hist8, {})
ok(any(c == [3, 9] for c, _ in sm), "8→15 상승 후 7 급락 → 스마트머니 감지")
ss = app._strong_signals({}, {}, [], None, hist8, {}, False)
ok(8 in types_of(ss), "유형8 감지")

print("[9] recommendLevel 강도별(1/2/3+)")
ss1 = app._strong_signals({"horseStreaks": {3: {"no": 3, "count": 3, "rebounded": False}}}, {}, [], None, [], {}, False)
ok(ss1["recommendLevel"] == "보조", "1개 유형 → 보조")
ss2 = app._strong_signals({"horseStreaks": {3: {"no": 3, "count": 3, "rebounded": False}}},
                          {"detected": True, "invLead": {"no": 7, "diffPct": 20, "level": "\U0001f7e0", "tag": "역배열"}},
                          [], None, [], {}, False)
ok(ss2["recommendLevel"] == "복승", "2개 유형 → 복승")
ss3 = app._strong_signals({"horseStreaks": {3: {"no": 3, "count": 3, "rebounded": False}},
                           "rebounds": [{"pattern": "recrash", "combo": [1, 2]}]},
                          {"detected": True, "invLead": {"no": 7, "diffPct": 20, "level": "\U0001f7e0", "tag": "역배열"}},
                          [], None, [], {}, False)
ok(ss3["recommendLevel"] == "강력" and ss3["count"] >= 3, "3개+ 유형 → 강력")

print("[10] 무신호 → recommendLevel None")
ss0 = app._strong_signals({}, {}, [], None, [], {}, False)
ok(ss0["recommendLevel"] is None and ss0["count"] == 0, "신호 없음 → None(추천 없음)")

print("[11] 유형별 적중률 학습(입상 기준) + 멱등")
tmp = tempfile.mkdtemp()
app.SIGNAL_TYPE_STATS_FILE = os.path.join(tmp, "sig.json")
an_hit = {"strongSignals": {"signals": [
    {"type": 3, "horses": [3, 9]}, {"type": 5, "horses": [3, 9]}, {"type": 2, "horses": [7]},
    {"type": 7, "horses": []}], "recommendLevel": "강력"}}
app._learn_strong_signals("R9", "2026-07-09", an_hit, [3, 9, 5])
app._learn_strong_signals("R9", "2026-07-09", an_hit, [3, 9, 5])   # 재입력(멱등)
app._learn_strong_signals("R8", "2026-07-09",
                          {"strongSignals": {"signals": [{"type": 2, "horses": [7]}]}}, [7, 1, 2])
rates = app._signal_type_hitrates()
ok(rates.get(3, {}).get("rate") == 100 and rates[3]["fired"] == 1, "유형3 신호말(3,9) 입상 → 100%")
ok(rates.get(5, {}).get("rate") == 100, "유형5 100%")
ok(rates.get(2, {}).get("fired") == 2 and rates[2]["rate"] == 50, "유형2 2회 중 1회 입상 → 50%(멱등 확인)")
ok(7 not in rates, "유형7(신호말 없음) 판정불가 → 집계 제외")

print("\n결과: 통과 %d / 실패 %d" % (_p, _f))
sys.exit(1 if _f else 0)
