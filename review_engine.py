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
    return out


AI_TRAINING_DIR = os.path.join(BASE, "data", "ai_training")


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
           for p in ("baseline", "signal_gate", "lowodds_trio")}
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

        def _book(p, use_q=True, use_t=True):
            hit = (use_q and q_hit) or (use_t and t_hit)
            pol[p]["judged"] += 1
            pol[p]["invested"] += stake
            if hit:
                pol[p]["hits"] += 1
                pay = po.get("quinella") if (use_q and q_hit) else (po.get("trifecta") if t_hit else None)
                if use_t and t_hit and not (use_q and q_hit):
                    pay = po.get("trifecta")
                if isinstance(pay, (int, float)) and pay > 0:
                    pol[p]["returned"] += pay * stake
                else:
                    pol[p]["unpaid"] += 1
        _book("baseline")
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
