# -*- coding: utf-8 -*-
"""[측정 · 읽기 전용] 조합 수 상한 규칙이 **두 곳에 따로** 있고 서로 다르다 — 어느 쪽이 나은가.

저장소  : data/analysis_log/*.json + data/race_results/*.json(최상위 payouts.quinella)
          + data/odds_history/*.json(.gz 포함 · 정제용 마감 배당)
날짜    : 파일명 날짜까지 일치(원칙 16) · 표본 시작 2026-08-01
분모    : 경주 단위 · 확정배당 보유 · payouts_approx/estimated 제외 · 정제 0.5~2.0
되돌리기: (측정 전용 · 배선 없음)

두 규칙
  ⓐ _combo_cap_of        app.py 17095~17111  최저배당 **3배 이상이면 상한을 안 건다**(LOW_ONLY)
  ⓑ 판정 명단 인라인      app.py 17796~17808  🔴 그 조건 없이 **항상** 건다
  구간은 둘 다 같다: <3배 → 1 · 3~6 → 2 · 6~10 → 3 · 10배+ → 4

⚠ 상한 적용 **전** 목록은 저장돼 있지 않다. 그래서
  finalQuinellas + quinellaRef 중 refReason 에 「조합 수 상한」이 있는 것을 합쳐 복원한다.
  ⇒ 상한이 실제로 걸린 경주만 두 규칙이 갈린다(안 걸린 경주는 두 안이 같다).
⚠ 정렬은 실제 코드와 같게 **배당 낮은 순**(COMBO_CAP_SORT=True 현행).
🔴 배선하지 않는다. 숫자만.
"""
import glob
import json
import os
import statistics as st
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "tools"))
import measure_recovery as M

B1, B2, B3 = 3.0, 6.0, 10.0


def cap_inline(mn):
    """ⓑ 인라인 — 항상 건다."""
    return 1 if mn < B1 else 2 if mn < B2 else 3 if mn < B3 else 4


def cap_lowonly(mn):
    """ⓐ _combo_cap_of — 3배 이상이면 상한 없음(None)."""
    if mn >= B1:
        return None
    return 1


def restore(cp):
    """상한 적용 전 복승 목록 복원 — (combo, odds) 리스트."""
    out = []
    for q in (cp.get("finalQuinellas") or []):
        if q.get("combo"):
            out.append((tuple(sorted(int(x) for x in q["combo"])), q.get("odds")))
    for q in (cp.get("quinellaRef") or []):
        if not q.get("combo"):
            continue
        if "조합 수 상한" not in str(q.get("refReason") or ""):
            continue
        out.append((tuple(sorted(int(x) for x in q["combo"])), q.get("odds")))
    return out


def sort_key(item):
    """배당 낮은 순 · 배당 없으면 뒤로(현행 _combo_sort_key 와 같은 방향)."""
    o = item[1]
    try:
        o = float(o)
    except (TypeError, ValueError):
        return (1, 0.0)
    return (0, o) if o > 0 else (1, 0.0)


def load(month="2026_08"):
    rows = []
    for f in sorted(glob.glob(os.path.join(BASE, "data", "analysis_log", month + "_*.json"))):
        b = os.path.basename(f)
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        cp = d.get("corePicks") or {}
        pre = restore(cp)
        if len(pre) < 2:
            continue                                   # 상한이 갈릴 수 없다
        # 결과·확정배당 — analysis_log 우선, 없으면 같은 이름의 race_results(원칙 16: 날짜 포함)
        res = d.get("result") or {}
        po = (res.get("payouts") or {}).get("quinella")
        a1, a2 = res.get("1st"), res.get("2nd")
        rr = os.path.join(BASE, "data", "race_results", b)
        if (po is None or a1 is None) and os.path.exists(rr):
            try:
                r2 = json.load(open(rr, encoding="utf-8"))
                if not r2.get("payouts_approx") and not r2.get("payouts_estimated"):
                    po = po if po is not None else (r2.get("payouts") or {}).get("quinella")
                    _r = r2.get("result") or {}
                    a1 = a1 if a1 is not None else _r.get("1st")
                    a2 = a2 if a2 is not None else _r.get("2nd")
            except Exception:
                pass
        if po is None or a1 is None or a2 is None:
            continue
        if d.get("payouts_approx") or d.get("payouts_estimated"):
            continue
        ans = tuple(sorted([int(a1), int(a2)]))
        # 정제 — 마감 직전 배당판과 확정배당의 괴리
        mo = None
        try:
            h = M._loadh(f.replace("analysis_log", "odds_history")) or {}
            dl = h.get("deadline_epoch")
            sn = [s for s in (h.get("snapshots") or [])
                  if s.get("t") and dl and -8 <= (s["t"] - dl) / 60 <= 0 and s.get("quinella")]
            if sn:
                for k, v in max(sn, key=lambda s: s["t"])["quinella"].items():
                    if tuple(sorted(int(z) for z in str(k).replace("-", "+").split("+"))) == ans:
                        mo = float(v)
        except Exception:
            pass
        clean = bool(mo and M.CLEAN_LO <= float(po) / mo <= M.CLEAN_HI)
        rows.append({"f": b[:-5], "sport": d.get("sport") or "?", "pre": pre,
                     "ans": ans, "po": float(po), "clean": clean})
    return rows


def score(rows, capfn):
    slots = 0
    hits = []
    per = []
    for r in rows:
        pre = sorted(r["pre"], key=sort_key)
        mn = None
        for _c, o in pre:
            try:
                o = float(o)
            except (TypeError, ValueError):
                continue
            if o > 0 and (mn is None or o < mn):
                mn = o
        cap = capfn(mn) if mn is not None else None
        use = pre if cap is None else pre[:cap]
        slots += len(use)
        per.append(len(use))
        if any(c == r["ans"] for c, _o in use):
            hits.append(r["po"])
    if not slots:
        return None
    ho = sorted(hits, reverse=True)
    return {"n": len(rows), "slots": slots, "hits": len(ho),
            "hitRate": 100.0 * len(ho) / len(rows),
            "roi": 100.0 * sum(ho) / slots,
            "ex3": 100.0 * (sum(ho) - sum(ho[:3])) / slots,
            "med": st.median(ho) if ho else 0.0,
            "per": slots / len(rows)}


def show(tag, rows):
    print("=" * 118)
    print("[%s] %d경주" % (tag, len(rows)))
    if not rows:
        return
    for name, fn in (("ⓑ 인라인(항상 상한 · 현행 판정 명단)", cap_inline),
                     ("ⓐ _combo_cap_of(3배 이상 면제)", cap_lowonly)):
        d = score(rows, fn)
        if not d:
            continue
        v = M.sample_verdict(d["hits"], sum(sorted([r["po"] for r in rows], reverse=True)[3:]) or None)
        print("  %-34s 구좌%6d 적중%5.1f%% 회수%7.1f%% 대박뺀%7.1f%% 배당중앙%6.2f 경주당%5.2f %s"
              % (name, d["slots"], d["hitRate"], d["roi"], d["ex3"], d["med"], d["per"],
                 "⚠판정불가(적중 %d)" % d["hits"] if d["hits"] < M.MIN_HITS else ""))


if __name__ == "__main__":
    rows = load()
    cl = [r for r in rows if r["clean"]]
    print("표본 %d경주 (정제 통과 %d · %.1f%%) · 시작 2026-08-01 · 판정선 %.1f%%"
          % (len(rows), len(cl), 100.0 * len(cl) / len(rows) if rows else 0, M.PAYBACK))
    show("🟢 정제 적용 · 전체", cl)
    for sp in ("cycle", "horse"):
        show("정제 · %s" % sp, [r for r in cl if r["sport"] == sp])
    show("⚠ 정제 안 함(참고)", rows)
