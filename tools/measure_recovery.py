# -*- coding: utf-8 -*-
"""[회수율 측정 — 유일한 창구 (2026-07-31 신설)]

🔴 **앞으로 회수율 측정은 이 파일로만 한다. 세션 중 즉석 코드 금지.**

왜: 2026-07-31 에 세션 중 즉석으로 짠 측정 코드가 **날짜 없이 파일을 매칭**해
   A일 확정배당과 B일 배당판을 짝지었다. 그 결과 모든 회수율이 **+10~25%p 부풀려졌고**
   "시장 3두 전조합 99.7%" 같은 잘못된 결론이 나왔다.
   즉석 코드는 파일로 남지 않아 전수 점검에도 안 잡힌다.

■ 코드에 박아 둔 공통 규칙 (바꾸려면 CLAUDE.md 를 먼저 고칠 것)
  ① 🔴 **날짜 필수 매칭** — `analysis_log` 파일에서 파생한 경로만 쓴다(같은 파일명 → 같은 날).
  ② 🔴 **확정배당 기준** — `result.payouts.quinella`. 배당판은 "근사"로만.
  ③ 🔴 **분모 명시** — 전체 / 정제(괴리 0.5~2.0배) 둘 다 출력.
  ④ 🔴 **상위1·3건 제외 자동** — 극단값 의존을 항상 드러낸다.
  ⑤ 🔴 **95% 신뢰구간 자동**(부트스트랩) — 판정 가능 여부를 숫자로.
  ⑥ 🔴 **판정선 = 환급률 74.5%** — 100% 가 아니다(그게 무작위 수준이므로).

사용:
  python tools/measure_recovery.py                 # 전체 안
  python tools/measure_recovery.py --sport cycle
  python tools/measure_recovery.py --json
"""
import argparse
import collections
import glob
import gzip
import itertools
import json
import os
import random
import re
import statistics
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAYBACK = 74.5          # 🔴 판정선 = 경륜 복승 실측 환급률(Σ1/배당 중앙 1.341)
CLEAN_LO, CLEAN_HI = 0.5, 2.0   # 확정/배당판 괴리 정제 범위
BOOT_N = 2000


def _loadh(base):
    """odds_history 로드. ⚠ `base` 는 analysis_log 경로에서 파생 — 날짜가 이미 포함돼 있다."""
    for p in (base, base + ".gz"):
        if os.path.exists(p):
            try:
                if p.endswith(".gz"):
                    return json.load(gzip.open(p, "rt", encoding="utf-8"))
                return json.load(open(p, encoding="utf-8"))
            except Exception:
                return None
    return None


# ── 🔴 [2026-08-02] 인기 기저선(`--base market`) ─────────────────────────────────
#   왜: 지금까지 무작위 기대를 **3 ÷ 두수** 로 썼다. 그건 **모든 말을 동등하게** 본다.
#   실측은 전혀 다르다(13~16두 1인기 58.6% ↔ 16인기 0.6%). ⇒ 복병 배수는 과소평가,
#   인기 상위 신호는 과대평가돼 왔다.
#   🔴 **말마다 자기 인기순위의 기저선을 쓴다.** 경주 단위로 뭉뚱그리면 배수가 부풀려진다.
#   🔴 **경마(JRA result.html) 실측이다 — 경륜에 쓰지 않는다**(인기 개념·두수 구조가 다르다).
POPBASE_FILE = os.path.join(BASE, "data", "simulation_db", "pop_baseline.json")
_PB_CACHE = None


def _popbase():
    global _PB_CACHE
    if _PB_CACHE is None:
        try:
            d = json.load(open(POPBASE_FILE, encoding="utf-8"))
            _PB_CACHE = d if d.get("verified") else {}   # ⚠ 검산 미통과분은 쓰지 않는다
        except Exception:
            _PB_CACHE = {}
    return _PB_CACHE


def _pband(n):
    """build_popbase.band() 와 **같은 구간**이어야 한다(다르면 셀이 어긋난다)."""
    return "≤8두" if n <= 8 else ("9~12두" if n <= 12 else ("13~16두" if n <= 16 else "17두+"))


def _base_market(nh, pop):
    """그 말 (두수구간, 인기) 셀의 **실측 3착률(%)**. 셀이 없으면 None — 추측하지 않는다."""
    pb = _popbase()
    if not pb or not nh or not pop:
        return None
    c = (pb.get("cells") or {}).get("%s|%d" % (_pband(int(nh)), min(int(pop), 18)))
    return float(c["in3"]) if c else None


def _pop_map(d, alog_path):
    """→ ({마번: 인기순위}, 출처). 🔴 못 구하면 **빈 dict** — 없는 값을 만들지 않는다."""
    m = {}
    for e in ((d.get("raw_profile") or {}).get("entries") or []):
        try:
            if e.get("pop") and e.get("no") is not None:
                m[int(e["no"])] = int(e["pop"])
        except Exception:
            pass
    if m:
        return m, "raw_profile.pop"                       # 🟢 진짜 인기 표기
    h = _loadh(alog_path.replace("analysis_log", "odds_history"))
    sn = [s for s in ((h or {}).get("snapshots") or []) if s.get("t")]
    dl = (h or {}).get("deadline_epoch")
    if dl:
        sn = [s for s in sn if (s["t"] - dl) / 60 <= 0] or sn
    if not sn:
        return {}, "없음"
    last = max(sn, key=lambda x: x["t"])
    try:                                                   # 🟡 단승 배당순 = 사실상 인기순위
        w = {int(k): float(v) for k, v in (last.get("win") or {}).items()}
        if w:
            return {no: i + 1 for i, (no, _o) in enumerate(sorted(w.items(), key=lambda x: x[1]))}, "단승배당순"
    except Exception:
        pass
    best = {}                                              # 🔴 대용: 말별 최저 복승 배당순
    for k, v in (last.get("quinella") or {}).items():
        try:
            nos = [int(x) for x in str(k).replace("-", "+").split("+")][:2]
            ov = float(v)
        except Exception:
            continue
        for n in nos:
            if n not in best or ov < best[n]:
                best[n] = ov
    if best:
        return {no: i + 1 for i, (no, _o) in enumerate(sorted(best.items(), key=lambda x: x[1]))}, "복승최저순(대용)"
    return {}, "없음"


def load_races(sport="cycle", pattern="2026_07_*"):
    """🔴 날짜 안전: analysis_log 파일 하나에서 odds_history 경로를 **파생**한다."""
    out = []
    for f in sorted(glob.glob(os.path.join(BASE, "data", "analysis_log", pattern + ".json"))):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        if (d.get("sport") or "") != sport:
            continue
        res = d.get("result") or {}
        po = (res.get("payouts") or {}).get("quinella")
        if not res.get("1st") or not po:
            continue                                   # ② 확정배당 기준
        cp = d.get("corePicks") or {}
        dc = [sorted(c) for c in ((cp.get("displayedCombos") or {}).get("quinellas") or [])]
        kh = [int(x) for x in (d.get("keyHorses") or [])][:3]
        if not dc or len(kh) < 3:
            continue
        h = _loadh(f.replace("analysis_log", "odds_history"))
        dl = (h or {}).get("deadline_epoch")
        if not (h and dl):
            continue
        sn = [s for s in (h.get("snapshots") or [])
              if s.get("t") and -8 <= (s["t"] - dl) / 60 <= 0 and s.get("quinella")]
        if not sn:
            continue
        q = {}
        for k, v in max(sn, key=lambda x: x["t"])["quinella"].items():
            try:
                q[tuple(sorted(int(x) for x in str(k).replace("-", "+").split("+")))] = float(v)
            except Exception:
                pass
        # 🔴 [2026-08-01] 1착·2착이 **둘 다** 있어야 한다. 한쪽만 있으면 정렬에서 죽는다
        #   (오늘 부분 착순 레코드가 들어와 도구가 통째로 크래시했다 — 조용히 넘기지 말고 건너뛴다).
        if res.get("1st") is None or res.get("2nd") is None:
            continue
        top2 = sorted({res.get("1st"), res.get("2nd")})
        mo = q.get(tuple(top2))
        if not mo:
            continue
        m = re.match(r"(\d{4}_\d{2}_\d{2})", os.path.basename(f))
        # 🔴 [2026-08-07 신설] 표시 **삼복승**도 싣는다. 우라와 6R(결과 2-10 · 복승 25배)에서
        #   2번과 10번이 **서로 다른 조합**에 각각 들어 있었는데 2+10 은 어디에도 없었다.
        #   "등장한 마번끼리의 짝"을 재려면 복승만으로는 분모가 모자란다.
        dt = [sorted(c) for c in ((cp.get("displayedCombos") or {}).get("trifectas") or [])]
        out.append({"q": q, "po": float(po), "mo": float(mo), "top2": top2, "dc": dc, "dt": dt, "kh": kh,
                    "bm": [sorted(x.get("combo") or [])
                           for x in (cp.get("bmedSpecial") or []) if x.get("combo")],
                    # 🔴 [2026-08-01] `quinellaRef` = **만들었다가 강등된 조합**(EV 미달·베팅규칙).
                    #   오비히로 5R 에서 정답 복승 `3+10`(18.9배)이 **ev 0.73 으로 여기 있었다.**
                    #   "생성 후 취소"를 재려면 이 목록이 필요하다 — 최종 추천만 봐서는 안 보인다.
                    "ref": [sorted(x.get("combo") or [])
                            for x in (cp.get("quinellaRef") or []) if x.get("combo")],
                    # 🔴 [2026-08-01 신설 · --ev-sweep] 강등분을 **EV 값과 함께** 싣는다.
                    #   ⚠ `ev` 보유는 강등분의 **59.5%** 뿐이다(나머지는 저배당 컷 등 다른 사유).
                    #     EV 임계 스윕은 **ev 보유분만** 대상으로 한다 — 없는 것을 0 으로 치면
                    #     임계를 아무리 낮춰도 안 들어와야 할 것이 들어온다.
                    "refev": [(sorted(x.get("combo")), float(x.get("ev")))
                              for x in (cp.get("quinellaRef") or [])
                              if x.get("combo") and x.get("ev") is not None],
                    # ev 가 없는 강등분(저배당 컷 등) — 스윕 대상이 아님을 밝히기 위해 건수만 싣는다.
                    "refnoev": len([x for x in (cp.get("quinellaRef") or [])
                                    if x.get("combo") and x.get("ev") is None]),
                    # 🔴 [2026-08-01 신설 · 정렬 리플레이] **정렬 전 후보 풀**.
                    #   `finalQuinellas`(채택) + `quinellaRef`(강등) 를 합친다.
                    #   ⚠ 🔴 **`_main_cand` 와 완전히 같지는 않다** — EV·저배당 컷 **이전** 단계에서
                    #     잘린 조합은 어디에도 저장되지 않는다(원칙 3: 재현 못 한 부분은 명시한다).
                    #     ⇒ 이 측정은 **"최종 후보 안에서 순서만 바꾸면"** 에 대한 답이다.
                    "pool": [{"c": sorted(x.get("combo")), "ev": x.get("ev"),
                              "odds": x.get("odds"), "stars": x.get("stars") or 0}
                             for x in ((cp.get("finalQuinellas") or []) + (cp.get("quinellaRef") or []))
                             if x.get("combo") and len(x.get("combo")) == 2],
                    # 🔴 [2026-08-01] `darkHorsePicks` = **복병 목록**(유력마와 다른 목록이다).
                    #   코치 4R 에서 7번이 복병 1순위·확신도 1위·축이었는데도
                    #   유력마 10번과의 조합 `7+10`(확정 37.7배)이 **어느 목록에도 없었다.**
                    #   ⇒ "둘 다 봤는데 못 산다"를 재려면 이 목록이 필요하다.
                    "dk": [int(x.get("no")) for x in (cp.get("darkHorsePicks") or [])
                           if x.get("no") is not None],
                    "hs": [x for x in (d.get("horses") or []) if x.get("no") is not None],
                    # 🔴 [2026-08-07] 확신도 측정용. 8/7 경륜 3경주에서 확신도 1위가 전부 3착 밖이었다
                    #   (기후 7R 44점 · 와카야마 1R 74.4점 · 도요하시 1R 39.1점).
                    #   화면에 점수를 띄우는데 성적과 무관하면 회원을 오도한다 → 배수로 잰다.
                    "conf1": cp.get("confTop1"),
                    "confv": (cp.get("confidence") if isinstance(cp.get("confidence"), (int, float))
                              else (cp.get("confidenceTop") if isinstance(cp.get("confidenceTop"), (int, float)) else None)),
                    "top3": sorted({res.get("1st"), res.get("2nd"), res.get("3rd")} - {None}),
                    "nh": len(d.get("horses") or []),
                    # 🔴 [2026-08-02] bmedSpecial 조건부 편입 측정용. 동결값 우선(마감 시점 신호).
                    "sig": int((((d.get("frozen") or {}).get("strong_signals")
                                 or d.get("strong_signals") or {}).get("count")) or 0),
                    "day": m.group(1) if m else "?"})
    return out


def _allc(l):
    return [list(c) for c in itertools.combinations(sorted(l), 2)]


def _mkt3(r):
    im = {}
    for k, o in r["q"].items():
        if o > 0:
            for x in k:
                im[x] = im.get(x, 0) + 1.0 / o
    return [x for x, _ in sorted(im.items(), key=lambda y: -y[1])][:3]


def _pace(r, sign):
    return [int(h["no"]) for h in sorted(
        r["hs"], key=lambda h: -((h.get("paceBonusBase") or 0) + sign * (h.get("paceBonus") or 0)))][:3]


def _sort_pool(r, key, rev=True):
    """[정렬 리플레이 (2026-08-01 신설)] 후보 풀을 `key` 로 정렬해 **현행과 같은 개수**만 뽑는다.

    🔴 **구좌가 같아야 정렬 비교가 성립한다.** 개수가 달라지면 정렬이 아니라 개수 변경이 섞인다.
    ⚠ 값이 없는 항목은 맨 뒤로 보낸다(정렬에서 유리해지지 않게).
    ⚠ 같은 조합이 채택·강등 양쪽에 있으면 dedupe 한다(calc 가 set 으로 다시 거른다).
    """
    k = len(r["dc"])
    if k <= 0:
        return []
    pool = r.get("pool") or []
    if not pool:
        return r["dc"]
    seen, uniq = set(), []
    for x in pool:
        t = tuple(x["c"])
        if t in seen:
            continue
        seen.add(t)
        uniq.append(x)
    neg = float("-inf")
    uniq.sort(key=lambda x: (x.get(key) if x.get(key) is not None else neg), reverse=rev)
    return [x["c"] for x in uniq[:k]]


def measure_pattern(sport="horse", pattern="2026_0*", tag="P2_우상향"):
    """[2026-08-06] 패턴 말의 3착 진입률 ↔ 같은 인기대 기저선 대비 **배수** · 대조군(비패턴)과 나란히.
    🔴 회수율의 **선행 지표**(대표 지시): 배수 1.0 이하면 어떤 조합으로 사도 소용없다.
    🔴 답은 절대값이 아니라 **패턴군 배수 − 대조군 배수**다(대조군 없이는 P2 값어치를 못 말한다).
    🔴 경마만 pop_baseline(VALID) 사용. 경륜은 기저선이 없어 **무작위(3÷평균두수)** 로 임시 표기(명시).
    ⚠ 확정배당이 아니라 **3착 진입**만 본다 — 배수는 회수율이 아니다(회수율은 별도 3단 조건).
    ⚠ 패턴 파서는 build_review 를 재사용한다(목록 이중화 금지).
    """
    import glob as _g
    sys.path.insert(0, os.path.join(BASE, "tools"))
    import build_review as _R
    G = {"pat": {"n": 0, "in3": 0, "bsum": 0.0, "bn": 0, "nhsum": 0, "nhn": 0},
         "ctl": {"n": 0, "in3": 0, "bsum": 0.0, "bn": 0, "nhsum": 0, "nhn": 0}}
    for f in sorted(_g.glob(os.path.join(BASE, "data", "analysis_log", pattern + ".json"))):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        if (d.get("sport") or "") != sport:
            continue
        res = d.get("result") or {}
        if not (res.get("1st") and res.get("2nd") and res.get("3rd")):
            continue                                   # 3착까지 있어야 3착 진입 판정
        top3 = set()
        for k in ("1st", "2nd", "3rd"):
            try:
                top3.add(int(res.get(k)))
            except (TypeError, ValueError):
                pass
        ent = [e for e in ((d.get("raw_profile") or {}).get("entries") or []) if isinstance(e, dict)]
        nh = len(ent) or (d.get("corePicks") or {}).get("raceHorseCount")
        popmap, _src = _pop_map(d, f)
        for e in ent:
            try:
                no = int(e.get("no"))
            except (TypeError, ValueError):
                continue
            ispat = tag in _R._pattern_tags(e.get("recent") or e.get("pastPlacings"), e.get("prev1"))
            g = G["pat"] if ispat else G["ctl"]
            g["n"] += 1
            if no in top3:
                g["in3"] += 1
            # 🔴 pop_baseline 은 경마(JRA) 실측이다 — 경륜에 쓰지 않는다(_base_market 원칙).
            #   경륜 인기·두수가 우연히 셀에 매칭돼 값이 나오면 **틀린 기저**다 → 경마만 조회한다.
            b = _base_market(nh, popmap.get(no)) if sport == "horse" else None
            if b is not None:
                g["bsum"] += b
                g["bn"] += 1
            if nh:
                g["nhsum"] += int(nh)
                g["nhn"] += 1
    return G, sport


def report_pattern(G, sport, tag):
    print("  종목 %s · 패턴 %s   🔴 배수 = 3착률 ÷ 기저 · 답은 두 군의 차이" % (sport, tag))

    def _one(g, lab):
        n = g["n"]
        if n == 0:
            print("    %s n=0" % lab)
            return None
        r3 = 100.0 * g["in3"] / n
        if g["bn"] >= 1:
            base = g["bsum"] / g["bn"]
            bsrc = "인기기저(pop_baseline · 셀보유 %d/%d)" % (g["bn"], n)
        else:
            avgnh = g["nhsum"] / g["nhn"] if g["nhn"] else 0
            base = 300.0 / avgnh if avgnh else 0
            bsrc = "🔴무작위(3÷평균두수 %.1f · 기저선없음)" % avgnh
        mult = r3 / base if base else 0
        flag = " ⚠판정불가(n<30)" if n < 30 else (" (방향만·n<200)" if n < 200 else "")
        print("    %s n=%d · 3착률 %.1f%% · 기저 %.1f%% · 배수 %.2f%s  [%s]"
              % (lab, n, r3, base, mult, flag, bsrc))
        return mult

    mp = _one(G["pat"], "패턴  ")
    mc = _one(G["ctl"], "대조군")
    if mp is not None and mc is not None:
        print("    🔴 배수 차이(패턴 − 대조군) = %+.2f  %s"
              % (mp - mc, "→ P2에 값어치 있음" if mp - mc > 0.05
                 else ("→ 차이 없음(P2 무의미)" if abs(mp - mc) <= 0.05 else "→ P2가 오히려 나쁨")))


def measure_trio_dark(sport="cycle", pattern="2026_0*"):
    """[2026-08-02 신설] **축 2두(유력마 1·2위) + 복병 1두** 삼복승 성적.

    🔴 착안: 복병 1순위의 3착 이내 배수는 1.27(경륜·n=622)로 실재하는데
      복승(1·2착) 기여는 약하다 ⇒ **복병은 삼복승 재료일 수 있다**(대표 가설).
    ⚠ 적중 = 조합이 top3 집합과 일치. 배당 = `trio` 우선(없으면 `trifecta`) — 마권 혼재 정정 규칙 준수.
    ⚠ 경마 삼복승은 **섀도우 유지 중**이라 실전 반영 불가(측정만).
    """
    import glob as _g
    rows = []
    for f in sorted(_g.glob(os.path.join(BASE, "data", "analysis_log", pattern + ".json"))):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        if (d.get("sport") or "") != sport:
            continue
        res = d.get("result") or {}
        pay = res.get("payouts") or {}
        po = pay.get("trio") if pay.get("trio") is not None else pay.get("trifecta")
        t3 = [res.get("1st"), res.get("2nd"), res.get("3rd")]
        if not po or any(x is None for x in t3):
            continue
        cp = d.get("corePicks") or {}
        kh = [int(x) for x in (d.get("keyHorses") or [])][:2]
        dk = [int(x.get("no")) for x in (cp.get("darkHorsePicks") or []) if x.get("no") is not None]
        if len(kh) < 2 or not dk:
            continue
        rows.append({"kh": kh, "dk": dk, "po": float(po),
                     "top3": sorted(int(x) for x in t3)})
    return rows


def report_trio_dark(rows, label, drank=1):
    sel = []
    for r in rows:
        ds = [r["dk"][0]] if drank == 1 else r["dk"][:3]
        for x in ds:
            if x in r["kh"]:
                continue
            sel.append((sorted(r["kh"] + [x]) == r["top3"], r["po"]))
    n = len(sel)
    if not n:
        print("  %-18s n=0 판정 불가" % label)
        return
    hits = sorted([p for ok, p in sel if ok], reverse=True)
    tot = sum(hits)
    rr = 100.0 * tot / n
    ex1 = 100.0 * sum(hits[1:]) / max(n - 1, 1)
    ex3 = 100.0 * sum(hits[3:]) / max(n - 3, 1)
    med = statistics.median(hits) if hits else 0
    print("  %-18s 구좌 %4d · 적중 %3d · 회수율 %6.1f%% · 1제외 %6.1f%% · 3제외 %6.1f%% · 배당중앙 %.1f배 %s"
          % (label, n, len(hits), rr, ex1, ex3, med, "⚠n<30" if n < 30 else ""))


def _bm_cross(r, drank=None):
    """bmedSpecial 중 **한 말은 유력마 · 다른 말은 복병**인 조합만.

    drank=1 이면 복병 1순위만 · 23 이면 복병 2~3순위만(`darkHorsePicks` 순서 = 순위).
    ⚠ 두 말이 모두 유력마이거나 모두 복병이면 '교차'가 아니다.
    """
    kh = set(r.get("kh") or [])
    dk = r.get("dk") or []
    out = []
    for c in (r.get("bm") or []):
        if len(c) != 2:
            continue
        a, b = c
        pair = None
        if a in kh and b in dk and b not in kh:
            pair = dk.index(b) + 1
        elif b in kh and a in dk and a not in kh:
            pair = dk.index(a) + 1
        if pair is None:
            continue
        if drank == 1 and pair != 1:
            continue
        if drank == 23 and pair not in (2, 3):
            continue
        out.append(c)
    return out


def _drop_low(r, k):
    """유력마 3두 전조합에서 **배당 낮은 것 k개**를 뺀다(구좌가 늘지 않는다).

    ⚠ 배당을 모르는 조합은 맨 뒤(=남는 쪽)로 보낸다 — 모르는 것이 유리해지면 안 된다.
    """
    cs = [c for c in _allc(r["kh"])]
    if not cs:
        return []
    q = r.get("q") or {}
    big = float("inf")
    cs.sort(key=lambda c: (q.get(tuple(c)) if q.get(tuple(c)) is not None else big))
    return cs[k:] if len(cs) > k else []


def _gap3(r):
    """유력마 3두 세 조합의 **최고 ÷ 최저 배당**. 셋 다 배당이 있어야 낸다(없으면 None)."""
    q = r.get("q") or {}
    vs = [q.get(tuple(c)) for c in _allc(r["kh"])]
    vs = [v for v in vs if v]
    if len(vs) < 3:
        return None
    return max(vs) / min(vs) if min(vs) else None


def _maxq_of(r):
    """경마 두수별 메인 상한(`_mainmax` 계단과 같은 값). 두수를 모르면 None.
    ⚠ 경륜·경정은 3 고정이라 이 함수를 쓰지 않는다(`--sport horse` 로만 쓴다)."""
    nh = len(r.get("hs") or [])
    if nh < 5:
        return None
    if nh <= 9:
        return 3
    if nh <= 12:
        return 4
    return 6


def _fill_gap_cross(r, wide=False, band=None):
    """🔴 [2026-08-06 신설] **빈 자리에만** 복병 × 유력마 교차를 채운다.

    대표 지적: *"전면 교차는 기각됐지만(52.5%) 자리가 남을 때만 채우는 것은 다르다."*
      전면 교차는 조합을 통째로 갈아치워 구좌가 폭발한다. 이 안은
      **max_q 미달분(빈 자리)만** 채우므로 상한을 넘지 않는다.
    ⚠ 그래도 **구좌는 는다** — 지금 2개 내는 경주가 3개를 내게 된다. "안 늘린다"가 아니다.
    ⚠ `keyHorses` 저장이 0% 라 `kh` 는 axis 대용이다(원본 로직과 다를 수 있다 · 결과에 명시).

    실물 근거(후나바시 9R · 결과 3-4-5): 3번은 복병 목록에, 4번은 삼복승 '마감급락 보존'에
      각각 있었는데 복승 3+4 는 어디에도 없었다. 표시가 2개(max_q=3)라 **자리도 남아 있었다.**"""
    dc = [sorted(c) for c in (r["dc"] or [])]
    mq = _maxq_of(r)
    if mq is None or len(dc) >= mq:
        return dc
    kh = list(r.get("kh") or [])
    dk = list(r.get("dk") or [])
    if not kh or not dk:
        return dc
    q = r.get("q") or {}
    have = set(tuple(c) for c in dc)
    cand = []
    src = dk if wide else dk[:2]           # 기본은 복병 상위 2두만(전면 교차와 구분)
    for a in src:
        for b in kh:
            if a == b:
                continue
            c = sorted([int(a), int(b)])
            if tuple(c) in have:
                continue
            v = q.get(tuple(c))
            if not v:
                continue
            # 🔴 [2026-08-06 실측] 배당 상한이 없으면 **중앙 102.8배·최대 11,713배**가 들어온다.
            #   그건 고배당이 아니라 사실상 안 오는 조합이다. 대표 원칙의 '중배당'을 지키려면
            #   band 로 구간을 좁혀야 한다(EV 복원이 12~30배로 성과를 낸 것과 같은 취지).
            if band and not (band[0] <= v <= band[1]):
                continue
            cand.append((v, c))
    if not cand:
        return dc
    cand.sort(key=lambda x: -x[0])          # 🔴 배당 높은순 — 저배당 편중을 고치는 것이 목적이다
    out = list(dc)
    for _v, c in cand:
        if len(out) >= mq:
            break
        if tuple(c) in have:
            continue
        have.add(tuple(c))
        out.append(c)
    return out


def _swap_low_for_ref(r, lo=None):
    """구좌를 **늘리지 않고** 현행의 최저배당 1개를 강등분 고배당 1개로 바꾼다.

    🔴 [2026-08-06] 대표 지시 "저배당 뻔한 조합을 바꾼다"의 최소 개입 형태다.
      · 현행이 1개 이하면 바꾸지 않는다(유일한 추천을 없애면 화면이 빈다).
      · 강등분에 쓸 조합이 없으면 현행 그대로 둔다(억지로 채우지 않는다).
      · lo=(a,b) 를 주면 그 배당대 안에서만 고른다 — 극단 고배당 한 건에 회수가 끌려가는 것을 막는다.
    ⚠ 반사실 시뮬레이션이다. 실제로는 강등돼 회원에게 안 나갔다."""
    dc = list(r["dc"] or [])
    ref = list(r["ref"] or [])
    q = r["q"] or {}
    if len(dc) < 2 or not ref:
        return dc
    cand = []
    for c in ref:
        v = q.get(tuple(sorted(c)))
        if not v:
            continue
        if lo and not (lo[0] <= v <= lo[1]):
            continue
        cand.append((v, sorted(c)))
    if not cand:
        return dc
    cand.sort(key=lambda x: -x[0])
    add = cand[0][1]
    if add in [sorted(c) for c in dc]:
        return dc
    dcv = [(q.get(tuple(sorted(c))) or 0, c) for c in dc]
    dcv.sort(key=lambda x: x[0])          # 최저배당이 앞
    keep = [c for _v, c in dcv[1:]]        # 가장 뻔한 것 하나를 뺀다
    return keep + [add]


# 🔴 오늘 잰 11개 안을 함수로 고정. 새 안은 여기에만 추가한다.
def _seen_nos(r):
    """표시된 복승·삼복승에 **등장한 마번 전부**. 우라와 6R 유형을 재는 모집단이다."""
    s = set()
    for c in (r.get("dc") or []) + (r.get("dt") or []):
        for n in c:
            try:
                s.add(int(n))
            except Exception:
                pass
    return sorted(s)


def _cross_missing(r):
    """등장 마번끼리의 짝 중 **표시에 없는 것**. 배당이 있는 것만(살 수 있어야 한다)."""
    have = set(tuple(sorted(c)) for c in (r.get("dc") or []))
    q = r.get("q") or {}
    nos = _seen_nos(r)
    out = []
    for i in range(len(nos)):
        for j in range(i + 1, len(nos)):
            c = (nos[i], nos[j])
            if c in have or c not in q:
                continue
            out.append(list(c))
    return out


def _cross_all(r):
    """현행 + 미생성 짝 전부. ⚠ 구좌가 크게 는다 — 한계 회수율로 함께 본다."""
    return [sorted(c) for c in (r.get("dc") or [])] + _cross_missing(r)


def _cross_swap(r, band=None):
    """🔴 구좌 동일 교체 — 현행 **저배당 1개**를 미생성 짝(band 안 최고배당)으로 바꾼다.
    ⚠ 현행이 1개 이하면 바꾸지 않는다(유일한 추천을 없애면 화면이 빈다)."""
    dc = [sorted(c) for c in (r.get("dc") or [])]
    if len(dc) <= 1:
        return dc
    q = r.get("q") or {}
    miss = _cross_missing(r)
    if band:
        lo, hi = band
        miss = [c for c in miss if lo <= q.get(tuple(c), 0) <= hi]
    if not miss:
        return dc
    best = max(miss, key=lambda c: q.get(tuple(c), 0))
    low = min(dc, key=lambda c: q.get(tuple(c), 9e9))
    return [c for c in dc if c != low] + [best]


PLANS = [
    ("현행(기준선)", lambda r: r["dc"]),
    ("현행 +1", lambda r: r["dc"] + _allc(r["kh"])[:1]),
    ("현행 +2", lambda r: r["dc"] + _allc(r["kh"])[:2]),
    ("현행 +3", lambda r: r["dc"] + _allc(r["kh"])[:3]),
    ("유력마 3두 전조합", lambda r: _allc(r["kh"])),
    ("유력마 5~50배 추가", lambda r: r["dc"] + [c for c in _allc(r["kh"])
                                            if r["q"].get(tuple(c)) and 5 <= r["q"][tuple(c)] <= 50]),
    ("시장 3두 전조합", lambda r: _allc(_mkt3(r))),
    ("paceBonus ① 현행", lambda r: _allc(_pace(r, +1))),
    ("paceBonus ② 반전", lambda r: _allc(_pace(r, -1))),
    ("paceBonus ③ 제거", lambda r: _allc(_pace(r, 0))),
    ("현행 + BMED", lambda r: r["dc"] + r["bm"]),
    # 🔴 [2026-08-01 신설] "만들었다가 지운 것"을 되살리면 어떻게 되나.
    #   ⚠ 이건 **반사실 시뮬레이션**이다 — 실제로는 강등돼 회원에게 안 나갔다.
    ("현행 + 강등분(quinellaRef)", lambda r: r["dc"] + r["ref"]),
    ("강등분만(quinellaRef)", lambda r: r["ref"]),
    # 🔴 [2026-08-06 신설] **구좌를 안 늘리고 저배당 1개를 강등분 고배당 1개로 교체**한다.
    #   대표 지시: "안 맞아도 회원들이 좋아한다. 맞아도 짜증나는 구조다" — 기준이 회수율에서
    #   회원 만족(적중배당)으로 바뀌었다. 그러나 **회수율이 얼마나 나빠지는지 숫자로 함께 낸다.**
    #   ⚠ 실측 배경: 현행 표시는 적중률 13.9%·배당중앙 3.1배 · 강등분은 8.6%·9.7배(경마 8월).
    #     보조가 더 자주 맞는 게 아니라 **맞을 때 배당이 3배**다. 그 교환을 재는 안이다.
    ("교체 저배당1→강등최고", lambda r: _swap_low_for_ref(r, lo=None)),
    ("교체 저배당1→강등5~30배", lambda r: _swap_low_for_ref(r, lo=(5.0, 30.0))),
    # 🔴 [2026-08-06 신설] 빈 자리(max_q 미달)에만 복병×유력마 교차를 채운다.
    #   전면 교차(52.5%로 기각)와 달리 상한을 넘지 않는다. ⚠ 그래도 구좌는 는다.
    ("빈자리 교차채움(복병2)", lambda r: _fill_gap_cross(r, wide=False)),
    ("빈자리 교차채움(복병전부)", lambda r: _fill_gap_cross(r, wide=True)),
    ("빈자리 교차 5~30배", lambda r: _fill_gap_cross(r, wide=False, band=(5.0, 30.0))),
    ("빈자리 교차 10~50배", lambda r: _fill_gap_cross(r, wide=False, band=(10.0, 50.0))),
    ("빈자리 교차 5~15배", lambda r: _fill_gap_cross(r, wide=False, band=(5.0, 15.0))),
    # 🔴 [2026-08-07] 우라와 6R 유형 — 등장 마번끼리의 미생성 짝
    ("전조합 교차 추가(전부)", _cross_all),
    ("전조합 교차 교체(구좌동일)", _cross_swap),
    ("전조합 교차 교체 5~30배", lambda r: _cross_swap(r, band=(5.0, 30.0))),
    # 🔴 이웃 구간 — 5~30 이 진짜 최적인지, 사후 최적화인지 가른다
    ("전조합 교차 교체 3~20배", lambda r: _cross_swap(r, band=(3.0, 20.0))),
    ("전조합 교차 교체 5~50배", lambda r: _cross_swap(r, band=(5.0, 50.0))),
    # 🔴 [2026-08-01 신설] **복병 × 유력마 교차**. 두 목록이 따로 놀아 조합이 안 만들어지는 문제.
    #   ⚠ 복병은 상위 2두만 쓴다(전부 쓰면 구좌가 폭발해 회수율이 자동으로 나빠 보인다).
    ("복병×유력마 교차 추가", lambda r: r["dc"] + [sorted([a, b]) for a in r["dk"][:2]
                                              for b in r["kh"] if a != b]),
    ("복병×유력마 교차만", lambda r: [sorted([a, b]) for a in r["dk"][:2]
                                for b in r["kh"] if a != b]),
    # 🔴 [2026-08-03 신설 · 대기 ① 짝 리플레이] **"복병 1순위 + 유력마 N위" 고정 1조합**.
    #   배경: 복병 1순위가 1·2착에 들었을 때 **짝이 유력마 안이었던 비율이 경륜 63% · 경마 56%** 였다.
    #     ⇒ *"그럼 마감 전에 그 짝을 고를 수 있나"* 가 남은 질문이고, 이 안이 그 답을 잰다.
    #   ⚠ **구좌가 경주당 1조합으로 같다** — 조합 확대안(구좌가 늘어 자동으로 불리)과 성격이 다르다.
    #   ⚠ N 은 `keyHorses` 순서(=통합 유력마 순위)를 그대로 쓴다. 없으면 그 경주는 조합 0.
    ("짝① 복병1+유력마1위", lambda r: ([sorted([r["dk"][0], r["kh"][0]])]
                                  if r["dk"] and len(r["kh"]) >= 1 and r["dk"][0] != r["kh"][0] else [])),
    ("짝② 복병1+유력마2위", lambda r: ([sorted([r["dk"][0], r["kh"][1]])]
                                  if r["dk"] and len(r["kh"]) >= 2 and r["dk"][0] != r["kh"][1] else [])),
    ("짝③ 복병1+유력마3위", lambda r: ([sorted([r["dk"][0], r["kh"][2]])]
                                  if r["dk"] and len(r["kh"]) >= 3 and r["dk"][0] != r["kh"][2] else [])),
    # 🔴 [2026-08-01 신설] **정렬 리플레이** — 지금까지 기각된 안은 전부 "무엇을 더 살까"였다.
    #   정렬은 **같은 것을 다른 순서로** 사므로 **구좌가 늘지 않는다.**
    #   ⚠ ④ "시장 대비 저평가 순"은 **② EV 순과 수학적으로 동일**하다:
    #     저평가도 = 우리확률 ÷ 시장확률 = p ÷ (1/배당) = p × 배당 = **EV**.
    #     ⇒ 별도 안으로 세지 않고 그 자리에 **배당 높은순**(고배당 지향)을 넣는다.
    ("정렬① 현행(배당낮은순)", lambda r: r["dc"]),
    ("정렬② EV 순", lambda r: _sort_pool(r, "ev")),
    ("정렬③ 신호강도(stars) 순", lambda r: _sort_pool(r, "stars")),
    ("정렬④ 배당 높은순", lambda r: _sort_pool(r, "odds")),
    # 🔴 [2026-08-02 신설] **유력마 3두 안에서 최저배당 조합을 뺀다.**
    #   착안(사세보 4R): 같은 유력마 3두(1·5·6) 안인데 `1+5=2.0배` ↔ `1+6=27.7배` 로 **13.8배 차이**다.
    #   현행은 배당 낮은순 정렬이라 **2.0배를 산다**. ⇒ 고배당은 유력마 **밖**만이 아니라 **안**에도 있다.
    #   ⚠ 이 안은 **구좌가 늘지 않는다**(오히려 준다) — 조합 확대안이 기각된 이유를 피한다.
    #   ⚠ 배당을 모르는 조합은 뒤로 보낸다(정렬에서 유리해지지 않게).
    # 🔴 [2026-08-02 신설] **bmedSpecial 조건부 편입**. 삿포로 6R 에서 복병 10번 × 유력마 3번 교차
    #   `[3,10]` 23.0배를 **만들어놓고 표시 명단에서 뺐다**(10번 우승).
    #   ⚠ 단순 전량 편입은 어제 실측 회수율 **37.4%** 로 근거가 없다 — **조건을 좁혀** 잰다.
    #   ⚠ 아래는 전부 **그 부분집합만** 사는 안이다(현행에 더하는 것이 아니다 · 구좌 비교 주의).
    ("BMED 전체만", lambda r: r["bm"]),
    ("BMED 복병×유력마 교차만", lambda r: _bm_cross(r)),
    ("BMED 교차·복병1순위", lambda r: _bm_cross(r, drank=1)),
    ("BMED 교차·복병2~3순위", lambda r: _bm_cross(r, drank=23)),
    ("BMED 교차·신호2개+", lambda r: _bm_cross(r) if r.get("sig", 0) >= 2 else []),
    ("BMED 교차·신호3개+", lambda r: _bm_cross(r) if r.get("sig", 0) >= 3 else []),
    ("BMED 교차·10~30배", lambda r: [c for c in _bm_cross(r)
                                  if r["q"].get(tuple(c)) and 10 <= r["q"][tuple(c)] <= 30]),
    ("BMED 교차·30~50배", lambda r: [c for c in _bm_cross(r)
                                  if r["q"].get(tuple(c)) and 30 < r["q"][tuple(c)] <= 50]),
    ("유력마3두 최저1제외(2개)", lambda r: _drop_low(r, 1)),
    ("유력마3두 최저2제외(1개)", lambda r: _drop_low(r, 2)),
]


def calc(rows, gen):
    inv = 0
    hits = []
    for r in rows:
        cs = [list(c) for c in {tuple(sorted(c)) for c in gen(r)}]
        inv += len(cs)
        if r["top2"] in cs:
            hits.append(r["po"])
    hits.sort(reverse=True)
    return inv, hits


def boot_ci(rows, gen, n=BOOT_N, seed=42):
    """⑤ 95% 신뢰구간(부트스트랩). 구간이 74.5% 를 포함하면 **판정 불가**다."""
    random.seed(seed)
    vals = []
    for _ in range(n):
        s = [random.choice(rows) for _ in range(len(rows))]
        i, h = calc(s, gen)
        vals.append(100.0 * sum(h) / max(i, 1))
    vals.sort()
    return vals[int(0.025 * n)], vals[int(0.975 * n)]


def measure(sport="cycle", pattern="2026_07_*", ci_for="현행(기준선)"):
    raw = load_races(sport, pattern)
    clean = [r for r in raw if CLEAN_LO <= r["po"] / r["mo"] <= CLEAN_HI]
    out = {"sport": sport, "pattern": pattern,
           "denom_all": len(raw), "denom_clean": len(clean), "payback": PAYBACK, "plans": []}
    for lb, g in PLANS:
        i1, h1 = calc(raw, g)
        i2, h2 = calc(clean, g)
        r1 = 100.0 * sum(h1) / max(i1, 1)
        r2 = 100.0 * sum(h2) / max(i2, 1)
        out["plans"].append({
            "name": lb, "slots": i2, "hits": len(h2),
            "rate_dirty": round(r1, 1), "rate": round(r2, 1),
            "inflated_by": round(r1 - r2, 1),
            "ex1": round(100.0 * sum(h2[1:]) / max(i2, 1), 1),
            "ex3": round(100.0 * sum(h2[3:]) / max(i2, 1), 1),
            "median_odds": round(statistics.median(h2), 1) if h2 else 0,
            "vs_payback": round(r2 - PAYBACK, 1),
        })
    if clean:
        g = dict(PLANS)[ci_for]
        lo, hi = boot_ci(clean, g)
        out["ci"] = {"plan": ci_for, "lo": round(lo, 1), "hi": round(hi, 1),
                     "includes_payback": bool(lo <= PAYBACK <= hi)}
    return out


EV_THRESHOLDS = [1.00, 0.90, 0.80, 0.70, 0.60, 0.50, 0.40, 0.30, 0.20, 0.00]


def _field_band(r):
    """두수 구간. ⚠ `horses` 길이를 쓴다 — `raceHorseCount` 는 마번 수와 어긋나는 사례가 있다."""
    n = len(r.get("hs") or [])
    if n <= 0:
        return "?"
    if n <= 9:
        return "≤9두"
    if n <= 12:
        return "10~12두"
    return "13두+"


def measure_ev_sweep(sport="cycle", pattern="2026_0*"):
    """[EV 임계 스윕 (2026-08-01 신설)] — **완전 읽기 전용 · 측정만**.

    🔴 왜: EV 필터가 강등한 조합의 **적중배당 중앙이 7.3배**(현행 3.4배의 2.1배)다.
      회수율만 보면 강등이 옳지만, 대표 원칙(**고배당·중배당이 기본**)상 회수율 단독으로 닫지 않는다.
    🔴 찾는 것: **회수율 74.5% 를 지키면서 적중배당 중앙이 최대인 임계**.
      ⚠ **회수율 최대가 아니다.** 두 곡선은 서로 다른 임계에서 최대가 될 수 있다.
    ⚠ 임계 t 의 뜻: 강등분 중 `ev >= t` 인 조합을 **현행 추천에 되살린다**(반사실 시뮬레이션).
      t=1.00 은 사실상 현행과 같다(ev≥1.0 이면 애초에 강등되지 않았다).
    ⚠ 정제 필터(괴리 0.5~2.0배)는 `measure()` 와 **동일하게** 적용한다.
    """
    raw = load_races(sport, pattern)
    clean = [r for r in raw if CLEAN_LO <= r["po"] / r["mo"] <= CLEAN_HI]
    out = {"sport": sport, "pattern": pattern, "denom_all": len(raw),
           "denom_clean": len(clean), "payback": PAYBACK, "rows": [], "bands": {}}
    out["ref_ev_total"] = sum(len(r.get("refev") or []) for r in clean)
    out["ref_noev_total"] = sum(int(r.get("refnoev") or 0) for r in clean)

    def gen_for(t):
        return lambda r: r["dc"] + [c for c, ev in (r.get("refev") or []) if ev >= t]

    # 🔴 [정정 2026-08-01] `EV 1.00` 은 현행과 **같지 않다.**
    #   ev≥1.0 인데도 **다른 사유**(베팅규칙 참고 강등·저배당 컷)로 강등된 조합이 실측 51개 있었다.
    #   ⇒ 기준선은 **복원 0 인 현행(dc)** 이며, 별도 행으로 먼저 찍는다. 이걸 빼면 비교 대상이 틀린다.
    i0, h0 = calc(clean, lambda r: r["dc"])
    out["base"] = {"slots": i0, "hits": len(h0),
                   "rate": round(100.0 * sum(h0) / max(i0, 1), 1),
                   "ex1": round(100.0 * sum(h0[1:]) / max(i0, 1), 1),
                   "ex3": round(100.0 * sum(h0[3:]) / max(i0, 1), 1),
                   "median_odds": round(statistics.median(h0), 1) if h0 else 0}
    for t in EV_THRESHOLDS:
        g = gen_for(t)
        i2, h2 = calc(clean, g)
        added = sum(len([1 for c, ev in (r.get("refev") or []) if ev >= t]) for r in clean)
        out["rows"].append({
            "ev": t, "slots": i2, "added": added, "hits": len(h2),
            "rate": round(100.0 * sum(h2) / max(i2, 1), 1),
            "ex1": round(100.0 * sum(h2[1:]) / max(i2, 1), 1),
            "ex3": round(100.0 * sum(h2[3:]) / max(i2, 1), 1),
            "median_odds": round(statistics.median(h2), 1) if h2 else 0,
            "vs_payback": round(100.0 * sum(h2) / max(i2, 1) - PAYBACK, 1),
        })
    # 두수별 분해 (⚠ 셀이 얇으면 판정 불가 — n 을 반드시 함께 본다)
    for band in ("≤9두", "10~12두", "13두+"):
        sub = [r for r in clean if _field_band(r) == band]
        if not sub:
            continue
        rows = []
        for t in EV_THRESHOLDS:
            g = gen_for(t)
            i2, h2 = calc(sub, g)
            rows.append({"ev": t, "n": len(sub), "slots": i2, "hits": len(h2),
                         "rate": round(100.0 * sum(h2) / max(i2, 1), 1),
                         "ex3": round(100.0 * sum(h2[3:]) / max(i2, 1), 1),
                         "median_odds": round(statistics.median(h2), 1) if h2 else 0})
        out["bands"][band] = rows
    return out


def report_ev_sweep(out):
    print("⚠ 분모: 전체 %d → 정제(괴리 %.1f~%.1f배) %d경주 (%.1f%%)" % (
        out["denom_all"], CLEAN_LO, CLEAN_HI, out["denom_clean"],
        100.0 * out["denom_clean"] / max(out["denom_all"], 1)))
    print("⚠ 강등분: ev 보유 %d조합(스윕 대상) · ev 없음 %d조합(**대상 아님** — 저배당 컷 등)" % (
        out["ref_ev_total"], out["ref_noev_total"]))
    print()
    print("  %-7s %7s %7s %6s %9s %8s %8s %9s %10s" % (
        "EV임계", "구좌", "복원", "적중", "회수율", "1제외", "3제외", "배당중앙", "74.5대비"))
    b = out.get("base") or {}
    if b:
        print("  %-7s %7d %7d %6d %8.1f%% %7.1f%% %7.1f%% %8.1f배 %9.1f%%p %s" % (
            "현행", b["slots"], 0, b["hits"], b["rate"], b["ex1"], b["ex3"],
            b["median_odds"], b["rate"] - out["payback"],
            "🟢" if b["rate"] >= out["payback"] else "🔴"))
        print("  " + "-" * 88)
    for r in out["rows"]:
        mark = "🟢" if r["rate"] >= out["payback"] else "🔴"
        print("  %-7.2f %7d %7d %6d %8.1f%% %7.1f%% %7.1f%% %8.1f배 %9.1f%%p %s" % (
            r["ev"], r["slots"], r["added"], r["hits"], r["rate"], r["ex1"], r["ex3"],
            r["median_odds"], r["vs_payback"], mark))
    ok = [r for r in out["rows"] if r["rate"] >= out["payback"]]
    print()
    if not ok:
        print("  🔴 **74.5%% 를 넘는 임계가 없다.** 어떤 임계도 판정선을 지키지 못한다 → 해답 없음.")
    else:
        best_odds = max(ok, key=lambda r: (r["median_odds"], r["rate"]))
        best_rate = max(ok, key=lambda r: r["rate"])
        print("  🟢 74.5%% 유지 임계: %s" % ", ".join("%.2f" % r["ev"] for r in ok))
        print("  🔴 **배당중앙 최대(권고 기준)**: EV %.2f · 회수율 %.1f%% · 배당중앙 %.1f배 · 3제외 %.1f%%"
              % (best_odds["ev"], best_odds["rate"], best_odds["median_odds"], best_odds["ex3"]))
        if b:
            dr = best_odds["rate"] - b["rate"]
            do = best_odds["median_odds"] - b["median_odds"]
            print("     ↳ 🔴 **현행 대비**: 회수율 %+.1f%%p · 배당중앙 %+.1f배 · 구좌 %+d (%d→%d)"
                  % (dr, do, best_odds["slots"] - b["slots"], b["slots"], best_odds["slots"]))
            if do <= 0:
                print("     ↳ 🔴 **배당중앙이 오르지 않는다. 채택 근거가 없다**(대표 원칙 기준 미충족).")
            elif dr < 0:
                print("     ↳ ⚠ 배당중앙은 오르나 **회수율은 내려간다.** 교환비를 대표가 판단할 사안이다.")
        print("  ⚠ (대조) 회수율 최대   : EV %.2f · 회수율 %.1f%% · 배당중앙 %.1f배"
              % (best_rate["ev"], best_rate["rate"], best_rate["median_odds"]))
        if best_odds["ev"] != best_rate["ev"]:
            print("  🔴 **두 최대가 서로 다른 임계다.** 회수율만 보면 배당중앙을 놓친다(대표 원칙).")
    for band, rows in out.get("bands", {}).items():
        n = rows[0]["n"] if rows else 0
        warn = "  ⚠ **n<30 판정 불가**" if n < 30 else ""
        print()
        print("  ── 두수별 · %s (경주 %d)%s ──" % (band, n, warn))
        for r in rows:
            if r["ev"] in (1.00, 0.80, 0.60, 0.40, 0.00):
                print("     EV %.2f · 구좌 %4d · 적중 %3d · 회수율 %6.1f%% · 3제외 %6.1f%% · 배당중앙 %5.1f배"
                      % (r["ev"], r["slots"], r["hits"], r["rate"], r["ex3"], r["median_odds"]))


def _rec3(r):
    """전적 100% 근사 — 저장된 `record_score` 상위 3두.
    ⚠ 실제 `_integrated_grades` 의 전적 축과 완전히 같지는 않다(보너스·등급 반영분이 빠진다).
      ⇒ **하한 근사**로 본다. 재현 못 한 부분은 결과에 명시한다(원칙 3)."""
    hs = [h for h in (r.get("hs") or []) if h.get("record_score") is not None]
    return [int(h["no"]) for h in sorted(hs, key=lambda h: -(h.get("record_score") or 0))][:3]


def measure_weights(sport="cycle", pattern="2026_0*"):
    """[전적이 실제로 순위를 바꾸는가 (2026-08-02 신설)] **완전 읽기 전용**.

    🔴 대표 지적: *"전적은 항상 같은 패턴이라 결국 배당판 정보로만 변동이 생기는 것 아닌가."*
      전적은 경주 전 확정이고 마감까지 재수집되지 않는다(`_KEIBA_FORM_DONE` 게이트).
      ⇒ 틱마다 순위가 바뀐다면 **그 변동은 전부 배당 때문**이다.
    🔴 무엇을 재나: 세 축의 **상위 3두 집합**을 만들고 **조합 일치율**과 성적을 나란히 본다.
      · 현행(60/40)  = `keyHorses`      (저장값)
      · 시장 100%    = `_mkt3(r)`       (배당 내재확률 상위 3두)
      · 전적 100%    = `_rec3(r)`       (`record_score` 상위 3두 · 하한 근사)
    ⚠ **일치율이 높으면 "60/40" 표기가 오해를 만든다** — 전적이 이름만 있는 것이다.
    """
    raw = load_races(sport, pattern)
    clean = [r for r in raw if CLEAN_LO <= r["po"] / r["mo"] <= CLEAN_HI]
    out = {"sport": sport, "pattern": pattern, "denom_all": len(raw), "denom_clean": len(clean),
           "pairs": [], "plans": [], "rec_missing": 0}
    axes = {"현행(60/40)": lambda r: r["kh"][:3],
            "시장 100%": _mkt3,
            "전적 100%": _rec3}
    # ① 축 사이의 집합·조합 일치율
    keys = list(axes)
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            a, b = keys[i], keys[j]
            same_set, same_combo, n = 0, 0, 0
            for r in clean:
                x, y = axes[a](r), axes[b](r)
                if len(x) < 3 or len(y) < 3:
                    continue
                n += 1
                if set(x) == set(y):
                    same_set += 1
                ca = {tuple(c) for c in _allc(x)}
                cb = {tuple(c) for c in _allc(y)}
                same_combo += len(ca & cb) / 3.0     # 3조합 중 겹치는 비율
            out["pairs"].append({"a": a, "b": b, "n": n,
                                 "set_same": round(100.0 * same_set / max(n, 1), 1),
                                 "combo_same": round(100.0 * same_combo / max(n, 1), 1)})
    # ② 축별 성적 (상위3 전조합 = 구좌 동일)
    for name, fn in axes.items():
        inv, hits, hit3 = 0, [], 0
        for r in clean:
            top = fn(r)
            if len(top) < 3:
                continue
            cs = _allc(top)
            inv += len(cs)
            if r["top2"] in [sorted(c) for c in cs]:
                hits.append(r["po"])
            if len(set(r["top2"]) & set(top)) == 2:
                hit3 += 1
        hits.sort(reverse=True)
        out["plans"].append({
            "name": name, "slots": inv, "hits": len(hits),
            "rate": round(100.0 * sum(hits) / max(inv, 1), 1),
            "ex1": round(100.0 * sum(hits[1:]) / max(inv, 1), 1),
            "ex3": round(100.0 * sum(hits[3:]) / max(inv, 1), 1),
            "median_odds": round(statistics.median(hits), 1) if hits else 0,
            "incl": round(100.0 * hit3 / max(len(clean), 1), 1),
        })
    out["rec_missing"] = sum(1 for r in clean if len(_rec3(r)) < 3)
    return out


def report_weights(out):
    print("⚠ 분모: 전체 %d → 정제 %d경주 (%.1f%%) · `record_score` 부족으로 전적축 산출불가 %d경주"
          % (out["denom_all"], out["denom_clean"],
             100.0 * out["denom_clean"] / max(out["denom_all"], 1), out["rec_missing"]))
    print()
    print("  ① 축 사이 일치율 (상위 3두)")
    print("     %-24s %8s %10s %10s" % ("비교", "n", "집합동일", "조합겹침"))
    for p in out["pairs"]:
        mark = " 🔴" if p["combo_same"] >= 80 else (" 🟡" if p["combo_same"] >= 60 else "")
        print("     %-24s %8d %9.1f%% %9.1f%%%s"
              % (p["a"] + " ↔ " + p["b"], p["n"], p["set_same"], p["combo_same"], mark))
    print()
    print("  ② 축별 성적 (상위3 전조합 · 구좌 동일 · 판정선 %.1f%%)" % PAYBACK)
    print("     %-14s %7s %6s %9s %8s %8s %9s %9s" %
          ("축", "구좌", "적중", "회수율", "1제외", "3제외", "배당중앙", "1·2착포함"))
    for p in out["plans"]:
        print("     %-14s %7d %6d %8.1f%% %7.1f%% %7.1f%% %8.1f배 %8.1f%% %s"
              % (p["name"], p["slots"], p["hits"], p["rate"], p["ex1"], p["ex3"],
                 p["median_odds"], p["incl"], "🟢" if p["rate"] >= PAYBACK else "🔴"))


def measure_forecast(sport=None, pattern="2026_0*"):
    """[F3 — Gemini 고배당 능력 (2026-08-01 신설)] **완전 읽기 전용**.

    🔴 왜: **F2(평균 적중 수)는 배당을 보지 않는다.** Gemini 가 82배를 맞추고 시장이 1.2배를 맞춰도
      F2 에서는 동점이다. 대표 원칙(**고배당·중배당이 기본**)을 F2 는 구조적으로 못 잰다.
      ⇒ F2 만으로 판정하면 고배당 능력을 **재보지도 못하고** 닫힌다.
    🔴 무엇을 재나:
      ① 시장과 **다른 답**(Gemini top3 중 market_top3 에 없는 말)이 실제 3착 안에 든 건수
      ② 그 건의 확정배당 분포 (시장 적중분과 나란히)
      ③ **가상 회수율** — Gemini top3 전조합 vs 시장 top3 전조합 (구좌 동일 = 3조합)
    ⚠ 🔴 **날짜 안전(원칙 16)**: `logs/forecast/YYYYMMDD_...` → `analysis_log/YYYY_MM_DD_...` 로
      **날짜를 포함해** 조인한다. 경기장·경주번호만으로 맞추면 다른 날이 섞인다.
    ⚠ 확정배당·정제 필터는 `load_races()` 와 **같은 기준**을 쓴다(비교 가능성 확보).
    """
    rows = []
    for f in sorted(glob.glob(os.path.join(BASE, "logs", "forecast", "*.json"))):
        base = os.path.basename(f)[:-5]
        m = re.match(r"(\d{4})(\d{2})(\d{2})_(.+)$", base)
        if not m:
            continue
        alog = os.path.join(BASE, "data", "analysis_log",
                            "%s_%s_%s_%s.json" % (m.group(1), m.group(2), m.group(3), m.group(4)))
        if not os.path.exists(alog):
            continue
        try:
            fc = json.load(open(f, encoding="utf-8"))
            d = json.load(open(alog, encoding="utf-8"))
        except Exception:
            continue
        if sport not in (None, "all") and (d.get("sport") or "") != sport:
            continue
        gr = fc.get("grading") or {}
        gtop = [int(x) for x in (fc.get("predicted_top3") or []) if x is not None]
        mtop = [int(x) for x in (gr.get("market_top3") or []) if x is not None]
        act = [int(x) for x in (gr.get("actual") or []) if x is not None]
        if len(gtop) < 2 or len(mtop) < 2 or len(act) < 3:
            continue
        res = d.get("result") or {}
        po = (res.get("payouts") or {}).get("quinella")
        if not po or res.get("1st") is None or res.get("2nd") is None:
            continue
        # 정제 필터용 마감배당(load_races 와 동일 기준)
        h = _loadh(alog.replace("analysis_log", "odds_history"))
        dl = (h or {}).get("deadline_epoch")
        mo = None
        if h and dl:
            sn = [s for s in (h.get("snapshots") or [])
                  if s.get("t") and -8 <= (s["t"] - dl) / 60 <= 0 and s.get("quinella")]
            if sn:
                q = {}
                for k, v in max(sn, key=lambda x: x["t"])["quinella"].items():
                    try:
                        q[tuple(sorted(int(x) for x in str(k).replace("-", "+").split("+")))] = float(v)
                    except Exception:
                        pass
                mo = q.get(tuple(sorted({res["1st"], res["2nd"]})))
        rows.append({"gtop": gtop, "mtop": mtop, "act": act, "po": float(po), "mo": mo,
                     "sport": d.get("sport") or "?", "day": "%s_%s_%s" % m.groups()[:3],
                     "top2": sorted({res["1st"], res["2nd"]}),
                     "ghit": gr.get("hit_count"), "mhit": gr.get("market_hit_count")})
    return rows


def report_forecast(rows, label="전체"):
    if not rows:
        print("  %-10s n=0 — 판정 불가" % label)
        return
    clean = [r for r in rows if r["mo"] and CLEAN_LO <= r["po"] / r["mo"] <= CLEAN_HI]
    print("  ⚠ 분모: 조인 %d → 정제 %d경주 (%.1f%%)  [%s]"
          % (len(rows), len(clean), 100.0 * len(clean) / max(len(rows), 1), label))
    # ① 시장과 다른 답 && 적중
    diff_hit, diff_n, diff_odds = 0, 0, []
    same_hit_odds = []
    for r in rows:
        uniq = [x for x in r["gtop"] if x not in r["mtop"]]      # Gemini 만 찍은 말
        if uniq:
            diff_n += 1
            got = [x for x in uniq if x in r["act"]]
            if got:
                diff_hit += 1
                diff_odds.append(r["po"])
        m_uniq = [x for x in r["mtop"] if x not in r["gtop"]]    # 시장만 찍은 말
        if m_uniq and [x for x in m_uniq if x in r["act"]]:
            same_hit_odds.append(r["po"])
    print("  ① 시장과 **다른 답**을 낸 경주 %d · 그중 그 말이 3착 안 = **%d건 (%.1f%%)**"
          % (diff_n, diff_hit, 100.0 * diff_hit / max(diff_n, 1)))
    print("     (대조) 시장만 찍은 말이 3착 안 = %d건" % len(same_hit_odds))

    def dist(v, tag):
        if not v:
            print("     %s n=0" % tag)
            return
        b = collections.Counter()
        for x in v:
            b["2배미만" if x < 2 else "2~5배" if x < 5 else "5~10배" if x < 10
              else "10~20배" if x < 20 else "20배+"] += 1
        print("     %s n=%d · 중앙 %.1f배 · 평균 %.1f배 · %s"
              % (tag, len(v), statistics.median(v), sum(v) / len(v),
                 " · ".join("%s %d" % (k, b[k]) for k in
                            ("2배미만", "2~5배", "5~10배", "10~20배", "20배+") if b[k])))
    print("  ② 확정배당 분포 (⚠ 경주 단위 복승 확정배당)")
    dist(diff_odds, "Gemini 단독 적중")
    dist(same_hit_odds, "시장 단독 적중")
    # ③ 열위 건 중 고배당
    lose = [r for r in rows if r["ghit"] is not None and r["mhit"] is not None and r["ghit"] < r["mhit"]]
    lose_hi = [r["po"] for r in lose
               if [x for x in r["gtop"] if x not in r["mtop"] and x in r["act"]] and r["po"] >= 10]
    print("  ③ F2 **열위** %d건 중 · 시장과 다른 답을 맞추고 확정배당 10배+ = **%d건**%s"
          % (len(lose), len(lose_hi),
             (" (중앙 %.1f배)" % statistics.median(lose_hi)) if lose_hi else ""))
    # ④ 가상 회수율 (구좌 동일 = 상위3 전조합 3구좌)
    print("  ④ 가상 회수율 (⚠ 정제 %d경주 · 상위3 전조합 = 경주당 3구좌 · 판정선 %.1f%%)"
          % (len(clean), PAYBACK))
    for tag, key in (("Gemini top3", "gtop"), ("시장 top3", "mtop")):
        inv, hits = 0, []
        for r in clean:
            cs = _allc(r[key][:3])
            inv += len(cs)
            if r["top2"] in [sorted(c) for c in cs]:
                hits.append(r["po"])
        hits.sort(reverse=True)
        rate = 100.0 * sum(hits) / max(inv, 1)
        print("     %-12s 구좌 %4d · 적중 %3d · 회수율 %6.1f%% · 1제외 %6.1f%% · 3제외 %6.1f%% · 배당중앙 %5.1f배 %s"
              % (tag, inv, len(hits), rate,
                 100.0 * sum(hits[1:]) / max(inv, 1), 100.0 * sum(hits[3:]) / max(inv, 1),
                 statistics.median(hits) if hits else 0,
                 "🟢" if rate >= PAYBACK else "🔴"))


def measure_trio(sport="horse", pattern="2026_0*"):
    """[삼복승 섀도우 성적 (2026-08-01 신설)] — **완전 읽기 전용**.

    🔴 왜: `trioShadow` 가 2026-07-29 에 켜진 뒤 삼복승은 **생성만 되고 화면·판정에서 빠진다.**
      그런데 이틀 연속(오비히로 5R `3+5+10` · 코치 4R `7+8+10` 37.6배) **정답을 정확히 만들어놓고
      버린 것**이 확인됐다. 해제 판단에는 **섀도우 기간 성적**이 필요하다.
    ⚠ 당시 근거는 *"삼복승 확정배당이 72%만 확보돼 평가가 박할 수 있다"* 였다.
      지금은 백필로 **79.5%** 가 됐으므로 다시 잴 수 있다.
    ⚠ 적중 = 생성된 삼복승 조합이 **1·2·3착 집합과 일치**. 배당 = `result.payouts.trifecta`.
    """
    rows = []
    for f in sorted(glob.glob(os.path.join(BASE, "data", "analysis_log", pattern + ".json"))):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        if (d.get("sport") or "") != sport:
            continue
        res = d.get("result") or {}
        # 🔴🔴 [2026-08-01 정정] `trifecta` 필드가 **경로마다 다른 마권**이다. 그대로 쓰면 회수가 부풀려진다.
        #   · 중앙(netkeiba `_JRA_PAY_MAP`) : 3連複 → **`trio`** · 3連単 → **`trifecta`**
        #   · 지방(`_keiba_result_payouts`) : 三連複 → **`trifecta`** (3連単 안 받음)
        #   · 경륜(`_keirin_result_parse`)  : 3連複 → **`trifecta`** (3連単 안 받음)
        #   ⇒ **`trio` 가 있으면 그것이 삼복승**이고, 그 경주의 `trifecta` 는 3連単이라 쓰면 안 된다.
        #   실측: 중앙 17건에서 trifecta/trio 배수 **중앙 4.7배**(최대 18.7배) — 그만큼 부풀려졌다.
        #   ⚠ `trifecta` 만 있는 125건은 **category 가 japan_central 로 오분류된 지방**이라 안전하다.
        _pay = res.get("payouts") or {}
        po = _pay.get("trio") if _pay.get("trio") is not None else _pay.get("trifecta")
        top3 = [res.get("1st"), res.get("2nd"), res.get("3rd")]
        if not po or any(x is None for x in top3):
            continue
        cp = d.get("corePicks") or {}
        # 섀도우면 finalTrifectas 가 곧 "걸었다면" 목록이다(shadowTrifectas 는 하위호환).
        ft = cp.get("finalTrifectas") or cp.get("shadowTrifectas") or []
        combos = []
        for t in ft:
            c = t.get("combo") or []
            if len(c) == 3:
                combos.append(sorted(int(x) for x in c))
        if not combos:
            continue
        # 🔴 [2026-08-01] 복병 목록 — "복병 포함 삼복승"을 따로 재기 위해 함께 싣는다.
        #   전체가 나빠도 부분은 다를 수 있다(경마 삼복승 3제외 42.9% ↔ 복병 포함분은 미측정이었다).
        dk = [int(x.get("no")) for x in (cp.get("darkHorsePicks") or [])
              if x.get("no") is not None]
        rows.append({"combos": combos, "po": float(po), "top3": sorted(int(x) for x in top3),
                     "cat": d.get("category") or "?", "n": len(combos),
                     "dk": dk, "dk1": (dk[0] if dk else None)})
    return rows


def report_trio(rows, label):
    slots = sum(r["n"] for r in rows)
    hits = []
    hit_races = 0
    for r in rows:
        h = [c for c in r["combos"] if c == r["top3"]]
        if h:
            hit_races += 1
            hits += [r["po"]] * len(h)
    hits.sort(reverse=True)
    ret = sum(hits)
    def pct(x, n):
        return 100.0 * x / max(n, 1)
    print("  %-16s 경주 %3d · 조합 %4d · 적중 %3d(%d경주) · 회수율 %6.1f%% · 1제외 %6.1f%% · 3제외 %6.1f%% · 적중배당중앙 %s"
          % (label, len(rows), slots, len(hits), hit_races, pct(ret, slots),
             pct(sum(hits[1:]), slots), pct(sum(hits[3:]), slots),
             ("%.1f배" % statistics.median(hits)) if hits else "-"))


def measure_dark3(sport="horse", pattern="2026_0*"):
    """[복병 3착 이내 진입률 (2026-08-01 신설)] — **완전 읽기 전용**.

    🔴 왜: 지금까지 복병 평가는 **복승(1·2착)** 기준뿐이었다. 대표 관찰은
      *"급락으로 잡힌 복병이 3착 안에 드는 경우가 많다"* 이고, 그건 **삼복승 재료**다.
      분모가 다르므로 **복승 기준 값과 섞어 쓰면 안 된다.**
    ⚠ 무작위 기대 = 3 / 두수. 그것과 대조해야 "많다"가 성립한다.
    """
    out = []
    for f in sorted(glob.glob(os.path.join(BASE, "data", "analysis_log", pattern + ".json"))):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        if (d.get("sport") or "") != sport:
            continue
        res = d.get("result") or {}
        top3 = [res.get("1st"), res.get("2nd"), res.get("3rd")]
        if any(x is None for x in top3):
            continue
        cp = d.get("corePicks") or {}
        dks = cp.get("darkHorsePicks") or []
        if not dks:
            continue
        nh = cp.get("raceHorseCount") or 0
        t3 = [int(x) for x in top3]
        pmap, psrc = _pop_map(d, f)                        # 🔴 말마다 자기 인기순위를 붙인다
        for i, x in enumerate(dks):
            if x.get("no") is None:
                continue
            no = int(x["no"])
            pop = pmap.get(no)
            out.append({"no": no, "rank": i + 1, "forced": bool(x.get("forced")),
                        "anom": int(x.get("anomCount") or 0),
                        "smart": bool(x.get("smartMoney")),
                        "place": (t3.index(no) + 1) if no in t3 else 0,
                        "nh": int(nh or 0), "cat": d.get("category") or "?",
                        "pop": pop, "popsrc": psrc,
                        "mbase": _base_market(nh, pop)})
    return out


def report_dark3(rows, label, base_mode="random", sport="horse"):
    """🔴 `--base` 는 **기존 계산을 지우지 않는다** — random 은 언제나 함께 찍는다(전후 병기)."""
    n = len(rows)
    if not n:
        print("  %-22s n=0 — 판정 불가" % label)
        return
    in3 = sum(1 for r in rows if r["place"])
    exp = [3.0 / r["nh"] for r in rows if r["nh"] >= 4]
    base = 100.0 * statistics.mean(exp) if exp else 0.0
    got = 100.0 * in3 / n
    mark = "⚠n<30" if n < 30 else ("🟢" if got >= base * 1.15 else ("🔴" if got <= base * 0.9 else "🟡"))
    p1 = sum(1 for r in rows if r["place"] == 1)
    p2 = sum(1 for r in rows if r["place"] == 2)
    p3 = sum(1 for r in rows if r["place"] == 3)
    line = ("  %-22s n=%4d | 1착 %3d · 2착 %3d · 3착 %3d · 미입상 %4d | **3착이내 %5.1f%%** (무작위 %4.1f%% · 배수 %.2f) %s"
            % (label, n, p1, p2, p3, n - in3, got, base,
               (got / base) if base else 0, mark))
    if base_mode == "market":
        # 🔴 경마 실측 기저선이다. 경륜에 쓰지 않는다(인기 개념이 다르다).
        if sport != "horse":
            line += "  | market: 🔴 경마 기저선을 %s 에 쓰지 않는다" % sport
        else:
            mb = [r for r in rows if r.get("mbase") is not None]
            cov = 100.0 * len(mb) / n
            if len(mb) < 30:
                line += "  | market: ⚠ 적용 n=%d(<30) 판정 불가 · 커버 %.0f%%" % (len(mb), cov)
            else:
                mbase = statistics.mean(r["mbase"] for r in mb)
                mgot = 100.0 * sum(1 for r in mb if r["place"]) / len(mb)
                mmark = "🟢" if mgot >= mbase * 1.15 else ("🔴" if mgot <= mbase * 0.9 else "🟡")
                line += ("  ‖ **market** n=%d(커버 %.0f%%) 3착이내 %.1f%% (시장기저 %.1f%% · **배수 %.2f**) %s"
                         % (len(mb), cov, mgot, mbase, (mgot / mbase) if mbase else 0, mmark))
    print(line)


def measure_drop3(sport="horse", pattern="2026_0*"):
    """[급락 신호의 3착 기여도 (2026-08-01 신설)] — **완전 읽기 전용**.

    🔴 왜: 급락 신호는 지금까지 **복승(1·2착) 판정으로만** 평가됐다. 3착 기준은 분모가 다르다.
    ⚠ 🔴 **한계를 먼저 밝힌다**: `drops_raw` 는 **조합 단위**(`{"combo":[1,9], "pct":-34}`)다.
      말 단위 급락은 **그 말이 낀 조합들의 평균**으로 환원한다 — 엔진의 `_excess_drop_analysis`
      와 같은 방식이다. **한 조합을 두 말에 그대로 귀속시키면 중복 계상**이 되므로 평균을 쓴다.
    ⚠ 무작위 기대 = 3 ÷ 두수.
    """
    out = []
    for f in sorted(glob.glob(os.path.join(BASE, "data", "analysis_log", pattern + ".json"))):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        if (d.get("sport") or "") != sport:
            continue
        res = d.get("result") or {}
        top3 = [res.get("1st"), res.get("2nd"), res.get("3rd")]
        if any(x is None for x in top3):
            continue
        dr = d.get("drops_raw") or []
        if not dr:
            continue
        cp = d.get("corePicks") or {}
        nh = int(cp.get("raceHorseCount") or 0)
        if nh < 4:
            continue
        acc = {}
        for x in dr:
            try:
                pct = float(x.get("pct"))
            except (TypeError, ValueError):
                continue
            for n0 in (x.get("combo") or []):
                acc.setdefault(int(n0), []).append(pct)
        t3 = [int(v) for v in top3]
        for no, ps in acc.items():
            out.append({"no": no, "mean": sum(ps) / len(ps), "n_combo": len(ps),
                        "place": (t3.index(no) + 1) if no in t3 else 0,
                        "nh": nh, "cat": d.get("category") or "?"})
    return out


def report_drop3(rows, label):
    n = len(rows)
    if not n:
        print("  %-24s n=0 — 판정 불가" % label)
        return
    in3 = sum(1 for r in rows if r["place"])
    base = 100.0 * statistics.mean([3.0 / r["nh"] for r in rows])
    got = 100.0 * in3 / n
    mark = "⚠n<30" if n < 30 else ("🟢" if got >= base * 1.15 else ("🔴" if got <= base * 0.9 else "🟡"))
    print("  %-24s n=%5d | 3착이내 %5.1f%% (무작위 %4.1f%% · 배수 %.2f) %s"
          % (label, n, got, base, (got / base) if base else 0, mark))


def measure_conf(rows, label):
    """🔴 [2026-08-07] 확신도 1위가 실제로 3착에 드나 — **기저선(3/두수) 대비 배수**로 잰다.

    왜: 8/7 경륜 3경주에서 확신도 1위가 전부 3착 밖이었다(44점·74.4점·39.1점).
      점수가 높아도 안 오면 화면 표시가 회원을 오도한다.
    ⚠ 기저선은 무작위(3 ÷ 두수)다. 경륜에는 인기별 기저선이 없다(pop_baseline 은 JRA 전용).
      그 한계를 결과에 명시한다.
    ⚠ n<30 은 판정 불가로 표시한다."""
    sub = [r for r in rows if r.get("conf1") and r.get("top3") and r.get("nh")]
    if not sub:
        print("  확신도 저장분 없음")
        return
    hit = sum(1 for r in sub if int(r["conf1"]) in set(r["top3"]))
    exp = sum(3.0 / r["nh"] for r in sub) / len(sub)
    act = hit / float(len(sub))
    print("\n[확신도 1위 · %s] n=%d · 3착 진입 %.1f%% · 기저(3/두수) %.1f%% · 배수 %.2f%s"
          % (label, len(sub), 100 * act, 100 * exp, (act / exp) if exp else 0,
             "  ⚠판정불가(n<30)" if len(sub) < 30 else ""))
    band = {}
    for r in sub:
        v = r.get("confv")
        if v is None:
            continue
        b = int(float(v) // 10) * 10
        d = band.setdefault(b, [0, 0, 0.0])
        d[0] += 1
        d[1] += 1 if int(r["conf1"]) in set(r["top3"]) else 0
        d[2] += 3.0 / r["nh"]
    if not band:
        print("  ⚠ 확신도 **점수값**이 저장돼 있지 않다 — 구간별 분해 불가(마번만 저장된다)")
        return
    print("  구간별(10점 단위):")
    for b in sorted(band):
        n, h, e = band[b]
        eb = e / n
        print("    %3d~%3d  n=%3d  3착 %5.1f%%  기저 %5.1f%%  배수 %.2f%s"
              % (b, b + 9, n, 100 * h / n, 100 * eb, (h / n) / eb if eb else 0,
                 "  ⚠n<30" if n < 30 else ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", default="cycle")
    ap.add_argument("--pattern", default="2026_07_*")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--conf", action="store_true", help="확신도 1위가 3착에 드나(배수)")
    ap.add_argument("--trio", action="store_true", help="삼복승 섀도우 성적(별도 측정)")
    ap.add_argument("--dark3", action="store_true", help="복병 3착 이내 진입률(복승 기준과 별개)")
    ap.add_argument("--drop3", action="store_true", help="급락 폭별 3착 이내 진입률")
    ap.add_argument("--ev-sweep", dest="ev_sweep", action="store_true",
                    help="EV 임계 스윕 — 74.5%% 유지하며 적중배당 중앙 최대인 임계를 찾는다")
    ap.add_argument("--forecast", action="store_true",
                    help="F3 — Gemini 고배당 능력(시장과 다른 답 && 적중 · 가상 회수율)")
    ap.add_argument("--weights", action="store_true",
                    help="전적이 실제로 순위를 바꾸는가 — 현행(60/40) ↔ 시장100% ↔ 전적100% 대조")
    # 🔴 [2026-08-02] 기저 선택. **random 을 지우지 않는다** — 항상 함께 찍어 전후를 병기한다.
    ap.add_argument("--base", choices=["random", "market"], default="random",
                    help="무작위(3÷두수) ↔ 인기별 실측 기저선. market 은 **경마 전용**")
    ap.add_argument("--pattern-mult", dest="pattern_mult", action="store_true",
                    help="패턴(P2) 말의 3착 배수 · 대조군 나란히 · 회수율의 선행 지표(대표 지시)")
    a = ap.parse_args()
    if a.pattern_mult:
        sports = ["horse", "cycle"] if a.sport in ("all", "any") else [a.sport]
        print("=" * 90)
        print("패턴 3착 배수 · %s   🔴 대조군과의 차이가 답 · n<200 방향만(사후 하향 금지)" % a.pattern)
        print("=" * 90)
        for sp in sports:
            G, _ = measure_pattern(sp, a.pattern, "P2_우상향")
            report_pattern(G, sp, "P2_우상향")
        return
    if a.weights:
        out = measure_weights(a.sport, a.pattern)
        print("=" * 110)
        print("전적의 실질 영향 · %s · %s   🔴 판정선 = 환급률 %.1f%%" % (a.sport, a.pattern, PAYBACK))
        print("=" * 110)
        print("🔴 전적은 경주 전 확정이고 마감까지 재수집되지 않는다 → 틱별 순위 변동은 **배당 때문**이다.")
        print("⚠ 전적축은 `record_score` 상위3 = **하한 근사**(보너스·등급 반영분이 빠진다).")
        report_weights(out)
        return
    if a.forecast:
        rows = measure_forecast(None if a.sport in ("all", "any") else a.sport, a.pattern)
        print("=" * 110)
        print("F3 · Gemini **고배당 능력** · %s · %s   🔴 판정선 = 환급률 %.1f%%"
              % (a.sport, a.pattern, PAYBACK))
        print("=" * 110)
        print("🔴 F2(평균 적중 수)는 **배당을 보지 않는다.** 82배와 1.2배가 동점이다 — F3 가 그것을 잰다.")
        print("⚠ 날짜 포함 조인(원칙 16) · 확정배당 기준 · 정제 필터는 measure() 와 동일.")
        report_forecast(rows, "전체")
        for sp in ("cycle", "horse"):
            sub = [r for r in rows if r["sport"] == sp]
            if sub:
                print()
                report_forecast(sub, sp)
        return
    if a.conf:
        # 🔴 [2026-08-07] 확신도 1위가 실제로 3착에 드나. 종목별로 따로 낸다(경마·경륜이 자주 반대다).
        for _sp in (["horse", "cycle"] if a.sport in ("all", "any") else [a.sport]):
            measure_conf(load_races(_sp, a.pattern), "%s · %s" % (_sp, a.pattern))
        print("\n⚠ 기저선은 무작위(3÷두수)다 — 경륜에는 인기별 기저선이 없다(pop_baseline 은 JRA 전용).")
        return 0
    if a.ev_sweep:
        out = measure_ev_sweep(a.sport, a.pattern)
        if a.json:
            print(json.dumps(out, ensure_ascii=False, indent=1))
            return
        print("=" * 110)
        print("EV 임계 스윕 · %s · %s   🔴 판정선 = 환급률 %.1f%%" % (a.sport, a.pattern, PAYBACK))
        print("=" * 110)
        print("🔴 찾는 것 = **74.5%% 를 지키면서 적중배당 중앙이 최대인 임계**(회수율 최대가 아니다).")
        print("⚠ 반사실 시뮬레이션이다 — 강등분은 실제로는 회원에게 나가지 않았다.")
        report_ev_sweep(out)
        return
    if a.drop3:
        rows = measure_drop3(a.sport, a.pattern)
        print("=" * 110)
        print("급락 신호의 **3착 기여도** · %s · %s" % (a.sport, a.pattern))
        print("=" * 110)
        print("⚠ 🔴 `drops_raw` 는 조합 단위다. 말 단위는 **그 말이 낀 조합들의 평균 급락률**로 환원했다.")
        print("⚠ 🔴 복승 기준 발동률(경마 84.5%·경륜 61.6%)과 **분모가 다르다.** 섞어 인용하지 말 것.")
        report_drop3(rows, "전체(급락 데이터 보유)")
        for lo, hi, lab in ((-1e9, -50, "평균급락 -50% 이하"), (-50, -40, "-40 ~ -50%"),
                            (-40, -30, "-30 ~ -40%"), (-30, -20, "-20 ~ -30%"),
                            (-20, -10, "-10 ~ -20%"), (-10, 0, "-10 ~ 0%"),
                            (0, 1e9, "상승(0% 이상)")):
            report_drop3([r for r in rows if lo <= r["mean"] < hi], lab)
        print("  ── 발동률(전체 대비 비중) ──")
        tot = len(rows) or 1
        for th in (-20, -30, -40, -50):
            k = len([r for r in rows if r["mean"] <= th])
            print("    평균급락 %d%% 이하 : %5d / %5d = %5.1f%%  %s"
                  % (th, k, tot, 100.0 * k / tot,
                     "🟢적정(5~30%)" if 5 <= 100.0 * k / tot <= 30 else "🔴부적정"))
        return 0
    if a.dark3:
        rows = measure_dark3(a.sport, a.pattern)
        print("=" * 126)
        print("복병 **3착 이내** 진입률 · %s · %s" % (a.sport, a.pattern))
        print("=" * 126)
        print("⚠ 🔴 복승(1·2착) 기준 값과 **분모가 다르다.** 섞어 인용하지 말 것.")
        print("⚠ 무작위 기대 = 3 ÷ 두수 (경주별 평균). 배수 1.0 이면 신호에 우위가 없다는 뜻이다.")
        if a.base == "market":
            pb = _popbase()
            print("🔴 --base market : 인기별 실측 기저선을 **말마다** 적용한다(경마 전용).")
            print("   기저선: %s경주 %s두 · 셀 %d · 생성 %s"
                  % (pb.get("races", "?"), pb.get("horses", "?"),
                     len(pb.get("cells") or {}), pb.get("builtAt", "?")))
            srcs = {}
            for r0 in rows:
                srcs[r0.get("popsrc") or "?"] = srcs.get(r0.get("popsrc") or "?", 0) + 1
            print("   인기순위 출처: %s" % srcs)
            print("   ⚠ '복승최저순(대용)' 은 진짜 인기 표기가 아니다 — 근사치임을 명시한다.")
            print("   ⚠ random 은 지우지 않고 항상 함께 찍는다(전후 병기).")
        _R = lambda rs, lab: report_dark3(rs, lab, a.base, a.sport)
        _R(rows, "전체")
        for k, lab in ((1, "복병 1순위"), (2, "복병 2순위"), (3, "복병 3순위")):
            _R([r for r in rows if r["rank"] == k], lab)
        _R([r for r in rows if r["forced"]], "forced=True")
        _R([r for r in rows if r["smart"]], "smartMoney=True")
        for lo, hi, lab in ((1, 2, "anomCount 1~2"), (3, 5, "anomCount 3~5"),
                            (6, 9, "anomCount 6~9"), (10, 999, "anomCount 10+")):
            _R([r for r in rows if lo <= r["anom"] <= hi], lab)
        _R([r for r in rows if r["anom"] == 0], "anomCount 0")
        cats = {}
        for r0 in rows:
            cats.setdefault(r0["cat"], []).append(r0)
        for c, rs in sorted(cats.items(), key=lambda x: -len(x[1]))[:4]:
            _R(rs, "[%s]" % c)
        return 0
    if a.trio:
        rows = measure_trio(a.sport, a.pattern)
        print("=" * 118)
        print("삼복승 **섀도우** 성적 · %s · %s   🔴 판정선 = 환급률 %.1f%%" % (a.sport, a.pattern, PAYBACK))
        print("=" * 118)
        print("⚠ 화면·판정에서 빠진 조합이다(회원에게 안 나갔다). **반사실 시뮬레이션**이다.")
        report_trio(rows, "전체")
        cats = {}
        for r0 in rows:
            cats.setdefault(r0["cat"], []).append(r0)
        for c, rs in sorted(cats.items(), key=lambda x: -len(x[1])):
            report_trio(rs, c)
        # 🔴 [2026-08-01] 복병 포함 / 미포함 분해. **조합 단위**로 가른다(경주 단위가 아니다).
        print("")
        print("  ── 복병 포함 여부로 분해 (⚠ 조합 단위 · 같은 경주가 양쪽에 나뉜다) ──")
        def _split(rs, pred, label):
            sub = []
            for r0 in rs:
                cs = [c for c in r0["combos"] if pred(r0, c)]
                if cs:
                    sub.append({**r0, "combos": cs, "n": len(cs)})
            if sub:
                report_trio(sub, label)
            else:
                print("  %-16s 조합 0 — 판정 불가" % label)
        _split(rows, lambda r0, c: bool(set(c) & set(r0["dk"][:2])), "복병(상위2) 포함")
        _split(rows, lambda r0, c: not (set(c) & set(r0["dk"][:2])), "복병 미포함")
        _split(rows, lambda r0, c: r0["dk1"] is not None and r0["dk1"] in c, "복병 1순위 포함")
        return 0
    r = measure(a.sport, a.pattern)
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return 0
    print("=" * 96)
    print("회수율 측정 · %s · %s   🔴 판정선 = 환급률 %.1f%%" % (a.sport, a.pattern, PAYBACK))
    print("=" * 96)
    print("⚠ 분모: 전체 %d → 정제(괴리 %.1f~%.1f배) %d경주 (%.1f%%)"
          % (r["denom_all"], CLEAN_LO, CLEAN_HI, r["denom_clean"],
             100.0 * r["denom_clean"] / max(r["denom_all"], 1)))
    print()
    print("  %-20s %-9s %-8s %-9s %-9s %-8s %s"
          % ("안", "오염", "🔴정제", "1제외", "3제외", "배당중앙", "74.5 대비"))
    for p in r["plans"]:
        mk = "🟢" if p["ex3"] >= PAYBACK else ("🟡" if p["rate"] >= PAYBACK else "🔴")
        print("  %-20s %6.1f%%   %6.1f%%  %6.1f%%   %6.1f%%   %5.1f배   %+6.1f%%p %s"
              % (p["name"], p["rate_dirty"], p["rate"], p["ex1"], p["ex3"],
                 p["median_odds"], p["vs_payback"], mk))
    if r.get("ci"):
        c = r["ci"]
        print()
        print("  95%%CI(%s · 부트스트랩 %d회 · n=%d): [%.1f%%, %.1f%%]"
              % (c["plan"], BOOT_N, r["denom_clean"], c["lo"], c["hi"]))
        print("  환급률 %.1f%% 포함: %s" % (PAYBACK,
              "🔴 예 → **통계적으로 구분 불가**" if c["includes_payback"] else "아니오"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
