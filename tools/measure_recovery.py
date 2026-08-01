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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", default="cycle")
    ap.add_argument("--pattern", default="2026_07_*")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
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
