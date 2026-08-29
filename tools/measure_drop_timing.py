# -*- coding: utf-8 -*-
"""급락이 **언제** 왔는가로 갈라 정보량을 잰다 (읽기 전용).

🔴 왜 이 축인가 (2026-08-28 대표 지적)
  「기수·전적은 남들이 다 본다」 — 실제로 오늘 여덟 번 측정해 여덟 번 다 시장을 못 이겼다.
  🟢 우리만 가진 것은 **배당 시계열**이다(8월 1,481경주 · 76,293틱 · 경주당 51.5틱).
    남들은 최종 배당만 본다. **마감까지 돈이 어떻게 들어왔는지는 우리만 안다.**
  ⇒ 「급락했다」가 아니라 **「언제 급락했나」**를 묻는다 — 시계열이 없으면 물을 수 없는 질문이다.

🔴 반드시 **엣지**로 본다(원칙 8)
  급락하면 배당이 내려가 적중률이 오르는 것은 **당연하다**. 그건 정보가 아니다.
  ⇒ 엣지 = 실측 적중률 ÷ **최종 배당의 시장암시확률**. 1.0 을 넘어야 시장 위의 정보다.

⚠ 원칙 27 마감 전 정상 틱만 · 원칙 1 적중 30건 · 원칙 2 대박3뺀 병기.
"""
import os, io, sys, json, glob, math, itertools

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import measure_score_edge as E
import measure_rank_pairs as R

DROP_MIN = 15.0        # 급락으로 볼 최소 하락률(%)


def series(doc):
    """마감 전 **정상** 틱만 (mb, qmap) 오름차순(시간순)."""
    out = []
    for t in E._ticks(doc or {}):
        if not isinstance(t, dict) or any(t.get(k) for k in E._BAD):
            continue
        mb = t.get("minutes_before")
        if mb is None or mb < 0 or not t.get("quinella"):
            continue
        out.append((float(t.get("t") or 0), float(mb), E._qmap(t["quinella"])))
    out.sort()
    return [(mb, q) for _, mb, q in out]


def drops(ser):
    """조합별 **최대 급락과 그 시점**. 반환 {combo: (하락률%, mb)}."""
    out = {}
    for i in range(1, len(ser)):
        pmb, pq = ser[i - 1]
        cmb, cq = ser[i]
        for c, o in cq.items():
            po = pq.get(c)
            if not po or not o or po <= 0:
                continue
            d = 100.0 * (o - po) / po
            if d <= -DROP_MIN and (c not in out or d < out[c][0]):
                out[c] = (d, cmb)
    return out


def collect(pattern="2026_08_*", limit=None):
    rows, st = [], {"파일": 0, "결과없음": 0, "틱부족": 0, "채택": 0}
    fs = sorted(glob.glob(os.path.join(ROOT, "data", "race_results", pattern + ".json")))
    for f in (fs[:limit] if limit else fs):
        st["파일"] += 1
        d = E._load(f)
        if not isinstance(d, dict) or d.get("payouts_approx"):
            continue
        pay = (d.get("payouts") or {}).get("quinella")
        base = os.path.basename(f)[:-5]
        al = E._load(os.path.join(ROOT, "data", "analysis_log", base + ".json")) or {}
        res = al.get("result") or {}
        try:
            top2 = (min(int(res["1st"]), int(res["2nd"])), max(int(res["1st"]), int(res["2nd"])))
        except Exception:
            st["결과없음"] += 1
            continue
        ser = series(E._load(os.path.join(ROOT, "data", "odds_history", base + ".json")))
        if len(ser) < 5:
            st["틱부족"] += 1
            continue
        fin = ser[-1][1]
        if not fin or top2 not in fin:
            st["틱부족"] += 1
            continue
        inv = sum(1.0 / o for o in fin.values() if o > 0)
        if inv <= 0:
            continue
        st["채택"] += 1
        dr = drops(ser)
        sp = E._sport(al)
        for c, o in fin.items():
            if o <= 0:
                continue
            g = dr.get(c)
            rows.append({"rk": base, "sport": sp, "combo": c, "odds": o,
                         "imp": (1.0 / o) / inv, "hit": 1 if c == top2 else 0,
                         "drop": (g[0] if g else None), "mb": (g[1] if g else None),
                         "pay": (float(pay) if (pay is not None and c == top2) else 0.0),
                         "haspay": pay is not None})
    return rows, st


def _wil(k, n):
    if n == 0:
        return (0.0, 0.0)
    z, p = 1.96, k / float(n)
    dd = 1 + z * z / n
    c = (p + z * z / (2 * n)) / dd
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / dd
    return (max(0.0, c - h), min(1.0, c + h))


def table(rows, title):
    print("\n  === %s ===  (⚠ 분모 = 조합 · 엣지 = 실측 ÷ 시장암시)" % title)
    print("  %-18s %7s %5s %8s %9s %7s %-18s %8s" %
          ("급락 시점", "조합", "적중", "적중률", "시장암시", "엣지", "엣지 95%CI", "배당중앙"))
    B = [("급락 없음", lambda r: r["drop"] is None),
         ("T-10분 이전", lambda r: r["drop"] is not None and r["mb"] > 10),
         ("T-10 ~ 5분", lambda r: r["drop"] is not None and 5 < r["mb"] <= 10),
         ("T-5 ~ 2분", lambda r: r["drop"] is not None and 2 < r["mb"] <= 5),
         ("T-2 ~ 0분", lambda r: r["drop"] is not None and r["mb"] <= 2)]
    for lab, sel in B:
        g = [r for r in rows if sel(r)]
        n = len(g)
        if n < 50:
            continue
        k = sum(r["hit"] for r in g)
        act = k / float(n)
        imp = sum(r["imp"] for r in g) / n
        edge = act / imp if imp else 0
        lo, hi = _wil(k, n)
        od = sorted(r["odds"] for r in g)
        mark = "" if k >= 30 else "  ⚠판정불가"
        print("  %-18s %7d %5d %7.2f%% %8.2f%% %7.3f [%5.3f,%5.3f] %7.1f배%s" %
              (lab, n, k, 100 * act, 100 * imp, edge,
               (lo / imp if imp else 0), (hi / imp if imp else 0), od[len(od) // 2], mark))


def main():
    pat = sys.argv[1] if len(sys.argv) > 1 else "2026_08_*"
    rows, st = collect(pat)
    print("  " + " · ".join("%s %d" % (k, v) for k, v in st.items()) + " · 조합 %d" % len(rows))
    print("  ⚠ 급락 기준: 직전 틱 대비 **-%.0f%%** 이상" % DROP_MIN)
    table(rows, "전체")
    for sp in ("경륜", "경마"):
        sub = [r for r in rows if r["sport"] == sp]
        if len(sub) >= 3000:
            table(sub, sp)


if __name__ == "__main__":
    main()
