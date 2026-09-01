# -*- coding: utf-8 -*-
"""[읽기 전용] 적중률을 **어디서 잃는가** — 천장 분해.

🔴 왜 이렇게 재나
  「적중률을 올리자」는 방향이 셋뿐이다:
    ① 조합 생성 — 유력마 안에 정답이 둘 다 있는데 **안 묶은** 경주
    ② 유력마 선정 — 정답이 후보엔 있는데 **유력마엔 없는** 경주
    ③ 제거 — 정답이 **제거마에 걸린** 경주
  각각 몇 %인지 알아야 어디를 고칠지 정해진다. 이 도구가 그것만 센다.

🔴 반드시 **배당을 함께** 본다(사업 원칙: 고배당·중배당이 기본).
  적중률만 올리면 저배당으로 수렴한다 — 실제로 이 프로젝트에서 그렇게 갔다
  (2026-08-24 배당대별 실측: ~2배 적중 53.2% ↔ 12~20배 5.6%).
  ⇒ 각 갈래의 **정답 조합 확정배당 중앙값**을 함께 낸다. 그것이 「잡으면 얼마인가」다.

⚠ 원칙 준수
  · 원칙 15 — **확정배당**(result.payouts.quinella)으로만 잰다.
  · 원칙 16 — 파일 매칭에 **날짜 포함**(analysis_log 파일명에서 파생).
  · 원칙 26 — 표본·분모·명단 기준을 함께 적는다.
  · 판정 명단 = `corePicks.displayedCombos.quinellas`(= 회원 수신에 맞춘 것 · 2026-08-29 정렬)

⚠ 재현 못 한 것
  · `_final_picks` 를 재현하지 않는다. **어디서 잃는지**만 센다.
    「그래서 얼마 버나」는 `measure_recovery` 로 따로 재야 한다.

실행: python tools/measure_hit_ceiling.py [패턴]
"""
import io
import os
import sys
import json
import gzip
import glob
import statistics as st
import collections

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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


def _ints(xs):
    out = set()
    for x in (xs or []):
        try:
            out.add(int(x))
        except (TypeError, ValueError):
            pass
    return out


def rows(pattern):
    out = []
    for f in sorted(glob.glob(os.path.join(BASE, "data", "analysis_log", pattern + ".json"))):
        name = os.path.basename(f)[:-5]
        d = _load(f)
        if not isinstance(d, dict):
            continue
        cp = d.get("corePicks") or {}
        judged = [tuple(sorted(int(x) for x in c))
                  for c in ((cp.get("displayedCombos") or {}).get("quinellas") or [])
                  if c and len(c) == 2]
        if not judged:
            continue
        rr = _load(os.path.join(BASE, "data", "race_results", name + ".json"))
        if not isinstance(rr, dict) or rr.get("payouts_approx"):
            continue
        r = rr.get("result") or {}
        po = (rr.get("payouts") or {}).get("quinella")
        try:
            a, b = int(r["1st"]), int(r["2nd"])
            po = float(po)
        except (TypeError, ValueError, KeyError):
            continue
        top2 = tuple(sorted((a, b)))
        elim = d.get("elimination") or {}
        out.append({
            "rk": name, "sport": d.get("sport"), "cat": d.get("category"),
            "judged": judged, "top2": top2, "po": po,
            "key": _ints(cp.get("keyHorses")),
            "cand": _ints(elim.get("candidates")),
            "elim": _ints(elim.get("eliminated")),
            "nq": len(judged),
        })
    return out


def _med(v):
    return st.median(v) if v else 0.0


def report(lbl, rs):
    if len(rs) < 30:
        print("  %-10s ⚠ 표본 %d < 30 — **판정 불가**(원칙 1)" % (lbl, len(rs)))
        return
    g = collections.OrderedDict((k, []) for k in
                                ("A적중", "B조합미생성", "C유력마밖", "D절반만", "E제거에걸림"))
    for r in rs:
        t = set(r["top2"])
        if r["top2"] in r["judged"]:
            g["A적중"].append(r)
        elif t <= r["key"]:
            g["B조합미생성"].append(r)
        elif t <= r["cand"]:
            g["C유력마밖"].append(r)
        elif t & r["elim"]:
            g["E제거에걸림"].append(r)
        else:
            g["D절반만"].append(r)
    n = len(rs)
    print("  ▣ %s — %d경주 · 경주당 판정 %.2f조합" % (lbl, n, sum(r["nq"] for r in rs) / n))
    print("     %-14s %6s %7s   %s" % ("갈래", "경주", "비율", "정답조합 확정배당(중앙 / 상위25%)"))
    for k, v in g.items():
        if not v:
            print("     %-14s %6d %6.1f%%" % (k, 0, 0.0))
            continue
        od = sorted(x["po"] for x in v)
        q3 = od[int(len(od) * 0.75)] if od else 0
        print("     %-14s %6d %6.1f%%   %6.1f배 / %6.1f배%s"
              % (k, len(v), 100.0 * len(v) / n, _med(od), q3,
                 "" if len(v) >= 30 else "   ⚠n<30"))
    hit = len(g["A적중"])
    reach = hit + len(g["B조합미생성"])
    print("     ⇒ 현재 적중률 **%.1f%%** · 조합만 고치면 천장 **%.1f%%** (+%.1f%%p)"
          % (100.0 * hit / n, 100.0 * reach / n, 100.0 * (reach - hit) / n))
    print("     ⇒ 유력마까지 고치면 천장 %.1f%% · 후보 안이면 %.1f%%"
          % (100.0 * (reach + len(g["C유력마밖"])) / n,
             100.0 * (reach + len(g["C유력마밖"]) + len(g["D절반만"])) / n))


def main():
    pat = sys.argv[1] if len(sys.argv) > 1 else "2026_0*"
    rs = rows(pat)
    print("▣ 적중률 천장 분해 — 어디서 잃는가")
    print("   ⚠ 표본: %s · 확정배당 보유 · 판정 명단(displayedCombos) 기준 **%d경주**" % (pat, len(rs)))
    print("   🔴 배당을 함께 본다 — 적중률만 올리면 저배당으로 수렴한다(사업 원칙)\n")
    if not rs:
        print("   표본 없음")
        return
    report("전체", rs)
    print()
    for k, lbl in (("cycle", "경륜"), ("horse", "경마")):
        report(lbl, [r for r in rs if r["sport"] == k])
    print()
    report("한국", [r for r in rs if r["cat"] == "korea"])
    print()
    print("   ⚠ B 는 「조합을 늘리면 잡힌다」가 아니다 — 늘리면 **구좌도 는다.**")
    print("      실제 값어치는 한계 회수율(추가회수÷추가구좌)로 따로 재야 한다.")


if __name__ == "__main__":
    main()
