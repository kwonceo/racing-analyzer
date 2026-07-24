# -*- coding: utf-8 -*-
"""[복기 도구] baseline vs fix_lowodds_exempt 비교 (전체 + 경륜분리). 읽기 전용.
   전체는 review_engine.replay_day, 경륜분리는 경륜 파일만 별도 재집계.
   실행: py tests/check_lowodds_exempt.py [날짜...]  (기본 7/21·22·23)"""
import os
import sys
import io

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import review_engine as R

POLS = ("baseline", "fix_lowodds_exempt")
_KRE = getattr(R, "_KEIRIN_ONLY_RE", None)


def _roi(p):
    return round(p["returned"] / p["invested"] * 100) if p["invested"] else 0


def _hr(p):
    return round(p["hits"] / p["judged"] * 100) if p["judged"] else 0


def _blk(title, res):
    print(title)
    print("  %-20s %6s %6s %6s %8s %8s" % ("정책", "judged", "hits", "적중%", "ROI%", "unpaid"))
    print("  " + "-" * 60)
    for name in POLS:
        p = res["policies"][name]
        print("  %-20s %6d %6d %5d%% %7s%% %8d" %
              (name, p["judged"], p["hits"], _hr(p),
               (_roi(p) if p["invested"] else "-"), p["unpaid"]))
    b, f = res["policies"]["baseline"], res["policies"]["fix_lowodds_exempt"]
    print("  → 순증 hits: %+d" % (f["hits"] - b["hits"]))


def _keirin_subset(date, stake=10000):
    """경륜 파일만 review_engine 판정 로직 재현(baseline·fix_lowodds_exempt)."""
    prefix = date.replace("-", "_")
    pol = {p: {"judged": 0, "hits": 0, "invested": 0, "returned": 0.0, "unpaid": 0} for p in POLS}
    RRD, ALD = R.RACE_RESULTS_DIR, R.ANALYSIS_LOG_DIR
    for fn in sorted(os.listdir(RRD) if os.path.isdir(RRD) else []):
        if not fn.startswith(prefix) or not fn.endswith(".json"):
            continue
        doc = R._load(os.path.join(RRD, fn))
        if not doc:
            continue
        rk = str(doc.get("raceKey") or "")
        if R._sport_of(doc, rk, _KRE) != "cycle":
            continue
        log_doc = R._load(os.path.join(ALD, fn))
        r = doc.get("result") or {}
        top3 = [r.get(k) for k in ("1st", "2nd", "3rd")]
        top3 = [int(x) for x in top3 if x is not None]
        if len(top3) < 3 or R._is_tainted(doc):
            continue
        disp_q, disp_t = R._displayed_sets(log_doc)
        if not disp_q and not disp_t:
            continue
        win_q, win_t = frozenset(top3[:2]), frozenset(top3[:3])
        po = doc.get("payouts") or {}
        q_hit, t_hit = (win_q in disp_q), (win_t in disp_t)
        cp = (log_doc or {}).get("corePicks") or {}

        def _book(pname, qh, th):
            hit = qh or th
            pol[pname]["judged"] += 1
            pol[pname]["invested"] += stake
            if hit:
                pol[pname]["hits"] += 1
                pay = po.get("quinella") if qh else po.get("trifecta")
                if th and not qh:
                    pay = po.get("trifecta")
                if isinstance(pay, (int, float)) and pay > 0:
                    pol[pname]["returned"] += pay * stake
                else:
                    pol[pname]["unpaid"] += 1

        _book("baseline", q_hit, t_hit)
        # fix_lowodds_exempt (review_engine 과 동일 규칙)
        _q_le = set(disp_q)
        try:
            _refs = [x for x in (cp.get("quinellaRef") or [])
                     if isinstance(x, dict) and x.get("combo")
                     and ("2.5배 미만" in str(x.get("refReason") or "")
                          or "3배 이상" in str(x.get("refReason") or ""))]
            if _refs:
                _kh12 = frozenset(int(x) for x in (log_doc or {}).get("keyHorses", [])[:2]
                                  if str(x).strip().lstrip("-").isdigit())
                _ct1 = cp.get("confTop1")
                _conf = set()
                for _cq in (cp.get("confQuinellas") or []):
                    _cs = R._combo_set(_cq.get("combo"))
                    if _cs:
                        _conf.add(_cs)
                _allc = []
                for _src in ("finalQuinellas", "quinellaRef", "confQuinellas"):
                    for _c in (cp.get(_src) or []):
                        _cs = R._combo_set(_c.get("combo"))
                        _od = _c.get("odds")
                        if _cs and isinstance(_od, (int, float)) and _od > 0:
                            _allc.append((_cs, float(_od)))
                _mkt = min(_allc, key=lambda x: x[1])[0] if _allc else None
                for _r in _refs:
                    _rc = R._combo_set(_r.get("combo"))
                    if not _rc:
                        continue
                    if ((_mkt is not None and _rc == _mkt)
                            or (len(_kh12) == 2 and _rc == _kh12)
                            or (_ct1 is not None and int(_ct1) in _rc)
                            or (_rc in _conf)):
                        _q_le.add(_rc)
        except (TypeError, ValueError):
            pass
        _book("fix_lowodds_exempt", win_q in _q_le, t_hit)
    return {"date": date, "policies": pol}


def main(dates):
    for d in dates:
        _blk(f"■ {d}  [전체]", R.replay_day(date=d, stake=10000))
        print()
    print("=" * 62)
    print("경륜만 분리")
    print("=" * 62)
    for d in dates:
        _blk(f"■ {d}  [경륜만]", _keirin_subset(d))
        print()


if __name__ == "__main__":
    args = sys.argv[1:]
    main(args if args else ["2026-07-21", "2026-07-22", "2026-07-23"])
