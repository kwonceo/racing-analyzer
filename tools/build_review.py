# -*- coding: utf-8 -*-
"""[복기 자동 기록] 매 경주 결과를 **읽기 전용**으로 복기해 별도 파일로 남긴다.

■ 무엇을 (코드 용어 없이)
  적중이든 미적중이든 **같은 형식**으로, 1·2착 말이 우리 순위 몇 위였고 전적이 어땠는지,
  놓친 배당이 얼마였는지를 경주마다 한 파일로 남긴다. 학습의 입력이 된다.

■ 🔴 원칙 (2026-08-06 승인)
  · `_apply_result_learning`(app.py) 을 **건드리지 않는다.** analysis_log + race_results 를
    **날짜 포함 조인**해 별도 파일(logs/race_review/)로 쓴다 → 재기동 위험 0 · 소급 생성 가능.
  · 🔴 **적중·미적중을 같은 깊이로** 기록한다(적중만 남기면 확증 편향이 재발한다).
  · 🔴 **우리 순위는 keyHorses** 다 — frozen.keyHorses(마감 동결) 우선, 없으면 최상위 keyHorses.
    (axis 는 조합 생성용이라 우리 순위가 아니다 · 대표 지시 2026-08-06)
  · 🔴 measure 지표를 임의로 바꾸지 않는다 — 없으면 None 으로 두고 명시한다(원칙 8-D).

사용:
  python tools/build_review.py            # --dry (기본) : 몇 건 생길지만 출력, 안 씀
  python tools/build_review.py --apply    # 실제 저장
  python tools/build_review.py --date 2026-08-05 --apply
  python tools/build_review.py --sample 3 # 적중3·미적중3 육안 확인용 출력(원칙 4)
"""
import argparse
import gzip
import io
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AL = os.path.join(BASE, "data", "analysis_log")
RR = os.path.join(BASE, "data", "race_results")
OUT = os.path.join(BASE, "logs", "race_review")
# 🔴 [2026-08-06] stdout 래핑은 **main() 안**에서만 한다.
#   app.py 가 이 모듈을 import 해 autorun() 을 부르므로, top-level 에서 sys.stdout 을
#   TextIOWrapper 로 덮으면 **app.py 의 stdout 을 통째로 바꿔** 서버 로그가 깨진다.
REVIEW_STAMP = os.path.join(BASE, "data", "_review_last.json")
PSTAT = os.path.join(BASE, "data", "pattern_stats.json")

# 패턴 정의는 thresholds 를 재사용(목록 이중화 금지)
try:
    sys.path.insert(0, os.path.join(BASE, "tools"))
    import thresholds as _TH
    _PATTERNS = getattr(_TH, "FORM_PATTERNS", {})
except Exception:
    _PATTERNS = {}


def _load(p):
    try:
        if p.endswith(".gz"):
            with gzip.open(p, "rt", encoding="utf-8") as f:
                return json.load(f)
        with io.open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _our_rank(kh, no):
    """우리 순위(1-based) 또는 None(순위 밖)."""
    try:
        return kh.index(int(no)) + 1
    except (ValueError, AttributeError, TypeError):
        return None


# 🔴 [2026-08-06] 경륜 recent 전용 파서 — 대표 지시 「경륜 recent가 문자열이라 P2가 안 걸린다」.
#   원문 실측(원칙: 추측 금지, 원문 먼저): recent = '8/ 3 初特選 ６着 12.3 8/ 4 準決勝 ２着 11.6'
#     · 착순 = **전각 숫자 + 着** (６着·２着) · 뒤 실수(11.6)는 타임이라 着가 없어 안 걸린다
#     · 방향 = **오래→최신**(8/3 → 8/4). 경마 recent(최신-앞)와 **반대**다.
#   경륜은 recent 에 이번 개최 2전만, prev1 에 직전 개최 3전이 있다 → 둘을 시간순으로 이어
#   최근 3전을 복원한다(prev1 먼저 · recent 나중).
_ZEN2HAN = {ord("０") + i: ord("0") + i for i in range(10)}


def _keirin_placings(recent, prev1=None):
    """경륜 recent/prev1 문자열 → 최근 착순 [최신, …]. 없으면 []. (원칙 8-D: 없으면 빈 값)"""
    seq = []                                          # 시간순(오래→최신)으로 모은다
    for s in (prev1, recent):                         # prev1(직전 개최) 먼저, recent(이번) 나중
        if not isinstance(s, str):
            continue
        for m in re.finditer(r"(\d)\s*着", s.translate(_ZEN2HAN)):
            seq.append(int(m.group(1)))
    return seq[::-1]                                  # 최신-앞으로 뒤집는다(경마와 방향 통일)


def _pattern_tags(recent, prev1=None):
    """그 말의 최근 착순 시계열이 어느 패턴에 걸리나. recent = [최신, …] 또는 경륜 문자열."""
    tags = []
    if isinstance(recent, str):                       # 🔴 경륜 — 문자열이면 전용 파서로 착순 복원
        seq = _keirin_placings(recent, prev1)
    else:
        seq = [x for x in (recent or []) if isinstance(x, int)]
    if len(seq) >= 3:
        s3 = seq[:3]
        # P2 우상향 — 최근 3전 단조 개선(비감소) AND 폭 ≥ 2 (thresholds 정의)
        if all(s3[i] <= s3[i + 1] for i in range(2)) and (s3[-1] - s3[0]) >= 2:
            tags.append("P2_우상향")
    if len(seq) >= 2 and seq[0] == seq[1] and 4 <= seq[0] <= 6:
        tags.append("P1_반복착순")
    return tags


def _miss_class(doc, cp, top2, kh):
    """미적중 4분류. 적중이면 None.
    ① 후보 밖   : 1·2착이 우리 순위(keyHorses)에 둘 다 없다 (= 못 봤다)
    ② 미생성    : 둘 다 순위엔 있는데 그 조합을 안 만들었다
    ③ 잘림      : 만들었다가 강등(quinellaRef)됐다
    🔴 ⑤ 원인미규명 : 위 어디에도 안 맞는 나머지. **「어쩔수없음」이 아니다** — 아직 안 본 것이다
       (2026-08-06 대표 지시: "어쩔 수 없는 건 없다. 이름이 사고를 가둔다").
       `_unknown_sub()` 로 다시 넷으로 쪼갠다.
    """
    disp = [tuple(sorted(c)) for c in ((cp.get("displayedCombos") or {}).get("quinellas") or [])]
    tkey = tuple(sorted(int(x) for x in top2 if x is not None))
    if len(tkey) == 2 and tkey in disp:
        return None                              # 적중
    r1 = _our_rank(kh, top2[0]); r2 = _our_rank(kh, top2[1])
    if r1 is None and r2 is None:
        return "①후보밖"
    ref = [tuple(sorted(c)) for c in
           ([x.get("combo") for x in (cp.get("quinellaRef") or [])])]
    if len(tkey) == 2 and tkey in ref:
        return "③잘림"
    if r1 and r2:
        return "②미생성"
    return "⑤원인미규명"


def _unknown_sub(doc, cp, top2, kh):
    """🔴 [2026-08-06] 「원인 미규명」을 넷으로 다시 쪼갠다 — ④(진짜 무작위)로 가둬 두지 않는다.
    ⑤a 순위낮음  : 1·2착이 후보 안에 있었으나 **우리 순위 4위 이하**(= 봤는데 낮게 봤다 · 순위 문제)
    ⑤b 조합밀림  : 순위는 상위(1~3)인데 조합 우선순위에서 밀렸다(= 조합 선택 문제)
    🔴 ⑤c 전적공부 : 1·2착 중 하나가 **순위 밖인데 전적이 좋다**(record_score 상위권 · 전적 공부 대상)
    ④ 무작위     : ①②③⑤a⑤b⑤c 를 다 보고도 단서가 없을 때만
    """
    hs = {h.get("no"): h for h in (doc.get("horses") or []) if isinstance(h, dict)}
    scores = sorted([h.get("record_score") for h in hs.values()
                     if isinstance(h.get("record_score"), (int, float))], reverse=True)
    hi_cut = scores[max(0, len(scores) // 3 - 1)] if scores else 0   # 상위 1/3 경계
    r1 = _our_rank(kh, top2[0]); r2 = _our_rank(kh, top2[1])
    ranks = [r for r in (r1, r2) if r]
    # ⑤a 순위낮음 — 후보 안이지만 4위 이하가 있다
    if any(r >= 4 for r in ranks):
        return "⑤a순위낮음"
    # ⑤c 전적공부 — 순위 밖인 말이 전적 상위권
    for no, r in ((top2[0], r1), (top2[1], r2)):
        if r is None:
            sc = (hs.get(no) or {}).get("record_score")
            if isinstance(sc, (int, float)) and sc >= hi_cut and hi_cut > 0:
                return "⑤c전적공부"
    # ⑤b 조합밀림 — 순위는 상위인데 조합에서 밀렸다(순위 있는 게 하나뿐이거나 상위)
    if ranks and all(r <= 3 for r in ranks):
        return "⑤b조합밀림"
    return "④무작위"


def _horse_row(doc, no, kh):
    """1·2착 말 1건의 복기 행."""
    hs = doc.get("horses") or []
    ent = (doc.get("raw_profile") or {}).get("entries") or []
    cp = doc.get("corePicks") or {}
    hmap = {h.get("no"): h for h in hs if isinstance(h, dict)}
    emap = {e.get("no"): e for e in ent if isinstance(e, dict)}
    h = hmap.get(no) or {}
    e = emap.get(no) or {}
    recent = e.get("recent") or e.get("pastPlacings")
    darks = {d.get("no"): d for d in (cp.get("darkHorsePicks") or []) if isinstance(d, dict)}
    return {
        "no": no,
        "ourRank": _our_rank(kh, no),                 # 🔴 keyHorses 순위(우리 순위)
        "record_score": h.get("record_score"),
        "grade": h.get("grade"),
        "confTop1": (cp.get("confTop1") == no),       # 확신도 1위였나
        "dark": (no in darks),
        "anomCount": (darks.get(no) or {}).get("anomCount"),
        "recent": recent,
        "fieldSizes": e.get("fieldSizes"),
        "corners": e.get("corners"),
        "last3f": e.get("last3fList"),
        "patternTags": _pattern_tags(recent, e.get("prev1")),   # 🔴 경륜은 prev1 도 넘긴다
    }


def build_one(fn):
    """analysis_log 파일 1건 → 복기 dict(결과 없으면 None)."""
    doc = _load(os.path.join(AL, fn))
    if not isinstance(doc, dict):
        return None
    r = doc.get("result") or {}
    top2 = [r.get("1st"), r.get("2nd")]
    if not (r.get("1st") and r.get("2nd")):
        return None                                    # 결과 미확정 — 복기 대상 아님
    cp = doc.get("corePicks") or {}
    kh = (doc.get("frozen") or {}).get("keyHorses") or doc.get("keyHorses") or []
    payouts = r.get("payouts") or {}
    disp = [tuple(sorted(c)) for c in ((cp.get("displayedCombos") or {}).get("quinellas") or [])]
    tkey = tuple(sorted(int(x) for x in top2 if x is not None))
    hit = (len(tkey) == 2 and tkey in disp)
    q = payouts.get("quinella")
    mc = None if hit else _miss_class(doc, cp, top2, kh)
    sub = _unknown_sub(doc, cp, top2, kh) if mc == "⑤원인미규명" else None
    r1 = _our_rank(kh, top2[0]); r2 = _our_rank(kh, top2[1])
    # 🔴 순위 진단: 봤는데 낮게(4위+) vs 못 봤다(순위밖)
    diag = None
    if not hit:
        if any(r and r >= 4 for r in (r1, r2)):
            diag = "봤는데낮게(4위+)"
        elif r1 is None and r2 is None:
            diag = "못봤다(둘다순위밖)"
        elif (r1 is None) != (r2 is None):
            diag = "한쪽만순위밖"
        else:
            diag = "상위인데놓침"
    return {
        "raceKey": doc.get("raceKey") or fn[:-5],
        "date": fn[:10].replace("_", "-"),
        "sport": doc.get("sport"),
        "fieldSize": cp.get("raceHorseCount") or len(doc.get("horses") or []),
        "source": (doc.get("raw_profile") or {}).get("source"),
        "frozen": bool(doc.get("frozen")),
        "hit": hit,                                    # 🔴 적중·미적중 동형
        "miss_class": mc,
        "unknown_sub": sub,                            # 🔴 원인미규명 재분류(넷)
        "rank_diag": diag,                             # 🔴 순위 문제 vs 정보 문제
        "result": {"1st": r.get("1st"), "2nd": r.get("2nd"), "3rd": r.get("3rd")},
        "ourKeyHorses": kh,                            # 🔴 우리 순위(frozen 우선)
        "our_source": "frozen" if (doc.get("frozen") or {}).get("keyHorses") else "top",
        "top12": [_horse_row(doc, top2[0], kh), _horse_row(doc, top2[1], kh)],
        "payout": {"quinella": q, "trifecta": payouts.get("trifecta")},
        "missed_payback": (None if hit else q),        # 미적중이면 놓친 복승 배당
    }


# ══════════ [패턴 성적 누적 · 2026-08-06 작업2] ══════════
#   🔴 read-modify-write. 그날 것만 재계산해 그 날짜 슬롯을 교체한다(다른 날짜는 병합 유지).
#     계수기가 두 번 지워진 사고(빈 값으로 덮어씀)를 반복하지 않기 위해 **절대 통째 덮지 않는다**.
#   🔴 회수율은 None 이다 — 복기 기록에 그 말이 낀 조합의 배당이 없다(원칙 8-D: 없으면 만들지 않는다).
#     발동(패턴 태그 가진 말 수)·in3(그중 3착 이내 든 말 수)는 정확히 누적한다.
def pattern_scan(doc):
    """그 경주 전체 말의 패턴 발동·성적. → {패턴: {발동, in3}} (말 단위)."""
    r = doc.get("result") or {}
    top3 = set()
    for k in ("1st", "2nd", "3rd"):
        try:
            top3.add(int(r.get(k)))
        except (TypeError, ValueError):
            pass
    ent = [e for e in ((doc.get("raw_profile") or {}).get("entries") or []) if isinstance(e, dict)]
    out = {}
    for e in ent:
        tags = _pattern_tags(e.get("recent") or e.get("pastPlacings"), e.get("prev1"))
        try:
            no = int(e.get("no"))
        except (TypeError, ValueError):
            no = None
        for tg in tags:
            o = out.setdefault(tg, {"발동": 0, "in3": 0})
            o["발동"] += 1
            if no is not None and no in top3:
                o["in3"] += 1
    return out


def _pattern_cum(cur, tag):
    """그 패턴의 누적 발동 수(전 날짜 합)."""
    return sum((v or {}).get("발동", 0) for v in (cur.get(tag) or {}).values())


def update_pattern_stats(date, files):
    """그날 파일 전체를 재계산해 pattern_stats.json 의 그 날짜 슬롯만 교체(병합)."""
    cur = _load(PSTAT)
    if not isinstance(cur, dict):
        cur = {}
    day = {}
    for fn in files:
        doc = _load(os.path.join(AL, fn))
        if not isinstance(doc, dict):
            continue
        r = doc.get("result") or {}
        if not (r.get("1st") and r.get("2nd")):
            continue
        for tg, o in pattern_scan(doc).items():
            d = day.setdefault(tg, {"발동": 0, "in3": 0, "경주": 0, "회수율": None})
            d["발동"] += o["발동"]
            d["in3"] += o["in3"]
            d["경주"] += 1
    for tg, d in day.items():                          # 🔴 그 날짜 슬롯만 교체 — 다른 날짜 유지
        cur.setdefault(tg, {})[date] = d
    _atomic(PSTAT, cur)
    return cur


def autorun(dates):
    """[스케줄러용] 새로 확정된 복기만 저장 + pattern_stats 갱신. 완전 읽기 전용(입력 파일 무수정).
    반환 {written, p2_cum, changed}. dates = [오늘, 어제] 같은 YYYY-MM-DD 목록."""
    stamp = _load(REVIEW_STAMP)
    if not isinstance(stamp, dict):
        stamp = {}
    prefs = tuple(d.replace("-", "_") for d in dates)
    stamp = {k: v for k, v in stamp.items() if k.startswith(prefs)}   # 범위 밖은 버린다
    os.makedirs(OUT, exist_ok=True)
    written = 0
    changed = set()
    try:
        names = os.listdir(AL)
    except OSError:
        names = []
    for fn in names:
        if not (fn.startswith(prefs) and fn.endswith(".json")):
            continue
        try:
            mt = os.path.getmtime(os.path.join(AL, fn))
        except OSError:
            continue
        if stamp.get(fn) == mt:                        # 변화 없음 — 이미 처리
            continue
        rv = build_one(fn)
        stamp[fn] = mt                                 # 결과 미확정이어도 mtime 기록(재확인 방지)
        if rv is None:
            continue
        key = re.sub(r"[^0-9A-Za-z가-힣]+", "_",
                     "%s_%s" % (fn[:10], rv["raceKey"])).strip("_")
        _atomic(os.path.join(OUT, key + ".json"), rv)
        written += 1
        changed.add(rv["date"])
    _atomic(REVIEW_STAMP, stamp)
    cur = None
    for date in changed:                               # 변경된 날짜만 패턴 재계산
        pref = date.replace("-", "_")
        files = [f for f in names if f.startswith(pref) and f.endswith(".json")]
        cur = update_pattern_stats(date, files)
    if cur is None:
        cur = _load(PSTAT)
        if not isinstance(cur, dict):
            cur = {}
    return {"written": written, "p2_cum": _pattern_cum(cur, "P2_우상향"),
            "changed": sorted(changed)}


def main():
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="실제 저장(기본은 dry)")
    ap.add_argument("--date", default=None, help="YYYY-MM-DD (없으면 전체)")
    ap.add_argument("--sample", type=int, default=0, help="적중N·미적중N 육안 확인 출력")
    a = ap.parse_args()
    pref = a.date.replace("-", "_") if a.date else "2026_0"
    files = sorted(f for f in os.listdir(AL) if f.startswith(pref) and f.endswith(".json"))
    made = hit = miss = 0
    hit_ex, miss_ex = [], []
    if a.apply:
        os.makedirs(OUT, exist_ok=True)
    for fn in files:
        rv = build_one(fn)
        if rv is None:
            continue
        made += 1
        if rv["hit"]:
            hit += 1
            if len(hit_ex) < a.sample:
                hit_ex.append(rv)
        else:
            miss += 1
            if len(miss_ex) < a.sample:
                miss_ex.append(rv)
        if a.apply:
            key = re.sub(r"[^0-9A-Za-z가-힣]+", "_", "%s_%s" % (fn[:10], rv["raceKey"])).strip("_")
            _atomic(os.path.join(OUT, key + ".json"), rv)
    print("복기 대상 %d경주 (적중 %d · 미적중 %d) · %s"
          % (made, hit, miss, "저장함" if a.apply else "dry(안 씀)"))
    # 미적중 분류 분포 + 원인미규명 재분류 + 순위진단
    if made:
        from collections import Counter
        cls = Counter(); sub = Counter(); diag = Counter(); diag_h = Counter()
        for fn in files:
            rv = build_one(fn)
            if rv and not rv["hit"]:
                cls[rv["miss_class"]] += 1
                if rv.get("unknown_sub"):
                    sub[rv["unknown_sub"]] += 1
                diag[rv.get("rank_diag")] += 1
                if rv.get("sport") == "horse":
                    diag_h[rv.get("rank_diag")] += 1
        print("  미적중 분류:", dict(cls))
        print("  🔴 원인미규명 재분류:", dict(sub))
        print("  🔴 순위진단(전체):", dict(diag))
        print("  🔴 순위진단(경마만):", dict(diag_h))
    for lab, ex in (("적중", hit_ex), ("미적중", miss_ex)):
        for rv in ex:
            print("  [%s] %s 결과%s-%s ourKH=%s" % (lab, rv["raceKey"],
                  rv["result"]["1st"], rv["result"]["2nd"], rv["ourKeyHorses"]))
            for t in rv["top12"]:
                print("     %s착말 no=%s 우리순위=%s score=%s 태그=%s" %
                      ("1·2", t["no"], t["ourRank"], t["record_score"], t["patternTags"]))
    return 0


def _atomic(p, obj):
    tmp = p + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, p)


if __name__ == "__main__":
    sys.exit(main())
