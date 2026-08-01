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


def fetch(url, timeout=15):
    r = urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=timeout)
    ct = r.headers.get("Content-Type", "")
    m = re.search(r"charset=([\w\-]+)", ct, re.I)
    return r.read().decode(m.group(1) if m else "utf-8", "replace")


def _txt(h):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h)).strip()


def parse_result(html):
    """→ [{placing, pop, field}] · ⚠ 착순/인기가 숫자가 아닌 행(중지·제외)은 버린다."""
    rows = _ROW.findall(html)
    out = []
    for r in rows:
        c = [_txt(x) for x in _TD.findall(r)]
        if len(c) < 11:
            continue
        try:
            out.append({"placing": int(c[0]), "pop": int(c[9])})
        except (ValueError, IndexError):
            continue                       # 중지·실격 등 — 조용히 넘기지 않고 분모에서 뺀다
    n = len(out)
    for x in out:
        x["field"] = n
    return out


def band(n):
    return "≤8두" if n <= 8 else ("9~12두" if n <= 12 else ("13~16두" if n <= 16 else "17두+"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    apply = a.apply and not a.dry

    print("=" * 96)
    print("인기 기저선 수집 · 최근 %d일 · %s" % (a.days, "APPLY" if apply else "DRY(저장 안 함)"))
    print("=" * 96)
    print("🔴 분석 로그를 읽지도 쓰지도 않는다(격리) · 원문은 logs/form_raw 에 보존")

    t0 = time.time()
    days, races, horses = 0, 0, 0
    agg = {}          # (band, pop) → [n, in3, in2]
    raw_bytes = 0
    for d in range(a.days):
        ymd = time.strftime("%Y%m%d", time.localtime(time.time() - d * 86400))
        try:
            lst = fetch("https://race.netkeiba.com/top/race_list_sub.html?kaisai_date=" + ymd)
        except Exception:
            continue
        ids = sorted(set(re.findall(r"race_id=(\d{12})", lst)))
        if not ids:
            continue
        days += 1
        for rid in ids:
            try:
                html = fetch("https://race.netkeiba.com/race/result.html?race_id=" + rid)
            except Exception:
                continue
            rows = parse_result(html)
            if not rows:
                continue
            races += 1
            horses += len(rows)
            raw_bytes += len(html.encode("utf-8"))
            if apply:                                  # 원문 보존(재파싱 가능하게)
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
        if days and days % 5 == 0:
            print("  … 개최일 %d · 경주 %d · 말 %d (%.0f초)" % (days, races, horses, time.time() - t0))
        if a.days <= 3 and days >= 2:
            break                                       # --dry 빠른 확인용

    el = time.time() - t0
    print()
    print("개최일 %d · 경주 %d · **말 %d두** · 소요 %.0f초 · 원문 %.1f MB(비압축)"
          % (days, races, horses, el, raw_bytes / 1048576.0))
    if not horses:
        print("🔴 표본 0 — 저장하지 않는다")
        return
    print()
    print("  %-8s %5s %7s %9s %9s %10s" % ("두수", "인기", "n", "3착률", "1·2착률", "3÷두수 대비"))
    doc = {"builtAt": time.strftime("%Y-%m-%d %H:%M:%S"), "days": days, "races": races,
           "horses": horses, "cells": {}}
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
    if apply:
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        json.dump(doc, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("\n🟢 저장: %s (셀 %d)" % (OUT, len(doc["cells"])))
    else:
        print("\n⚠ DRY — 저장하지 않았다. --apply 로 저장한다.")


if __name__ == "__main__":
    main()
