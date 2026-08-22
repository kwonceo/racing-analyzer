# -*- coding: utf-8 -*-
"""[예상문 피드백] 하루치 경주에서 **예상문 ↔ 우리 추천 ↔ 실제 결과** 를 대조한다.

🔴 완전 읽기 전용이다. 추천·판정·저장 어디에도 개입하지 않는다.

왜 만들었나
  한국 PDF 사전분석은 경주마다 **A·B·C 유력마 지목**(gradePicks)과 말별 등급·점수·근거를
  담고 있는데, 저장만 하고 한 번도 대조한 적이 없다.
  2026-08-22 제주 6경주에서 1번·5번이 **전적이 전혀 없어** 우리 점수가 안 나왔고 조합이
  아예 안 만들어졌는데 결과가 1-5(39배)였다. 그 말들에 대한 판단이 예상문에는 적혀 있었다.
  ⇒ "우리 점수가 없는 말을 예상문은 뭐라고 했나" 를 매일 보는 것이 이 도구의 목적이다.

무엇을 대조하나
  예상문 지목 = prerace_report.gradePicks 의 A·B·C 마번
  우리 지목   = corePicks.keyHorses (상위 3두)
  실제        = race_results.result 의 1·2착
  ⚠ 조합(betting_recommend)은 담지 않는다 — 추천 경로와 섞이면 안 된다.

읽는 곳 (원칙 26 · 저장소를 명시한다)
  data/analysis_log/<YYYY_MM_DD>_<경기장>_<N>경주.json
      → corePicks.keyHorses · corePicks.displayedCombos.quinellas · horses[].record_score
      → prerace_report.{gradePicks, horses, specialNotes, analysis}
  data/race_results/<같은 파일명>.json  → result.1st/2nd
  data/prerace/<YYYY-MM-DD>_<경기장>_<N>.json  (분석로그에 없으면 폴백)
      → report.grade_picks · horses[].{rating, training, weight}

쓰는 법
  python tools/prerace_review.py                 오늘
  python tools/prerace_review.py --date 2026-08-22
  python tools/prerace_review.py --date 2026-08-22 --detail    경주별 근거까지
"""
import os
import re
import sys
import json
import glob
import time
import argparse

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(BASE, "data", "analysis_log")
RES_DIR = os.path.join(BASE, "data", "race_results")
PRE_DIR = os.path.join(BASE, "data", "prerace")

KR_RE = re.compile(r"(서울|부산|부경|제주|과천)")


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _int(v):
    try:
        return int(v)
    except Exception:
        return None


def _grade_picks(log_doc, base):
    """예상문 A·B·C 지목 → [(등급, 마번, 마명, 근거)]. 분석로그 → 원본 순으로 찾는다."""
    pr = (log_doc or {}).get("prerace_report") or {}
    gp = pr.get("gradePicks") or {}
    if not gp:
        # 폴백: data/prerace 원본 (파일명 규칙이 다르다 — 2026-08-22_제주_7.json)
        m = re.match(r"^(\d{4})_(\d{2})_(\d{2})_(.+?)_(\d+)경주$", base)
        if m:
            y, mo, d, venue, no = m.groups()
            p = os.path.join(PRE_DIR, "%s-%s-%s_%s_%s.json" % (y, mo, d, venue, int(no)))
            src = _load(p) or {}
            gp = ((src.get("report") or {}).get("grade_picks")) or {}
    out = []
    for g in ("A", "B", "C", "D"):
        it = gp.get(g)
        if isinstance(it, dict) and _int(it.get("no")) is not None:
            out.append((g, _int(it.get("no")), it.get("name") or "", it.get("reason") or ""))
    return out


def _prerace_horses(log_doc, base):
    """예상문 말별 판단 {마번: {grade, score, reason}}"""
    pr = (log_doc or {}).get("prerace_report") or {}
    hs = pr.get("horses") or []
    out = {}
    for h in hs:
        n = _int(h.get("no"))
        if n is not None:
            out[n] = {"grade": h.get("grade"), "score": h.get("score"),
                      "reason": h.get("reason") or "", "name": h.get("name") or ""}
    return out


def _our_scores(log_doc):
    """우리 전적 점수 {마번: record_score or None} — 전적 결손을 찾기 위한 것"""
    out = {}
    for h in (log_doc or {}).get("horses") or []:
        n = _int(h.get("no"))
        if n is None:
            continue
        sc = h.get("record_score")
        if sc is None:
            sc = h.get("totalScore")
        out[n] = sc
    return out


def collect(date_str):
    """date_str: YYYY-MM-DD → 그날 한국 경주 대조 행"""
    ymd = date_str.replace("-", "_")
    rows = []
    for p in sorted(glob.glob(os.path.join(LOG_DIR, "%s_*.json" % ymd))):
        base = os.path.basename(p)[:-5]
        if not KR_RE.search(base):
            continue                       # 한국만 — 예상문(PDF)이 한국 전용이다
        d = _load(p)
        if not d:
            continue
        cp = d.get("corePicks") or {}
        kh = [x for x in (_int(v) for v in (cp.get("keyHorses") or [])) if x is not None]
        dc = [sorted(_int(y) for y in c) for c in
              ((cp.get("displayedCombos") or {}).get("quinellas") or []) if len(c) == 2]
        r = _load(os.path.join(RES_DIR, base + ".json")) or {}
        res = r.get("result") or {}
        a, b = _int(res.get("1st")), _int(res.get("2nd"))
        top2 = sorted([a, b]) if (a and b and a != b) else None
        rows.append({
            "base": base,
            "label": base[11:],
            "keyHorses": kh,
            "combos": dc,
            "top2": top2,
            "payout": (r.get("payouts") or {}).get("quinella"),
            "picks": _grade_picks(d, base),
            "pre": _prerace_horses(d, base),
            "ours": _our_scores(d),
            "notes": ((d.get("prerace_report") or {}).get("specialNotes") or ""),
        })
    return rows


def report(rows, detail=False):
    n = len(rows)
    done = [x for x in rows if x["top2"]]
    withpre = [x for x in rows if x["picks"]]
    print("=" * 78)
    print("예상문 피드백 · 한국 %d경주 (결과 %d · 예상문 보유 %d)" % (n, len(done), len(withpre)))
    print("=" * 78)
    if not n:
        print("  그날 한국 경주가 없다.")
        return
    # ── 경주별 ──
    hit_ours = hit_pre = both = neither = 0
    only_pre_won = []      # 예상문만 짚었는데 입상
    only_ours_won = []     # 우리만 짚었는데 입상
    blind_spot = []        # 우리 전적 점수 없음 + 예상문 등급 있음 + 입상
    print()
    print("%-14s %-14s %-14s %-9s %s" % ("경주", "예상문 A·B·C", "우리 상위3", "결과", "판정"))
    print("-" * 78)
    for x in rows:
        pn = [p[1] for p in x["picks"]][:3]
        on = x["keyHorses"][:3]
        t = x["top2"]
        if not t:
            mark = "⏳"
        else:
            po = sum(1 for h in t if h in pn)
            oo = sum(1 for h in t if h in on)
            if po == 2:
                hit_pre += 1
            if oo == 2:
                hit_ours += 1
            if po == 2 and oo == 2:
                both += 1
            if po < 2 and oo < 2:
                neither += 1
            mark = "예상문%d/2 우리%d/2" % (po, oo)
            if x["combos"] and t in x["combos"]:
                mark = "🟢 적중 " + mark
            for h in t:
                if h in pn and h not in on:
                    only_pre_won.append((x["label"], h, x["pre"].get(h, {})))
                if h in on and h not in pn:
                    only_ours_won.append((x["label"], h))
                sc = x["ours"].get(h)
                if (sc is None or sc == 0) and x["pre"].get(h, {}).get("grade"):
                    blind_spot.append((x["label"], h, x["pre"][h]))
        print("%-14s %-14s %-14s %-9s %s"
              % (x["label"], str(pn) if pn else "-", str(on) if on else "-",
                 str(t) if t else "-", mark))
    # ── 요약 ──
    d = len(done) or 1
    print()
    print("── 지목 정확도 (1·2착 두 두 모두 맞힌 경주) ──")
    print("  예상문 A·B·C 상위3   %2d / %d = %.1f%%" % (hit_pre, len(done), 100.0 * hit_pre / d))
    print("  우리 유력마 상위3     %2d / %d = %.1f%%" % (hit_ours, len(done), 100.0 * hit_ours / d))
    print("  🟢 둘 다 맞힘         %2d" % both)
    print("  🔴 둘 다 놓침         %2d" % neither)

    if only_pre_won:
        print()
        print("🔴 예상문은 짚었는데 우리가 뺀 말이 입상 — %d건" % len(only_pre_won))
        for lab, h, info in only_pre_won[:12]:
            print("   %-14s %2d번 %-8s 예상문 %s등급 %s점"
                  % (lab, h, str(info.get("name"))[:8], info.get("grade"), info.get("score")))
            if detail and info.get("reason"):
                print("      근거: %s" % str(info["reason"])[:110])
    if only_ours_won:
        print()
        print("🟡 우리는 짚었는데 예상문이 뺀 말이 입상 — %d건" % len(only_ours_won))
        print("   " + " · ".join("%s %d번" % (l, h) for l, h in only_ours_won[:12]))
    if blind_spot:
        print()
        print("🔴🔴 우리 전적 점수가 없는데 예상문에는 판단이 있고, 그 말이 입상 — %d건" % len(blind_spot))
        print("   (2026-08-22 제주 6경주 유형 — 결손이 조합 생성을 통째로 막은 자리)")
        for lab, h, info in blind_spot[:10]:
            print("   %-14s %2d번 %-8s 예상문 %s등급 %s점 | %s"
                  % (lab, h, str(info.get("name"))[:8], info.get("grade"), info.get("score"),
                     str(info.get("reason"))[:64]))
    print()
    print("⚠ 이 도구는 대조·보고만 한다. 추천에 반영하지 않는다.")
    print("⚠ 예상문이 우리보다 나은지는 아직 판정되지 않았다 — 표본이 쌓여야 한다(원칙 1).")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=time.strftime("%Y-%m-%d"))
    ap.add_argument("--detail", action="store_true", help="예상문 근거 문장까지 출력")
    a = ap.parse_args()
    report(collect(a.date), detail=a.detail)


if __name__ == "__main__":
    main()
