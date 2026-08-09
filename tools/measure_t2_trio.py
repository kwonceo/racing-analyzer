# -*- coding: utf-8 -*-
"""[t2_strong 회수율 — 복승 · 삼복승 · 합계]  실전 경로 무변경 · 완전 읽기 전용.

🔴 왜 `measure_recovery.py` 에 넣지 않았나 (2026-08-09 · 판단 근거를 남긴다)
   그 도구는 **`PLANS`(조합 생성 함수) 기반**이라 「경주 하나에 정책별 조합집합」 구조다.
   t2_strong 은 `review_engine` 의 **동결 시점 판정**(recommendation_history 를 되감아
   T-2 명단을 만든 뒤 급락 신호로 편입)이라 그 구조에 들어가지 않는다.
   ⇒ 계산 규칙(정제·판정선·부트스트랩)만 import 해 쓰고 **별도 도구로 분리**했다.
   ⚠ 즉석 코드가 아니다 — 파일로 남겨 재현·전수점검이 가능하게 한다(원칙 16 · 즉석 코드 금지).

사용: python tools/measure_t2_trio.py


■ measure_recovery 에서 import: CLEAN_LO/HI · PAYBACK · BOOT_N · _loadh
   ⇒ 정제 범위 · 판정선 74.5% · 부트스트랩 2000회는 도구 것 그대로. seed=42 동일.
■ 복사: load_races 필터(도구가 파일명을 안 실어 매칭 키가 없다) · review_engine _freeze_sets/t2_strong
   🔴 날짜 매칭은 analysis_log 파일 하나에서 파생(원칙 16).
■ 🔴 재현 못 한 것 (원칙 3)
   ⓐ 정제 필터는 **복승 괴리(확정/배당판 0.5~2.0)** 기준을 삼복승에도 그대로 적용했다
      — 배당판 삼복승 시계열이 없어 삼복승 자체 괴리를 못 잰다.
   ⓑ 합계 = 복승구좌+삼복구좌 / 복승회수+삼복회수. review_engine `_book`(경주 1구좌)과 다르다.
"""
import os, sys, re, json, glob, random, importlib.util

BASE = os.path.abspath(".")
sys.path.insert(0, BASE)
spec = importlib.util.spec_from_file_location("mr", os.path.join(BASE, "tools", "measure_recovery.py"))
MR = importlib.util.module_from_spec(spec); spec.loader.exec_module(MR)
import review_engine as RE


def _t2(alog, disp_q, disp_t):
    rows = [x for x in (alog.get("recommendation_history") or []) if isinstance(x, dict)]
    live = [x for x in rows if not x.get("closed")]
    cref = None
    for e in rows:
        if e.get("closed") and e.get("time"):
            cref = RE._hms_sec(e["time"]); break
    if cref is None and live and live[-1].get("time"):
        cref = RE._hms_sec(live[-1]["time"])

    def mb(e):
        v = e.get("minutes_before")
        if isinstance(v, (int, float)):
            return float(v)
        t = RE._hms_sec(e.get("time"))
        return (cref - t) / 60.0 if (t is not None and cref is not None and cref >= t) else None

    ef = None
    for e in live:
        v = mb(e)
        if v is not None and v >= 2:
            ef = e
    if ef is None:
        ef = live[0] if live else None
    if ef is None:
        return None, None, len(live)
    qf = set()
    for c in (ef.get("quinellas") or []):
        cc = c.get("combo") or []
        if len(cc) == 2:
            try:
                qf.add(frozenset(int(x) for x in cc))
            except (TypeError, ValueError):
                pass
    if not qf and ef.get("quinella_main"):
        try:
            qf.add(frozenset(int(x) for x in str(ef["quinella_main"]).split("+")))
        except (TypeError, ValueError):
            pass
    tf = set()
    for s3 in [ef.get("trifecta_main")] + list(ef.get("trifecta_ins") or [])[:1]:
        try:
            p3 = frozenset(int(x) for x in str(s3).split("+"))
            if len(p3) == 3:
                tf.add(p3)
        except (TypeError, ValueError):
            pass
    sh = set()
    for sg in (alog.get("signals_detected") or []):
        sty, sdt = str(sg.get("type") or ""), str(sg.get("detail") or "")
        if "집중급락" in sty:
            m = re.search(r"(\d+)\s*번", sdt)
            if m:
                sh.add(int(m.group(1)))
        elif "급락" in sty:
            mp = re.search(r"-?(\d+(?:\.\d+)?)\s*%", sdt)
            if (float(mp.group(1)) if mp else 0.0) >= 30.0:
                for a, b in re.findall(r"(\d+)\s*\+\s*(\d+)", sdt):
                    sh.add(int(a)); sh.add(int(b))
    qs, ts = set(qf), set(tf)
    if sh:
        for c in disp_q:
            if frozenset(c) not in qs and (frozenset(c) & sh):
                qs.add(frozenset(c))
        for c in disp_t:
            if frozenset(c) not in ts and (frozenset(c) & sh):
                ts.add(frozenset(c))
    return qs, ts, len(live)


def load(sport, pattern="2026_0*"):
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
        pt = (res.get("payouts") or {}).get("trifecta")
        if not res.get("1st") or not po:
            continue
        cp = d.get("corePicks") or {}
        dcm = (cp.get("displayedCombos") or {})
        dc = [sorted(c) for c in (dcm.get("quinellas") or [])]
        dt = [sorted(c) for c in (dcm.get("trifectas") or [])]
        kh = [int(x) for x in (d.get("keyHorses") or [])][:3]
        if not dc or len(kh) < 3:
            continue
        h = MR._loadh(f.replace("analysis_log", "odds_history"))
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
        if res.get("1st") is None or res.get("2nd") is None:
            continue
        top2 = sorted({res.get("1st"), res.get("2nd")})
        mo = q.get(tuple(top2))
        if not mo:
            continue
        top3 = sorted({res.get("1st"), res.get("2nd"), res.get("3rd")} - {None})
        qs, ts, nlive = _t2(d, dc, dt)
        out.append({"po": float(po), "pt": (float(pt) if pt else None), "mo": float(mo),
                    "top2": frozenset(top2), "top3": (frozenset(top3) if len(top3) == 3 else None),
                    "dc": {frozenset(c) for c in dc}, "dt": {frozenset(c) for c in dt},
                    "t2q": (qs if qs is not None else {frozenset(c) for c in dc}),
                    "t2t": (ts if ts is not None else {frozenset(c) for c in dt}),
                    "nlive": nlive})
    return out


def stat(rows, qk, tk, mode):
    """mode: q=복승만 · t=삼복승만 · both=합계.  반환 (구좌, 적중배당리스트)"""
    inv, hits = 0, []
    for r in rows:
        if mode in ("q", "both"):
            inv += len(r[qk])
            if r["top2"] in r[qk]:
                hits.append(r["po"])
        if mode in ("t", "both"):
            if r["pt"] is not None and r["top3"] is not None:
                inv += len(r[tk])
                if r["top3"] in r[tk]:
                    hits.append(r["pt"])
            elif r[tk]:
                inv += len(r[tk])          # 배당 없으면 회수 0 (정직 — 도구 unpaid 와 같은 취지)
    hits.sort(reverse=True)
    return inv, hits


def ci(rows, qk, tk, mode, n=None, seed=42):
    n = n or MR.BOOT_N
    random.seed(seed)
    vals = []
    for _ in range(n):
        s = [random.choice(rows) for _ in range(len(rows))]
        i, h = stat(s, qk, tk, mode)
        vals.append(100.0 * sum(h) / max(i, 1))
    vals.sort()
    return vals[int(0.025 * n)], vals[int(0.975 * n)]


def rep(label, rows, mode):
    if len(rows) < 5:
        print("  %-10s 표본 %d — 판정 불가" % (label, len(rows))); return
    for nm, qk, tk in (("baseline ", "dc", "dt"), ("t2_strong", "t2q", "t2t")):
        i, h = stat(rows, qk, tk, mode)
        f = lambda x: 100.0 * sum(x) / max(i, 1)
        med = sorted(h)[len(h) // 2] if h else 0
        lo, hi = ci(rows, qk, tk, mode)
        print("    %s 구좌 %4d · 적중 %3d · 회수율 %6.1f%% · 1제외 %5.1f%% · 3제외 %5.1f%% · 배당중앙 %5.1f배 · CI[%.1f, %.1f]"
              % (nm, i, len(h), f(h), f(h[1:]), f(h[3:]), med, lo, hi))
    ai = ah = 0
    for r in rows:
        if mode in ("q", "both"):
            add = r["t2q"] - r["dc"]
            ai += len(add)
            if r["top2"] in add:
                ah += r["po"]
        if mode in ("t", "both"):
            add = r["t2t"] - r["dt"]
            ai += len(add)
            if r["top3"] is not None and r["pt"] is not None and r["top3"] in add:
                ah += r["pt"]
    print("    🔴 한계 회수율 %5.1f%%  (추가 %d구좌 · 추가 회수 %.1f)"
          % (100.0 * ah / ai if ai else 0.0, ai, ah))


for sport in ("horse", "cycle"):
    raw = load(sport)
    clean = [r for r in raw if MR.CLEAN_LO <= r["po"] / r["mo"] <= MR.CLEAN_HI]
    nt = sum(1 for r in clean if r["dt"])
    ntp = sum(1 for r in clean if r["dt"] and r["pt"] is not None)
    print()
    print("=" * 104)
    print("[%s]  전체 %d → 정제 %d경주 · 그중 삼복승 표시 %d · 삼복승 확정배당까지 %d  (판정선 %.1f%%)"
          % (sport, len(raw), len(clean), nt, ntp, MR.PAYBACK))
    for mode, lab in (("q", "복승만"), ("t", "삼복승만"), ("both", "합계")):
        print("  -- %s --" % lab)
        rep("전체", clean, mode)
    print("  -- 합계 · 재검토 대상 제외(1~2행) --")
    rep("제외후", [r for r in clean if r["nlive"] > 2], "both")
