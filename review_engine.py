# -*- coding: utf-8 -*-
"""review_engine.py — 엄격 복기·학습 엔진 (복기학습 시스템 계획서 v4 [1][2] · 2026-07-22 D1)

원칙(계획서 대원칙 준수):
  · 판정은 라이브 기록만 — 분류는 race_results + analysis_log 의 '저장된 사실'만 읽는다(재분석 없음).
  · 기존 데이터 무수정 — 분류 결과는 data/review_stats.json 에만 기록(경주 파일은 읽기 전용).
  · 반증 가능 형태 — 각 분류에 근거(detail)를 남겨 사람이 검증할 수 있게 한다.

실패 유형(5) + 적중 세부:
  data_tainted      오염 구간 — 학습 제외
  no_signal_forced  신호 0인데 추천 노출 → 패스 기준 강화 재료
  caught_then_lost  정답 조합을 이력에서 잡았다가 최종에서 놓침 → 교체 로직 재료
  composition_miss  정답 말들을 신호·유력마로 알고 있었는데 조합 미편성 → 편성 로직 재료
  pure_upset        어떤 신호에도 없던 결과 — 교훈 없음(비율만 감시: 급증=커버리지 붕괴)
슬리피지: 표시(잠금) 시점 배당 vs 공식 확정배당 델타 — 전 판정 경주 기록(EV 마진 실측 재료).
"""
import json
import os
import re
import time

BASE = os.path.dirname(os.path.abspath(__file__))
RACE_RESULTS_DIR = os.path.join(BASE, "data", "race_results")
ANALYSIS_LOG_DIR = os.path.join(BASE, "data", "analysis_log")
REVIEW_STATS_FILE = os.path.join(BASE, "data", "review_stats.json")

TAINTED_WINDOWS = [("2026-07-19 17:00:00", "2026-07-20 08:55:00")]

REVIEW_TYPES = ("hit", "data_tainted", "no_signal_forced", "caught_then_lost",
                "composition_miss", "pure_upset", "near_structure")


def _load(path):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return None


def _combo_set(v):
    """'1+4' / [1,4] / '1-4' → frozenset({1,4})"""
    if v is None:
        return None
    if isinstance(v, (list, tuple)):
        nums = [int(x) for x in v if str(x).strip().lstrip("-").isdigit()]
    else:
        nums = [int(x) for x in re.split(r"[+\-]", str(v)) if str(x).strip().isdigit()]
    return frozenset(nums) if nums else None


def _displayed_sets(log_doc):
    """마감 동결 표시 조합(판정 대상) → (복승 set 목록, 삼복승 set 목록)."""
    cp = (log_doc or {}).get("corePicks") or {}
    dc = cp.get("displayedCombos") or {}
    qs = [_combo_set(c) for c in (dc.get("quinellas") or [])]
    ts = [_combo_set(c) for c in (dc.get("trifectas") or [])]
    if not qs and not ts:                        # 구데이터 폴백: finalQuinellas 전체 + 삼복승 상위 2
        qs = [_combo_set(q.get("combo")) for q in (cp.get("finalQuinellas") or [])]
        ts = [_combo_set(t.get("combo")) for t in (cp.get("finalTrifectas") or [])[:2]]
    return [q for q in qs if q], [t for t in ts if t]


def _signal_horses(log_doc):
    """이 경주에서 시스템이 '알고 있었던' 말 전부(유력마·복병·신호·확신도·고배당후보)."""
    if not log_doc:
        return set()
    out = set()
    for n in (log_doc.get("keyHorses") or []):
        try:
            out.add(int(n))
        except (TypeError, ValueError):
            pass
    cp = log_doc.get("corePicks") or {}
    for lst_key in ("finalQuinellas", "finalTrifectas", "bmedSpecial"):
        for c in (cp.get(lst_key) or []):
            s = _combo_set(c.get("combo"))
            if s:
                out |= s
    if cp.get("confTop1") is not None:
        try:
            out.add(int(cp["confTop1"]))
        except (TypeError, ValueError):
            pass
    for e in (log_doc.get("recommendation_history") or []):
        for k in ("quinella_main", "quinella_sub", "trifecta_main"):
            s = _combo_set(e.get(k))
            if s:
                out |= s
    return out


def _history_combos(log_doc):
    """추천 이력에 등장했던 복승·삼복승 조합 전부(시점 무관)."""
    qs, ts = set(), set()
    for e in ((log_doc or {}).get("recommendation_history") or []):
        for k in ("quinella_main", "quinella_sub"):
            s = _combo_set(e.get(k))
            if s and len(s) == 2:
                qs.add(s)
        s = _combo_set(e.get("trifecta_main"))
        if s and len(s) == 3:
            ts.add(s)
        for c in (e.get("quinellas") or []):
            s = _combo_set(c.get("combo"))
            if s and len(s) == 2:
                qs.add(s)
    return qs, ts


def _locked_quinella_odds(log_doc, combo_set):
    """표시(잠금) 시점의 해당 복승 배당 — rec_history 마지막 quinellas 스냅샷에서."""
    hist = ((log_doc or {}).get("recommendation_history") or [])
    for e in reversed(hist):
        for c in (e.get("quinellas") or []):
            if _combo_set(c.get("combo")) == combo_set and c.get("odds"):
                try:
                    return float(c["odds"])
                except (TypeError, ValueError):
                    pass
    return None


def _is_tainted(doc):
    if doc.get("tainted"):
        return True
    sa = str(doc.get("saved_at") or "")
    for a, b in TAINTED_WINDOWS:
        if a <= sa <= b:
            return True
    return False


def classify_race(doc, log_doc):
    """한 경주 분류. 반환 dict(type, hit, detail, slippage 등) — 저장은 호출부."""
    r = doc.get("result") or {}
    top3 = [r.get(k) for k in ("1st", "2nd", "3rd")]
    top3 = [int(x) for x in top3 if x is not None]
    if len(top3) < 2:
        return None
    win_q = frozenset(top3[:2])
    win_t = frozenset(top3[:3]) if len(top3) >= 3 else None
    disp_q, disp_t = _displayed_sets(log_doc)
    hit = (win_q in disp_q) or (win_t is not None and win_t in disp_t)
    out = {"top3": top3, "hit": bool(hit), "displayedQ": len(disp_q), "displayedT": len(disp_t)}

    # 슬리피지(적중 복승만: 잠금 vs 공식 확정)
    if win_q in disp_q:
        locked = _locked_quinella_odds(log_doc, win_q)
        official = ((doc.get("payouts") or {}).get("quinella"))
        if locked and official:
            out["slippage"] = {"locked": locked, "official": official,
                               "deltaPct": round((official - locked) / locked * 100, 1)}
    if _is_tainted(doc):
        out["type"] = "data_tainted"
        out["detail"] = "오염 구간 저장분 — 학습 제외"
        return out
    if hit:
        out["type"] = "hit"
        return out
    # ---- 미적중 유형 ----
    sig_cnt = 0
    try:
        sig_cnt = int(((log_doc or {}).get("strong_signals") or {}).get("count") or 0)
    except (TypeError, ValueError):
        pass
    known = _signal_horses(log_doc)
    hist_q, hist_t = _history_combos(log_doc)
    if (win_q in hist_q) or (win_t is not None and win_t in hist_t):
        out["type"] = "caught_then_lost"
        out["detail"] = "정답 조합이 추천 이력에 존재했으나 최종 표시에서 밀림"
    elif win_q and win_q <= known:
        out["type"] = "composition_miss"
        out["detail"] = "정답 1·2착 말을 전부 인지(신호·유력마)했으나 조합 미편성: %s" % sorted(win_q)
    elif sig_cnt == 0 and (disp_q or disp_t):
        out["type"] = "no_signal_forced"
        out["detail"] = "신호 0 경주에 추천 %d개 노출 — 패스 후보" % (len(disp_q) + len(disp_t))
    elif win_q and not (win_q & known):
        out["type"] = "pure_upset"
        out["detail"] = "정답 말이 어떤 신호·유력 목록에도 없음"
    else:
        out["type"] = "near_structure"
        out["detail"] = "정답 일부만 인지(%s) — 축/상대 선택 미스" % sorted(win_q & known)
    return out


def sweep(date=None):
    """[1] 하루치 일괄 분류 → review_stats.json 병합 저장. 반환 요약."""
    date = date or time.strftime("%Y-%m-%d")
    prefix = date.replace("-", "_")
    stats = _load(REVIEW_STATS_FILE) or {"cases": {}, "note": "복기 분류(계획서 v4 [1]) — 경주 파일 무수정·읽기 전용 분류"}
    by_type, slips = {}, []
    n = 0
    for fn in sorted(os.listdir(RACE_RESULTS_DIR) if os.path.isdir(RACE_RESULTS_DIR) else []):
        if not fn.startswith(prefix) or not fn.endswith(".json"):
            continue
        doc = _load(os.path.join(RACE_RESULTS_DIR, fn))
        if not doc:
            continue
        log_doc = _load(os.path.join(ANALYSIS_LOG_DIR, fn))
        c = classify_race(doc, log_doc)
        if not c:
            continue
        n += 1
        c["raceKey"] = doc.get("raceKey")
        c["date"] = date
        stats["cases"][fn] = c
        by_type[c["type"]] = by_type.get(c["type"], 0) + 1
        if c.get("slippage"):
            slips.append(c["slippage"]["deltaPct"])
    stats["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        tmp = REVIEW_STATS_FILE + ".tmp"
        json.dump(stats, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        os.replace(tmp, REVIEW_STATS_FILE)
    except Exception as e:
        print("[복기 분류] 저장 실패:", e)
    slip_avg = round(sum(slips) / len(slips), 1) if slips else None
    out = {"date": date, "classified": n, "byType": by_type,
           "slippageSamples": len(slips), "slippageAvgPct": slip_avg}
    # [급락 유형별 입상률 (2026-07-23 권대표 — "마감 급락마 입상 잘 된다" 검증)] 시점×강도 밴드별
    #   급락마 입상률 상설 집계 — 급락 가중치 튜닝의 실측 근거. 첫 실측(7/21-22 오염 포함 데이터):
    #   전구간 15%+ 급락 49.6%(기대 39.4% 대비 +10%p) · 마감구간만 31.2% · 50%+ 극단 12.5%(오염 의심).
    #   검역 가드 이후의 깨끗한 데이터로 매일 누적 → 주 단위로 밴드별 판정. 읽기 전용·판정/추천 무영향.
    try:
        out["dropStats"] = drop_stats(date)
    except Exception as e:
        out["dropStatsError"] = str(e)[:120]
    try:
        out["signalPlaceStats"] = signal_place_stats(date)
    except Exception as e:
        out["signalPlaceStatsError"] = str(e)[:120]
    return out


AI_TRAINING_DIR = os.path.join(BASE, "data", "ai_training")
TIMELINE_SNAP_DIR = os.path.join(BASE, "data", "timeline_snapshot")


def _hms_sec(s):
    try:
        h, m, sec = str(s).split(":")
        return int(h) * 3600 + int(m) * 60 + int(sec)
    except (ValueError, AttributeError):
        return None


def signal_place_stats(date=None):
    """[유력마 1위·급락 복병 입상률 (2026-07-23 권대표)] ①분석 유력마 1위(T-5경 이력 keyHorses[0])의
    입상률·1착률 ②T-5 스냅샷 복병(dark_horse 상위 2)의 입상률 — 무작위 기대와 비교. 읽기 전용."""
    date = date or time.strftime("%Y-%m-%d")
    prefix = date.replace("-", "_")
    k1 = {"n": 0, "placed": 0, "won": 0}
    k1f = {"n": 0, "placed": 0, "won": 0}
    dk = {"n": 0, "placed": 0}
    base_sum = base_n = 0
    for fn in sorted(os.listdir(RACE_RESULTS_DIR) if os.path.isdir(RACE_RESULTS_DIR) else []):
        if not fn.startswith(prefix) or not fn.endswith(".json"):
            continue
        doc = _load(os.path.join(RACE_RESULTS_DIR, fn))
        if not doc:
            continue
        r = doc.get("result") or {}
        top3 = {int(r[k]) for k in ("1st", "2nd", "3rd") if r.get(k) is not None}
        if len(top3) < 3:
            continue
        try:
            win1 = int(r.get("1st"))
        except (TypeError, ValueError):
            win1 = None
        n_h = doc.get("horse_count") or 0
        if n_h:
            base_sum += 3.0 / max(3, n_h)
            base_n += 1
        log_doc = _load(os.path.join(ANALYSIS_LOG_DIR, fn))
        _rh = [x for x in ((log_doc or {}).get("recommendation_history") or []) if not x.get("closed")]
        if _rh:
            _cr = _hms_sec(_rh[-1].get("time"))

            def _kh_at(min_mb):
                _pick = None
                for _e in _rh:
                    _mb = _e.get("minutes_before")
                    if not isinstance(_mb, (int, float)):
                        _ts = _hms_sec(_e.get("time"))
                        _mb = ((_cr - _ts) / 60.0) if (_ts is not None and _cr is not None and _cr >= _ts) else None
                    if _mb is not None and _mb >= min_mb:
                        _pick = _e
                _kh = (_pick or _rh[0]).get("keyHorses") or []
                try:
                    return int(_kh[0]) if _kh else None
                except (TypeError, ValueError):
                    return None
            _h5 = _kh_at(4.5)
            if _h5 is not None:
                k1["n"] += 1
                k1["placed"] += (_h5 in top3)
                k1["won"] += (_h5 == win1)
            _khf = (_rh[-1].get("keyHorses") or [])
            try:
                _hf = int(_khf[0]) if _khf else None
            except (TypeError, ValueError):
                _hf = None
            if _hf is not None:
                k1f["n"] += 1
                k1f["placed"] += (_hf in top3)
                k1f["won"] += (_hf == win1)
        # T-5 스냅샷 복병 (dark_horse 상위 2 · 스냅샷 doc 날짜 일치 시)
        _sn_fn = fn[len(prefix) + 1:]
        _sd = _load(os.path.join(TIMELINE_SNAP_DIR, _sn_fn))
        if _sd and _sd.get("date") == date:
            _t5s = (_sd.get("snapshots") or {}).get("T-5") or {}
            for _d in (_t5s.get("dark_horse") or [])[:2]:
                try:
                    dk["n"] += 1
                    dk["placed"] += (int(_d) in top3)
                except (TypeError, ValueError):
                    dk["n"] -= 1

    def _r(d, extra=None):
        o = {"n": d["n"], "placed": d["placed"],
             "rate": round(100.0 * d["placed"] / d["n"], 1) if d["n"] else None}
        if extra and d["n"]:
            o["winRate"] = round(100.0 * d["won"] / d["n"], 1)
        return o
    return {"date": date,
            "baselineRate": round(100.0 * base_sum / base_n, 1) if base_n else None,
            "key1_T5": _r(k1, True), "key1_final": _r(k1f, True), "darkT5": _r(dk)}


def drop_stats(date=None):
    """급락 시점(전구간/마감구간)×강도(15-30/30-50/50+%) 밴드별 급락마 입상률 집계.
    소스 = ai_training 타임라인(수집 배당 시계열)+결과. 말 대표배당 = 그 말 포함 복승 최저배당."""
    date = date or time.strftime("%Y-%m-%d")
    prefix = date.replace("-", "_")

    def _horse_min(qmap):
        m = {}
        for c, v in (qmap or {}).items():
            try:
                a, b = c.split("+")
                v = float(v)
                for x in (int(a), int(b)):
                    if v > 0 and (x not in m or v < m[x]):
                        m[x] = v
            except (ValueError, AttributeError):
                continue
        return m

    def _band(dr):
        return "15-30" if dr < 30 else ("30-50" if dr < 50 else "50+")

    races = 0
    base_sum = base_n = 0
    full = {"15-30": [0, 0], "30-50": [0, 0], "50+": [0, 0]}   # [입상, 표본]
    late = {"15-30": [0, 0], "30-50": [0, 0], "50+": [0, 0]}
    for fn in sorted(os.listdir(AI_TRAINING_DIR) if os.path.isdir(AI_TRAINING_DIR) else []):
        if not fn.startswith(prefix) or not fn.endswith(".json"):
            continue
        d = _load(os.path.join(AI_TRAINING_DIR, fn))
        if not d:
            continue
        res = d.get("result") or {}
        top3 = {res.get("1st"), res.get("2nd"), res.get("3rd")} - {None}
        tl = (d.get("odds_features") or {}).get("timeline") or []
        if len(top3) < 3 or len(tl) < 4:
            continue
        races += 1
        n_h = (d.get("race_info") or {}).get("horse_count") or 0
        if n_h:
            base_sum += 3.0 / max(3, n_h)
            base_n += 1
        e = _horse_min(tl[0].get("quinella"))
        mid = _horse_min(tl[max(0, len(tl) - 4)].get("quinella"))
        fin = _horse_min(tl[-1].get("quinella"))
        for h, fv in fin.items():
            ev = e.get(h)
            if ev and fv < ev:
                dr = (ev - fv) / ev * 100
                if dr >= 15:
                    b = full[_band(dr)]
                    b[1] += 1
                    b[0] += (h in top3)
            mv = mid.get(h)
            if mv and fv < mv:
                dr2 = (mv - fv) / mv * 100
                if dr2 >= 15:
                    b2 = late[_band(dr2)]
                    b2[1] += 1
                    b2[0] += (h in top3)

    def _fmt(t):
        return {k: {"n": v[1], "placed": v[0],
                    "rate": round(100.0 * v[0] / v[1], 1) if v[1] else None}
                for k, v in t.items()}
    return {"date": date, "races": races,
            "baselineRate": round(100.0 * base_sum / base_n, 1) if base_n else None,
            "fullDrop": _fmt(full), "lateDrop": _fmt(late)}


# ══════════ [2] 리플레이 — 정책별 가상 성적(전략 검증 1호) ══════════
def _sport_of(doc, rk, keirin_re):
    sp = str(doc.get("sport") or "").lower()
    if sp == "cycle" or (keirin_re and keirin_re.search(rk or "")):
        return "cycle"
    return "horse"


def _fix_connector_combos(log_doc, limit=None):
    """[fix_connectors 계열] favAxis 2두 × 연결마(keyHorses[2:] + confTop1) 복승 조합 집합.
    limit=None → 전체 연결마 / 1·2 → favAxis 페어 최저배당 오름차순 상위 N개만(조합 폭증 억제 변형).
    반환: 추가할 복승 frozenset 집합(disp 미포함). 연결마 없으면 빈 집합(미발동)."""
    cp = (log_doc or {}).get("corePicks") or {}
    fav = [int(x) for x in (cp.get("favAxis") or []) if str(x).strip().lstrip("-").isdigit()][:2]
    if len(fav) != 2:
        return set()
    conn = []
    for _x in ((log_doc or {}).get("keyHorses") or [])[2:]:      # keyHorses 3위 이하(전적 상위)
        if str(_x).strip().lstrip("-").isdigit():
            conn.append(int(_x))
    if cp.get("confTop1") is not None:                           # confTop1(확신도 1위)
        try:
            conn.append(int(cp["confTop1"]))
        except (TypeError, ValueError):
            pass
    conn = [c for c in dict.fromkeys(conn) if c not in fav]      # 중복·축 제거
    if not conn:
        return set()
    if limit is not None:
        # 연결마 랭킹: favAxis 페어 최저배당(=가장 인기) 오름차순. 배당 소스는 다중(방어).
        odds = {}
        _qo = cp.get("quinellaOdds")
        if isinstance(_qo, dict):
            for _k, _v in _qo.items():
                try:
                    _p = re.split(r"[+\-]", str(_k))
                    if isinstance(_v, (int, float)) and _v > 0:
                        odds.setdefault(frozenset((int(_p[0]), int(_p[1]))), float(_v))
                except (ValueError, IndexError):
                    continue
        for _src in ("finalQuinellas", "quinellaRef", "confQuinellas"):
            for _c in (cp.get(_src) or []):
                _cs = _combo_set(_c.get("combo"))
                _od = _c.get("odds")
                if _cs and isinstance(_od, (int, float)) and _od > 0:
                    odds.setdefault(_cs, float(_od))

        def _c_odds(_c):
            _cand = [odds.get(frozenset((fav[0], _c))), odds.get(frozenset((fav[1], _c)))]
            _cand = [o for o in _cand if o is not None]
            return min(_cand) if _cand else 9999.0
        conn = sorted(conn, key=_c_odds)[:limit]
    out = set()
    for _c in conn:
        out.add(frozenset((fav[0], _c)))
        out.add(frozenset((fav[1], _c)))
    return out


def replay_day(date=None, stake=10000, keirin_re=None):
    """[2] 하루치를 정책별로 재생 — displayedCombos·공식 확정배당 기준 가상 성적.
    정책:
      baseline        현행 그대로(표시=판정)
      signal_gate     경마는 신호(strong_signals)≥1 경주만 판정(무신호 경마=패스) — 전략①
      lowodds_trio    복승 최저배당이 임계(경마3·경륜1.8) 미만 경주는 삼복승만 판정 — 전략②
    반환 정책별 {judged, hits, invested, returned, roi}. 확정배당 없는 적중은 회수 제외(정직)."""
    date = date or time.strftime("%Y-%m-%d")
    prefix = date.replace("-", "_")
    pol = {p: {"judged": 0, "hits": 0, "invested": 0, "returned": 0.0, "unpaid": 0}
           for p in ("baseline", "signal_gate", "lowodds_trio", "t5_freeze", "t2_freeze", "t1_freeze",
                     "t2_strong", "t2_strong_cycle",
                     "fix_main_keep", "fix_axis2_trio", "fix_special_incl", "fix_conf_pair", "fix_backing_ev",
                     "fix_lowodds_exempt", "fix_connectors",
                     "fix_connectors_top1", "fix_connectors_top2")}
    for fn in sorted(os.listdir(RACE_RESULTS_DIR) if os.path.isdir(RACE_RESULTS_DIR) else []):
        if not fn.startswith(prefix) or not fn.endswith(".json"):
            continue
        doc = _load(os.path.join(RACE_RESULTS_DIR, fn))
        if not doc:
            continue
        log_doc = _load(os.path.join(ANALYSIS_LOG_DIR, fn))
        r = doc.get("result") or {}
        top3 = [r.get(k) for k in ("1st", "2nd", "3rd")]
        top3 = [int(x) for x in top3 if x is not None]
        if len(top3) < 3 or _is_tainted(doc):
            continue
        rk = str(doc.get("raceKey") or "")
        disp_q, disp_t = _displayed_sets(log_doc)
        if not disp_q and not disp_t:
            continue
        win_q, win_t = frozenset(top3[:2]), frozenset(top3[:3])
        po = doc.get("payouts") or {}
        q_hit, t_hit = (win_q in disp_q), (win_t in disp_t)
        sig_cnt = 0
        try:
            sig_cnt = int(((log_doc or {}).get("strong_signals") or {}).get("count") or 0)
        except (TypeError, ValueError):
            pass
        sport = _sport_of(doc, rk, keirin_re)
        # 복승 최저배당(잠금) — 마지막 이력 스냅샷 최저값
        min_q = None
        for e in reversed(((log_doc or {}).get("recommendation_history") or [])):
            odds = [c.get("odds") for c in (e.get("quinellas") or []) if c.get("odds")]
            if odds:
                min_q = min(float(o) for o in odds)
                break

        def _book(p, use_q=True, use_t=True, qh=None, th=None):
            _qh = q_hit if qh is None else qh
            _th = t_hit if th is None else th
            hit = (use_q and _qh) or (use_t and _th)
            pol[p]["judged"] += 1
            pol[p]["invested"] += stake
            if hit:
                pol[p]["hits"] += 1
                pay = po.get("quinella") if (use_q and _qh) else (po.get("trifecta") if _th else None)
                if use_t and _th and not (use_q and _qh):
                    pay = po.get("trifecta")
                if isinstance(pay, (int, float)) and pay > 0:
                    pol[p]["returned"] += pay * stake
                else:
                    pol[p]["unpaid"] += 1
        _book("baseline")
        # 전략③ T-5 동결 (2026-07-23 사세보 1R·소노다 2R 양방향 실증): T-5 시점(마감 5분+ 전 마지막
        #   이력)의 명단으로 판정 — "마감 2분 내 교체가 득이냐 실이냐"를 측정. 카톡 발송본과 근사 일치.
        #   삼복승은 표시 규칙과 동일하게 상위 2(메인+보험1)만. 이력 없으면 현행 명단과 동일 처리.
        # [동결 커브 (2026-07-23 소노다 4R "최소 1분은 남겨야")] T-5/T-2/T-1 세 동결 시점을 나란히 측정 —
        #   마감 직전 교체(12:09:54 유형)의 득실 곡선. 각 시점 = 그 시점 이전 마지막 이력 명단으로 판정.
        _rhist = ((log_doc or {}).get("recommendation_history") or [])
        _live_h = [x for x in _rhist if not x.get("closed")]
        # [mb 추정 보정 (2026-07-23)] minutes_before 결측 항목이 많아 T-5/T-2/T-1이 같은 항목으로 수렴 —
        #   마감 기준시각(closed 행 시각, 없으면 마지막 이력 시각)과의 차이로 mb 를 추정해 커브를 분해.
        _close_ref = None
        for _e in _rhist:
            if _e.get("closed") and _e.get("time"):
                _close_ref = _hms_sec(_e["time"])
                break
        if _close_ref is None and _live_h and _live_h[-1].get("time"):
            _close_ref = _hms_sec(_live_h[-1]["time"])

        def _mb_of(_e):
            _mb = _e.get("minutes_before")
            if isinstance(_mb, (int, float)):
                return float(_mb)
            _ts = _hms_sec(_e.get("time"))
            if _ts is not None and _close_ref is not None and _close_ref >= _ts:
                return (_close_ref - _ts) / 60.0
            return None

        def _freeze_sets(min_mb):
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
        for _mb_min, _pname in ((5, "t5_freeze"), (2, "t2_freeze"), (1, "t1_freeze")):
            _qf, _tf = _freeze_sets(_mb_min)
            if _qf is None:
                _book(_pname)
            else:
                _book(_pname, qh=(win_q in _qf), th=(win_t in _tf))
        # ══ [t2_strong (2026-07-24) — T-2 동결 + 마감 강급락 예외 편입] ══
        #   목표: t2_freeze 가 버린 '마감 직전 진짜 급락 신호' 조합만 되살린다(마쓰사카 10R 2+5 -30.3% 유형).
        #   정의: ⓐ T-2 동결 명단(_freeze_sets(2))을 기본으로 하되 ⓑ 그 이후 새로 편성된(=최종 표시엔 있으나
        #   T-2 명단엔 없는) 조합 중, '집중급락' 또는 '급락 30%+' 신호에 걸린 말을 포함한 조합만 예외로 추가한다.
        #   ⚠ 신호원은 strong_signals(대부분 count=0·미집계)가 아니라 실제 시점 신호 로그 signals_detected 를 사용.
        #   집합에 '추가만' 하므로(제거 없음) t2_strong 적중 ⊇ t2_freeze 적중 — 부활분은 항상 보존(교환손실 0).
        _qf2, _tf2 = _freeze_sets(2)
        if _qf2 is None:
            _book("t2_strong")                    # 라이브 이력 없음 → t2_freeze 와 동일(baseline 처리)
            # [t2_strong_cycle] 경륜만 t2_strong·경마는 baseline → 이력 없으면 양쪽 다 baseline 과 동일
            _book("t2_strong_cycle")
        else:
            # 강급락 말 집합: 집중급락(말 단위) + 급락류 30%+(조합 양 말)
            _strong_h = set()
            for _sg in ((log_doc or {}).get("signals_detected") or []):
                _sty = str(_sg.get("type") or "")
                _sdt = str(_sg.get("detail") or "")
                if "집중급락" in _sty:
                    _mh = re.search(r"(\d+)\s*번", _sdt)
                    if _mh:
                        try:
                            _strong_h.add(int(_mh.group(1)))
                        except ValueError:
                            pass
                elif "급락" in _sty:               # 급락·마감급락·급락속도 등
                    _mp = re.search(r"-?(\d+(?:\.\d+)?)\s*%", _sdt)
                    _pct = float(_mp.group(1)) if _mp else 0.0
                    if _pct >= 30.0:
                        for _a, _b in re.findall(r"(\d+)\s*\+\s*(\d+)", _sdt):
                            try:
                                _strong_h.add(int(_a))
                                _strong_h.add(int(_b))
                            except ValueError:
                                pass
            _qf_s, _tf_s = set(_qf2), set(_tf2 or set())
            if _strong_h:                          # 강급락 말 있을 때만 새 조합 예외 편입
                for _dc in disp_q:                 # 최종 표시 복승 중 T-2 명단에 없던 새 조합
                    if _dc not in _qf_s and (_dc & _strong_h):
                        _qf_s.add(_dc)
                for _dc in disp_t:                 # 최종 표시 삼복승도 동일 규칙
                    if _dc not in _tf_s and (_dc & _strong_h):
                        _tf_s.add(_dc)
            _book("t2_strong", qh=(win_q in _qf_s), th=(win_t in _tf_s))
            # ══ [t2_strong_cycle (2026-07-24) — 종목 게이트] ══
            #   경륜(cycle)만 t2_strong(동결+강급락 예외) 적용, 경마(horse)는 baseline 명단 그대로.
            #   근거: t2_strong 우위가 경륜에서만 뚜렷(경마는 동결이 오히려 손해인 날 존재) → 종목별 분리 적용.
            if sport == "cycle":
                _book("t2_strong_cycle", qh=(win_q in _qf_s), th=(win_t in _tf_s))
            else:
                _book("t2_strong_cycle")          # 경마 = baseline(현행 표시=판정)
        # ══ [수정안 검증 정책 4종 (2026-07-23 LOGIC_AUDIT — 본 코드 무수정·기록 재현)] ══
        _cp_r = (log_doc or {}).get("corePicks") or {}
        # ① fix_main_keep (모순1 원 메인 밀림): 이력상 최초 삼복승 원 메인이 판정 밖이면 추가(상위2→최대3)
        _t_keep = set(disp_t)
        try:
            for _e in _live_h:
                if _e.get("trifecta_main"):
                    _fm = frozenset(int(x) for x in str(_e["trifecta_main"]).split("+"))
                    if len(_fm) == 3:
                        _t_keep.add(_fm)
                    break
        except (TypeError, ValueError):
            pass
        _book("fix_main_keep", qh=q_hit, th=(win_t in _t_keep))
        # ② fix_axis2_trio (모순3 축 몰빵): 복승② 2두 + (복승③ 비겹침 1두 또는 확신도1위) 삼복승 추가
        _t_ax2 = set(disp_t)
        try:
            _last_e = _live_h[-1] if _live_h else None
            _qs_l = [c.get("combo") for c in ((_last_e or {}).get("quinellas") or []) if c.get("combo")]
            if len(_qs_l) >= 2:
                _q2 = [int(x) for x in _qs_l[1]]
                _extra = None
                if len(_qs_l) >= 3:
                    for x in _qs_l[2]:
                        if int(x) not in _q2:
                            _extra = int(x)
                            break
                if _extra is None and _cp_r.get("confTop1") is not None \
                        and int(_cp_r["confTop1"]) not in _q2:
                    _extra = int(_cp_r["confTop1"])
                if _extra is not None:
                    _c3x = frozenset(_q2 + [_extra])
                    if len(_c3x) == 3:
                        _t_ax2.add(_c3x)
        except (TypeError, ValueError):
            pass
        _book("fix_axis2_trio", qh=q_hit, th=(win_t in _t_ax2))
        # ③ fix_special_incl (모순2 고배당 복병 이동): 💎bmedSpecial 복승도 판정 포함했다면
        _q_sp = set(disp_q)
        try:
            for _c in (_cp_r.get("bmedSpecial") or []):
                _cc = _c.get("combo") or []
                if len(_cc) == 2:
                    _q_sp.add(frozenset(int(x) for x in _cc))
        except (TypeError, ValueError):
            pass
        _book("fix_special_incl", qh=(win_q in _q_sp), th=t_hit)
        # ④ fix_conf_pair (모순4 조합 단절): 확신도1위 + 💎첫 조합의 비-확신도 말 쌍 복승 추가
        _q_cp = set(disp_q)
        try:
            _ct = _cp_r.get("confTop1")
            _sp0 = ((_cp_r.get("bmedSpecial") or [{}])[0].get("combo")) or []
            if _ct is not None and _sp0:
                _ct = int(_ct)
                for x in _sp0:
                    if int(x) != _ct:
                        _q_cp.add(frozenset((_ct, int(x))))
                        break
        except (TypeError, ValueError):
            pass
        _book("fix_conf_pair", qh=(win_q in _q_cp), th=t_hit)
        # ⑤ fix_backing_ev (소노다 7R 3-1-8): EV 컷으로 quinellaRef 에 강등된 '유력마 받치기' 조합을
        #   판정에 포함했다면 — "유력마 받치기 보존 EV 면제" 수정안 검증 (LOGIC_AUDIT 후속).
        _q_bk = set(disp_q)
        try:
            for _c in (_cp_r.get("quinellaRef") or []):
                _cc = _c.get("combo") or []
                _rsn_b = str(_c.get("reason") or "")
                if len(_cc) == 2 and ("받치기" in _rsn_b or "유력마" in _rsn_b):
                    _q_bk.add(frozenset(int(x) for x in _cc))
        except (TypeError, ValueError):
            pass
        _book("fix_backing_ev", qh=(win_q in _q_bk), th=t_hit)
        # ⑥ fix_lowodds_exempt (2026-07-24 LOGIC_AUDIT — 정액 컷 면제): 카사마츠 4R 2+5(2.1배) 실증 —
        #   _apply_profit_strategy 의 '2.5배/3.0배 미만 메인 제외(수익성)' 정액 컷은 EV 필터와 달리 면제가
        #   전혀 없어 시장 1위·유력마 저배당 조합을 통째로 강등한다(quinellaRef 로 밀림). 이 정액 컷으로
        #   강등된 조합 중 아래 4조건 중 하나라도 해당하면 판정에 되살렸을 때의 성적을 측정:
        #     ⓐ 시장 최저배당 조합  ⓑ keyHorses 1·2위 조합  ⓒ confTop1 포함 조합  ⓓ 확신도(confQuinellas) 조합.
        #   집합에 '추가만' → 적중 ⊇ baseline(교환손실 0). 삼복승은 baseline 유지(복승만 대상).
        _q_le = set(disp_q)
        try:
            _refs_le = [r for r in (_cp_r.get("quinellaRef") or [])
                        if isinstance(r, dict) and r.get("combo")
                        and ("2.5배 미만" in str(r.get("refReason") or "")
                             or "3배 이상" in str(r.get("refReason") or ""))]
            if _refs_le:
                # 면제 재료
                _kh12 = frozenset(int(x) for x in (log_doc or {}).get("keyHorses", [])[:2]
                                  if str(x).strip().lstrip("-").isdigit())
                _ct1_le = _cp_r.get("confTop1")
                _conf_le = set()
                for _cq in (_cp_r.get("confQuinellas") or []):
                    _cs = _combo_set(_cq.get("combo"))
                    if _cs:
                        _conf_le.add(_cs)
                # 시장 최저 조합 = final+ref+conf 통합에서 배당 최저(odds 있는 것만)
                _allc = []
                for _src in ("finalQuinellas", "quinellaRef", "confQuinellas"):
                    for _c in (_cp_r.get(_src) or []):
                        _cs = _combo_set(_c.get("combo"))
                        _od = _c.get("odds")
                        if _cs and isinstance(_od, (int, float)) and _od > 0:
                            _allc.append((_cs, float(_od)))
                _mkt_low = min(_allc, key=lambda x: x[1])[0] if _allc else None
                for _r in _refs_le:
                    _rc = _combo_set(_r.get("combo"))
                    if not _rc:
                        continue
                    _exempt = ((_mkt_low is not None and _rc == _mkt_low)          # ⓐ 시장 최저
                               or (len(_kh12) == 2 and _rc == _kh12)               # ⓑ keyHorses 1·2위
                               or (_ct1_le is not None and int(_ct1_le) in _rc)    # ⓒ confTop1 포함
                               or (_rc in _conf_le))                                # ⓓ 확신도 조합
                    if _exempt:
                        _q_le.add(_rc)
        except (TypeError, ValueError):
            pass
        _book("fix_lowodds_exempt", qh=(win_q in _q_le), th=t_hit)
        # ⑦ fix_connectors (2026-07-24 LOGIC_AUDIT — composition_miss 회수): 정답 말을 신호·유력마로
        #   알고도 조합을 안 만들어 놓치던 유형(모순 #3·#4·#5) 대응. favAxis(시장 축) 2두를 '연결마'
        #   = keyHorses 3위 이하(전적 상위) + confTop1(확신도 1위) 와 각각 이어 복승을 추가한다
        #   (favAxis[0]×연결마, favAxis[1]×연결마). EV·정액 컷 무관하게 판정에 편입. 연결마 없으면 미발동.
        #   집합에 '추가만' → 적중 ⊇ baseline. 삼복승은 baseline 유지(복승만 대상).
        _q_cn = set(disp_q)
        try:
            _fav = [int(x) for x in (_cp_r.get("favAxis") or [])
                    if str(x).strip().lstrip("-").isdigit()][:2]
            if len(_fav) == 2:
                _conn = []
                for _x in ((log_doc or {}).get("keyHorses") or [])[2:]:   # keyHorses 3위 이하
                    if str(_x).strip().lstrip("-").isdigit():
                        _conn.append(int(_x))
                if _cp_r.get("confTop1") is not None:                     # confTop1(확신도 1위)
                    try:
                        _conn.append(int(_cp_r["confTop1"]))
                    except (TypeError, ValueError):
                        pass
                _conn = [c for c in dict.fromkeys(_conn) if c not in _fav]   # 중복·축 제거
                for _c in _conn:
                    _q_cn.add(frozenset((_fav[0], _c)))
                    _q_cn.add(frozenset((_fav[1], _c)))
        except (TypeError, ValueError):
            pass
        _book("fix_connectors", qh=(win_q in _q_cn), th=t_hit)
        # ⑦-b/c fix_connectors_top1·top2 (연결마 상한 변형 — 조합 폭증 억제): 연결마를 favAxis 페어
        #   최저배당 오름차순 상위 1개(top1·최대 2조합)·상위 2개(top2·최대 4조합)만 사용. 나머지 규칙 동일.
        _book("fix_connectors_top1",
              qh=(win_q in (set(disp_q) | _fix_connector_combos(log_doc, 1))), th=t_hit)
        _book("fix_connectors_top2",
              qh=(win_q in (set(disp_q) | _fix_connector_combos(log_doc, 2))), th=t_hit)
        # 전략① 경마 신호 게이트: 경마 & 신호 0 → 패스
        if not (sport == "horse" and sig_cnt == 0):
            _book("signal_gate")
        # 전략② 저배당 삼복승 집중: 임계 미만이면 삼복승만 판정
        thr = 1.8 if sport == "cycle" else 3.0
        if min_q is not None and min_q < thr:
            _book("lowodds_trio", use_q=False, use_t=True)
        else:
            _book("lowodds_trio")
    for p, s in pol.items():
        s["roi"] = round(s["returned"] / s["invested"] * 100) if s["invested"] else None
        s["hitRate"] = round(s["hits"] / s["judged"] * 100) if s["judged"] else 0
        s["returned"] = int(s["returned"])
    return {"date": date, "stake": stake, "policies": pol}
