# -*- coding: utf-8 -*-
"""[읽기 전용] 카톡 상위 3개 컷 — 4번째 이후를 회원에게 보내면 얼마인가.

🔴 무엇을 재나
  카톡은 `_fq3 = _judge_quinellas(cp)[:3]` 로 **상위 3개만** 낸다(app.py).
  판정 명단(displayedCombos)은 전부다 ⇒ **성적은 4개로 재는데 회원은 3개를 받는다.**
  실측(8월): 판정 4개 이상인 경주 1,000/3,737(26.8%) · 잘린 자리에서 적중 89건(배당중앙 9.9배).

🔴 어떻게 재나 (원칙 준수)
  · 원칙 15 — **확정배당**(result.payouts.quinella)으로만 잰다.
  · 원칙 16 — 파일 매칭에 **날짜를 포함**한다(analysis_log 파일명에서 파생).
  · 원칙 2  — 상위 1건·3건 제외를 **항상 병기**한다.
  · 원칙 26 — 표본·정제·구좌·명단을 함께 적는다.
  🔴 **추가 매수는 한계 회수율로 본다**(추가회수 ÷ 추가구좌). 전체 회수율만 보면
     이미 사는 것에 묻혀 「늘리면 좋아 보이는」 착시가 난다(2026-07-31 신설 원칙).

⚠ 재현 못 한 것(정직하게)
  · `_judge_quinellas` 의 **정렬 순서**를 그대로 쓴다(displayedCombos 순서 = 저장 순서).
    카톡이 실제로 자른 3개와 순서가 같다는 전제다.
  · 카톡에는 💎·kakaoExtra 도 실리는데 여기서는 **복승 명단만** 센다.

실행: python tools/measure_kakao_cut.py
"""
import io
import os
import re
import sys
import json
import gzip
import glob
import statistics as st

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "tools"))

# 🔴 정제·판정선은 measure_recovery 것을 **그대로 쓴다**(규칙을 두 곳에 두지 않는다).
try:
    import importlib.util
    _sp = importlib.util.spec_from_file_location("mr", os.path.join(BASE, "tools", "measure_recovery.py"))
    _mr = importlib.util.module_from_spec(_sp)
    _sp.loader.exec_module(_mr)
    CLEAN_LO, CLEAN_HI, PAYBACK = _mr.CLEAN_LO, _mr.CLEAN_HI, _mr.PAYBACK
except Exception as _e:                                    # 원칙 19 — 조용히 다른 값을 쓰지 않는다
    print("🔴 measure_recovery 를 못 읽었다 — 정제 기준이 갈릴 수 있어 중단한다: %s" % str(_e)[:80])
    raise SystemExit(1)

CUT = 3          # 카톡이 내는 개수(app.py `_fq3 = ...[:3]`)


def _norm(k):
    for sep in ("+", "-", "_"):
        if sep in str(k):
            a, b = str(k).split(sep)[:2]
            try:
                return tuple(sorted((int(a), int(b))))
            except ValueError:
                return None
    return None


def _qmap(sn):
    out = {}
    for k, v in ((sn or {}).get("quinella") or {}).items():
        t = _norm(k)
        if t:
            try:
                out[t] = float(v.get("odds") if isinstance(v, dict) else v)
            except (TypeError, ValueError):
                continue
    return out


def _load(p):
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


def rows(pattern="2026_08_*"):
    out = []
    for f in sorted(glob.glob(os.path.join(BASE, "data", "analysis_log", pattern + ".json"))):
        name = os.path.basename(f)[:-5]
        d = _load(f)
        if not isinstance(d, dict):
            continue
        cp = d.get("corePicks") or {}
        dc = [tuple(sorted(int(x) for x in c))
              for c in ((cp.get("displayedCombos") or {}).get("quinellas") or []) if c and len(c) == 2]
        if not dc:
            continue
        rr = _load(os.path.join(BASE, "data", "race_results", name + ".json"))   # 🔴 날짜 포함(원칙 16)
        if not isinstance(rr, dict) or rr.get("payouts_approx") or rr.get("payouts_suspect"):
            continue
        po = (rr.get("payouts") or {}).get("quinella")                            # 🔴 최상위(원칙 8-E)
        r = rr.get("result") or {}
        try:
            top2 = tuple(sorted((int(r["1st"]), int(r["2nd"]))))
            po = float(po)
        except (TypeError, ValueError, KeyError):
            continue
        oh = _load(os.path.join(BASE, "data", "odds_history", name + ".json")) or {}
        sn = [s for s in (oh.get("snapshots") or []) if s.get("quinella")]
        mo = _qmap(sn[-1] if sn else {}).get(top2)
        if not mo:
            continue
        if not (CLEAN_LO <= po / mo <= CLEAN_HI):                                  # 🔴 정제
            continue
        out.append({"rk": name, "dc": dc, "top2": top2, "po": po,
                    "sport": d.get("sport"), "cat": d.get("category")})
    return out


def _ex(v, k):
    v = sorted(v, reverse=True)
    return sum(v[k:])


def calc(rs, sel):
    seats = hits = rec = 0
    od = []
    for r in rs:
        cs = sel(r)
        seats += len(cs)
        if r["top2"] in cs:
            hits += 1
            rec += r["po"]
            od.append(r["po"])
    return {"seats": seats, "hits": hits, "rec": rec, "od": od,
            "rate": (100.0 * rec / seats) if seats else 0.0,
            "ex1": (100.0 * _ex(od, 1) / seats) if seats else 0.0,
            "ex3": (100.0 * _ex(od, 3) / seats) if seats else 0.0}


def report(lbl, rs):
    if len(rs) < 30:
        print("  %-16s ⚠ 표본 %d < 30 — **판정 불가**(원칙 1)" % (lbl, len(rs)))
        return
    a = calc(rs, lambda r: r["dc"][:CUT])       # 카톡(현행)
    b = calc(rs, lambda r: r["dc"])             # 판정 명단 전부
    ds, dr = b["seats"] - a["seats"], b["rec"] - a["rec"]
    extra_od = [r["po"] for r in rs if r["top2"] in r["dc"][CUT:]]
    marg = (100.0 * dr / ds) if ds else 0.0
    m1 = (100.0 * _ex(extra_od, 1) / ds) if ds else 0.0
    m3 = (100.0 * _ex(extra_od, 3) / ds) if ds else 0.0
    print("  ▣ %s — %d경주" % (lbl, len(rs)))
    print("     카톡(3개)   구좌 %5d · 적중 %4d · 회수율 %6.1f%% · 1제외 %5.1f%% · 3제외 %5.1f%% · 경주당 %.2f"
          % (a["seats"], a["hits"], a["rate"], a["ex1"], a["ex3"], a["seats"] / len(rs)))
    print("     전부 발송   구좌 %5d · 적중 %4d · 회수율 %6.1f%% · 1제외 %5.1f%% · 3제외 %5.1f%% · 경주당 %.2f"
          % (b["seats"], b["hits"], b["rate"], b["ex1"], b["ex3"], b["seats"] / len(rs)))
    mk = "🟢" if marg >= PAYBACK else "🔴"
    print("     %s 추가분 **한계 회수율 %.1f%%** (구좌 +%d · 적중 +%d · 회수 +%.1f)"
          % (mk, marg, ds, b["hits"] - a["hits"], dr))
    print("        1제외 %.1f%% · 3제외 %.1f%% · 판정선 %.1f%% 대비 %+.1f%%p"
          % (m1, m3, PAYBACK, marg - PAYBACK))
    if extra_od:
        print("        추가 적중 배당 중앙 %.1f배 · 최대 %.1f배 · 10배+ %d건"
              % (st.median(extra_od), max(extra_od), sum(1 for x in extra_od if x >= 10)))


def main():
    rs = rows()
    print("▣ 카톡 상위 %d개 컷 — 4번째 이후를 보내면 얼마인가" % CUT)
    print("   ⚠ 표본: 8월 · 확정배당 보유 · 정제(확정÷마감 %.1f~%.1f) **%d경주** · 구좌 단위 · 복승 판정 명단"
          % (CLEAN_LO, CLEAN_HI, len(rs)))
    print("   🔴 추가 매수는 **한계 회수율**로 본다(추가회수÷추가구좌) · 판정선 %.1f%%\n" % PAYBACK)
    over = [r for r in rs if len(r["dc"]) > CUT]
    print("   판정 %d개 초과 경주 %d / %d (%.1f%%)\n" % (CUT, len(over), len(rs), 100.0 * len(over) / max(len(rs), 1)))
    report("전체", rs)
    print()
    for k, lbl in (("cycle", "경륜"), ("horse", "경마")):
        report(lbl, [r for r in rs if r["sport"] == k])
    print()
    report("한국", [r for r in rs if r["cat"] == "korea"])
    print()
    print("  ── 기간 3분할(원칙: 판정 4단계 ③) ──")
    rs2 = sorted(rs, key=lambda r: r["rk"])
    n = len(rs2) // 3
    for i, lbl in enumerate(("8/01~", "중간 ", "~8/31")):
        report(lbl, rs2[i * n:(i + 1) * n] if i < 2 else rs2[2 * n:])


if __name__ == "__main__":
    main()
