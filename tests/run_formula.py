#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""유력마/제거마 선정 공식 [1·2·3번] 스펙 정합성 검증 (서버 불필요, app.py 직접 로드).

검증 대상:
  [1] _confidence(배당신뢰도) · _fav_score(유력마 점수 = 전적40+신뢰30+기수20+적성10)
  [2] _elim_score(제거 점수) + verdict 컷(30/50/70) + 거리경험 -15 훅
  [3] _prob_ev(시장·전적·통합확률·기대값)

실행: python tests/run_formula.py
"""
import sys, os, importlib.util

sys.stdout.reconfigure(encoding="utf-8")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("appmod", os.path.join(_ROOT, "app.py"))
app = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(app)

P = {"pass": 0, "fail": 0}


def chk(label, ok, extra=""):
    print(f"  {'✅' if ok else '❌'} {label}{(' — ' + extra) if extra else ''}")
    P["pass" if ok else "fail"] += 1
    return ok


def approx(a, b, tol=0.15):
    return a is not None and b is not None and abs(a - b) <= tol


print("=" * 60)
print("유력마/제거마 공식 스펙 정합성 검증")
print("=" * 60)

# ── [1번] 배당신뢰도 _confidence(ftotal, o) ─────────────────────────
print("[1번] 배당신뢰도 (배당 낮음<30배 전제)")
chk("전적우수(70)+배당낮음(10) → +30", app._confidence(70, 10) == 30, str(app._confidence(70, 10)))
chk("전적불량(30)+배당낮음(10) → -10(이변의심)", app._confidence(30, 10) == -10, str(app._confidence(30, 10)))
chk("전적중간(50)+배당낮음 단독 → 0(중립)", app._confidence(50, 10) == 0, str(app._confidence(50, 10)))
chk("배당높음(50배)이면 신뢰도 미판정 → 0", app._confidence(70, 50) == 0, str(app._confidence(70, 50)))

# ── [1번] 유력마 점수 _fav_score(ftotal, o, jk_rate) ────────────────
#   전적우수+배당낮음+기수우수 → 높은 점수 / 전적불량+배당낮음(이변의심) → 낮은 점수
print("[1번] 유력마 점수 (전적40+신뢰30+기수20+적성10)")
hi = app._fav_score(80, 10, 40)   # 전적80·배당낮음·기수40 → conf_sub=90
lo = app._fav_score(30, 10, 10)   # 전적30·배당낮음(이변의심) → conf_sub=20
chk("전적우수+배당낮음 유력점수 > 전적불량+배당낮음", hi > lo, f"{hi} vs {lo}")
# 수식 직접 검산: 80*.4 + 90*.3 + 40*.2 + 50*.1 = 32+27+8+5 = 72
chk("전적80·배당10·기수40 = 72.0 (수식 검산)", approx(hi, 72.0), str(hi))

# ── [2번] 제거 점수 _elim_score(o, avg_place, jk_rate, drop30, top_exa, no_dist_exp) ─
print("[2번] 제거 점수 (기본100 감점 + 이변 보류 가점)")
chk("배당 200배 → -40 (100→60)", app._elim_score(200, None, None, False, False)[0] == 60)
chk("배당 100배 → -20 (100→80)", app._elim_score(100, None, None, False, False)[0] == 80)
chk("평균 5착+ → -30 (100→70)", app._elim_score(10, 5.0, None, False, False)[0] == 70)
chk("기수 복승률 8%(<10) → -10 (100→90)", app._elim_score(10, None, 8, False, False)[0] == 90)
chk("거리경험 없음 → -15 (100→85)", app._elim_score(10, None, None, False, False, True)[0] == 85)
chk("급락30%+ → +30 보류가점", app._elim_score(10, None, None, True, False)[0] == 130)
chk("쌍승 상위 → +20 보류가점", app._elim_score(10, None, None, False, True)[0] == 120)
# 복합: 배당200(-40)+평균6착(-30)+기수5%(-10) = 20 → 확실제거 구간(<30)
worst = app._elim_score(200, 6.0, 5, False, False)[0]
chk("최악 복합 = 20점", worst == 20, str(worst))


def verdict(total):
    if total < 30:
        return "🔴"
    if total < 50:
        return "🟠"
    if total < 70:
        return "🟡"
    return "🟢"


print("[2번] verdict 컷 (30/50/70)")
chk("20점 → 🔴 확실제거", verdict(20) == "🔴")
chk("40점 → 🟠 제거권장", verdict(40) == "🟠")
chk("60점 → 🟡 관찰", verdict(60) == "🟡")
chk("85점 → 🟢 후보", verdict(85) == "🟢")

# ── [3번] 확률/기대값 _prob_ev(o, placings) ─────────────────────────
#   o=4, 최근5경주 착순 [1,2,3,4,5] → 입상(≤3) 3회 → 전적확률 0.6
#   시장 = 1/4*0.75 = 0.1875 → 18.8% / 통합 = .1875*.6 + .6*.4 = .3525 → 35.3%
#   기대값 = .3525*4 - 1 = 0.41 → +41%
print("[3번] 확률/기대값 (o=4, 착순[1,2,3,4,5])")
mkt, form, comb, ev = app._prob_ev(4, [1, 2, 3, 4, 5])
chk("시장확률 = 1/배당×0.75 ≈ 18.8%", approx(mkt, 18.8), str(mkt))
chk("전적확률 = 입상3/5 = 60%", approx(form, 60.0), str(form))
chk("통합확률 = 시장×0.6+전적×0.4 ≈ 35.3%", approx(comb, 35.3), str(comb))
chk("기대값 = 통합×배당-1 ≈ +41%", approx(ev, 41.0, 0.5), str(ev))
# 배당 미수집 시 방어
m2, f2, c2, e2 = app._prob_ev(None, [])
chk("배당 미수집 → 기대값 None(방어)", e2 is None)

print("=" * 60)
print(f"결과: 통과 {P['pass']} / 실패 {P['fail']}")
print("=" * 60)
sys.exit(1 if P["fail"] else 0)
