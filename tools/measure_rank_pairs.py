# -*- coding: utf-8 -*-
"""복승 조합을 **시장순위 쌍**으로 갈라 회수율을 잰다 (읽기 전용).

🔴 왜 이 축인가
  2026-08-28 측정에서 `record_score` 가 시장 위에 정보를 더하지 못한다는 것이 확정됐다
  (2,857경주·22,552마 · 기간 3분할 미달). 예측을 더 잘하는 방향은 네 번 다 막혔다.
  ⇒ 남은 레버는 **「어느 자리를 사는가」**다. 그래서 시장 자신의 분포 안에서
    **어느 순위 쌍이 돈이 되는가**를 본다.

⚠ 이것은 원칙 14(포함률 최대화 = 시장 복사)와 다르다.
  포함률을 늘리는 것이 아니라 **같은 시장 정보 안에서 어느 자리만 살 것인가**를 고르는 것이다.
  시장이 틀린 곳을 찾는 게 아니라 **공제율이 덜 먹는 자리**를 찾는 것이다.

⚠ 원칙 15 — 회수율은 **확정배당** 기준. ⚠ 원칙 2 — 상위 1·3건 제외 병기.
⚠ 원칙 26 — 표본·정제·구좌를 함께 적는다. ⚠ 원칙 1 — 적중 30건 미만은 판정 불가.
⚠ 원칙 27 — 배당·순위는 **마감 전 마지막 정상 틱**만 쓴다.
"""
import os, io, sys, json, glob

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import measure_score_edge as E   # _load/_qmap/_last_pre_close/_market_rank 재사용(규칙을 두 곳에 두지 않는다)

RES = os.path.join(ROOT, "data", "race_results")
LOG = os.path.join(ROOT, "data", "analysis_log")
CLEAN_LO, CLEAN_HI = 0.5, 2.0     # 정제: 확정 ÷ 마감


def _bucket(r):
    return 1 if r == 1 else (2 if r == 2 else (3 if r == 3 else (45 if r <= 5 else 6)))


_LAB = {1: "1위", 2: "2위", 3: "3위", 45: "4~5위", 6: "6위+"}


def collect(pattern="2026_08_*"):
    rows, st = [], {"파일": 0, "결과없음": 0, "확정배당없음": 0, "근사": 0,
                    "배당없음": 0, "정제탈락": 0, "채택": 0}
    for f in sorted(glob.glob(os.path.join(RES, pattern + ".json"))):
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
        al = E._load(os.path.join(LOG, base + ".json")) or {}
        res = al.get("result") or {}
        try:
            top2 = {int(res.get("1st")), int(res.get("2nd"))}
        except Exception:
            st["결과없음"] += 1
            continue
        if len(top2) < 2:
            st["결과없음"] += 1
            continue
        qm = E._qmap(E._last_pre_close(E._load(os.path.join(ROOT, "data", "odds_history", base + ".json"))) or {})
        mr = E._market_rank(qm)
        if not mr:
            st["배당없음"] += 1
            continue
        key = (min(top2), max(top2))
        mkt_close = qm.get(key)
        if not mkt_close:
            st["정제탈락"] += 1
            continue
        ratio = float(pay) / mkt_close
        if not (CLEAN_LO <= ratio <= CLEAN_HI):
            st["정제탈락"] += 1
            continue
        st["채택"] += 1
        sp = E._sport(al)
        for (a, b), o in qm.items():
            if a not in mr or b not in mr:
                continue
            r1, r2 = sorted((_bucket(mr[a]), _bucket(mr[b])))
            hit = 1 if {a, b} == top2 else 0
            rows.append({"rk": base, "sport": sp, "pair": (r1, r2),
                         "odds": o, "hit": hit, "pay": float(pay) if hit else 0.0})
    return rows, st


def _ex(vals, k):
    """상위 k건 제외 회수 합 — 원칙 2(극단값)."""
    return sum(sorted(vals, reverse=True)[k:])


def table(rows, title, minseats=200):
    print("\n  === %s ===  (⚠ 분모 = 구좌 · 조합 1개 = 1구좌 · 확정배당 기준)" % title)
    print("  %-12s %8s %6s %8s %8s %8s %9s" %
          ("시장순위 쌍", "구좌", "적중", "회수율", "1건뺀", "대박3뺀", "배당중앙"))
    agg = {}
    for r in rows:
        agg.setdefault(r["pair"], []).append(r)
    out = []
    for pair, g in agg.items():
        n = len(g)
        if n < minseats:
            continue
        k = sum(x["hit"] for x in g)
        pays = [x["pay"] for x in g if x["hit"]]
        tot = sum(pays)
        od = sorted(x["odds"] for x in g)
        out.append((100.0 * tot / n, pair, n, k, tot,
                    100.0 * _ex(pays, 1) / n, 100.0 * _ex(pays, 3) / n,
                    od[len(od) // 2]))
    for rec, pair, n, k, tot, e1, e3, med in sorted(out, reverse=True):
        mark = "" if k >= 30 else "  ⚠판정불가"
        print("  %-12s %8d %6d %7.1f%% %7.1f%% %7.1f%% %8.1f배%s" %
              ("%s+%s" % (_LAB[pair[0]], _LAB[pair[1]]), n, k, rec, e1, e3, med, mark))


def main():
    pat = sys.argv[1] if len(sys.argv) > 1 else "2026_08_*"
    rows, st = collect(pat)
    print("  표본: %s" % pat)
    print("  " + " · ".join("%s %d" % (k, v) for k, v in st.items()))
    print("  ⚠ 정제: 확정÷마감 %.1f~%.1f · 구좌 %d" % (CLEAN_LO, CLEAN_HI, len(rows)))
    table(rows, "전체")
    for sp in ("경륜", "경마"):
        sub = [r for r in rows if r["sport"] == sp]
        if len(sub) >= 2000:
            table(sub, sp)


if __name__ == "__main__":
    main()
