# -*- coding: utf-8 -*-
"""[읽기 전용] 실패 유형 사전 — **마감 전에 알 수 있는 조건**으로만 만든다.

🔴 왜 새로 만드나 (2026-09-02)
  `data/failure_review.json` 에 이미 유형 4종·규칙 4개·`improvement` 문구가 있다. 그런데:
    ① 유형이 **전부 사후 판정**이다 — 「전적오판」은 *정답마가 전적 하위였다*, 「신호미반영」은
       *정답마에 신호가 있었다*. **정답을 알아야 분류된다** ⇒ 마감 전에는 쓸 수 없다.
    ② 처방이 전부 **「더 사자」** 방향이다("전부 추천에 포함"·"배당 높아도 표시"·"신호 유지").
       🔴 그런데 실측은 반대다 — 조합 +1 69.3% · +2 64.7% · +3 64.6%(2026-08-31) ·
         입상률을 올리는 조건은 ROI 가 낮다(2026-09-01 교차셀 461개).
    ③ `before_rate` 는 있는데 **`after_rate` 가 없다** — 규칙이 실제로 이득인지 아무도 안 쟀다.

🔴 그래서 이 사전은 규칙을 **「이 판은 피한다」** 쪽으로 만든다.
  오늘까지 세 번 확인됐다 — **예측 정확도를 올리는 방향은 막다른 길**이고,
  남은 것은 **회피(경주 선별)** 와 **타이밍(마감 직전 급락)** 뿐이다.

판정 (사후 최적화 방지)
  ⓐ 조건은 **마감 전 관측값**만 쓴다(정답·결과를 절대 안 본다)
  ⓑ 전반기에서 찾고 **후반기에서 재현**되는지 본다(OOS)
  ⓒ 「피했을 때 남는 경주」의 회수율을 본다 — 판정선 **74.5%**
  ⓓ 훑은 조건 수를 함께 출력한다(다중비교)

⚠ 원칙 1(n<30) · 2(상위 1·3건 제외) · 15(확정배당) · 16(날짜 매칭) · 26(표본·정제 병기)

실행: python tools/failure_dict.py [패턴]
"""
import io
import os
import sys
import json
import gzip
import glob
import collections
import importlib.util

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BAD = ("odds_suspect", "baseline_reset", "next_race_blocked")

_sp = importlib.util.spec_from_file_location("mr", os.path.join(BASE, "tools", "measure_recovery.py"))
_mr = importlib.util.module_from_spec(_sp)
_sp.loader.exec_module(_mr)
CLEAN_LO, CLEAN_HI, PAYBACK = _mr.CLEAN_LO, _mr.CLEAN_HI, _mr.PAYBACK


def load(p):
    for path, gz in ((p, False), (p + ".gz", True)):
        try:
            if gz:
                with gzip.open(path, "rt", encoding="utf-8") as f:
                    return json.load(f)
            with io.open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            continue
    return None


def qmap(sn):
    q = (sn or {}).get("quinella")
    if isinstance(q, list):
        q = {"+".join(str(z) for z in (it.get("combo") or [])): it.get("odds")
             for it in q if isinstance(it, dict)}
    out = {}
    for k, v in (q or {}).items():
        pr = [x for x in str(k).replace("-", "+").split("+") if x.strip().isdigit()]
        if len(pr) != 2 or pr[0] == pr[1]:
            continue
        try:
            o = float(v.get("odds") if isinstance(v, dict) else v)
        except Exception:
            continue
        if o > 0:
            out[(min(int(pr[0]), int(pr[1])), max(int(pr[0]), int(pr[1])))] = o
    return out


def _ex(v, k):
    return sum(sorted(v, reverse=True)[k:])


def build(pat):
    """경주별 재료 — 🔴 조건 계산에 쓰는 값은 **전부 마감 전 관측**이다."""
    out = []
    for f in sorted(glob.glob(os.path.join(BASE, "data", "analysis_log", pat + ".json"))):
        d = load(f)
        if not isinstance(d, dict):
            continue
        cp = d.get("corePicks") or {}
        judged = [tuple(sorted(int(x) for x in c))
                  for c in ((cp.get("displayedCombos") or {}).get("quinellas") or [])
                  if c and len(c) == 2]
        if not judged:
            continue
        nm = os.path.basename(f)[:-5]
        rr = load(os.path.join(BASE, "data", "race_results", nm + ".json"))
        if not isinstance(rr, dict) or rr.get("payouts_approx"):
            continue
        res = rr.get("result") or {}
        try:
            top2 = tuple(sorted((int(res["1st"]), int(res["2nd"]))))
            po = float((rr.get("payouts") or {}).get("quinella"))
        except (TypeError, ValueError, KeyError):
            continue
        oh = load(os.path.join(BASE, "data", "odds_history", nm + ".json")) or {}
        sn = [s for s in (oh.get("snapshots") or [])
              if isinstance(s, dict) and s.get("quinella") and not s.get("after_close")
              and not any(s.get(b) for b in BAD)]
        if not sn:
            continue
        qm = qmap(sn[-1])
        if len(qm) < 6:
            continue
        mo = qm.get(top2)
        if not mo or not (CLEAN_LO <= po / mo <= CLEAN_HI):     # 원칙 15 · 정제
            continue
        inv = {c: 1.0 / o for c, o in qm.items()}
        tot = sum(inv.values())
        P = collections.defaultdict(float)
        for (a, b), v in inv.items():
            P[a] += v / tot
            P[b] += v / tot
        ps = sorted(P.values(), reverse=True)
        # ── 마감 전 관측값 ──────────────────────────────────────────
        jo = [qm.get(c) for c in judged if qm.get(c)]
        drops = d.get("drops_raw") or d.get("drops") or []
        out.append({
            "rk": nm, "sport": d.get("sport"), "judged": judged, "top2": top2, "po": po,
            "conc": sum(ps[:3]) / 2.0,                       # 집중도(상위3두 내재확률 비중)
            "nH": len(P),                                    # 두수
            "minOdds": min(qm.values()),                     # 시장 최저 복승
            "seats": len(judged),                            # 우리가 사는 구좌
            "pickMin": min(jo) if jo else None,               # 우리 명단 최저 배당
            "pickMax": max(jo) if jo else None,
            "spread": (max(jo) / min(jo)) if (jo and min(jo) > 0) else None,
            "nDrop": len(drops) if isinstance(drops, list) else 0,
            "ticks": len(sn),
        })
    out.sort(key=lambda x: x["rk"])
    return out


# ── 유형 정의 — 🔴 전부 마감 전 관측값. 결과·정답을 안 본다 ─────────────
TYPES = (
    ("난전(집중도<0.60)",       lambda r: r["conc"] < 0.60),
    ("초혼전(집중도<0.55)",     lambda r: r["conc"] < 0.55),
    ("다두수(13두↑)",           lambda r: r["nH"] >= 13),
    ("소두수(7두↓)",            lambda r: r["nH"] <= 7),
    ("시장최저 2배↓(압도적1인기)", lambda r: r["minOdds"] <= 2.0),
    ("시장최저 8배↑(무주공산)",   lambda r: r["minOdds"] >= 8.0),
    ("우리명단 전부 저배당(<3배)", lambda r: r["pickMax"] is not None and r["pickMax"] < 3.0),
    ("우리명단 폭 5배↑(분산)",    lambda r: r["spread"] is not None and r["spread"] >= 5.0),
    ("급락 신호 0",             lambda r: r["nDrop"] == 0),
    ("틱 5개 이하(수집 얕음)",    lambda r: r["ticks"] <= 5),
    ("구좌 1개",                lambda r: r["seats"] <= 1),
    ("구좌 4개↑",               lambda r: r["seats"] >= 4),
)


def calc(rows):
    if not rows:
        return None
    seats = sum(r["seats"] for r in rows)
    od = [r["po"] for r in rows if r["top2"] in r["judged"]]
    if seats <= 0:
        return None
    return {"n": len(rows), "seats": seats, "hit": len(od),
            "hr": 100.0 * len(od) / len(rows),
            "r0": 100.0 * sum(od) / seats,
            "r1": 100.0 * _ex(od, 1) / seats,
            "r3": 100.0 * _ex(od, 3) / seats}


def line(lbl, c, base=None):
    if not c or c["n"] < 30:
        print("      %-26s ⚠ n=%s 판정 불가" % (lbl, c["n"] if c else 0))
        return
    d = ("  Δ3제외 %+.1f%%p" % (c["r3"] - base["r3"])) if base else ""
    print("      %-26s %4d경주 구좌%5d · 적중 %5.1f%% · 회수 %6.1f%% · 3제외 %6.1f%%%s %s"
          % (lbl, c["n"], c["seats"], c["hr"], c["r0"], c["r3"], d,
             "🟢" if c["r3"] >= PAYBACK else ""))


def main():
    pat = sys.argv[1] if len(sys.argv) > 1 else "2026_0*"
    rows = build(pat)
    n3 = len(rows) // 2
    tr, te = rows[:n3], rows[n3:]
    print("[실패 유형 사전] 마감 전 관측값으로만 만든다")
    print("   ⚠ 표본 %s · 정제(확정÷마감 %.1f~%.1f) 후 **%d경주** · 판정선 %.1f%%"
          % (pat, CLEAN_LO, CLEAN_HI, len(rows), PAYBACK))
    print("   전반기 %d · 후반기 %d · 훑는 유형 %d개" % (len(tr), len(te), len(TYPES)))
    b_all, b_tr, b_te = calc(rows), calc(tr), calc(te)
    print("")
    print("   ● 기준선(전부 산다)")
    line("전체", b_all)
    line("  전반기", b_tr)
    line("  후반기", b_te)
    print("")
    print("   ● 유형별 — 그 유형에 **해당하는** 경주만 / 그것을 **뺀** 나머지")
    surv = []
    for lbl, fn in TYPES:
        In = [r for r in rows if fn(r)]
        Out = [r for r in rows if not fn(r)]
        cIn, cOut = calc(In), calc(Out)
        if not cIn or cIn["n"] < 30 or not cOut:
            print("   ○ %-24s ⚠ n=%d 판정 불가" % (lbl, len(In)))
            continue
        print("   ○ %s" % lbl)
        line("해당 경주", cIn)
        line("이 유형을 뺀 나머지", cOut, b_all)
        # OOS — 전·후반 모두 개선돼야 후보
        oTr, oTe = calc([r for r in tr if not fn(r)]), calc([r for r in te if not fn(r)])
        ok = (oTr and oTe and b_tr and b_te
              and oTr["r3"] > b_tr["r3"] and oTe["r3"] > b_te["r3"] and cIn["n"] >= 30)
        if ok:
            surv.append((lbl, cOut["r3"] - b_all["r3"], oTr["r3"] - b_tr["r3"], oTe["r3"] - b_te["r3"]))
            print("         🟢 OOS 통과 — 전반 %+.1f%%p · 후반 %+.1f%%p"
                  % (oTr["r3"] - b_tr["r3"], oTe["r3"] - b_te["r3"]))
    print("")
    print("   🔴 OOS 통과 %d / 훑은 %d" % (len(surv), len(TYPES)))
    for lbl, d0, d1, d2 in sorted(surv, key=lambda x: -x[1]):
        print("      · %-26s 전체 %+.1f%%p (전반 %+.1f · 후반 %+.1f)" % (lbl, d0, d1, d2))
    if not surv:
        print("      → 마감 전 조건으로 피할 수 있는 유형이 없다. **회피 방향도 막힌 것이다.**")
    else:
        print("      ⚠ 통과해도 **판정선 74.5%% 를 넘는지**가 따로다. 그리고 피하면 회원에게 줄 것이 준다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
