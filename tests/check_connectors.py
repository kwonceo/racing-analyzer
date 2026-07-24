# -*- coding: utf-8 -*-
"""[복기 도구] baseline vs fix_connectors 비교 + composition_miss 회수 건수. 읽기 전용.
   실행: py tests/check_connectors.py [날짜...]  (기본 7/21·22·23·24)"""
import os
import sys
import io

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import review_engine as R

POLS = ("baseline", "fix_connectors")


def _roi(p):
    return round(p["returned"] / p["invested"] * 100) if p["invested"] else 0


def _hr(p):
    return round(p["hits"] / p["judged"] * 100) if p["judged"] else 0


def _connector_set(log_doc):
    """review_engine fix_connectors 와 동일하게 favAxis×연결마 복승 집합을 만든다(disp_q 제외분만)."""
    cp = (log_doc or {}).get("corePicks") or {}
    add = set()
    fav = [int(x) for x in (cp.get("favAxis") or []) if str(x).strip().lstrip("-").isdigit()][:2]
    if len(fav) != 2:
        return add
    conn = []
    for x in ((log_doc or {}).get("keyHorses") or [])[2:]:
        if str(x).strip().lstrip("-").isdigit():
            conn.append(int(x))
    if cp.get("confTop1") is not None:
        try:
            conn.append(int(cp["confTop1"]))
        except (TypeError, ValueError):
            pass
    conn = [c for c in dict.fromkeys(conn) if c not in fav]
    for c in conn:
        add.add(frozenset((fav[0], c)))
        add.add(frozenset((fav[1], c)))
    return add


def main(dates):
    # 전체 비교 (review_engine.replay_day)
    for d in dates:
        res = R.replay_day(date=d, stake=10000)
        b, f = res["policies"]["baseline"], res["policies"]["fix_connectors"]
        print(f"■ {d} [전체]  baseline hits={b['hits']}({_hr(b)}%) roi={_roi(b)}%  "
              f"→ fix_connectors hits={f['hits']}({_hr(f)}%) roi={_roi(f)}%  순증 {f['hits']-b['hits']:+d}")
    print()

    # composition_miss 회수 분석 (파일 단위 재분류)
    print("=" * 66)
    print("composition_miss 회수 분석 (정답 말 알고도 미편성 → 연결마로 복원되는가)")
    print("=" * 66)
    RRD, ALD = R.RACE_RESULTS_DIR, R.ANALYSIS_LOG_DIR
    g_cm = g_rec = 0
    for d in dates:
        prefix = d.replace("-", "_")
        cm = rec = 0
        rec_list = []
        for fn in sorted(os.listdir(RRD) if os.path.isdir(RRD) else []):
            if not fn.startswith(prefix) or not fn.endswith(".json"):
                continue
            doc = R._load(os.path.join(RRD, fn))
            if not doc:
                continue
            log_doc = R._load(os.path.join(ALD, fn))
            c = R.classify_race(doc, log_doc)
            if not c or c.get("type") != "composition_miss":
                continue
            cm += 1
            top3 = c.get("top3", [])
            win_q = frozenset(top3[:2]) if len(top3) >= 2 else None
            add = _connector_set(log_doc)
            if win_q and win_q in add:
                rec += 1
                rec_list.append((str(doc.get("raceKey") or ""), sorted(win_q)))
        g_cm += cm
        g_rec += rec
        print(f"■ {d}  composition_miss {cm}건 중 fix_connectors 회수: {rec}건")
        for rk, wq in rec_list:
            print(f"    회수: {rk} 정답1-2착 {wq}")
    print("-" * 66)
    print(f"합계: composition_miss {g_cm}건 중 {g_rec}건 회수 "
          f"({round(g_rec / g_cm * 100) if g_cm else 0}%)")


if __name__ == "__main__":
    args = sys.argv[1:]
    main(args if args else ["2026-07-21", "2026-07-22", "2026-07-23", "2026-07-24"])
