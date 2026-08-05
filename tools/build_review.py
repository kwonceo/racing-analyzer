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
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

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


def _pattern_tags(recent):
    """그 말의 최근 착순 시계열이 어느 패턴에 걸리나. recent = [최신, …]."""
    tags = []
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
    ① 후보 밖   : 1·2착이 우리 순위(keyHorses)에 둘 다 없다
    ② 미생성    : 둘 다 순위엔 있는데 그 조합을 안 만들었다
    ③ 잘림      : 만들었다가 강등(quinellaRef)됐다
    ④ 어쩔수없음: 위 어디에도 안 맞는 나머지
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
    return "④어쩔수없음"


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
        "patternTags": _pattern_tags(recent),
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
    return {
        "raceKey": doc.get("raceKey") or fn[:-5],
        "date": fn[:10].replace("_", "-"),
        "sport": doc.get("sport"),
        "fieldSize": cp.get("raceHorseCount") or len(doc.get("horses") or []),
        "source": (doc.get("raw_profile") or {}).get("source"),
        "frozen": bool(doc.get("frozen")),
        "hit": hit,                                    # 🔴 적중·미적중 동형
        "miss_class": None if hit else _miss_class(doc, cp, top2, kh),
        "result": {"1st": r.get("1st"), "2nd": r.get("2nd"), "3rd": r.get("3rd")},
        "ourKeyHorses": kh,                            # 🔴 우리 순위(frozen 우선)
        "our_source": "frozen" if (doc.get("frozen") or {}).get("keyHorses") else "top",
        "top12": [_horse_row(doc, top2[0], kh), _horse_row(doc, top2[1], kh)],
        "payout": {"quinella": q, "trifecta": payouts.get("trifecta")},
        "missed_payback": (None if hit else q),        # 미적중이면 놓친 복승 배당
    }


def main():
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
    # 미적중 분류 분포
    if made:
        from collections import Counter
        cls = Counter()
        for fn in files:
            rv = build_one(fn)
            if rv and not rv["hit"]:
                cls[rv["miss_class"]] += 1
        print("  미적중 분류:", dict(cls))
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
