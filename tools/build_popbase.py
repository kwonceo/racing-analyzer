# -*- coding: utf-8 -*-
"""[인기 기저선 수집 (2026-08-02 승인)] netkeiba `result.html` → 인기순위별 실측 3착률.

🔴 왜 필요한가 — **지금까지 "무작위 기대 = 3 ÷ 두수" 를 기저선으로 썼다.**
  그건 **모든 말을 동등하게** 본다(12두면 전원 25%). 실제 시장은 전혀 그렇지 않다.
  ⇒ 복병(인기 하위)의 배수는 **과소평가**되고, 인기 상위 신호는 **과대평가**된다.
  실제 기저선이 있어야 "이 신호에 우위가 있다"는 판정이 정확해진다(학습 설계 원칙 1).

🔴 **격리 원칙(대표 지시)**: 기저선은 **우리 시스템이 만지지 않은 데이터**여야 한다.
  ⇒ `data/analysis_log` 를 **읽지도 쓰지도 않는다.** 결과 페이지만 받아 별도 저장소에 쌓는다.
  ⚠ 그래서 **경주 수 제약이 없다** — 우리가 분석하지 않은 경주도 전부 들어온다.
  🔴 **미적중 경주가 전부 포함된다** — 대표 지시("못 맞춘 경기 복기")의 재료가 된다.

⚠ **읽기 전용**: 추천·판정·학습 경로를 일절 건드리지 않는다. 원문은 `logs/form_raw/` 에 보존한다.
⚠ `--dry` 가 기본이다. `--apply` 를 붙여야 저장한다.

사용:
    python tools/build_popbase.py --days 60 --dry
    python tools/build_popbase.py --days 60 --apply
"""
import argparse
import gzip
import io
import json
import os
import re
import sys
import time
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "data", "simulation_db", "pop_baseline.json")
RAWDIR = os.path.join(BASE, "logs", "form_raw")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

H = {"User-Agent": "Mozilla/5.0", "Accept": "*/*", "Accept-Encoding": "identity"}
# 🔴 [2026-08-02] Content-Type 을 먼저 읽는다 — euc-jp 로 넘겨짚어 "데이터 없음" 오판을 한 적이 있다.
_ROW = re.compile(r'<tr[^>]*class="[^"]*HorseList[^"]*"[^>]*>(.*?)</tr>', re.S)
_TD = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
# 🔴 [2026-08-02] 결과표로 **범위를 먼저 좁힌다** — 원본이 `id="All_Result_Table"` 로 명시한다.
#   왜: 과거 페이지에는 `RapSummary_Table`(각마 랩) · `milage_summary`(주행거리) ·
#   `LapSummary_Table`(각질) **분석표 3개**가 더 있고 **그 행들도 class="HorseList" 를 쓴다.**
#   그중 주행거리표 행은 c[0]="1"(착순)·c[9]=숫자라 **말 행으로 읽혀 매 경주 +1 이 됐다.**
#   ⇒ 채택률 14.1% · 3착합÷경주수 3.51 의 원인이 정확히 이것이었다(원문 588건 대조).
#   ⚠ **최근 페이지에는 이 분석표가 아직 없다** — "과거 구조가 다르다"가 아니라
#     "최근 페이지에 아직 안 붙었다"가 맞다(방향이 반대였다).
_RESULT_TABLE = re.compile(r'<table[^>]*id="All_Result_Table"[^>]*>(.*?)</table>', re.S)


def fetch(url, timeout=15):
    r = urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=timeout)
    ct = r.headers.get("Content-Type", "")
    m = re.search(r"charset=([\w\-]+)", ct, re.I)
    return r.read().decode(m.group(1) if m else "utf-8", "replace")


def _txt(h):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h)).strip()


_FIELD_RE = re.compile(r"(\d{1,2})頭")


def parse_result(html):
    """→ ([{placing, pop, field}], 진단) · 🔴 **두수는 세지 않고 페이지 표기(N頭)에서 읽는다.**

    🔴 [2026-08-02 검산 실패 정정] 종전에는 `field = len(out)` 이었다.
      파싱 실패분이 곧 두수 오류가 되어 **12두 경주가 13두로** 잡혔다(실측 15행 ↔ 표기 12頭).
      ⇒ **원본이 명시한 값이 있으면 그것을 읽는다. 세지 않는다.**
    ⚠ 표기가 없으면 **그 경주를 통째로 건너뛴다**(추측 금지).
    """
    m = _FIELD_RE.search(_txt(html))
    field = int(m.group(1)) if m else None
    mt = _RESULT_TABLE.search(html)          # 🔴 결과표 밖의 분석표 행을 원천 배제한다
    if not mt:
        return [], {"rows": 0, "parsed": 0, "field": field, "why": "결과표없음"}
    rows = _ROW.findall(mt.group(1))
    out = []
    for r in rows:
        c = [_txt(x) for x in _TD.findall(r)]
        if len(c) < 15:                    # 🔴 말 행은 셀 15개 — 범례·요약 행을 배제한다
            continue
        try:
            pl, pp = int(c[0]), int(c[9])
        except (ValueError, IndexError):
            continue                       # 中止·除外 등(착순·인기가 숫자가 아니다) — 분모에서 뺀다
        out.append({"placing": pl, "pop": pp, "field": field})
    diag = {"rows": len(rows), "parsed": len(out), "field": field}
    if field is None or len(out) != field:
        return [], diag                    # 🔴 표기와 불일치 → 그 경주는 버린다(오염 차단)
    return out, diag


def iter_raw(days_limit):
    """보존된 원문을 **최신 개최일부터** 돌려준다(네트워크 미사용)."""
    if not os.path.isdir(RAWDIR):
        return
    for d in sorted(os.listdir(RAWDIR), reverse=True)[:days_limit]:
        dd = os.path.join(RAWDIR, d)
        if not os.path.isdir(dd):
            continue
        for f in sorted(os.listdir(dd)):
            if not (f.startswith("jra_result_") and f.endswith(".html.gz")):
                continue
            try:
                with gzip.open(os.path.join(dd, f), "rt", encoding="utf-8") as fh:
                    yield d, f[11:23], fh.read()
            except Exception:
                continue                     # 손상 파일은 건너뛴다(덮어쓰지 않는다)


def iter_live(days):
    """netkeiba 에서 개최일 목록 → 경주별 결과 페이지를 받아 돌려준다."""
    for d in range(days):
        ymd = time.strftime("%Y%m%d", time.localtime(time.time() - d * 86400))
        try:
            lst = fetch("https://race.netkeiba.com/top/race_list_sub.html?kaisai_date=" + ymd)
        except Exception:
            continue
        for rid in sorted(set(re.findall(r"race_id=(\d{12})", lst))):
            try:
                yield ymd, rid, fetch("https://race.netkeiba.com/race/result.html?race_id=" + rid)
            except Exception:
                continue


def band(n):
    return "≤8두" if n <= 8 else ("9~12두" if n <= 12 else ("13~16두" if n <= 16 else "17두+"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry", action="store_true")
    # 🔴 [2026-08-02] 보존해 둔 원문으로 **재파싱**한다 — 네트워크를 쓰지 않는다.
    #   원문 보존의 원래 취지가 이것이다: **파서를 고치면 과거가 즉시 소급된다.**
    ap.add_argument("--from-raw", action="store_true", help="logs/form_raw 원문으로 재파싱(네트워크 미사용)")
    a = ap.parse_args()
    apply = a.apply and not a.dry

    print("=" * 96)
    print("인기 기저선 수집 · 최근 %d일 · %s" % (a.days, "APPLY" if apply else "DRY(저장 안 함)"))
    print("=" * 96)
    print("🔴 분석 로그를 읽지도 쓰지도 않는다(격리) · 원문은 logs/form_raw 에 보존")

    t0 = time.time()
    days, races, horses, skipped = 0, 0, 0, 0
    agg = {}          # (band, pop) → [n, in3, in2]
    rc_by_band = {}   # band → 경주 수 (검산②에 쓴다)
    raw_bytes = 0
    seen_days = set()
    src = iter_raw(a.days) if a.from_raw else iter_live(a.days)
    for ymd, rid, html in src:
        if a.days <= 3 and ymd not in seen_days and len(seen_days) >= 2:
            break                                       # --dry 빠른 확인용(개최일 2일)
        seen_days.add(ymd)
        days = len(seen_days)
        rows, diag = parse_result(html)
        if not rows:
            skipped += 1
            if skipped <= 3:
                print("   ⚠ 스킵(표기↔파싱 불일치): %s 행%s 파싱%s 표기%s"
                      % (rid, diag["rows"], diag["parsed"], diag["field"]))
            continue
        races += 1
        rc_by_band[band(rows[0]["field"])] = rc_by_band.get(band(rows[0]["field"]), 0) + 1
        horses += len(rows)
        raw_bytes += len(html.encode("utf-8"))
        if apply and not a.from_raw:                    # 원문 보존(재파싱 가능하게)
            try:
                dd = os.path.join(RAWDIR, ymd)
                os.makedirs(dd, exist_ok=True)
                p = os.path.join(dd, "jra_result_%s.html.gz" % rid)
                if not os.path.exists(p):
                    with gzip.open(p, "wt", encoding="utf-8") as f:
                        f.write(html)
            except Exception:
                pass
        for x in rows:
            k = (band(x["field"]), min(x["pop"], 18))
            s = agg.setdefault(k, [0, 0, 0])
            s[0] += 1
            if x["placing"] <= 3:
                s[1] += 1
            if x["placing"] <= 2:
                s[2] += 1
        if races and races % 100 == 0:
            print("  … 개최일 %d · 경주 %d · 말 %d (%.0f초)" % (days, races, horses, time.time() - t0))

    el = time.time() - t0
    print()
    print("개최일 %d · 경주 %d · **말 %d두** · 소요 %.0f초 · 원문 %.1f MB(비압축)"
          % (days, races, horses, el, raw_bytes / 1048576.0))
    if not horses:
        print("🔴 표본 0 — 저장하지 않는다")
        return
    print()
    print("  %-8s %5s %7s %9s %9s %10s" % ("두수", "인기", "n", "3착률", "1·2착률", "3÷두수 대비"))
    # ⚠ 판단 근거를 값과 **같이** 저장한다 — 나중에 "이 값이 어디서 왔나"를 물을 수 있어야 한다.
    doc = {"builtAt": time.strftime("%Y-%m-%d %H:%M:%S"), "days": days, "races": races,
           "horses": horses, "source": ("from-raw" if a.from_raw else "live"),
           "skippedRaces": skipped,
           "note": "스킵분은 中止·除外·取消 로 표기두수↔파싱두수가 어긋난 경주다(추측 금지 원칙에 따라 통째로 제외).",
           "cells": {}}
    mid = {"≤8두": 7.5, "9~12두": 10.5, "13~16두": 14.5, "17두+": 17.5}
    for (b, p), (n, i3, i2) in sorted(agg.items()):
        if n < 20:
            continue                                    # ⚠ n<20 셀은 출력·저장에서 뺀다(판정 불가)
        r3 = 100.0 * i3 / n
        rnd = 100.0 * 3.0 / mid[b]
        print("  %-8s %5d %7d %8.1f%% %8.1f%% %9.2f배"
              % (b, p, n, r3, 100.0 * i2 / n, r3 / rnd if rnd else 0))
        doc["cells"]["%s|%d" % (b, p)] = {"n": n, "in3": round(r3, 2),
                                          "in2": round(100.0 * i2 / n, 2)}
    # ── 🔴 검산 3종 (2026-08-02 승인) — **통과 못 하면 저장하지 않는다** ──────────────
    #   왜: 직전 수집이 두수를 `len(out)` 으로 세어 **1인기 3착률이 77.6% 로 부풀려졌다**.
    #   집계표는 "합이 말이 되는가"를 스스로 검산해야 한다(새 절차).
    print("\n" + "=" * 60)
    print("검산 3종")
    fails = []
    # ① 인기별 n 일치 — 같은 경주에 인기 1·2가 각 1마리씩이므로 n 은 같아야 한다
    for b in sorted(rc_by_band):
        ns = [v[0] for (bb, p), v in agg.items() if bb == b and p <= 5]
        if len(ns) >= 2:
            lo, hi = min(ns), max(ns)
            gap = 100.0 * (hi - lo) / max(hi, 1)
            mark = "🟢" if gap <= 5 else "🔴"
            print("  ① %-8s 인기1~5 n %d~%d (편차 %.1f%%) %s" % (b, lo, hi, gap, mark))
            if gap > 5:
                fails.append("① %s 인기별 n 편차 %.1f%% > 5%%" % (b, gap))
    # ② 3착 건수 합 ÷ 경주 수 ≈ 3
    for b, rc in sorted(rc_by_band.items()):
        tot3 = sum(v[1] for (bb, p), v in agg.items() if bb == b)
        val = tot3 / max(rc, 1)
        mark = "🟢" if abs(val - 3.0) <= 0.15 else "🔴"
        print("  ② %-8s 3착합 %d ÷ 경주 %d = %.2f (기대 3.00) %s" % (b, tot3, rc, val, mark))
        if abs(val - 3.0) > 0.15:
            fails.append("② %s 3착합÷경주수 %.2f (기대 3.00)" % (b, val))
    # ③ 두수 일치율 — 표기와 다른 경주는 이미 스킵했으므로 채택분은 100% 여야 한다
    rate = 100.0 * races / max(races + skipped, 1)
    print("  ③ 두수 일치 채택 %d · 스킵 %d → 채택률 %.1f%% (스킵분은 표기↔파싱 불일치)"
          % (races, skipped, rate))
    if races == 0:
        fails.append("③ 채택 경주 0")
    print("=" * 60)

    if fails:
        print("🔴 **검산 실패 %d건 — 저장하지 않는다**" % len(fails))
        for f in fails:
            print("   -", f)
        sys.exit(1)
    print("🟢 검산 통과")
    if apply:
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        doc["verified"] = True
        json.dump(doc, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("🟢 저장: %s (셀 %d)" % (OUT, len(doc["cells"])))
    else:
        print("⚠ DRY — 저장하지 않았다. --apply 로 저장한다.")


if __name__ == "__main__":
    main()
