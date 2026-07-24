# -*- coding: utf-8 -*-
"""[복기 도구] baseline·t2_freeze·t2_strong 3정책 비교 (전체 + 경륜분리). 읽기 전용.
   전체는 review_engine.replay_day 를, 경륜분리는 동일 판정을 경륜 파일에만 적용한 축소판.
   실행: py check_t2_strong.py [날짜...]  (기본 7/21·22·23)"""
import os
import re as _re
import sys
import io

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import review_engine as R

POLS = ("baseline", "t2_strong", "t2_strong_cycle")
_KRE = getattr(R, "_KEIRIN_ONLY_RE", None)


def _roi(p):
    return round(p["returned"] / p["invested"] * 100) if p["invested"] else 0


def _hitrate(p):
    return round(p["hits"] / p["judged"] * 100) if p["judged"] else 0


def _print_block(title, res):
    print(title)
    print("  %-11s %6s %6s %6s %8s %8s" % ("정책", "judged", "hits", "적중%", "ROI%", "unpaid"))
    print("  " + "-" * 52)
    for name in POLS:
        p = res["policies"][name]
        print("  %-11s %6d %6d %5d%% %7d%% %8d" %
              (name, p["judged"], p["hits"], _hitrate(p), _roi(p), p["unpaid"]))


def _freeze_sets_at(log_doc, min_mb):
    """replay_day 내부 _freeze_sets(min_mb) 재현 — (복승집합, 삼복승집합)."""
    _rhist = ((log_doc or {}).get("recommendation_history") or [])
    _live_h = [x for x in _rhist if not x.get("closed")]
    _close_ref = None
    for _e in _rhist:
        if _e.get("closed") and _e.get("time"):
            _close_ref = R._hms_sec(_e["time"])
            break
    if _close_ref is None and _live_h and _live_h[-1].get("time"):
        _close_ref = R._hms_sec(_live_h[-1]["time"])

    def _mb_of(_e):
        _mb = _e.get("minutes_before")
        if isinstance(_mb, (int, float)):
            return float(_mb)
        _ts = R._hms_sec(_e.get("time"))
        if _ts is not None and _close_ref is not None and _close_ref >= _ts:
            return (_close_ref - _ts) / 60.0
        return None

    _ef = None
    for _e in _live_h:
        _mb = _mb_of(_e)
        if _mb is not None and _mb >= min_mb:
            _ef = _e
    if _ef is None:
        _ef = _live_h[0] if _live_h else None
    if _ef is None:
        return None, None
    _qf = set()
    for _c in (_ef.get("quinellas") or []):
        _cc = _c.get("combo") or []
        if len(_cc) == 2:
            try:
                _qf.add(frozenset(int(x) for x in _cc))
            except (TypeError, ValueError):
                continue
    if not _qf and _ef.get("quinella_main"):
        try:
            _qf.add(frozenset(int(x) for x in str(_ef["quinella_main"]).split("+")))
        except (TypeError, ValueError):
            pass
    _tf = set()
    for _s3 in [_ef.get("trifecta_main")] + list(_ef.get("trifecta_ins") or [])[:1]:
        try:
            _p3 = frozenset(int(x) for x in str(_s3).split("+"))
            if len(_p3) == 3:
                _tf.add(_p3)
        except (TypeError, ValueError):
            continue
    return _qf, _tf


def _strong_horses(log_doc):
    """집중급락(말단위) + 급락류 30%+(조합 양말) → 강급락 말 집합. review_engine.t2_strong 과 동일 규칙."""
    out = set()
    for sg in ((log_doc or {}).get("signals_detected") or []):
        sty, sdt = str(sg.get("type") or ""), str(sg.get("detail") or "")
        if "집중급락" in sty:
            m = _re.search(r"(\d+)\s*번", sdt)
            if m:
                out.add(int(m.group(1)))
        elif "급락" in sty:
            mp = _re.search(r"-?(\d+(?:\.\d+)?)\s*%", sdt)
            if mp and float(mp.group(1)) >= 30.0:
                for a, b in _re.findall(r"(\d+)\s*\+\s*(\d+)", sdt):
                    out.add(int(a))
                    out.add(int(b))
    return out


def _replay_keirin_subset(date, stake=10000):
    """replay_day 판정을 경륜 파일에만 적용(baseline·t2_freeze·t2_strong)."""
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

        _book("baseline", win_q in disp_q, win_t in disp_t)
        _qf2, _tf2 = _freeze_sets_at(log_doc, 2)
        if _qf2 is None:
            _book("t2_strong", win_q in disp_q, win_t in disp_t)
            _book("t2_strong_cycle", win_q in disp_q, win_t in disp_t)
        else:
            strong_h = _strong_horses(log_doc)
            qf_s, tf_s = set(_qf2), set(_tf2 or set())
            if strong_h:
                for dc in disp_q:
                    if dc not in qf_s and (dc & strong_h):
                        qf_s.add(dc)
                for dc in disp_t:
                    if dc not in tf_s and (dc & strong_h):
                        tf_s.add(dc)
            _book("t2_strong", win_q in qf_s, win_t in tf_s)
            # 경륜 서브셋은 전부 cycle → t2_strong_cycle == t2_strong
            _book("t2_strong_cycle", win_q in qf_s, win_t in tf_s)
    return {"date": date, "policies": pol}


def main(dates):
    for d in dates:
        res = R.replay_day(date=d, stake=10000)
        _print_block(f"■ {d}  [전체]", res)
        print()
    print("=" * 56)
    print("경륜만 분리 집계 (raceKey 가 경륜 전용 지명인 파일만)")
    print("=" * 56)
    for d in dates:
        res = _replay_keirin_subset(d)
        _print_block(f"■ {d}  [경륜만]", res)
        print()


if __name__ == "__main__":
    args = sys.argv[1:]
    dates = args if args else ["2026-07-21", "2026-07-22", "2026-07-23"]
    main(dates)
