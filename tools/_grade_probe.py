# -*- coding: utf-8 -*-
"""[일회성 측정 · 읽기 전용] 경주 등급(강력승부) 판정이 왜 거꾸로 붙었나.

2026-08-14 대표 지시. 실측에서 🔥 강력승부 경주가 3제외 28.4% 로 가장 나빴다.
판정에 들어가는 항목(신호 종수·확신도)을 하나씩 떼어 어느 쪽이 범인인지 본다.

⚠ 확신도·신호 종수는 별도 필드가 없다. `corePicks.raceGrade.basis` 문자열
  (예: "신호 2종(3건) · 확신도 34.9")에서 파싱한다 — 원자료를 열어 확인한 형식이다.
⚠ 회수율 규칙(정제·확정배당·날짜매칭)은 measure_recovery 를 import 해 그대로 쓴다.
"""
import sys, glob, json, os, re, statistics as st
sys.path.insert(0, 'tools')
import measure_recovery as M

RS = re.compile(r"신호\s*(\d+)\s*[종개]")
RC = re.compile(r"확신도\s*([\d.]+)")


def clean(r):
    try:
        return M.CLEAN_LO <= r["po"] / r["mo"] <= M.CLEAN_HI
    except Exception:
        return False


def build_meta():
    meta = {}
    for f in sorted(glob.glob('data/analysis_log/2026_0*.json')):
        try:
            d = json.load(open(f, encoding='utf-8'))
        except Exception:
            continue
        res = d.get('result') or {}
        po = (res.get('payouts') or {}).get('quinella')
        if po is None or res.get('1st') is None or res.get('2nd') is None:
            continue
        rg = (d.get('corePicks') or {}).get('raceGrade') or {}
        b = rg.get('basis') or ''
        ms, mc = RS.search(b), RC.search(b)
        t = sorted({res['1st'], res['2nd']})
        meta[(t[0], t[1], float(po))] = {
            'gs': int(ms.group(1)) if ms else None,
            'conf': float(mc.group(1)) if mc else None,
            'label': rg.get('label') or '?',
            'date': os.path.basename(f)[:10]}
    return meta


def line(nm, rs):
    if not rs:
        print("  %-28s 경주 0" % nm)
        return
    slots = sum(len(r["dc"]) for r in rs)
    hit = [r for r in rs if sorted(r["top2"]) in [sorted(c) for c in r["dc"]]]
    ret = sum(r["po"] for r in hit)
    ho = sorted([r["po"] for r in hit], reverse=True)
    o = [r["q"].get(tuple(sorted(c))) for r in rs for c in r["dc"]]
    o = [x for x in o if x]
    print("  %-28s 경주%4d 적중%3d(%4.1f%%) 3제외%6.1f%% 배당중앙%5.2f %s"
          % (nm, len(rs), len(hit), len(hit) / len(rs) * 100,
             (ret - sum(ho[:3])) / slots * 100 if slots else 0,
             st.median(o) if o else 0, "" if len(hit) >= 30 else "⚠판정불가"))


def main(sport="cycle"):
    meta = build_meta()
    rs = [r for r in M.load_races(sport=sport, pattern="2026_0*") if clean(r)]
    for r in rs:
        r.update(meta.get((r["top2"][0], r["top2"][1], r["po"]), {}))
    ok = [r for r in rs if r.get('conf') is not None]
    print("=" * 104)
    print("%s %d경주(등급값 보유 %d) — 강력승부 조건 분해" % (sport, len(rs), len(ok)))
    line("전체", rs)
    line("S 신호2+ 그리고 확신65+", [r for r in ok if (r['gs'] or 0) >= 2 and r['conf'] >= 65])
    line("  신호2+ 만", [r for r in ok if (r['gs'] or 0) >= 2])
    line("  확신65+ 만", [r for r in ok if r['conf'] >= 65])
    line("  신호2+ 인데 확신65미만", [r for r in ok if (r['gs'] or 0) >= 2 and r['conf'] < 65])
    line("  확신65+ 인데 신호2미만", [r for r in ok if r['conf'] >= 65 and (r['gs'] or 0) < 2])
    print(" -- 확신도 구간 --")
    for lo, hi, nm in ((0, 40, "40미만"), (40, 50, "40~50"), (50, 65, "50~65"), (65, 999, "65이상")):
        line("  확신 " + nm, [r for r in ok if lo <= r['conf'] < hi])
    print(" -- 신호 종수 --")
    for lo, hi, nm in ((0, 1, "0종"), (1, 2, "1종"), (2, 3, "2종"), (3, 4, "3종"), (4, 999, "4종+")):
        line("  신호 " + nm, [r for r in ok if lo <= (r['gs'] or 0) < hi])
    print(" -- 기간 반 나눔 --")
    ds = sorted({r['date'] for r in ok if r.get('date')})
    if ds:
        mid = ds[len(ds) // 2]
        S = [r for r in ok if (r['gs'] or 0) >= 2 and r['conf'] >= 65]
        line("  S 전반(~%s)" % mid, [r for r in S if r['date'] < mid])
        line("  S 후반(%s~)" % mid, [r for r in S if r['date'] >= mid])
        line("  전체 전반", [r for r in ok if r['date'] < mid])
        line("  전체 후반", [r for r in ok if r['date'] >= mid])


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "cycle")


def invert(sport="cycle"):
    """[작업3] 지금 판정을 뜻만 뒤집어 쓰는 안 — S 조건 경주를 거른다."""
    meta = build_meta()
    rs = [r for r in M.load_races(sport=sport, pattern="2026_0*") if clean(r)]
    for r in rs:
        r.update(meta.get((r["top2"][0], r["top2"][1], r["po"]), {}))
    ok = [r for r in rs if r.get('conf') is not None]
    print("=" * 104)
    print("%s — 뒤집어 쓰는 안 (전체 %d경주)" % (sport, len(ok)))
    line("현행 전체(대조군)", ok)
    line("안 S조건 경주를 거른다", [r for r in ok if not ((r['gs'] or 0) >= 2 and r['conf'] >= 65)])
    line("안 신호2종+ 를 거른다", [r for r in ok if (r['gs'] or 0) < 2])
    line("안 확신65+ 를 거른다", [r for r in ok if r['conf'] < 65])
    line("안 둘 다 거른다", [r for r in ok if (r['gs'] or 0) < 2 and r['conf'] < 65])
    line("안 확신40~50 만 산다", [r for r in ok if 40 <= r['conf'] < 50])
    line("안 확신40~50 이고 신호1종", [r for r in ok if 40 <= r['conf'] < 50 and (r['gs'] or 0) == 1])
    ds = sorted({r['date'] for r in ok if r.get('date')})
    if ds:
        mid = ds[len(ds) // 2]
        print(" -- 기간 반 나눔 (둘 다 거르는 안) --")
        for tag, sub in (("전반", [r for r in ok if r['date'] < mid]),
                         ("후반", [r for r in ok if r['date'] >= mid])):
            line("  %s 현행" % tag, sub)
            line("  %s 둘다거름" % tag, [r for r in sub if (r['gs'] or 0) < 2 and r['conf'] < 65])
