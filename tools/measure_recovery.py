# -*- coding: utf-8 -*-
"""[회수율 측정 — 유일한 창구 (2026-07-31 신설)]

🔴 **앞으로 회수율 측정은 이 파일로만 한다. 세션 중 즉석 코드 금지.**

왜: 2026-07-31 에 세션 중 즉석으로 짠 측정 코드가 **날짜 없이 파일을 매칭**해
   A일 확정배당과 B일 배당판을 짝지었다. 그 결과 모든 회수율이 **+10~25%p 부풀려졌고**
   "시장 3두 전조합 99.7%" 같은 잘못된 결론이 나왔다.
   즉석 코드는 파일로 남지 않아 전수 점검에도 안 잡힌다.

■ 코드에 박아 둔 공통 규칙 (바꾸려면 CLAUDE.md 를 먼저 고칠 것)
  ① 🔴 **날짜 필수 매칭** — `analysis_log` 파일에서 파생한 경로만 쓴다(같은 파일명 → 같은 날).
  ② 🔴 **확정배당 기준** — `result.payouts.quinella`. 배당판은 "근사"로만.
  ③ 🔴 **분모 명시** — 전체 / 정제(괴리 0.5~2.0배) 둘 다 출력.
  ④ 🔴 **상위1·3건 제외 자동** — 극단값 의존을 항상 드러낸다.
  ⑤ 🔴 **95% 신뢰구간 자동**(부트스트랩) — 판정 가능 여부를 숫자로.
  ⑥ 🔴 **판정선 = 환급률 74.5%** — 100% 가 아니다(그게 무작위 수준이므로).

사용:
  python tools/measure_recovery.py                 # 전체 안
  python tools/measure_recovery.py --sport cycle
  python tools/measure_recovery.py --json
"""
import argparse
import glob
import gzip
import itertools
import json
import os
import random
import re
import statistics
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAYBACK = 74.5          # 🔴 판정선 = 경륜 복승 실측 환급률(Σ1/배당 중앙 1.341)
CLEAN_LO, CLEAN_HI = 0.5, 2.0   # 확정/배당판 괴리 정제 범위
BOOT_N = 2000


def _loadh(base):
    """odds_history 로드. ⚠ `base` 는 analysis_log 경로에서 파생 — 날짜가 이미 포함돼 있다."""
    for p in (base, base + ".gz"):
        if os.path.exists(p):
            try:
                if p.endswith(".gz"):
                    return json.load(gzip.open(p, "rt", encoding="utf-8"))
                return json.load(open(p, encoding="utf-8"))
            except Exception:
                return None
    return None


def load_races(sport="cycle", pattern="2026_07_*"):
    """🔴 날짜 안전: analysis_log 파일 하나에서 odds_history 경로를 **파생**한다."""
    out = []
    for f in sorted(glob.glob(os.path.join(BASE, "data", "analysis_log", pattern + ".json"))):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        if (d.get("sport") or "") != sport:
            continue
        res = d.get("result") or {}
        po = (res.get("payouts") or {}).get("quinella")
        if not res.get("1st") or not po:
            continue                                   # ② 확정배당 기준
        cp = d.get("corePicks") or {}
        dc = [sorted(c) for c in ((cp.get("displayedCombos") or {}).get("quinellas") or [])]
        kh = [int(x) for x in (d.get("keyHorses") or [])][:3]
        if not dc or len(kh) < 3:
            continue
        h = _loadh(f.replace("analysis_log", "odds_history"))
        dl = (h or {}).get("deadline_epoch")
        if not (h and dl):
            continue
        sn = [s for s in (h.get("snapshots") or [])
              if s.get("t") and -8 <= (s["t"] - dl) / 60 <= 0 and s.get("quinella")]
        if not sn:
            continue
        q = {}
        for k, v in max(sn, key=lambda x: x["t"])["quinella"].items():
            try:
                q[tuple(sorted(int(x) for x in str(k).replace("-", "+").split("+")))] = float(v)
            except Exception:
                pass
        # 🔴 [2026-08-01] 1착·2착이 **둘 다** 있어야 한다. 한쪽만 있으면 정렬에서 죽는다
        #   (오늘 부분 착순 레코드가 들어와 도구가 통째로 크래시했다 — 조용히 넘기지 말고 건너뛴다).
        if res.get("1st") is None or res.get("2nd") is None:
            continue
        top2 = sorted({res.get("1st"), res.get("2nd")})
        mo = q.get(tuple(top2))
        if not mo:
            continue
        m = re.match(r"(\d{4}_\d{2}_\d{2})", os.path.basename(f))
        out.append({"q": q, "po": float(po), "mo": float(mo), "top2": top2, "dc": dc, "kh": kh,
                    "bm": [sorted(x.get("combo") or [])
                           for x in (cp.get("bmedSpecial") or []) if x.get("combo")],
                    # 🔴 [2026-08-01] `quinellaRef` = **만들었다가 강등된 조합**(EV 미달·베팅규칙).
                    #   오비히로 5R 에서 정답 복승 `3+10`(18.9배)이 **ev 0.73 으로 여기 있었다.**
                    #   "생성 후 취소"를 재려면 이 목록이 필요하다 — 최종 추천만 봐서는 안 보인다.
                    "ref": [sorted(x.get("combo") or [])
                            for x in (cp.get("quinellaRef") or []) if x.get("combo")],
                    # 🔴 [2026-08-01 신설 · --ev-sweep] 강등분을 **EV 값과 함께** 싣는다.
                    #   ⚠ `ev` 보유는 강등분의 **59.5%** 뿐이다(나머지는 저배당 컷 등 다른 사유).
                    #     EV 임계 스윕은 **ev 보유분만** 대상으로 한다 — 없는 것을 0 으로 치면
                    #     임계를 아무리 낮춰도 안 들어와야 할 것이 들어온다.
                    "refev": [(sorted(x.get("combo")), float(x.get("ev")))
                              for x in (cp.get("quinellaRef") or [])
                              if x.get("combo") and x.get("ev") is not None],
                    # ev 가 없는 강등분(저배당 컷 등) — 스윕 대상이 아님을 밝히기 위해 건수만 싣는다.
                    "refnoev": len([x for x in (cp.get("quinellaRef") or [])
                                    if x.get("combo") and x.get("ev") is None]),
                    # 🔴 [2026-08-01] `darkHorsePicks` = **복병 목록**(유력마와 다른 목록이다).
                    #   코치 4R 에서 7번이 복병 1순위·확신도 1위·축이었는데도
                    #   유력마 10번과의 조합 `7+10`(확정 37.7배)이 **어느 목록에도 없었다.**
                    #   ⇒ "둘 다 봤는데 못 산다"를 재려면 이 목록이 필요하다.
                    "dk": [int(x.get("no")) for x in (cp.get("darkHorsePicks") or [])
                           if x.get("no") is not None],
                    "hs": [x for x in (d.get("horses") or []) if x.get("no") is not None],
                    "day": m.group(1) if m else "?"})
    return out


def _allc(l):
    return [list(c) for c in itertools.combinations(sorted(l), 2)]


def _mkt3(r):
    im = {}
    for k, o in r["q"].items():
        if o > 0:
            for x in k:
                im[x] = im.get(x, 0) + 1.0 / o
    return [x for x, _ in sorted(im.items(), key=lambda y: -y[1])][:3]


def _pace(r, sign):
    return [int(h["no"]) for h in sorted(
        r["hs"], key=lambda h: -((h.get("paceBonusBase") or 0) + sign * (h.get("paceBonus") or 0)))][:3]


# 🔴 오늘 잰 11개 안을 함수로 고정. 새 안은 여기에만 추가한다.
PLANS = [
    ("현행(기준선)", lambda r: r["dc"]),
    ("현행 +1", lambda r: r["dc"] + _allc(r["kh"])[:1]),
    ("현행 +2", lambda r: r["dc"] + _allc(r["kh"])[:2]),
    ("현행 +3", lambda r: r["dc"] + _allc(r["kh"])[:3]),
    ("유력마 3두 전조합", lambda r: _allc(r["kh"])),
    ("유력마 5~50배 추가", lambda r: r["dc"] + [c for c in _allc(r["kh"])
                                            if r["q"].get(tuple(c)) and 5 <= r["q"][tuple(c)] <= 50]),
    ("시장 3두 전조합", lambda r: _allc(_mkt3(r))),
    ("paceBonus ① 현행", lambda r: _allc(_pace(r, +1))),
    ("paceBonus ② 반전", lambda r: _allc(_pace(r, -1))),
    ("paceBonus ③ 제거", lambda r: _allc(_pace(r, 0))),
    ("현행 + BMED", lambda r: r["dc"] + r["bm"]),
    # 🔴 [2026-08-01 신설] "만들었다가 지운 것"을 되살리면 어떻게 되나.
    #   ⚠ 이건 **반사실 시뮬레이션**이다 — 실제로는 강등돼 회원에게 안 나갔다.
    ("현행 + 강등분(quinellaRef)", lambda r: r["dc"] + r["ref"]),
    ("강등분만(quinellaRef)", lambda r: r["ref"]),
    # 🔴 [2026-08-01 신설] **복병 × 유력마 교차**. 두 목록이 따로 놀아 조합이 안 만들어지는 문제.
    #   ⚠ 복병은 상위 2두만 쓴다(전부 쓰면 구좌가 폭발해 회수율이 자동으로 나빠 보인다).
    ("복병×유력마 교차 추가", lambda r: r["dc"] + [sorted([a, b]) for a in r["dk"][:2]
                                              for b in r["kh"] if a != b]),
    ("복병×유력마 교차만", lambda r: [sorted([a, b]) for a in r["dk"][:2]
                                for b in r["kh"] if a != b]),
]


def calc(rows, gen):
    inv = 0
    hits = []
    for r in rows:
        cs = [list(c) for c in {tuple(sorted(c)) for c in gen(r)}]
        inv += len(cs)
        if r["top2"] in cs:
            hits.append(r["po"])
    hits.sort(reverse=True)
    return inv, hits


def boot_ci(rows, gen, n=BOOT_N, seed=42):
    """⑤ 95% 신뢰구간(부트스트랩). 구간이 74.5% 를 포함하면 **판정 불가**다."""
    random.seed(seed)
    vals = []
    for _ in range(n):
        s = [random.choice(rows) for _ in range(len(rows))]
        i, h = calc(s, gen)
        vals.append(100.0 * sum(h) / max(i, 1))
    vals.sort()
    return vals[int(0.025 * n)], vals[int(0.975 * n)]


def measure(sport="cycle", pattern="2026_07_*", ci_for="현행(기준선)"):
    raw = load_races(sport, pattern)
    clean = [r for r in raw if CLEAN_LO <= r["po"] / r["mo"] <= CLEAN_HI]
    out = {"sport": sport, "pattern": pattern,
           "denom_all": len(raw), "denom_clean": len(clean), "payback": PAYBACK, "plans": []}
    for lb, g in PLANS:
        i1, h1 = calc(raw, g)
        i2, h2 = calc(clean, g)
        r1 = 100.0 * sum(h1) / max(i1, 1)
        r2 = 100.0 * sum(h2) / max(i2, 1)
        out["plans"].append({
            "name": lb, "slots": i2, "hits": len(h2),
            "rate_dirty": round(r1, 1), "rate": round(r2, 1),
            "inflated_by": round(r1 - r2, 1),
            "ex1": round(100.0 * sum(h2[1:]) / max(i2, 1), 1),
            "ex3": round(100.0 * sum(h2[3:]) / max(i2, 1), 1),
            "median_odds": round(statistics.median(h2), 1) if h2 else 0,
            "vs_payback": round(r2 - PAYBACK, 1),
        })
    if clean:
        g = dict(PLANS)[ci_for]
        lo, hi = boot_ci(clean, g)
        out["ci"] = {"plan": ci_for, "lo": round(lo, 1), "hi": round(hi, 1),
                     "includes_payback": bool(lo <= PAYBACK <= hi)}
    return out


EV_THRESHOLDS = [1.00, 0.90, 0.80, 0.70, 0.60, 0.50, 0.40, 0.30, 0.20, 0.00]


def _field_band(r):
    """두수 구간. ⚠ `horses` 길이를 쓴다 — `raceHorseCount` 는 마번 수와 어긋나는 사례가 있다."""
    n = len(r.get("hs") or [])
    if n <= 0:
        return "?"
    if n <= 9:
        return "≤9두"
    if n <= 12:
        return "10~12두"
    return "13두+"


def measure_ev_sweep(sport="cycle", pattern="2026_0*"):
    """[EV 임계 스윕 (2026-08-01 신설)] — **완전 읽기 전용 · 측정만**.

    🔴 왜: EV 필터가 강등한 조합의 **적중배당 중앙이 7.3배**(현행 3.4배의 2.1배)다.
      회수율만 보면 강등이 옳지만, 대표 원칙(**고배당·중배당이 기본**)상 회수율 단독으로 닫지 않는다.
    🔴 찾는 것: **회수율 74.5% 를 지키면서 적중배당 중앙이 최대인 임계**.
      ⚠ **회수율 최대가 아니다.** 두 곡선은 서로 다른 임계에서 최대가 될 수 있다.
    ⚠ 임계 t 의 뜻: 강등분 중 `ev >= t` 인 조합을 **현행 추천에 되살린다**(반사실 시뮬레이션).
      t=1.00 은 사실상 현행과 같다(ev≥1.0 이면 애초에 강등되지 않았다).
    ⚠ 정제 필터(괴리 0.5~2.0배)는 `measure()` 와 **동일하게** 적용한다.
    """
    raw = load_races(sport, pattern)
    clean = [r for r in raw if CLEAN_LO <= r["po"] / r["mo"] <= CLEAN_HI]
    out = {"sport": sport, "pattern": pattern, "denom_all": len(raw),
           "denom_clean": len(clean), "payback": PAYBACK, "rows": [], "bands": {}}
    out["ref_ev_total"] = sum(len(r.get("refev") or []) for r in clean)
    out["ref_noev_total"] = sum(int(r.get("refnoev") or 0) for r in clean)

    def gen_for(t):
        return lambda r: r["dc"] + [c for c, ev in (r.get("refev") or []) if ev >= t]

    # 🔴 [정정 2026-08-01] `EV 1.00` 은 현행과 **같지 않다.**
    #   ev≥1.0 인데도 **다른 사유**(베팅규칙 참고 강등·저배당 컷)로 강등된 조합이 실측 51개 있었다.
    #   ⇒ 기준선은 **복원 0 인 현행(dc)** 이며, 별도 행으로 먼저 찍는다. 이걸 빼면 비교 대상이 틀린다.
    i0, h0 = calc(clean, lambda r: r["dc"])
    out["base"] = {"slots": i0, "hits": len(h0),
                   "rate": round(100.0 * sum(h0) / max(i0, 1), 1),
                   "ex1": round(100.0 * sum(h0[1:]) / max(i0, 1), 1),
                   "ex3": round(100.0 * sum(h0[3:]) / max(i0, 1), 1),
                   "median_odds": round(statistics.median(h0), 1) if h0 else 0}
    for t in EV_THRESHOLDS:
        g = gen_for(t)
        i2, h2 = calc(clean, g)
        added = sum(len([1 for c, ev in (r.get("refev") or []) if ev >= t]) for r in clean)
        out["rows"].append({
            "ev": t, "slots": i2, "added": added, "hits": len(h2),
            "rate": round(100.0 * sum(h2) / max(i2, 1), 1),
            "ex1": round(100.0 * sum(h2[1:]) / max(i2, 1), 1),
            "ex3": round(100.0 * sum(h2[3:]) / max(i2, 1), 1),
            "median_odds": round(statistics.median(h2), 1) if h2 else 0,
            "vs_payback": round(100.0 * sum(h2) / max(i2, 1) - PAYBACK, 1),
        })
    # 두수별 분해 (⚠ 셀이 얇으면 판정 불가 — n 을 반드시 함께 본다)
    for band in ("≤9두", "10~12두", "13두+"):
        sub = [r for r in clean if _field_band(r) == band]
        if not sub:
            continue
        rows = []
        for t in EV_THRESHOLDS:
            g = gen_for(t)
            i2, h2 = calc(sub, g)
            rows.append({"ev": t, "n": len(sub), "slots": i2, "hits": len(h2),
                         "rate": round(100.0 * sum(h2) / max(i2, 1), 1),
                         "ex3": round(100.0 * sum(h2[3:]) / max(i2, 1), 1),
                         "median_odds": round(statistics.median(h2), 1) if h2 else 0})
        out["bands"][band] = rows
    return out


def report_ev_sweep(out):
    print("⚠ 분모: 전체 %d → 정제(괴리 %.1f~%.1f배) %d경주 (%.1f%%)" % (
        out["denom_all"], CLEAN_LO, CLEAN_HI, out["denom_clean"],
        100.0 * out["denom_clean"] / max(out["denom_all"], 1)))
    print("⚠ 강등분: ev 보유 %d조합(스윕 대상) · ev 없음 %d조합(**대상 아님** — 저배당 컷 등)" % (
        out["ref_ev_total"], out["ref_noev_total"]))
    print()
    print("  %-7s %7s %7s %6s %9s %8s %8s %9s %10s" % (
        "EV임계", "구좌", "복원", "적중", "회수율", "1제외", "3제외", "배당중앙", "74.5대비"))
    b = out.get("base") or {}
    if b:
        print("  %-7s %7d %7d %6d %8.1f%% %7.1f%% %7.1f%% %8.1f배 %9.1f%%p %s" % (
            "현행", b["slots"], 0, b["hits"], b["rate"], b["ex1"], b["ex3"],
            b["median_odds"], b["rate"] - out["payback"],
            "🟢" if b["rate"] >= out["payback"] else "🔴"))
        print("  " + "-" * 88)
    for r in out["rows"]:
        mark = "🟢" if r["rate"] >= out["payback"] else "🔴"
        print("  %-7.2f %7d %7d %6d %8.1f%% %7.1f%% %7.1f%% %8.1f배 %9.1f%%p %s" % (
            r["ev"], r["slots"], r["added"], r["hits"], r["rate"], r["ex1"], r["ex3"],
            r["median_odds"], r["vs_payback"], mark))
    ok = [r for r in out["rows"] if r["rate"] >= out["payback"]]
    print()
    if not ok:
        print("  🔴 **74.5%% 를 넘는 임계가 없다.** 어떤 임계도 판정선을 지키지 못한다 → 해답 없음.")
    else:
        best_odds = max(ok, key=lambda r: (r["median_odds"], r["rate"]))
        best_rate = max(ok, key=lambda r: r["rate"])
        print("  🟢 74.5%% 유지 임계: %s" % ", ".join("%.2f" % r["ev"] for r in ok))
        print("  🔴 **배당중앙 최대(권고 기준)**: EV %.2f · 회수율 %.1f%% · 배당중앙 %.1f배 · 3제외 %.1f%%"
              % (best_odds["ev"], best_odds["rate"], best_odds["median_odds"], best_odds["ex3"]))
        if b:
            dr = best_odds["rate"] - b["rate"]
            do = best_odds["median_odds"] - b["median_odds"]
            print("     ↳ 🔴 **현행 대비**: 회수율 %+.1f%%p · 배당중앙 %+.1f배 · 구좌 %+d (%d→%d)"
                  % (dr, do, best_odds["slots"] - b["slots"], b["slots"], best_odds["slots"]))
            if do <= 0:
                print("     ↳ 🔴 **배당중앙이 오르지 않는다. 채택 근거가 없다**(대표 원칙 기준 미충족).")
            elif dr < 0:
                print("     ↳ ⚠ 배당중앙은 오르나 **회수율은 내려간다.** 교환비를 대표가 판단할 사안이다.")
        print("  ⚠ (대조) 회수율 최대   : EV %.2f · 회수율 %.1f%% · 배당중앙 %.1f배"
              % (best_rate["ev"], best_rate["rate"], best_rate["median_odds"]))
        if best_odds["ev"] != best_rate["ev"]:
            print("  🔴 **두 최대가 서로 다른 임계다.** 회수율만 보면 배당중앙을 놓친다(대표 원칙).")
    for band, rows in out.get("bands", {}).items():
        n = rows[0]["n"] if rows else 0
        warn = "  ⚠ **n<30 판정 불가**" if n < 30 else ""
        print()
        print("  ── 두수별 · %s (경주 %d)%s ──" % (band, n, warn))
        for r in rows:
            if r["ev"] in (1.00, 0.80, 0.60, 0.40, 0.00):
                print("     EV %.2f · 구좌 %4d · 적중 %3d · 회수율 %6.1f%% · 3제외 %6.1f%% · 배당중앙 %5.1f배"
                      % (r["ev"], r["slots"], r["hits"], r["rate"], r["ex3"], r["median_odds"]))


def measure_trio(sport="horse", pattern="2026_0*"):
    """[삼복승 섀도우 성적 (2026-08-01 신설)] — **완전 읽기 전용**.

    🔴 왜: `trioShadow` 가 2026-07-29 에 켜진 뒤 삼복승은 **생성만 되고 화면·판정에서 빠진다.**
      그런데 이틀 연속(오비히로 5R `3+5+10` · 코치 4R `7+8+10` 37.6배) **정답을 정확히 만들어놓고
      버린 것**이 확인됐다. 해제 판단에는 **섀도우 기간 성적**이 필요하다.
    ⚠ 당시 근거는 *"삼복승 확정배당이 72%만 확보돼 평가가 박할 수 있다"* 였다.
      지금은 백필로 **79.5%** 가 됐으므로 다시 잴 수 있다.
    ⚠ 적중 = 생성된 삼복승 조합이 **1·2·3착 집합과 일치**. 배당 = `result.payouts.trifecta`.
    """
    rows = []
    for f in sorted(glob.glob(os.path.join(BASE, "data", "analysis_log", pattern + ".json"))):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        if (d.get("sport") or "") != sport:
            continue
        res = d.get("result") or {}
        # 🔴🔴 [2026-08-01 정정] `trifecta` 필드가 **경로마다 다른 마권**이다. 그대로 쓰면 회수가 부풀려진다.
        #   · 중앙(netkeiba `_JRA_PAY_MAP`) : 3連複 → **`trio`** · 3連単 → **`trifecta`**
        #   · 지방(`_keiba_result_payouts`) : 三連複 → **`trifecta`** (3連単 안 받음)
        #   · 경륜(`_keirin_result_parse`)  : 3連複 → **`trifecta`** (3連単 안 받음)
        #   ⇒ **`trio` 가 있으면 그것이 삼복승**이고, 그 경주의 `trifecta` 는 3連単이라 쓰면 안 된다.
        #   실측: 중앙 17건에서 trifecta/trio 배수 **중앙 4.7배**(최대 18.7배) — 그만큼 부풀려졌다.
        #   ⚠ `trifecta` 만 있는 125건은 **category 가 japan_central 로 오분류된 지방**이라 안전하다.
        _pay = res.get("payouts") or {}
        po = _pay.get("trio") if _pay.get("trio") is not None else _pay.get("trifecta")
        top3 = [res.get("1st"), res.get("2nd"), res.get("3rd")]
        if not po or any(x is None for x in top3):
            continue
        cp = d.get("corePicks") or {}
        # 섀도우면 finalTrifectas 가 곧 "걸었다면" 목록이다(shadowTrifectas 는 하위호환).
        ft = cp.get("finalTrifectas") or cp.get("shadowTrifectas") or []
        combos = []
        for t in ft:
            c = t.get("combo") or []
            if len(c) == 3:
                combos.append(sorted(int(x) for x in c))
        if not combos:
            continue
        # 🔴 [2026-08-01] 복병 목록 — "복병 포함 삼복승"을 따로 재기 위해 함께 싣는다.
        #   전체가 나빠도 부분은 다를 수 있다(경마 삼복승 3제외 42.9% ↔ 복병 포함분은 미측정이었다).
        dk = [int(x.get("no")) for x in (cp.get("darkHorsePicks") or [])
              if x.get("no") is not None]
        rows.append({"combos": combos, "po": float(po), "top3": sorted(int(x) for x in top3),
                     "cat": d.get("category") or "?", "n": len(combos),
                     "dk": dk, "dk1": (dk[0] if dk else None)})
    return rows


def report_trio(rows, label):
    slots = sum(r["n"] for r in rows)
    hits = []
    hit_races = 0
    for r in rows:
        h = [c for c in r["combos"] if c == r["top3"]]
        if h:
            hit_races += 1
            hits += [r["po"]] * len(h)
    hits.sort(reverse=True)
    ret = sum(hits)
    def pct(x, n):
        return 100.0 * x / max(n, 1)
    print("  %-16s 경주 %3d · 조합 %4d · 적중 %3d(%d경주) · 회수율 %6.1f%% · 1제외 %6.1f%% · 3제외 %6.1f%% · 적중배당중앙 %s"
          % (label, len(rows), slots, len(hits), hit_races, pct(ret, slots),
             pct(sum(hits[1:]), slots), pct(sum(hits[3:]), slots),
             ("%.1f배" % statistics.median(hits)) if hits else "-"))


def measure_dark3(sport="horse", pattern="2026_0*"):
    """[복병 3착 이내 진입률 (2026-08-01 신설)] — **완전 읽기 전용**.

    🔴 왜: 지금까지 복병 평가는 **복승(1·2착)** 기준뿐이었다. 대표 관찰은
      *"급락으로 잡힌 복병이 3착 안에 드는 경우가 많다"* 이고, 그건 **삼복승 재료**다.
      분모가 다르므로 **복승 기준 값과 섞어 쓰면 안 된다.**
    ⚠ 무작위 기대 = 3 / 두수. 그것과 대조해야 "많다"가 성립한다.
    """
    out = []
    for f in sorted(glob.glob(os.path.join(BASE, "data", "analysis_log", pattern + ".json"))):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        if (d.get("sport") or "") != sport:
            continue
        res = d.get("result") or {}
        top3 = [res.get("1st"), res.get("2nd"), res.get("3rd")]
        if any(x is None for x in top3):
            continue
        cp = d.get("corePicks") or {}
        dks = cp.get("darkHorsePicks") or []
        if not dks:
            continue
        nh = cp.get("raceHorseCount") or 0
        t3 = [int(x) for x in top3]
        for i, x in enumerate(dks):
            if x.get("no") is None:
                continue
            no = int(x["no"])
            out.append({"no": no, "rank": i + 1, "forced": bool(x.get("forced")),
                        "anom": int(x.get("anomCount") or 0),
                        "smart": bool(x.get("smartMoney")),
                        "place": (t3.index(no) + 1) if no in t3 else 0,
                        "nh": int(nh or 0), "cat": d.get("category") or "?"})
    return out


def report_dark3(rows, label):
    n = len(rows)
    if not n:
        print("  %-22s n=0 — 판정 불가" % label)
        return
    in3 = sum(1 for r in rows if r["place"])
    exp = [3.0 / r["nh"] for r in rows if r["nh"] >= 4]
    base = 100.0 * statistics.mean(exp) if exp else 0.0
    got = 100.0 * in3 / n
    mark = "⚠n<30" if n < 30 else ("🟢" if got >= base * 1.15 else ("🔴" if got <= base * 0.9 else "🟡"))
    p1 = sum(1 for r in rows if r["place"] == 1)
    p2 = sum(1 for r in rows if r["place"] == 2)
    p3 = sum(1 for r in rows if r["place"] == 3)
    print("  %-22s n=%4d | 1착 %3d · 2착 %3d · 3착 %3d · 미입상 %4d | **3착이내 %5.1f%%** (무작위 %4.1f%% · 배수 %.2f) %s"
          % (label, n, p1, p2, p3, n - in3, got, base,
             (got / base) if base else 0, mark))


def measure_drop3(sport="horse", pattern="2026_0*"):
    """[급락 신호의 3착 기여도 (2026-08-01 신설)] — **완전 읽기 전용**.

    🔴 왜: 급락 신호는 지금까지 **복승(1·2착) 판정으로만** 평가됐다. 3착 기준은 분모가 다르다.
    ⚠ 🔴 **한계를 먼저 밝힌다**: `drops_raw` 는 **조합 단위**(`{"combo":[1,9], "pct":-34}`)다.
      말 단위 급락은 **그 말이 낀 조합들의 평균**으로 환원한다 — 엔진의 `_excess_drop_analysis`
      와 같은 방식이다. **한 조합을 두 말에 그대로 귀속시키면 중복 계상**이 되므로 평균을 쓴다.
    ⚠ 무작위 기대 = 3 ÷ 두수.
    """
    out = []
    for f in sorted(glob.glob(os.path.join(BASE, "data", "analysis_log", pattern + ".json"))):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        if (d.get("sport") or "") != sport:
            continue
        res = d.get("result") or {}
        top3 = [res.get("1st"), res.get("2nd"), res.get("3rd")]
        if any(x is None for x in top3):
            continue
        dr = d.get("drops_raw") or []
        if not dr:
            continue
        cp = d.get("corePicks") or {}
        nh = int(cp.get("raceHorseCount") or 0)
        if nh < 4:
            continue
        acc = {}
        for x in dr:
            try:
                pct = float(x.get("pct"))
            except (TypeError, ValueError):
                continue
            for n0 in (x.get("combo") or []):
                acc.setdefault(int(n0), []).append(pct)
        t3 = [int(v) for v in top3]
        for no, ps in acc.items():
            out.append({"no": no, "mean": sum(ps) / len(ps), "n_combo": len(ps),
                        "place": (t3.index(no) + 1) if no in t3 else 0,
                        "nh": nh, "cat": d.get("category") or "?"})
    return out


def report_drop3(rows, label):
    n = len(rows)
    if not n:
        print("  %-24s n=0 — 판정 불가" % label)
        return
    in3 = sum(1 for r in rows if r["place"])
    base = 100.0 * statistics.mean([3.0 / r["nh"] for r in rows])
    got = 100.0 * in3 / n
    mark = "⚠n<30" if n < 30 else ("🟢" if got >= base * 1.15 else ("🔴" if got <= base * 0.9 else "🟡"))
    print("  %-24s n=%5d | 3착이내 %5.1f%% (무작위 %4.1f%% · 배수 %.2f) %s"
          % (label, n, got, base, (got / base) if base else 0, mark))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", default="cycle")
    ap.add_argument("--pattern", default="2026_07_*")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--trio", action="store_true", help="삼복승 섀도우 성적(별도 측정)")
    ap.add_argument("--dark3", action="store_true", help="복병 3착 이내 진입률(복승 기준과 별개)")
    ap.add_argument("--drop3", action="store_true", help="급락 폭별 3착 이내 진입률")
    ap.add_argument("--ev-sweep", dest="ev_sweep", action="store_true",
                    help="EV 임계 스윕 — 74.5%% 유지하며 적중배당 중앙 최대인 임계를 찾는다")
    a = ap.parse_args()
    if a.ev_sweep:
        out = measure_ev_sweep(a.sport, a.pattern)
        if a.json:
            print(json.dumps(out, ensure_ascii=False, indent=1))
            return
        print("=" * 110)
        print("EV 임계 스윕 · %s · %s   🔴 판정선 = 환급률 %.1f%%" % (a.sport, a.pattern, PAYBACK))
        print("=" * 110)
        print("🔴 찾는 것 = **74.5%% 를 지키면서 적중배당 중앙이 최대인 임계**(회수율 최대가 아니다).")
        print("⚠ 반사실 시뮬레이션이다 — 강등분은 실제로는 회원에게 나가지 않았다.")
        report_ev_sweep(out)
        return
    if a.drop3:
        rows = measure_drop3(a.sport, a.pattern)
        print("=" * 110)
        print("급락 신호의 **3착 기여도** · %s · %s" % (a.sport, a.pattern))
        print("=" * 110)
        print("⚠ 🔴 `drops_raw` 는 조합 단위다. 말 단위는 **그 말이 낀 조합들의 평균 급락률**로 환원했다.")
        print("⚠ 🔴 복승 기준 발동률(경마 84.5%·경륜 61.6%)과 **분모가 다르다.** 섞어 인용하지 말 것.")
        report_drop3(rows, "전체(급락 데이터 보유)")
        for lo, hi, lab in ((-1e9, -50, "평균급락 -50% 이하"), (-50, -40, "-40 ~ -50%"),
                            (-40, -30, "-30 ~ -40%"), (-30, -20, "-20 ~ -30%"),
                            (-20, -10, "-10 ~ -20%"), (-10, 0, "-10 ~ 0%"),
                            (0, 1e9, "상승(0% 이상)")):
            report_drop3([r for r in rows if lo <= r["mean"] < hi], lab)
        print("  ── 발동률(전체 대비 비중) ──")
        tot = len(rows) or 1
        for th in (-20, -30, -40, -50):
            k = len([r for r in rows if r["mean"] <= th])
            print("    평균급락 %d%% 이하 : %5d / %5d = %5.1f%%  %s"
                  % (th, k, tot, 100.0 * k / tot,
                     "🟢적정(5~30%)" if 5 <= 100.0 * k / tot <= 30 else "🔴부적정"))
        return 0
    if a.dark3:
        rows = measure_dark3(a.sport, a.pattern)
        print("=" * 126)
        print("복병 **3착 이내** 진입률 · %s · %s" % (a.sport, a.pattern))
        print("=" * 126)
        print("⚠ 🔴 복승(1·2착) 기준 값과 **분모가 다르다.** 섞어 인용하지 말 것.")
        print("⚠ 무작위 기대 = 3 ÷ 두수 (경주별 평균). 배수 1.0 이면 신호에 우위가 없다는 뜻이다.")
        report_dark3(rows, "전체")
        for k, lab in ((1, "복병 1순위"), (2, "복병 2순위"), (3, "복병 3순위")):
            report_dark3([r for r in rows if r["rank"] == k], lab)
        report_dark3([r for r in rows if r["forced"]], "forced=True")
        report_dark3([r for r in rows if r["smart"]], "smartMoney=True")
        for lo, hi, lab in ((1, 2, "anomCount 1~2"), (3, 5, "anomCount 3~5"),
                            (6, 9, "anomCount 6~9"), (10, 999, "anomCount 10+")):
            report_dark3([r for r in rows if lo <= r["anom"] <= hi], lab)
        report_dark3([r for r in rows if r["anom"] == 0], "anomCount 0")
        cats = {}
        for r0 in rows:
            cats.setdefault(r0["cat"], []).append(r0)
        for c, rs in sorted(cats.items(), key=lambda x: -len(x[1]))[:4]:
            report_dark3(rs, "[%s]" % c)
        return 0
    if a.trio:
        rows = measure_trio(a.sport, a.pattern)
        print("=" * 118)
        print("삼복승 **섀도우** 성적 · %s · %s   🔴 판정선 = 환급률 %.1f%%" % (a.sport, a.pattern, PAYBACK))
        print("=" * 118)
        print("⚠ 화면·판정에서 빠진 조합이다(회원에게 안 나갔다). **반사실 시뮬레이션**이다.")
        report_trio(rows, "전체")
        cats = {}
        for r0 in rows:
            cats.setdefault(r0["cat"], []).append(r0)
        for c, rs in sorted(cats.items(), key=lambda x: -len(x[1])):
            report_trio(rs, c)
        # 🔴 [2026-08-01] 복병 포함 / 미포함 분해. **조합 단위**로 가른다(경주 단위가 아니다).
        print("")
        print("  ── 복병 포함 여부로 분해 (⚠ 조합 단위 · 같은 경주가 양쪽에 나뉜다) ──")
        def _split(rs, pred, label):
            sub = []
            for r0 in rs:
                cs = [c for c in r0["combos"] if pred(r0, c)]
                if cs:
                    sub.append({**r0, "combos": cs, "n": len(cs)})
            if sub:
                report_trio(sub, label)
            else:
                print("  %-16s 조합 0 — 판정 불가" % label)
        _split(rows, lambda r0, c: bool(set(c) & set(r0["dk"][:2])), "복병(상위2) 포함")
        _split(rows, lambda r0, c: not (set(c) & set(r0["dk"][:2])), "복병 미포함")
        _split(rows, lambda r0, c: r0["dk1"] is not None and r0["dk1"] in c, "복병 1순위 포함")
        return 0
    r = measure(a.sport, a.pattern)
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return 0
    print("=" * 96)
    print("회수율 측정 · %s · %s   🔴 판정선 = 환급률 %.1f%%" % (a.sport, a.pattern, PAYBACK))
    print("=" * 96)
    print("⚠ 분모: 전체 %d → 정제(괴리 %.1f~%.1f배) %d경주 (%.1f%%)"
          % (r["denom_all"], CLEAN_LO, CLEAN_HI, r["denom_clean"],
             100.0 * r["denom_clean"] / max(r["denom_all"], 1)))
    print()
    print("  %-20s %-9s %-8s %-9s %-9s %-8s %s"
          % ("안", "오염", "🔴정제", "1제외", "3제외", "배당중앙", "74.5 대비"))
    for p in r["plans"]:
        mk = "🟢" if p["ex3"] >= PAYBACK else ("🟡" if p["rate"] >= PAYBACK else "🔴")
        print("  %-20s %6.1f%%   %6.1f%%  %6.1f%%   %6.1f%%   %5.1f배   %+6.1f%%p %s"
              % (p["name"], p["rate_dirty"], p["rate"], p["ex1"], p["ex3"],
                 p["median_odds"], p["vs_payback"], mk))
    if r.get("ci"):
        c = r["ci"]
        print()
        print("  95%%CI(%s · 부트스트랩 %d회 · n=%d): [%.1f%%, %.1f%%]"
              % (c["plan"], BOOT_N, r["denom_clean"], c["lo"], c["hi"]))
        print("  환급률 %.1f%% 포함: %s" % (PAYBACK,
              "🔴 예 → **통계적으로 구분 불가**" if c["includes_payback"] else "아니오"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
