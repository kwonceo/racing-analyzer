# -*- coding: utf-8 -*-
"""💎 복병(bmedSpecial) 성적 — **한방 축 기준으로** 판정한다 (읽기 전용).

🔴 판정 기준이 다르다 (2026-08-11 대표 확정)
  본선 축 = **대박3뺀 회수율**(판정선 74.5%)
  한방 축 = **적중배당 중앙**  · 🔴 **회수율로 기각하지 않는다**
  최소선  = **30경주에 적중 1건**(= 적중률 3.3%). 미달이면 그 자리를 접는다.
  ⚠ 회수율을 함께 내되 그것으로 판정하지 않는다 — 섞으면 분리가 무의미해진다.

⚠ 원칙 15 확정배당 · 원칙 2 대박3뺀 병기 · 원칙 26 표본/정제/구좌 병기 · 원칙 1 적중 30건.
"""
import os, io, sys, json, glob

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import measure_score_edge as E
import measure_rank_pairs as R


def _cb(x):
    if isinstance(x, dict):
        x = x.get("combo")
    if isinstance(x, (list, tuple)) and len(x) >= 2:
        try:
            return (min(int(x[0]), int(x[1])), max(int(x[0]), int(x[1])))
        except Exception:
            return None
    return None


def collect(pattern="2026_08_*"):
    rows, st = [], {"파일": 0, "결과없음": 0, "확정배당없음": 0, "근사": 0, "정제탈락": 0, "채택": 0}
    for f in sorted(glob.glob(os.path.join(ROOT, "data", "race_results", pattern + ".json"))):
        st["파일"] += 1
        d = E._load(f)
        if not isinstance(d, dict):
            continue
        if d.get("payouts_approx") or d.get("payouts_suspect"):
            st["근사"] += 1
            continue
        pay = (d.get("payouts") or {}).get("quinella")
        if pay is None:
            st["확정배당없음"] += 1
            continue
        base = os.path.basename(f)[:-5]
        al = E._load(os.path.join(ROOT, "data", "analysis_log", base + ".json")) or {}
        res = al.get("result") or {}
        try:
            top2 = {int(res.get("1st")), int(res.get("2nd"))}
        except Exception:
            st["결과없음"] += 1
            continue
        if len(top2) < 2:
            st["결과없음"] += 1
            continue
        qm = E._qmap(E._last_pre_close(
            E._load(os.path.join(ROOT, "data", "odds_history", base + ".json"))) or {})
        key = (min(top2), max(top2))
        mc = qm.get(key)
        if not mc:
            st["정제탈락"] += 1
            continue
        if not (R.CLEAN_LO <= float(pay) / mc <= R.CLEAN_HI):
            st["정제탈락"] += 1
            continue
        st["채택"] += 1
        cp = al.get("corePicks") or {}
        cur = [c for c in (_cb(x) for x in ((cp.get("displayedCombos") or {}).get("quinellas") or [])) if c]
        dia_raw = cp.get("bmedSpecial") or []
        dia = [c for c in (_cb(x) for x in dia_raw) if c]
        dia_odds = {}
        for x in dia_raw:
            c = _cb(x)
            if c and isinstance(x, dict) and x.get("odds"):
                dia_odds[c] = float(x["odds"])
        rows.append({"rk": base, "sport": E._sport(al), "top2": key, "pay": float(pay),
                     "cur": cur, "dia": dia, "dia1": dia[:1],
                     "diaNew": [c for c in dia if c not in cur],
                     "odds": dia_odds, "qm": {str(k): v for k, v in qm.items()}})
    return rows, st


def per(r):
    d = r["rk"][:10]
    return 0 if d <= "2026_08_09" else (1 if d <= "2026_08_19" else 2)


def ev(rows, fld, sp=None, pi=None):
    g = [r for r in rows if (sp is None or r["sport"] == sp) and (pi is None or per(r) == pi)]
    g = [r for r in g if r[fld]]                    # 그 안이 조합을 낸 경주만
    seats = sum(len(r[fld]) for r in g)
    pays = [r["pay"] for r in g if r["top2"] in r[fld]]
    if seats == 0:
        return None
    med = sorted(pays)[len(pays) // 2] if pays else None
    return {"경주": len(g), "구좌": seats, "적중": len(pays),
            "회수율": 100.0 * sum(pays) / seats,
            "대박3뺀": 100.0 * R._ex(pays, 3) / seats,
            "배당중앙": med,
            "적중률": 100.0 * len(pays) / seats,
            "경주당": seats / float(max(1, len(g)))}


def show(rows, sp=None):
    print("\n  === %s ===  (⚠ 분모 = 구좌 · 확정배당 · 그 안이 조합을 낸 경주만)" % (sp or "전체"))
    print("  %-18s %6s %7s %6s %9s %9s %9s %7s" %
          ("안", "경주", "구좌", "적중", "적중배당중앙", "회수율", "대박3뺀", "경주당"))
    for fld, lab in (("cur", "현행 판정 명단"), ("dia", "💎 전체"),
                     ("dia1", "💎 상위1"), ("diaNew", "💎 판정명단 밖")):
        v = ev(rows, fld, sp)
        if not v:
            continue
        mark = "" if v["적중"] >= 30 else "  ⚠판정불가"
        print("  %-18s %6d %7d %6d %8s %8.1f%% %8.1f%% %7.2f%s" %
              (lab, v["경주"], v["구좌"], v["적중"],
               ("%.1f배" % v["배당중앙"]) if v["배당중앙"] else "-",
               v["회수율"], v["대박3뺀"], v["경주당"], mark))


def main():
    pat = sys.argv[1] if len(sys.argv) > 1 else "2026_08_*"
    rows, st = collect(pat)
    print("  표본: %s" % pat)
    print("  " + " · ".join("%s %d" % (k, v) for k, v in st.items()))
    show(rows)
    for sp in ("경륜", "경마"):
        if len([r for r in rows if r["sport"] == sp]) >= 300:
            show(rows, sp)
    print("\n  === 판정 4단계 ③ 기간 3분할 · 적중배당 중앙 ===")
    for fld, lab in (("cur", "현행"), ("dia1", "💎 상위1"), ("diaNew", "💎 명단밖")):
        s = []
        for pi, pl in ((0, "8/01~09"), (1, "8/10~19"), (2, "8/20~")):
            v = ev(rows, fld, None, pi)
            if v:
                s.append("%s %s(적중 %d)" % (pl, ("%.1f배" % v["배당중앙"]) if v["배당중앙"] else "-", v["적중"]))
        print("   %-10s %s" % (lab, " · ".join(s)))
    print("\n  === 최소선(30경주에 적중 1건 = 적중률 3.3%) ===")
    for fld, lab in (("dia", "💎 전체"), ("dia1", "💎 상위1"), ("diaNew", "💎 명단밖")):
        v = ev(rows, fld)
        if v:
            print("   %-12s 적중률 %.2f%%  → %s" %
                  (lab, v["적중률"], "🟢 통과" if v["적중률"] >= 3.3 else "🔴 미달"))


if __name__ == "__main__":
    main()
