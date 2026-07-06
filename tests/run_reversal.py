#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""쌍승 역전 감지 정합성 검증 (서버 불필요, app.py 직접 로드).

검증 대상(2026-07-06 #보완):
  [A] _reversal_level(비율→등급 컷 0.60/0.80/0.95)
  [B] _win_exacta_reversal(다중순위 확장) — primary(1위) 보존 + 상위권 순위쌍 역전 + 노이즈 게이트
  [C] _exa_fav_dirs(무순쌍 유력방향) + _history_append 쌍승 flip 다중조합 영구 기록

실행: python tests/run_reversal.py
"""
import sys, os, json, tempfile, importlib.util

sys.stdout.reconfigure(encoding="utf-8")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("appmod_rev", os.path.join(_ROOT, "app.py"))
app = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(app)

P = {"pass": 0, "fail": 0}


def chk(label, ok, extra=""):
    print(f"  {'✅' if ok else '❌'} {label}{(' — ' + extra) if extra else ''}")
    P["pass" if ok else "fail"] += 1
    return ok


print("=" * 60)
print("쌍승 역전 감지 정합성 검증")
print("=" * 60)

# ── [A] _reversal_level ─────────────────────────────────────────────
print("[A] 역전 등급 컷")
chk("0.5 → 압도적(🔴🔴)", app._reversal_level(0.5) == ("🔴🔴", "압도적 역전"))
chk("0.7 → 강한(🔴)", app._reversal_level(0.7) == ("🔴", "강한 역전"))
chk("0.9 → 역전신호(🟡)", app._reversal_level(0.9) == ("🟡", "역전 신호"))
chk("0.95 → 신호없음(None)", app._reversal_level(0.95)[0] is None)

# ── [B] _win_exacta_reversal 다중순위 ───────────────────────────────
wx = app._win_exacta_reversal
print("[B] primary(1위 기준) 동작 보존")
curD = {(1, 2): 10.0, (2, 1): 6.0, (1, 3): 10.0, (3, 1): 10.0, (2, 3): 5.0, (3, 2): 5.0}
r = wx([1, 2, 3], curD)
chk("primary 1건", len(r) == 1)
chk("challenger=2 · ratio=0.6", r[0]["challenger"] == 2 and r[0]["ratio"] == 0.6)
chk("multiRank=False", r[0]["multiRank"] is False)
chk("기존 문구('실질 1착') 유지", "실질 1착" in r[0]["text"])

print("[B] 다중순위 감지 + 노이즈 게이트")
curD2 = {(1, 2): 6.0, (2, 1): 6.0, (1, 3): 8.0, (3, 1): 8.0, (1, 4): 10.0, (4, 1): 10.0,
         (2, 3): 12.0, (3, 2): 6.0,   # 0.5 압도적 → multi 채택
         (2, 4): 10.0, (4, 2): 9.5,   # 0.95 → 제외
         (3, 4): 8.0, (4, 3): 7.9}    # 0.9875 → 제외
r2 = wx([1, 2, 3, 4], curD2)
chk("multi 1건만(2·3위)", len(r2) == 1 and r2[0]["multiRank"] is True)
chk("favRank=2·chalRank=3", r2[0]["favRank"] == 2 and r2[0]["chalRank"] == 3)
chk("약한 하위쌍(<0.80 미달) 제외", all(x["ratio"] < 0.80 for x in r2))

print("[B] wx[0] primary 우선순위 보존(AI 피처 reversal_ratio 안정)")
curD3 = {(1, 2): 10.0, (2, 1): 8.5, (1, 3): 10.0, (3, 1): 10.0, (2, 3): 10.0, (3, 2): 4.0}
r3 = wx([1, 2, 3], curD3)
chk("2건(primary+multi)", len(r3) == 2)
chk("out[0]=primary(약해도 앞)", r3[0]["multiRank"] is False and r3[0]["ratio"] == 0.85)

print("[B] 방어 입력")
chk("빈 fav_rank → []", wx([], {(1, 2): 5.0}) == [])
chk("None → []", wx(None, None) == [])

# ── [C] _exa_fav_dirs + _history_append 다중 flip ───────────────────
print("[C] _exa_fav_dirs 유력방향")
fav = app._exa_fav_dirs({"1+2": 3.0, "2+1": 5.0, "3+4": 8.0, "4+3": 4.0})
chk("{1,2} 유력=(1,2) 3.0", fav[(1, 2)] == ((1, 2), 3.0))
chk("{3,4} 유력=(4,3) 4.0", fav[(3, 4)] == ((4, 3), 4.0))
chk("빈 입력 → {}", app._exa_fav_dirs({}) == {})

print(f"[C] _history_append 쌍승 flip 다중조합 기록 (TOPN={app.EXA_REVERSAL_TOPN})")
_orig_dir = app.ODDS_HISTORY_DIR
try:
    app.ODDS_HISTORY_DIR = tempfile.mkdtemp(prefix="revtest_")
    rk = "2026-07-06 테스트 1경주"

    def _exa(d):
        return [{"combo": [int(x) for x in k.split("+")], "odds": v} for k, v in d.items()]

    QUIN = [{"combo": [1, 2], "odds": 5.0}, {"combo": [3, 4], "odds": 6.0}]
    prev_exa = {"1+2": 3.0, "2+1": 5.0, "3+4": 4.0, "4+3": 8.0,
                "5+6": 6.0, "6+5": 10.0, "7+8": 20.0, "8+7": 25.0}
    cur_exa = {"1+2": 5.0, "2+1": 3.0, "3+4": 8.0, "4+3": 4.0,   # {1,2}·{3,4} 반전
               "5+6": 6.0, "6+5": 10.0, "7+8": 25.0, "8+7": 20.0}  # {7,8}(4위) 반전
    app._history_append(rk, QUIN, _exa(prev_exa))
    app._history_append(rk, QUIN, _exa(prev_exa))
    path = app._history_append(rk, QUIN, _exa(cur_exa))
    anoms = json.load(open(path, encoding="utf-8"))["snapshots"][-1]["anomalies"]
    revs = [a for a in anoms if a.startswith("쌍승역전:")]
    chk("역전 2건(다중조합)", len(revs) == 2, str(revs))
    chk("최저 {1,2} 기록(하위호환)", "쌍승역전: 2↔1" in revs)
    chk("2위 {3,4} 기록(신규)", "쌍승역전: 4↔3" in revs)
    chk("4위(topN 밖) {7,8} 미기록", not any("8" in r and "7" in r for r in revs))
finally:
    app.ODDS_HISTORY_DIR = _orig_dir

print("=" * 60)
print(f"결과: 통과 {P['pass']} / 실패 {P['fail']}")
print("=" * 60)
sys.exit(1 if P["fail"] else 0)
