# -*- coding: utf-8 -*-
"""[반적중 · 전적 대조] 복승 2두 중 **1두만** 맞힌 경주를 전적표와 대조할 수 있게 데이터셋으로 만든다.

🔴 완전 읽기 전용이다. analysis_log · race_results · odds_history · kakao_sent 를 읽기만 하고
   logs/half_hit/ 에만 쓴다. 추천·판정·수집 어디에도 개입하지 않는다.

■ 무엇을 (2026-09-06 대표 지시)
  「복승 2마리 중 1마리만 맞췄던 경주에서 실패했던 경우를 살펴보고 전적표와 대조」
  ⇒ 경주마다 ⓐ 잡은 말(caught) ⓑ 놓친 짝(missed) ⓒ 우리가 잡은 말 옆에 붙인 엉뚱한 짝(wrong_partner)
     ⓓ 나머지 말을 **역할(role)** 로 표시하고, 말마다 T-5 시점 전적·시장·우리 순위를 한 행으로 편다.

■ 시간 축 (원칙 27)
  · 전적·우리 순위 : `frozen`(T-5 스냅샷 · snapPhase) 우선. 없으면 `horses` 를 쓰되 form_src="horses" 로 표시
  · 시장            : `odds_history` 스냅샷 중 minutes_before 가 5 에 가장 가까운 정상 틱(오염 표식 3종 제외)
  · 판정 명단       : corePicks.displayedCombos.quinellas (없으면 finalQuinellas 조합) · list_src 표시
  · 회원 수신 명단  : kakao_sent 의 T-5/T-7 sentQuinellas (같은 날짜 파일 · raceKey 일치)
  · 확정배당        : race_results.payouts.quinella (원칙 15) · payouts_approx 표시

■ 결손 (원칙 30)
  경주마다 전적 보유율(recentPlacings · last3fList 보유 말 비율)을 함께 적는다.
  역할별 비교를 할 때 보유율이 다른 무리끼리는 비교가 성립하지 않는다.

실행: python tools/measure_half_hit_form.py [--pattern 2026_0*] [--out logs/half_hit]
"""
import argparse
import collections
import glob
import gzip
import io
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AL = os.path.join(BASE, "data", "analysis_log")
RR = os.path.join(BASE, "data", "race_results")
OH = os.path.join(BASE, "data", "odds_history")
KS = os.path.join(BASE, "data", "kakao_sent")
BAD_TICK = ("odds_suspect", "baseline_reset", "next_race_blocked")


def load(p):
    for path, gz in ((p, False), (p + ".gz", True)):
        try:
            if gz:
                with gzip.open(path, "rt", encoding="utf-8") as f:
                    return json.load(f)
            with io.open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            continue
    return None


def _f(v):
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _i(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _ints(lst):
    # 🔴 [F1 · 2026-09-06 H0 검증] 경륜 recentPlacings 가 '8/28 ' 같은 **문자열**이면 글자 단위로 돌아
    #   [8,2,8] 이라는 가짜 착순이 됐다(경륜 말 행 45.0%). 문자열은 착순 배열이 아니다 → 빈 배열.
    if isinstance(lst, str) or not isinstance(lst, (list, tuple)):
        return []
    out = []
    for x in lst:
        v = _i(x)
        if v is not None:
            out.append(v)
    return out


def _floats(lst):
    if isinstance(lst, str) or not isinstance(lst, (list, tuple)):
        return []
    out = []
    for x in lst:
        v = _f(x)
        if v is not None:
            out.append(v)
    return out


def _pick(lst, idxs):
    """원본 배열에서 idxs 자리만 뽑는다(자리 밖이면 None). [F5] 취소·제외 슬롯(None) 정렬용."""
    if isinstance(lst, str) or not isinstance(lst, (list, tuple)):
        return [None] * len(idxs)
    return [lst[i] if i < len(lst) else None for i in idxs]


def _avg(xs):
    xs = [x for x in xs if x is not None]
    return (sum(xs) / len(xs)) if xs else None


def parse_rk(fname):
    """'2026_09_06_미즈사와_7경주' → (date '2026-09-06', track '미즈사와', race_no 7, raceKey '미즈사와 7경주')"""
    m = re.match(r"^(\d{4})_(\d{2})_(\d{2})_(.+)_(\d+)경주$", fname)
    if not m:
        return None
    date = "%s-%s-%s" % (m.group(1), m.group(2), m.group(3))
    return date, m.group(4), int(m.group(5)), "%s %s경주" % (m.group(4), m.group(5))


def qmap(sn):
    q = (sn or {}).get("quinella")
    if isinstance(q, list):
        q = {"+".join(str(z) for z in (it.get("combo") or [])): it.get("odds")
             for it in q if isinstance(it, dict)}
    out = {}
    for k, v in (q or {}).items():
        pr = [x for x in str(k).replace("-", "+").split("+") if x.strip().isdigit()]
        if len(pr) != 2 or pr[0] == pr[1]:
            continue
        o = _f(v.get("odds") if isinstance(v, dict) else v)
        # 🔴 [F7] kra_api 껍데기 배당(9999.9 · 356.2 반복)은 시장값이 아니다 → 9000 이상은 버린다
        if o and 0 < o < 9000:
            out[tuple(sorted((int(pr[0]), int(pr[1]))))] = o
    return out


def wmap(sn):
    out = {}
    for k, v in ((sn or {}).get("win") or {}).items():
        n = _i(k)
        o = _f(v.get("odds") if isinstance(v, dict) else v)
        if n is not None and o and 0 < o < 9000:
            out[n] = o
    return out


def pick_t5(od):
    """minutes_before 가 5 에 가장 가까운 정상 틱(마감 전). 없으면 None."""
    best = None
    for s in (od or {}).get("snapshots") or []:
        if not isinstance(s, dict):
            continue
        if any(s.get(b) for b in BAD_TICK) or s.get("after_close"):
            continue
        mb = _f(s.get("minutes_before"))
        if mb is None or mb < 0:
            continue
        d = abs(mb - 5)
        if best is None or d < best[0]:
            best = (d, s)
    return best[1] if best else None


def corner_pos(c):
    """'3-2' → (first=3, last=2). 파싱 실패면 (None, None)."""
    toks = [t for t in re.split(r"[-\s]+", str(c or "")) if t.strip().isdigit()]
    if not toks:
        return None, None
    return int(toks[0]), int(toks[-1])


def horse_features(h, ent, cur_dist):
    """전적 배열 → 파생값. 배열은 **최신이 맨 앞**(record_detail '최근 a-b-c' 순서와 같다 · H0 실증 971쌍).

    🔴 [F5 · 2026-09-06 H0 검증] recentPlacings 는 취소·제외(None)를 **뺀** 배열이고 corners·pastDistances·
      pastPops·fieldSizes·last3fList 는 그 슬롯을 **남긴** 배열이라 인덱스가 어긋났다(지방 3.2% 행).
      ⇒ None 슬롯이 있는 원본(pastPlacings)을 기준으로 자리를 맞춘다. 원본이 없으면 aligned=False 로 표시.
    """
    ent = ent or {}
    raw = None
    for cand in (h.get("pastPlacings"), ent.get("pastPlacings")):
        if isinstance(cand, (list, tuple)) and cand:
            raw = list(cand)
            break
    if raw is not None:
        idxs = [i for i, v in enumerate(raw) if _i(v) is not None]
        rp = [int(raw[i]) for i in idxs]
        fs = _ints(_pick(h.get("fieldSizes") or ent.get("fieldSizes"), idxs))
        l3 = _floats(_pick(h.get("last3fList") or ent.get("last3fList"), idxs))
        cn = _pick(h.get("corners") or ent.get("corners"), idxs)
        pd = _ints(_pick(h.get("pastDistances") or ent.get("pastDistances"), idxs))
        pops_raw = _pick(ent.get("pastPops"), idxs)
        pops = [_i(x) for x in pops_raw]
        aligned = True
    else:
        rp = _ints(h.get("recentPlacings"))
        fs = _ints(h.get("fieldSizes") or ent.get("fieldSizes"))
        l3 = _floats(h.get("last3fList") or ent.get("last3fList"))
        cn = list(h.get("corners") or ent.get("corners") or []) if not isinstance(h.get("corners") or ent.get("corners"), str) else []
        pd = _ints(h.get("pastDistances") or ent.get("pastDistances"))
        pops = [_i(x) for x in (ent.get("pastPops") or [])] if isinstance(ent.get("pastPops"), (list, tuple)) else []
        aligned = False
    # _ints 가 None 을 빼므로 fs·pd 는 길이가 줄 수 있다 → 자리 보존 버전
    fs_al = [_i(x) for x in (_pick(h.get("fieldSizes") or ent.get("fieldSizes"), idxs) if raw is not None else (h.get("fieldSizes") or ent.get("fieldSizes") or []))] if not isinstance(h.get("fieldSizes") or ent.get("fieldSizes"), str) else []
    pd_al = [_i(x) for x in (_pick(h.get("pastDistances") or ent.get("pastDistances"), idxs) if raw is not None else (h.get("pastDistances") or ent.get("pastDistances") or []))] if not isinstance(h.get("pastDistances") or ent.get("pastDistances"), str) else []
    l3_al = [_f(x) for x in (_pick(h.get("last3fList") or ent.get("last3fList"), idxs) if raw is not None else (h.get("last3fList") or ent.get("last3fList") or []))] if not isinstance(h.get("last3fList") or ent.get("last3fList"), str) else []
    fs, pd = fs_al, pd_al
    out = {"rp_aligned": aligned,
        "n_rp": len(rp), "rp1": rp[0] if rp else None, "rp2": rp[1] if len(rp) > 1 else None,
        "rp_avg3": _avg(rp[:3]) if rp else None, "rp_avg5": _avg(rp[:5]) if rp else None,
        "rp_best5": min(rp[:5]) if rp else None,
        "rp_top3_n5": sum(1 for x in rp[:5] if x <= 3) if rp else None,
        "rp_improving": (len(rp) >= 3 and rp[0] <= rp[1] <= rp[2] and (rp[2] - rp[0]) >= 2) if rp else None,
        "rp_repeat": (len(rp) >= 2 and rp[0] == rp[1] and 4 <= rp[0] <= 6) if rp else None,
    }
    # 두수 정규화 착순(0=1착 · 1=꼴찌)
    rel = []
    for i, p in enumerate(rp[:5]):
        f = fs[i] if i < len(fs) and fs[i] and fs[i] > 1 else None
        if f:
            rel.append((p - 1) / (f - 1))
    out["rp_rel_avg3"] = _avg(rel[:3]) if rel else None
    out["rp_rel1"] = rel[0] if rel else None
    # 상3F
    out["n_l3f"] = len(l3)
    out["l3f_last"] = l3[0] if l3 else None
    out["l3f_best3"] = min(l3[:3]) if l3 else None
    out["l3f_avg3"] = _avg(l3[:3]) if l3 else None
    # 코너
    firsts, lasts, gains, early_rel = [], [], [], []
    for i, c in enumerate(cn[:5]):
        a, b = corner_pos(c)
        if a is None:
            continue
        firsts.append(a)
        lasts.append(b)
        if i < len(rp):
            gains.append(b - rp[i])          # 마지막 코너 → 결승선에서 몇 두 올라왔나(+면 추월)
        f = fs[i] if i < len(fs) and fs[i] and fs[i] > 1 else None
        if f:
            early_rel.append((a - 1) / (f - 1))
    out["n_corner"] = len(firsts)
    out["corner_first_avg3"] = _avg(firsts[:3]) if firsts else None
    out["corner_first_rel_avg3"] = _avg(early_rel[:3]) if early_rel else None
    out["corner_last_avg3"] = _avg(lasts[:3]) if lasts else None
    out["stretch_gain_avg3"] = _avg(gains[:3]) if gains else None
    out["stretch_gain_last"] = gains[0] if gains else None
    # 거리
    pdv = [d for d in pd if d is not None]
    out["n_dist"] = len(pdv)
    if pdv and cur_dist:
        same = [i for i, d in enumerate(pd[:5]) if d == cur_dist and i < len(rp)]
        out["same_dist_n"] = len(same)
        out["same_dist_best"] = min(rp[i] for i in same) if same else None
        out["dist_diff_last"] = (pd[0] - cur_dist) if pd and pd[0] is not None else None
        out["dist_up"] = (pd[0] < cur_dist) if pd and pd[0] is not None else None
    else:
        out["same_dist_n"] = out["same_dist_best"] = out["dist_diff_last"] = out["dist_up"] = None
    # 인기 대비 착순(과거) — 자리 맞춘 (pop, placing) 쌍만
    pairs = [(pops[i], rp[i]) for i in range(min(len(pops), len(rp), 5)) if pops[i] is not None]
    out["n_pop"] = len(pairs)
    if pairs:
        out["beat_pop_n"] = sum(1 for p, r in pairs if r < p)
        out["beat_pop_last"] = pairs[0][1] < pairs[0][0]
        out["betray_n"] = sum(1 for p, r in pairs if p <= 3 and r >= 4)   # P4 기대배반
        out["pop_last"] = pairs[0][0]
        out["pop_avg3"] = _avg([p for p, _ in pairs[:3]])
        out["pop_minus_place_avg3"] = _avg([p - r for p, r in pairs[:3]])  # +면 인기보다 잘 달렸다
    else:
        out["beat_pop_n"] = out["beat_pop_last"] = out["betray_n"] = out["pop_last"] = out["pop_avg3"] = out["pop_minus_place_avg3"] = None
    # 마체중·부담
    # 🔴 [F4 · 2026-09-06 H0 검증] raw_profile.entries[].weight 는 **부담중량**(kg)이다. 마체중 폴백으로 쓰면
    #   지방 40% 행에서 55kg 이 마체중이 되고 burden_ratio 가 1.0 상수가 된다 → 폴백 제거 · 300kg 미만은 마체중이 아니다.
    bw = _f(h.get("bodyWeight"))
    if bw is not None and bw < 300:
        bw = None
    pbw = [x for x in _floats((ent or {}).get("pastBodyWeights")) if x >= 300]
    bu = _f(h.get("burdenWeight"))
    pbu = _floats((ent or {}).get("pastBurdens"))
    out["bodyWeight"] = bw
    out["bw_change"] = (bw - pbw[0]) if (bw and pbw) else None
    out["burdenWeight"] = bu
    out["burden_change"] = (bu - pbu[0]) if (bu and pbu) else None
    out["burden_ratio"] = (bu / bw) if (bu and bw) else None
    # 기수
    pj = list((ent or {}).get("pastJockeys") or [])
    jk = h.get("jockey")
    out["jockey"] = jk
    out["jockey_changed"] = (bool(pj) and bool(jk) and str(pj[0]).strip() != str(jk).strip()) if (pj and jk) else None
    out["jockeyRate"] = _f(h.get("jockeyRate"))
    out["jockeyDistRate"] = _f(h.get("jockeyDistRate"))
    # 등급(클래스) 변화 — 원문 그대로
    pg = list((ent or {}).get("pastGrades") or [])
    out["grade_last"] = pg[0] if pg else None
    out["grade_prev"] = pg[1] if len(pg) > 1 else None
    out["grade_changed"] = (pg[0] != pg[1]) if len(pg) > 1 else None
    # 각질·우리 점수
    out["gait"] = h.get("gait")
    out["styleType"] = (ent or {}).get("styleType")
    out["paceBonus"] = _f(h.get("paceBonus"))
    out["paceBonusBase"] = _f(h.get("paceBonusBase"))
    out["baseScore"] = _f(h.get("baseScore"))
    out["record_score"] = _f(h.get("record_score"))
    out["grade"] = h.get("grade")
    out["rank_field"] = _i(h.get("rank"))
    # [F2] horses[].odds 는 마감 후 사본의 값이다 → 따로 이름 붙여 두고, 전적 수집 시점 단승은 entries[].winOdds 만
    out["win_odds_postclose"] = _f(h.get("odds"))
    wf = _f((ent or {}).get("winOdds"))
    out["win_odds_form"] = wf if (wf is not None and 0 < wf < 9000) else None
    out["pop_form"] = _i((ent or {}).get("pop"))
    # 경륜
    kp = _ints(h.get("keirinPlacings"))
    out["kp_n"] = len(kp)
    out["kp1"] = kp[0] if kp else None
    out["kp_avg3"] = _avg(kp[:3]) if kp else None
    out["kp_top3_n"] = sum(1 for x in kp[:5] if x <= 3) if kp else None
    out["linePos"] = _i(h.get("linePos"))
    out["lineageNb"] = h.get("lineageNb")
    return out


def rank_within(rows, key, reverse=False):
    """같은 경주 안에서 key 값의 순위(1=가장 좋음). None 은 순위 없음."""
    vals = [(r[key], r["no"]) for r in rows if r.get(key) is not None]
    vals.sort(key=lambda x: x[0], reverse=reverse)
    pos = {}
    for i, (_, no) in enumerate(vals):
        pos[no] = i + 1
    for r in rows:
        r[key + "_rk"] = pos.get(r["no"])


def build(pattern, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    kakao_cache = {}
    races, horses = [], []
    skip = collections.Counter()
    files = sorted(glob.glob(os.path.join(AL, pattern + ".json")))
    for p in files:
        fname = os.path.basename(p)[:-5]
        pr = parse_rk(fname)
        if not pr:
            skip["bad_name"] += 1
            continue
        date, track, race_no, rk = pr
        d = load(p)
        if not d:
            skip["unreadable"] += 1
            continue
        sport = d.get("sport") or "?"
        cat = d.get("category") or "?"
        if sport not in ("horse", "cycle"):
            skip["sport_" + str(sport)] += 1
            continue
        res = d.get("result") or {}
        a1, a2, a3 = _i(res.get("1st")), _i(res.get("2nd")), _i(res.get("3rd"))
        if a1 is None or a2 is None:
            skip["no_result"] += 1
            continue
        ans = tuple(sorted((a1, a2)))
        cp = d.get("corePicks") or {}
        dc = (cp.get("displayedCombos") or {}).get("quinellas") or []
        list_src = "displayedCombos"
        if not dc:
            dc = [x.get("combo") for x in (cp.get("finalQuinellas") or []) if x.get("combo")]
            list_src = "finalQuinellas"
        judge = sorted({tuple(sorted(int(v) for v in c)) for c in dc if isinstance(c, (list, tuple)) and len(c) == 2})
        if not judge:
            skip["no_list"] += 1
            continue
        # 확정배당(원칙 15) — 🔴 [F3 · 2026-09-06 H0 검증] race_results.payouts.quinella 는 _apply_result_learning 이
        #   **배당판(final_odds)** 을 1순위로 쓴 값이라 approx=False 여도 확정이 아니다(지방 41.0% 가 analysis_log 와 다름).
        #   analysis_log.result.payouts 가 마감 후 oddspark 틱과 일치하는 백필 공식값이다 → measure_recovery 와 같은 출처를 1순위로.
        rr = load(os.path.join(RR, fname + ".json")) or {}
        pay_al = _f((res.get("payouts") or {}).get("quinella"))
        pay_rr = _f((rr.get("payouts") or {}).get("quinella"))
        if pay_al is not None:
            pay, pay_src = pay_al, "analysis_log"
        elif pay_rr is not None:
            pay, pay_src = pay_rr, "race_results"
        else:
            pay, pay_src = None, None
        approx = bool(rr.get("payouts_approx")) if rr else None
        pay_diff = (abs(pay_al - pay_rr) / pay_rr > 0.05) if (pay_al is not None and pay_rr) else None
        # 회원 수신(카톡 T-5)
        kf = os.path.join(KS, date.replace("-", "") + ".json")
        if kf not in kakao_cache:
            kk = load(kf) or []
            idx = {}
            for it in kk:
                if not isinstance(it, dict) or not it.get("kakaoOk"):
                    continue
                if it.get("phase") not in ("T-5", "T-7"):
                    continue
                sq = [tuple(sorted(int(v) for v in (x.get("combo") or []))) for x in (it.get("sentQuinellas") or it.get("quinellas") or []) if x.get("combo")]
                sq = [c for c in sq if len(c) == 2]
                if sq:
                    idx.setdefault(it.get("raceKey"), sq)
            kakao_cache[kf] = idx
        member = kakao_cache[kf].get(rk)
        # 시간 축 — 🔴 [F2·F8 · 2026-09-06 H0 검증] frozen.horses 는 horses 와 바이트 동일(마감 후 첫 저장 때 덮어쓴 것)이고
        #   frozen.keyHorses 는 recommendation_history 의 **마감 후(closed) 행**이다(지방 1,331/1,331 동일).
        #   전적 배열은 「오늘 경주 끼어듦」이 없어(971쌍 실증) 그대로 쓰되, **우리 순위는 마감 전 행**에서만 읽는다.
        fz = d.get("frozen") or {}
        hs = (fz.get("horses") if isinstance(fz, dict) and fz.get("horses") else None) or (d.get("horses") or [])
        form_src, snap = "horses(postclose_copy)", (fz.get("snapPhase") if isinstance(fz, dict) else None)
        kh, kh_src, kh_mb = None, None, None
        best = None
        for e in (d.get("recommendation_history") or []):
            mb = _f(e.get("minutes_before"))
            if mb is None or mb < 0 or not e.get("keyHorses"):
                continue
            dist = abs(mb - 5) if mb >= 1 else 100 + (1 - mb)     # 1분 이상 남은 행 중 5분에 가장 가까운 것 · 없으면 0분 행
            if best is None or dist < best[0]:
                best = (dist, mb, _ints(e.get("keyHorses")))
        if best:
            kh, kh_src, kh_mb = best[2], ("rec_hist_pre" if best[1] >= 1 else "rec_hist_mb0"), best[1]
        if not kh:
            kh, kh_src = _ints(fz.get("keyHorses")) if isinstance(fz, dict) and fz.get("keyHorses") else _ints(cp.get("keyHorses") or d.get("keyHorses")), "postclose"
        kh_post = _ints(fz.get("keyHorses")) if isinstance(fz, dict) and fz.get("keyHorses") else []
        # 우리 순위 시간선: 그 말이 keyHorses 에 있었던 mb 목록
        kh_timeline = collections.defaultdict(list)
        for e in (d.get("recommendation_history") or []):
            mb = e.get("minutes_before")
            for n in _ints(e.get("keyHorses")):
                kh_timeline[n].append(mb)
        # 시장 T-5
        od = load(os.path.join(OH, fname + ".json"))
        t5 = pick_t5(od)
        q5 = qmap(t5) if t5 else {}
        w5 = wmap(t5) if t5 else {}
        n_ticks = len([s for s in ((od or {}).get("snapshots") or []) if isinstance(s, dict)]) if od else 0
        q_rank = {c: i + 1 for i, (c, _) in enumerate(sorted(q5.items(), key=lambda kv: kv[1]))}
        w_rank = {n: i + 1 for i, (n, _) in enumerate(sorted(w5.items(), key=lambda kv: kv[1]))}
        rp = d.get("raw_profile") or {}
        cur_dist = _i(rp.get("distance")) if isinstance(rp, dict) else None
        ents = {}
        if isinstance(rp, dict):
            for e in (rp.get("entries") or []):
                n = _i((e or {}).get("no"))
                if n is not None:
                    ents[n] = e
        # 분류
        mine = {h for c in judge for h in c}
        if ans in judge:
            cls = "hit"
        elif (ans[0] in mine) != (ans[1] in mine):
            cls = "half"
        elif ans[0] in mine and ans[1] in mine:
            cls = "both_in_pair_missing"
        else:
            cls = "zero"
        cls_m = None
        if member:
            mm = {h for c in member for h in c}
            if ans in member:
                cls_m = "hit"
            elif (ans[0] in mm) != (ans[1] in mm):
                cls_m = "half"
            elif ans[0] in mm and ans[1] in mm:
                cls_m = "both_in_pair_missing"
            else:
                cls_m = "zero"
        caught = missed = None
        wrong = []
        if cls == "half":
            caught = ans[0] if ans[0] in mine else ans[1]
            missed = ans[1] if caught == ans[0] else ans[0]
            wrong = sorted({(c[0] if c[1] == caught else c[1]) for c in judge if caught in c})
        # 말 행
        rows = []
        for h in hs:
            n = _i(h.get("no"))
            if n is None:
                continue
            fe = horse_features(h, ents.get(n), cur_dist)
            fe.update({
                "rk": fname, "date": date, "sport": sport, "category": cat, "no": n, "name": h.get("name"),
                "ourRank": (kh.index(n) + 1) if n in kh else None,
                "ourRank_post": (kh_post.index(n) + 1) if n in kh_post else None,   # 마감 후 값 — 비교용. 입력에 쓰지 않는다
                "in_kh_mbs": kh_timeline.get(n) or [],
                "win_odds_t5": w5.get(n), "win_rank_t5": w_rank.get(n),
                "min_q_t5": min([o for c, o in q5.items() if n in c], default=None),
                "in_mine": n in mine,
                "role": ("caught" if n == caught else "missed" if n == missed else "wrong_partner" if n in wrong
                         else "other_pick" if n in mine else "other") if cls == "half"
                        else ("winner" if n in ans else "pick" if n in mine else "other"),
                "placed": (1 if n == a1 else 2 if n == a2 else 3 if n == a3 else None),
            })
            rows.append(fe)
        for key, rev in (("rp1", False), ("rp_avg3", False), ("rp_rel_avg3", False), ("l3f_best3", False), ("l3f_avg3", False),
                         ("stretch_gain_avg3", True), ("corner_first_rel_avg3", False), ("pop_minus_place_avg3", True),
                         ("beat_pop_n", True), ("jockeyRate", True), ("record_score", True), ("baseScore", True),
                         ("win_odds_t5", False), ("win_odds_form", False), ("min_q_t5", False), ("kp_avg3", False)):
            rank_within(rows, key, reverse=rev)
        n_h = len(rows)
        races.append({
            "rk": fname, "date": date, "track": track, "race_no": race_no, "raceKey": rk, "sport": sport, "category": cat,
            "cls": cls, "cls_member": cls_m, "ans": list(ans), "third": a3, "payout_q": pay, "payout_src": pay_src, "payout_approx": approx,
            "payout_rr": pay_rr, "payout_diff": pay_diff,
            "source": (rp.get("source") if isinstance(rp, dict) else None),   # [F7] 종목 판정은 category 가 아니라 이것·경기장 토큰으로
            "judge": [list(c) for c in judge], "list_src": list_src, "member": ([list(c) for c in member] if member else None),
            "caught": caught, "missed": missed, "wrong_partners": wrong,
            "n_horses": n_h, "n_entries": len(hs), "form_src": form_src, "snapPhase": snap,
            "kh": kh, "kh_src": kh_src, "kh_mb": kh_mb, "kh_post": kh_post,
            "distance": cur_dist, "surface": rp.get("surface") if isinstance(rp, dict) else None,
            "trackCond": rp.get("trackCond") if isinstance(rp, dict) else None,
            "shape": (d.get("raceShape") or {}).get("shape"), "leadCount": (d.get("raceShape") or {}).get("leadCount"),
            "n_ticks": n_ticks, "t5_mb": (t5 or {}).get("minutes_before"), "t5_src": (t5 or {}).get("src"),
            "ans_q_t5": q5.get(ans), "ans_q_rank_t5": q_rank.get(ans), "n_q_t5": len(q5),
            "judge_q_ranks_t5": [q_rank.get(c) for c in judge],
            "cov_rp": (sum(1 for r in rows if r["n_rp"] > 0) / n_h) if n_h else 0,
            "cov_l3f": (sum(1 for r in rows if r["n_l3f"] > 0) / n_h) if n_h else 0,
            "cov_pop": (sum(1 for r in rows if r["n_pop"] > 0) / n_h) if n_h else 0,
            "cov_corner": (sum(1 for r in rows if r["n_corner"] > 0) / n_h) if n_h else 0,
        })
        horses.extend(rows)
    with io.open(os.path.join(out_dir, "dataset_races.jsonl"), "w", encoding="utf-8") as f:
        for r in races:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with io.open(os.path.join(out_dir, "dataset_horses.jsonl"), "w", encoding="utf-8") as f:
        for r in horses:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return races, horses, skip, len(files)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pattern", default="2026_0*")
    ap.add_argument("--out", default=os.path.join(BASE, "logs", "half_hit"))
    a = ap.parse_args()
    races, horses, skip, nfiles = build(a.pattern, a.out)
    print("files %d · races %d · horse rows %d · skip %s" % (nfiles, len(races), len(horses), dict(skip)))
    by = collections.defaultdict(collections.Counter)
    for r in races:
        by[r["category"]][r["cls"]] += 1
        by[r["category"]]["_n"] += 1
        if r["cls_member"]:
            by[r["category"]]["member_" + r["cls_member"]] += 1
            by[r["category"]]["_member_n"] += 1
        if r["form_src"] == "frozen":
            by[r["category"]]["frozen"] += 1
        if r["ans_q_t5"] is not None:
            by[r["category"]]["t5_market"] += 1
        if r["payout_q"] is not None and r["payout_approx"] is False:
            by[r["category"]]["official_pay"] += 1
    for cat, c in sorted(by.items()):
        n = c["_n"]
        print("\n== %s == n %d | hit %d (%.1f%%) half %d (%.1f%%) both %d zero %d | frozen %.0f%% t5_market %.0f%% official_pay %.0f%%" % (
            cat, n, c["hit"], 100 * c["hit"] / n, c["half"], 100 * c["half"] / n, c["both_in_pair_missing"], c["zero"],
            100 * c["frozen"] / n, 100 * c["t5_market"] / n, 100 * c["official_pay"] / n))
        if c["_member_n"]:
            m = c["_member_n"]
            print("   회원 수신 명단 기준(/%d): hit %d (%.1f%%) half %d (%.1f%%) both %d zero %d" % (
                m, c["member_hit"], 100 * c["member_hit"] / m, c["member_half"], 100 * c["member_half"] / m,
                c["member_both_in_pair_missing"], c["member_zero"]))
    print("\n출력: %s" % a.out)


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    main()
