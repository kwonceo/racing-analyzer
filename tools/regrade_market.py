# -*- coding: utf-8 -*-
"""[시장 대조군 소급 재채점 (2026-07-31)]

🔴 왜: `_market_top3` 가 **단승(`win`)만** 보는데 경륜은 그 값이 항상 빈 dict 라
   `market_hit_count` 가 통째로 `None` 이었다. **예측 검증의 축이 비어 있었다.**
   실측: 7월 스냅샷 보유 104경주 중 `win/single` 34.6% · `quinella` **100%**.
   ⇒ 복승 내재확률로 대체했고, **이미 채점된 건을 소급 재계산**한다.

⚠ 예측 필드는 건드리지 않는다 — `grading.market_*` 만 채운다.
⚠ 마감 전 스냅샷만 쓴다(마감 후 배당 사용 금지) — `_market_top3` 안에 제약이 있다.
⚠ `--dry` 가 기본. 실제 기록은 `--apply`.

사용:
  python tools/regrade_market.py            # 미리보기
  python tools/regrade_market.py --apply
"""
import argparse
import glob
import json
import os
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
import gemini_forecast as G          # noqa: E402


def _hist_for(rk, ymd=None):
    """raceKey → odds_history 문서(스냅샷·마감시각).

    🔴 [2026-07-31] 종전에는 날짜 없이 `*_<경주>.json` 로 훑어 **가장 오래된 동명 파일**
      (스냅샷 0개인 과거분)을 먼저 잡았다 — 아오모리 1경주가 07-25 파일로 매칭돼
      `market_top3` 가 None 이 됐다. **예측 파일의 날짜를 우선 사용**한다.
      같은 경주명이 여러 날 존재하는 것은 정상이므로 날짜가 없으면 스냅샷이 있는 최신본을 쓴다.
    """
    slug = rk.replace(" ", "_")
    # ⚠ 여기서는 후보를 모으기만 한다 — **바로 아래에서 `ymd`(날짜)로 좁힌다**(원칙 16).
    #   날짜 없이 이 목록을 그대로 쓰면 다른 날 데이터가 섞인다(2026-07-31 실사고).
    _pat = "*_%s.json" % slug          # date-filtered below
    cands = sorted(glob.glob(os.path.join(BASE, "data", "odds_history", _pat)))
    if ymd:
        pref = "%s_%s_%s_" % (ymd[:4], ymd[4:6], ymd[6:8])
        exact = [p for p in cands if os.path.basename(p).startswith(pref)]
        if exact:
            cands = exact
    best = None
    for p in reversed(cands):                  # 최신 날짜부터
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        if d.get("snapshots") and d.get("deadline_epoch"):
            return d                           # 스냅샷·마감시각을 갖춘 것 우선
        best = best or d
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    files = sorted(glob.glob(os.path.join(BASE, "logs", "forecast", "*.json")))
    rows, skipped = [], 0
    for f in files:
        try:
            doc = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        g = doc.get("grading")
        if not doc.get("graded") or not isinstance(g, dict):
            skipped += 1
            continue
        if g.get("market_hit_count") is not None:
            skipped += 1                      # 이미 채워짐 → 멱등
            continue
        rk = doc.get("raceKey") or ""
        # 예측 파일명이 `YYYYMMDD_<경주>.json` 이므로 앞 8자리가 개최일이다.
        _ymd = os.path.basename(f)[:8]
        h = _hist_for(rk, _ymd if _ymd.isdigit() else None)
        if not h:
            rows.append({"rk": rk, "path": f, "note": "odds_history 없음"})
            continue
        mt3, mb = G._market_top3(h.get("snapshots"), h.get("deadline_epoch"))
        src = G._market_src(h.get("snapshots"), h.get("deadline_epoch"))
        actual = g.get("actual") or []
        mh = len([n for n in (mt3 or []) if n in actual]) if mt3 else None
        rows.append({"rk": rk, "path": f, "market_top3": mt3, "market_hit": mh,
                     "mb": mb, "src": src, "gemini_hit": g.get("hit_count"),
                     "actual": actual})
    print("=" * 78)
    print("시장 대조군 소급 재채점  %s" % ("🔴 --apply" if a.apply else "🟢 --dry(기본)"))
    print("=" * 78)
    print("⚠ 분모 = 예측 파일 %d건 · 채점완료+미보유 대상 %d건 · 건너뜀 %d건"
          % (len(files), len(rows), skipped))
    done = 0
    for r in rows:
        if r.get("note"):
            print("  %-18s %s" % (r["rk"], r["note"]))
            continue
        print("  %-18s 시장 %s (%s/3, T%s분, %s) ↔ Gemini %s/3 · 실제 %s"
              % (r["rk"], r["market_top3"], r["market_hit"], r["mb"], r["src"],
                 r["gemini_hit"], r["actual"]))
        if a.apply and r["market_top3"]:
            doc = json.load(open(r["path"], encoding="utf-8"))
            doc["grading"]["market_top3"] = r["market_top3"]
            doc["grading"]["market_hit_count"] = r["market_hit"]
            doc["grading"]["market_snapshot_mb"] = r["mb"]
            doc["grading"]["market_src"] = r["src"]
            doc["grading"]["marketRegradedAt"] = time.strftime("%Y-%m-%d %H:%M:%S")
            tmp = r["path"] + ".tmp%d" % os.getpid()
            json.dump(doc, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            os.replace(tmp, r["path"])
            done += 1
    if a.apply:
        print("\n재채점 %d건 기록" % done)
    else:
        print("\n🟢 --dry 이므로 파일을 건드리지 않았다. 기록하려면 --apply")
    return 0


if __name__ == "__main__":
    sys.exit(main())
