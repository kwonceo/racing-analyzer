# -*- coding: utf-8 -*-
"""[복기 도구] caught_then_lost 경륜 경주 중 t2_freeze(마감 2분 전 명단)였다면 살아났을 경주 대조.
   review_engine.replay_day 의 _freeze_sets(2) 로직을 그대로 복원(본 코드 무수정·읽기 전용).
   판정: 정답 복승(1·2착) 또는 삼복승(1·2·3착) 조합이 T-2 동결 명단에 포함되면 '살아남(REVIVE)'.
   실행: py check_t2_revive.py [날짜...]  (기본 7/23·22·21)"""
import os
import sys
import io

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import review_engine as re

RRD = re.RACE_RESULTS_DIR
ALD = re.ANALYSIS_LOG_DIR
_KRE = getattr(re, "_KEIRIN_ONLY_RE", None)


def _freeze_sets_at(log_doc, min_mb):
    """replay_day 내부 _freeze_sets(min_mb) 재현 — (복승집합, 삼복승집합) 반환."""
    _rhist = ((log_doc or {}).get("recommendation_history") or [])
    _live_h = [x for x in _rhist if not x.get("closed")]
    # 마감 기준시각(closed 행 시각, 없으면 마지막 라이브 이력 시각)
    _close_ref = None
    for _e in _rhist:
        if _e.get("closed") and _e.get("time"):
            _close_ref = re._hms_sec(_e["time"])
            break
    if _close_ref is None and _live_h and _live_h[-1].get("time"):
        _close_ref = re._hms_sec(_live_h[-1]["time"])

    def _mb_of(_e):
        _mb = _e.get("minutes_before")
        if isinstance(_mb, (int, float)):
            return float(_mb)
        _ts = re._hms_sec(_e.get("time"))
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


def _short(rk):
    for p in ("2026-07-21 ", "2026-07-22 ", "2026-07-23 "):
        rk = rk.replace(p, "")
    return rk


def main(dates):
    revive_rows = []   # caught_then_lost(baseline 미적중) → t2 명단엔 포함 = 부활
    loss_rows = []     # baseline 적중 → t2 명단엔 미포함 = 손실
    for d in dates:
        prefix = d.replace("-", "_")
        for fn in sorted(os.listdir(RRD) if os.path.isdir(RRD) else []):
            if not fn.startswith(prefix) or not fn.endswith(".json"):
                continue
            doc = re._load(os.path.join(RRD, fn))
            if not doc:
                continue
            log_doc = re._load(os.path.join(ALD, fn))
            c = re.classify_race(doc, log_doc)
            if not c:
                continue
            rk = str(doc.get("raceKey") or "")
            if re._sport_of(doc, rk, _KRE) != "cycle":
                continue
            typ = c.get("type")
            # 부활/손실 판정 모두 baseline 적중과 caught_then_lost 만 관심 대상
            if typ not in ("hit", "caught_then_lost"):
                continue
            top3 = c.get("top3", [])
            win_q = frozenset(top3[:2]) if len(top3) >= 2 else None
            win_t = frozenset(top3[:3]) if len(top3) >= 3 else None
            _qf, _tf = _freeze_sets_at(log_doc, 2)
            q_in = bool(win_q and _qf is not None and win_q in _qf)
            t_in = bool(win_t and _tf is not None and win_t in _tf)
            in_t2 = q_in or t_in
            rec = {
                "date": d, "rk": rk,
                "win_q": sorted(win_q) if win_q else None,
                "win_t": sorted(win_t) if win_t else None,
                "q_in": q_in, "t_in": t_in,
                "qf_n": (len(_qf) if _qf is not None else 0),
                "tf_n": (len(_tf) if _tf is not None else 0),
            }
            if typ == "caught_then_lost" and in_t2:
                revive_rows.append(rec)              # baseline 미적중 → t2 적중 = 순수 부활
            elif typ == "hit" and not in_t2:
                loss_rows.append(rec)                # baseline 적중 → t2 미적중 = 순수 손실

    def _print_table(title, rows):
        print(title)
        print("%-11s %-18s %-12s %-9s %-7s %-7s" %
              ("날짜", "경주키", "정답1-2착", "정답1-2-3", "복승T2", "삼복T2"))
        print("-" * 74)
        for r in rows:
            print("%-11s %-18s %-12s %-9s %-7s %-7s" % (
                r["date"], _short(r["rk"]),
                "+".join(str(x) for x in (r["win_q"] or [])),
                "+".join(str(x) for x in (r["win_t"] or [])),
                ("O" if r["q_in"] else "-") + f"({r['qf_n']})",
                ("O" if r["t_in"] else "-") + f"({r['tf_n']})"))
        print("-" * 74)

    _print_table("🟢 [부활] baseline 미적중(caught_then_lost) → t2_freeze 명단엔 정답 포함", revive_rows)
    print()
    _print_table("❌ [손실] baseline 적중 → t2_freeze 명단엔 정답 빠짐", loss_rows)
    print()
    print("=" * 74)
    print(f"대상 날짜: {', '.join(dates)}  (경륜만)")
    print(f"  🟢 부활: {len(revive_rows)}건")
    print(f"  ❌ 손실: {len(loss_rows)}건")
    print(f"  ➡️  순효과(부활-손실): {len(revive_rows) - len(loss_rows):+d}건")
    print("  · 복승T2/삼복T2 괄호숫자 = T-2 동결 명단의 복승/삼복승 조합 수")
    print("  · in_t2 = 정답 복승(1·2착) 또는 삼복승(1·2·3착)이 T-2 명단에 포함")


if __name__ == "__main__":
    args = sys.argv[1:]
    dates = args if args else ["2026-07-23", "2026-07-22"]   # 7/21 오염 제외
    main(dates)
