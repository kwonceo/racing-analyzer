# -*- coding: utf-8 -*-
"""[유력마 혼합 비율 측정 · 2026-08-24 신설 · 읽기 전용]

🔴 대표 지적(나고야 8경주): 전적 1위 4번(105.0)이 유력마에서 빠지고
   전적 최하위 2번(6.8)이 들어갔다. 코드가 그대로 말한다 —

     # 4) 유력마 3마리 (상위 10개 복승 조합 등장 빈도 + 인기가중)
     ranked = sorted(freq, ...)     ← **배당만 쓴다**
     key_horses = ranked[:3]
     form = _form_from_starters(...)  ← 전적은 그 **뒤에** 계산된다

   ⇒ 유력마 선정에 전적이 들어갈 자리가 아예 없다.

이 도구는 `ranked` 에 전적을 섞었을 때 무엇이 달라지는지 잰다.
  혼합식 : score = (1-w)*odds_score + w*record_score      (`_integrated_grades` 와 같은 방식)
  w=0 이 현행(배당만)에 해당하고 w=1 이 전적만이다.

■ 지표 셋
  ① 포함률      1·2착이 **둘 다** 상위3에 드는 비율 — 조합을 아무리 잘 짜도 이것이 천장이다
  ② 시장최저율  상위3 전조합 중 최저배당이 **시장 최저**와 같은 비율 — 대표가 지적한 지표(현재 81.7%)
  ③ 회수율      상위3 전조합(경주당 3구좌) · 확정배당 · 정제 0.5~2.0

⚠ **대리 정책이다.** 실제 `_final_picks` 는 EV필터·상한·되살림·교차짝을 거치므로 다르다.
  절대값이 아니라 **안 사이 비교**로만 읽을 것(원칙 3).
⚠ 정제·판정선은 `measure_recovery` 것을 import 해 쓴다(규칙을 두 곳에 두지 않는다).
"""
import glob
import json
import os
import statistics
import sys
from itertools import combinations

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "tools"))
import measure_recovery as MR   # noqa: E402  (CLEAN_LO/HI · PAYBACK 재사용)

WS = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]


def odds_score(o):
    """app.py `_odds_score` 연속판과 같은 값(2026-08-24 배선분)."""
    SM = [(20, 100), (40, 60), (65, 40), (115, 20), (150, 0)]
    if o is None or o >= 150:
        return 0.0
    if o <= 20:
        return 100.0
    for (x0, y0), (x1, y1) in zip(SM, SM[1:]):
        if o <= x1:
            return y0 + (y1 - y0) * (o - x0) / (x1 - x0)
    return 0.0


def load(sport, pattern):
    out = []
    for f in sorted(glob.glob(os.path.join(BASE, "data", "analysis_log", pattern + ".json"))):
        try:
            j = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        if str(j.get("sport") or "") != sport:
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
        hs = j.get("horses") or []
        if len(hs) < 4:
            continue
        # 시장 배당판(마지막 스냅샷)
        oh = os.path.join(BASE, "data", "odds_history", os.path.basename(f))
        q = {}
        # 🔴 [2026-08-24] 오래된 배당 파일은 **.gz 로 압축**돼 있다.
        #   그걸 못 읽어 8/01~09 표본이 통째로 0 이었고, 기간 분할이 성립하지 않았다(원칙 30).
        sn = []
        if os.path.exists(oh):
            try:
                sn = (json.load(open(oh, encoding="utf-8")).get("snapshots") or [])
            except Exception:
                sn = []
        elif os.path.exists(oh + ".gz"):
            import gzip
            try:
                with gzip.open(oh + ".gz", "rt", encoding="utf-8") as _g:
                    sn = (json.load(_g).get("snapshots") or [])
            except Exception:
                sn = []
        if True:
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
                    break
        if len(q) < 5:
            continue
        # 말별 대표배당 = 그 말이 든 복승 중 최저
        rep = {}
        for (x, y), o in q.items():
            for h in (x, y):
                if rep.get(h) is None or o < rep[h]:
                    rep[h] = o
        rows = []
        for h in hs:
            try:
                no = int(h.get("no"))
            except (TypeError, ValueError):
                continue
            if no not in rep:
                continue
            try:
                rs = float(h.get("record_score"))
            except (TypeError, ValueError):
                rs = None
            rows.append({"no": no, "rec": rs, "rep": rep[no]})
        if len(rows) < 4 or sum(1 for r2 in rows if r2["rec"] is not None) < 4:
            continue
        # 정제(원칙 15) — 확정 ÷ 마감 배당판
        wo = q.get(tuple(sorted([int(a), int(b)])))
        if wo and wo > 0:
            rr = po / wo
            if rr < MR.CLEAN_LO or rr > MR.CLEAN_HI:
                continue
        out.append({"rows": rows, "q": q, "win": sorted([int(a), int(b)]), "po": po})
    return out


def top3(rows, w):
    def sc(r):
        os_ = odds_score(r["rep"])
        rc = r["rec"] if r["rec"] is not None else 0.0
        return (1 - w) * os_ + w * rc
    return [r["no"] for r in sorted(rows, key=lambda r: -sc(r))][:3]


def run(sport, pattern):
    data = load(sport, pattern)
    if not data:
        print("  [%s %s] 표본 없음" % (sport, pattern))
        return
    mkt_low = {}
    print()
    print("=" * 96)
    print("[%s] %s   정제 %d경주   판정선 %.1f%%" % (sport, pattern, len(data), MR.PAYBACK))
    print("  ⚠ 대리 정책 = 상위3 전조합(경주당 3구좌). 실제 _final_picks 와 다르다.")
    print("  %-14s %8s %10s %9s %9s %9s" % ("전적 비중 w", "포함률", "시장최저율", "회수율", "3제외", "배당중앙"))
    for w in WS:
        inc = low = 0
        seats = 0
        pays = []
        for d in data:
            t3 = top3(d["rows"], w)
            if len(t3) < 3:
                continue
            cs = [tuple(sorted(c)) for c in combinations(sorted(t3), 2)]
            seats += len(cs)
            if d["win"][0] in t3 and d["win"][1] in t3:
                inc += 1
            if tuple(d["win"]) in cs:
                pays.append(d["po"])
            # 시장 최저율 — 우리 조합 중 최저배당이 시장 최저와 같은가
            mine = sorted((d["q"].get(c, 9e9), c) for c in cs)
            srt = sorted(d["q"].items(), key=lambda z: z[1])
            if mine and srt and mine[0][1] == srt[0][0]:
                low += 1
        n = len(data)
        pays.sort()
        ret = sum(pays) / seats * 100 if seats else 0
        p3 = sum(pays[:-3]) / seats * 100 if len(pays) > 3 else 0
        print("  %-14s %7.1f%% %9.1f%% %8.1f%% %8.1f%% %8.1f배%s" % (
            ("%.1f (현행)" % w) if w == 0 else "%.1f" % w,
            100.0 * inc / n, 100.0 * low / n, ret, p3,
            statistics.median(pays) if pays else 0,
            "  🟢" if w > 0 and p3 else ""))


if __name__ == "__main__":
    pat = sys.argv[1] if len(sys.argv) > 1 else "2026_08_*"
    for sp in ("cycle", "horse"):
        run(sp, pat)
