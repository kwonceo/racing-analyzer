# -*- coding: utf-8 -*-
"""[진단] `build_popbase.parse_result` 가 과거 result.html 에서 왜 실패하는가.

🔴 왜 도구로 만드나 — 즉석 코드로 재면 **분모를 매번 다르게 잡는다**(원칙 8-C·15).
  이 도구는 `logs/form_raw/` 에 **이미 저장된 원문**만 읽는다. **네트워크를 쓰지 않는다.**
  ⇒ 같은 입력에 같은 답이 나오고, 수정 전후를 그대로 대조할 수 있다.

⚠ **완전 읽기 전용.** analysis_log·추천·학습 경로를 일절 건드리지 않는다.

사용:
    python tools/diag_popbase_parse.py                 # 요약
    python tools/diag_popbase_parse.py --dump 5        # 실패 사례 5건 셀 덤프
"""
import argparse
import gzip
import io
import os
import re
import sys
from collections import Counter, defaultdict

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAWDIR = os.path.join(BASE, "logs", "form_raw")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── build_popbase.py 와 **같은** 정규식 (베끼지 않고 그대로 옮긴다 · 달라지면 진단이 무의미) ──
_ROW = re.compile(r'<tr[^>]*class="[^"]*HorseList[^"]*"[^>]*>(.*?)</tr>', re.S)
_TD = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_FIELD_RE = re.compile(r"(\d{1,2})頭")

# 🔴 [수정안] 결과표만 잘라낸다 — 원본이 `id="All_Result_Table"` 로 **명시**하고 있다.
#   과거 페이지에는 `RapSummary_Table`(각마 랩) · `milage_summary`(주행거리) · `LapSummary_Table`(각질)
#   3개 분석표가 더 있고, **그 행들도 class="HorseList" 를 쓴다.** 그래서 여분 행이 섞였다.
_RESULT_TABLE = re.compile(
    r'<table[^>]*id="All_Result_Table"[^>]*>(.*?)</table>', re.S)


def _txt(h):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h)).strip()


def diag_one(html, fixed=False):
    """현행 파서를 그대로 재현하고 **왜 실패했는지**까지 분류해 돌려준다.

    fixed=True 면 결과표(`All_Result_Table`)로 범위를 좁힌 **수정안**으로 잰다.
    """
    flat = _txt(html)
    scope = html
    if fixed:
        mt = _RESULT_TABLE.search(html)
        if not mt:
            return {"field": None, "rows": 0, "parsed": 0, "shortcell": 0, "badnum": [],
                    "cellcnt": Counter(), "reason": "D:결과표없음", "ctx": "", "allfield": [],
                    "placings": [], "pops": []}
        scope = mt.group(1)
    m = _FIELD_RE.search(flat)
    field = int(m.group(1)) if m else None
    # 🔴 첫 `N頭` 앞뒤 문맥 — 다른 경주의 두수를 잡고 있는지 눈으로 보려고 남긴다
    ctx = flat[max(0, m.start() - 60):m.end() + 20] if m else ""
    allfield = _FIELD_RE.findall(flat)

    rows = _ROW.findall(scope)
    cellcnt = Counter()
    parsed, badnum, shortcell = [], [], 0
    for r in rows:
        c = [_txt(x) for x in _TD.findall(r)]
        cellcnt[len(c)] += 1
        if len(c) < 15:
            shortcell += 1
            continue
        try:
            pl, pp = int(c[0]), int(c[9])
        except (ValueError, IndexError):
            badnum.append((c[0] if c else "", c[9] if len(c) > 9 else ""))
            continue
        parsed.append({"placing": pl, "pop": pp})

    reason = "OK"
    if field is None:
        reason = "A:頭표기없음"
    elif len(parsed) != field:
        reason = ("B:파싱>표기" if len(parsed) > field else "C:파싱<표기")
    return {
        "field": field, "rows": len(rows), "parsed": len(parsed),
        "shortcell": shortcell, "badnum": badnum, "cellcnt": cellcnt,
        "reason": reason, "ctx": ctx, "allfield": allfield[:6],
        "placings": [p["placing"] for p in parsed],
        "pops": [p["pop"] for p in parsed],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", type=int, default=0, help="실패 사례 N건의 셀 원문 덤프")
    ap.add_argument("--fixed", action="store_true", help="수정안(결과표로 범위 제한)으로 잰다")
    a = ap.parse_args()

    files = []
    for d in sorted(os.listdir(RAWDIR)):
        dd = os.path.join(RAWDIR, d)
        if not os.path.isdir(dd):
            continue
        for f in sorted(os.listdir(dd)):
            if f.startswith("jra_result_") and f.endswith(".html.gz"):
                files.append((d, os.path.join(dd, f), f[11:23]))
    print("=" * 96)
    print("원문 %d건 · 개최일 %d일 · **네트워크 미사용(저장된 원문만)**"
          % (len(files), len(set(x[0] for x in files))))
    print("=" * 96)

    reasons = Counter()
    by_day = defaultdict(lambda: [0, 0])          # ymd → [ok, total]
    by_field = defaultdict(lambda: [0, 0])        # field → [ok, total]
    cell_hist = Counter()
    badnum_tok = Counter()
    diffs = Counter()                             # (parsed - field)
    dumped = 0
    samples = []
    in3_extra = 0
    ok_races = 0
    tot3 = 0

    for ymd, path, rid in files:
        try:
            with gzip.open(path, "rt", encoding="utf-8") as f:
                html = f.read()
        except Exception as e:
            reasons["Z:읽기실패"] += 1
            continue
        d = diag_one(html, fixed=a.fixed)
        reasons[d["reason"]] += 1
        by_day[ymd][1] += 1
        by_field[d["field"]][1] += 1
        for k, v in d["cellcnt"].items():
            cell_hist[k] += v
        for t in d["badnum"]:
            badnum_tok[t[0][:6]] += 1
        if d["field"] is not None:
            diffs[d["parsed"] - d["field"]] += 1
        if d["reason"] == "OK":
            by_day[ymd][0] += 1
            by_field[d["field"]][0] += 1
            ok_races += 1
            n3 = sum(1 for p in d["placings"] if p <= 3)
            tot3 += n3
            if n3 != 3:
                in3_extra += 1
                if len(samples) < 8:
                    samples.append((rid, ymd, d["field"], n3, sorted(d["placings"])[:8]))
        elif dumped < a.dump:
            dumped += 1
            print("\n🔴 실패 %s (%s) 사유=%s 표기=%s 행=%d 파싱=%d 짧은셀=%d"
                  % (rid, ymd, d["reason"], d["field"], d["rows"], d["parsed"], d["shortcell"]))
            print("   첫 N頭 문맥: …%s…" % d["ctx"])
            print("   페이지 내 頭 표기들: %s" % d["allfield"])
            print("   셀 개수 분포: %s" % dict(d["cellcnt"]))
            print("   숫자아님 토큰: %s" % d["badnum"][:5])
            print("   착순들: %s" % sorted(d["placings"])[:20])

    print("\n[1] 스킵 사유")
    for k, v in sorted(reasons.items()):
        print("   %-14s %5d (%.1f%%)" % (k, v, 100.0 * v / max(len(files), 1)))

    print("\n[2] 파싱수 − 표기두수 분포 (0 이면 채택)")
    for k in sorted(diffs):
        print("   %+3d : %5d" % (k, diffs[k]))

    print("\n[3] 날짜별 채택률")
    for d in sorted(by_day):
        ok, tt = by_day[d]
        print("   %s  %3d/%3d = %5.1f%%" % (d, ok, tt, 100.0 * ok / max(tt, 1)))

    print("\n[4] 표기두수별 채택률 (표기가 틀렸을 수 있음에 주의)")
    for f in sorted(by_field, key=lambda x: (x is None, x)):
        ok, tt = by_field[f]
        print("   %s두  %3d/%3d = %5.1f%%" % (str(f).rjust(4), ok, tt, 100.0 * ok / max(tt, 1)))

    print("\n[5] 말 행 셀 개수 분포 (전체 행 기준)")
    for k in sorted(cell_hist):
        print("   %2d셀 : %6d" % (k, cell_hist[k]))

    print("\n[6] 착순 칸이 숫자가 아닌 토큰 상위")
    for k, v in badnum_tok.most_common(12):
        print("   %-8s %4d" % (repr(k), v))

    print("\n[7] 채택 경주의 3착 이내 인원 검산")
    print("   채택 %d경주 · 3착합 %d ÷ %d = %.2f (기대 3.00)"
          % (ok_races, tot3, max(ok_races, 1), tot3 / max(ok_races, 1)))
    print("   3착 이내가 3명이 아닌 경주: %d" % in3_extra)
    for s in samples:
        print("     %s %s 표기%s두 3착이내%d명 착순=%s" % s)


if __name__ == "__main__":
    main()
