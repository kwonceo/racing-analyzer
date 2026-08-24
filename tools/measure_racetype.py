# -*- coding: utf-8 -*-
"""[경주 유형별 분기 측정 · 2026-08-24 신설 · 읽기 전용]

🔴 대표: "난 경주 흐름·정확한 축이 없는 경주·거리 등을 보고 터진다/안터진다를 직감적으로 안다.
   이런 게 경쟁력이다. 이걸 우리만의 루틴으로 만들자."

그 직감을 **배당판만으로 계산되는 세 지표**로 옮겼다(마감 전에 알 수 있다 · 결과 불필요).
  축 선명도  = 2위배당 ÷ 1위배당
  집중도     = 상위3두 내재확률 합          ← 이 도구가 쓰는 축
  시장 최저복승

실측(8월 2,545경주 · 정제 · 확정배당) — 셋 다 **단조로 갈린다**
  축 평평(≤1.3) 정답배당중앙 8.8배 · 20배+ 27%   ↔ 선명(4.0+) 1.7배 · 18%
  상위3합 45~55% 16.7배 · 46%                  ↔ 65%+ 5.0배 · 18%
  최저 5~8배 15.4배 · 45%                      ↔ ~2배 1.9배 · 17%

🔴 그런데 우리는 세 유형을 **똑같이** 다룬다 (회수율 65.3 / 68.1 / 69.8% 로 거의 같다)
  분산 경주에서 우리 배당중앙 10.6배 ↔ 정답 8.6배 — **너무 비싼 것을 산다**
  집중 경주에서 5.2배 ↔ 2.3배 — 역시 비싼 쪽

⇒ 이 도구는 「유형별로 다르게 대응」했을 때를 잰다.
⚠ **대리 정책이 아니다** — 실제 `displayedCombos` 를 출발점으로 삼는다.
  다만 넓히는 안은 시장 배당판에서 뽑으므로 실제 `_final_picks` 의 신호·EV 는 재현하지 못한다(원칙 3).
⚠ 정제·판정선은 measure_recovery 에서 import(규칙을 두 곳에 두지 않는다).
"""
import collections
import glob
import gzip
import json
import os
import statistics
import sys
from itertools import combinations

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "tools"))
import measure_recovery as MR   # noqa: E402


def qmap(nm):
    for p in (os.path.join(BASE, "data", "odds_history", nm + ".json"),
              os.path.join(BASE, "data", "odds_history", nm + ".json.gz")):
        if not os.path.exists(p):
            continue
        try:
            fh = gzip.open(p, "rt", encoding="utf-8") if p.endswith(".gz") else open(p, encoding="utf-8")
            sn = (json.load(fh).get("snapshots") or [])
        except Exception:
            return {}
        q = {}
        for s in reversed(sn):
            qq = s.get("quinella")
            if not qq:
                continue
            it = qq if isinstance(qq, list) else [
                {"combo": [int(x) for x in str(k).replace("-", "+").split("+") if x.isdigit()],
                 "odds": v} for k, v in qq.items()]
            for e in it:
                cb = e.get("combo") or []
                try:
                    o = float(e.get("odds"))
                except (TypeError, ValueError):
                    continue
                if len(cb) == 2 and o > 0:
                    q[tuple(sorted(int(x) for x in cb))] = o
            if q:
                return q
    return {}


def rtype(q):
    """상위3두 내재확률 합 → 유형. 배당판만으로 계산된다(마감 전 판정 가능)."""
    im = {}
    for (x, y), o in q.items():
        for h in (x, y):
            im[h] = im.get(h, 0) + 1.0 / o
    tot = sum(im.values()) or 1
    pr = sorted((v / tot for v in im.values()), reverse=True)
    t3 = sum(pr[:3])
    return ("분산(≤55%)" if t3 <= 0.55 else "중간(55~65)" if t3 <= 0.65 else "집중(65%+)"), im


def mkt_top(im, n):
    return [x for x, _ in sorted(im.items(), key=lambda y: -y[1])][:n]


def load(sport, pattern):
    out = []
    for f in sorted(glob.glob(os.path.join(BASE, "data", "analysis_log", pattern + ".json"))):
        nm = os.path.basename(f)[:-5]
        try:
            j = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        if sport and str(j.get("sport") or "") != sport:
            continue
        cp = j.get("corePicks") or {}
        dc = [sorted(c) for c in ((cp.get("displayedCombos") or {}).get("quinellas") or [])]
        if not dc:
            continue
        r = j.get("result") or {}
        a, b = r.get("1st"), r.get("2nd")
        po = (r.get("payouts") or {}).get("quinella")
        if a is None or b is None or po is None:
            continue
        try:
            po = float(po)
        except (TypeError, ValueError):
            continue
        q = qmap(nm)
        if len(q) < 8:
            continue
        win = tuple(sorted([int(a), int(b)]))
        wo = q.get(win)
        if wo and wo > 0:
            rr = po / wo
            if rr < MR.CLEAN_LO or rr > MR.CLEAN_HI:
                continue
        t, im = rtype(q)
        out.append({"t": t, "q": q, "im": im, "dc": dc, "win": win, "po": po,
                    "kh": [int(x) for x in (cp.get("keyHorses") or []) if str(x).isdigit()]})
    return out


def plans(r):
    """유형별 대응안. 반환 {이름: 조합목록}"""
    dc = [list(c) for c in r["dc"]]
    im = r["im"]
    out = {"현행": dc}
    out["상위2개만"] = sorted(dc, key=lambda c: r["q"].get(tuple(c), 9e9))[:2]
    out["상위1개만"] = sorted(dc, key=lambda c: r["q"].get(tuple(c), 9e9))[:1]
    for n in (4, 5, 6):
        pool = mkt_top(im, n)
        add = [list(c) for c in combinations(sorted(pool), 2)
               if tuple(sorted(c)) not in {tuple(x) for x in dc}]
        out["+시장상위%d두" % n] = dc + add
    kh = r["kh"][:3]
    if len(kh) >= 3:
        add = [list(c) for c in combinations(sorted(kh), 2)
               if tuple(sorted(c)) not in {tuple(x) for x in dc}]
        out["+유력마전조합"] = dc + add
    return out


def run(sport, pattern):
    data = load(sport, pattern)
    if not data:
        print("  [%s %s] 표본 없음" % (sport or "all", pattern))
        return
    G = collections.defaultdict(list)
    for r in data:
        G[r["t"]].append(r)
    print()
    print("=" * 100)
    print("[%s] %s   정제 %d경주   판정선 %.1f%%" % (sport or "all", pattern, len(data), MR.PAYBACK))
    for t in ("집중(65%+)", "중간(55~65)", "분산(≤55%)"):
        rows = G.get(t) or []
        if len(rows) < 25:
            if rows:
                print("  [%s] %d경주 — ⚠ 표본 부족(판정 불가)" % (t, len(rows)))
            continue
        print()
        print("  [%s] %d경주   정답배당중앙 %.1f배" % (
            t, len(rows), statistics.median([x["po"] for x in rows])))
        print("    %-16s %6s %6s %8s %9s %9s %9s" % ("안", "구좌", "적중", "적중률", "회수율", "3제외", "배당중앙"))
        names = list(plans(rows[0]).keys())
        for nmp in names:
            seats = hitn = 0
            pays = []
            for r in rows:
                cs = plans(r).get(nmp)
                if cs is None:
                    continue
                seats += len(cs)
                if list(r["win"]) in cs:
                    hitn += 1
                    pays.append(r["po"])
            if not seats:
                continue
            pays.sort()
            ret = sum(pays) / seats * 100
            p3 = sum(pays[:-3]) / seats * 100 if len(pays) > 3 else 0
            mark = " 🟢" if nmp != "현행" else ""
            print("    %-16s %6d %6d %7.1f%% %8.1f%% %8.1f%% %8.1f배%s" % (
                nmp, seats, hitn, 100.0 * hitn / len(rows), ret, p3,
                statistics.median(pays) if pays else 0, mark))


if __name__ == "__main__":
    pat = sys.argv[1] if len(sys.argv) > 1 else "2026_08_*"
    sp = sys.argv[2] if len(sys.argv) > 2 else None
    for s in ([sp] if sp else ["cycle", "horse"]):
        run(s, pat)
