# -*- coding: utf-8 -*-
"""한국경마 1층·3층 소급 리플레이 — 완전 읽기 전용.

🔴 왜: 금요일(8/7)까지 한국 실데이터가 없다. 과거 개최일로 **미리 재야** 한다.
⚠ 한국은 확장(private)이 유일 소스라 명단 확장(출마표 ∪ oddspark)이 **작동하지 않는다.**
  그 상태 그대로 재야 실전과 같다.
🔴 오탐 판정: 배당 조합수가 **점진 충전**(작은 그리드 → 큰 그리드)이면 오염이 아니라
  실제 두수가 그만큼이고 출마표가 덜 판독된 것이다(7/30 부산 3경주 실증).
"""
import collections
import gzip
import io
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KRA = re.compile(r"서울|부산|부경|제주|과천")
PR = os.path.join(ROOT, "data", "prerace")
AL = os.path.join(ROOT, "data", "analysis_log")
OH = os.path.join(ROOT, "data", "odds_history")


def load(p):
    try:
        if p.endswith(".gz"):
            with gzip.open(p, "rt", encoding="utf-8") as f:
                return json.load(f)
        with io.open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def rosters():
    """명단 = prerace(PDF) ∪ analysis_log 의 korea raw_profile."""
    out = {}
    if os.path.isdir(PR):
        for nm in os.listdir(PR):
            if nm == "index.json" or not nm.endswith(".json"):
                continue
            d = load(os.path.join(PR, nm))
            m = re.match(r"(\d{4})-(\d{2})-(\d{2})_(.+?)_(\d+)$", nm[:-5])
            if not isinstance(d, dict) or not m:
                continue
            nos = set()
            for h in (d.get("horses") or []):
                v = h.get("horseNum", h.get("no"))
                try:
                    nos.add(int(v))
                except (TypeError, ValueError):
                    pass
            if nos:
                out["%s_%s_%s_%s_%s경주" % m.groups()] = nos
    for nm in os.listdir(AL):
        if not KRA.search(nm) or not nm.endswith(".json"):
            continue
        a = load(os.path.join(AL, nm))
        rp = (a or {}).get("raw_profile") or {}
        if str(rp.get("source")) != "korea":
            continue
        nos = set()
        for h in (rp.get("entries") or []):
            try:
                nos.add(int(h.get("no")))
            except (TypeError, ValueError):
                pass
        if nos:
            out.setdefault(nm[:-5], nos)
    return out


def shape(q):
    keys = list(q.keys()) if isinstance(q, dict) else [
        (x.get("combo") if isinstance(x, dict) else x) for x in (q or [])]
    nos = set()
    for k in keys:
        for x in re.findall(r"\d+", str(k)):
            nos.add(int(x))
    return len(keys), nos


def main():
    ros = rosters()
    tot = blocked3 = fired1 = 0
    nomatch = 0
    detail = []
    for nm in sorted(os.listdir(OH)):
        if not KRA.search(nm) or "TEST" in nm:
            continue
        d = load(os.path.join(OH, nm))
        if not isinstance(d, dict):
            continue
        key = nm.replace(".json.gz", "").replace(".json", "")
        grids = []
        for t in ("snapshots", "archive_snapshots"):
            for s in (d.get(t) or []):
                if isinstance(s, dict) and s.get("quinella"):
                    n, nos = shape(s["quinella"])
                    if n:
                        grids.append((n, max(nos), nos))
        if not grids:
            continue
        tot += 1
        roster = ros.get(key)
        if not roster or len(roster) < 2:
            nomatch += 1
            blocked3 += 1                      # 3층: 「출주 명단 없음」 → 가림
            detail.append((key, "명단없음", 0, grids[-1][0], grids[-1][1], [], "오탐후보"))
            continue
        n, mx, nos = grids[-1]                 # 마지막(가장 완전한) 틱으로 판정
        ghost = sorted(nos - roster)
        if not ghost:
            continue
        blocked3 += 1
        exact = (n == mx * (mx - 1) // 2)
        if exact:
            fired1 += 1
        # 🔴 점진 충전이면 오탐 — 작은 그리드가 먼저 왔고 커지기만 했다
        sizes = [g[0] for g in grids]
        ramp = (len(set(sizes)) > 1 and sizes[0] < sizes[-1]
                and sizes == sorted(sizes)[:len(sizes)] or
                (len(set(sizes)) > 2 and max(sizes) == sizes[-1]))
        detail.append((key, "유령", len(roster), n, mx, ghost[:5],
                       "오탐후보(점진충전)" if ramp else "오염후보"))
    print("한국 배당 보유 %d경주" % tot)
    print()
    print("[완화 전]")
    print("  3층 차단(추천 가림)  : %d (%.1f%%)" % (blocked3, 100.0 * blocked3 / max(tot, 1)))
    print("     └ 명단 없음       : %d" % nomatch)
    print("     └ 유령 마번       : %d" % (blocked3 - nomatch))
    print("  1층 폐기(exact)      : %d (%.1f%%)" % (fired1, 100.0 * fired1 / max(tot, 1)))
    fp = sum(1 for x in detail if "오탐" in x[6])
    print("  🔴 오탐 후보         : %d / %d = %.1f%%" % (fp, blocked3, 100.0 * fp / max(blocked3, 1)))
    # 🔴 [완화안 적용 후 · 2026-08-05 승인] 한국은 가리지 않고 **경고만** 하고,
    #   1층은 exact 예외 해제를 적용하지 않는다. 플래그·계수기는 그대로 남는다(완화 ③).
    #   ⚠ 이 스크립트는 여기서 대상이 **전부 한국**이므로 완화 후 차단은 0 이 된다.
    print()
    print("[완화 후 · 승인 적용]")
    print("  3층 차단(추천 가림)  : 0 (0.0%)      ← 한국은 배너만")
    print("  3층 경고 배너        : %d (%.1f%%)   ← 플래그·계수기는 그대로 남는다(완화 ③)"
          % (blocked3, 100.0 * blocked3 / max(tot, 1)))
    print("  1층 폐기             : 0 (0.0%)      ← exact 예외 해제 미적용")
    print("  🔴 오탐              : 0 / 0")
    print()
    for x in detail[:40]:
        print("   %-30s %-8s 명단%2d 조합%-4d 최대%2d 유령%-12s %s" % x)


if __name__ == "__main__":
    main()
