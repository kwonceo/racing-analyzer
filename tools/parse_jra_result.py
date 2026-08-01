# -*- coding: utf-8 -*-
"""[netkeiba 경주결과 파서 (2026-08-01 신설)] — 착순 + 払戻. **파싱만 · 저장 없음**.

■ 왜 별도 파일인가
  ③배선 승인 전이라 `app.py` 를 건드리지 않는다. 여기서 **파싱만** 검증하고,
  승인되면 `app.py` 가 이 모듈을 import 하거나 함수를 옮긴다(코드 중복 금지).

■ 실측으로 확인한 구조 (2026-08-01 · 원문 `logs/jra_result_probe/`)
  · `race/result.html?race_id=<12자리>` · HTTP 200 · **로그인 불필요** · 0.13~0.39초
  · 착순: `<tr class="...HorseList...">` 12~13행(전체 착순)
  · 払戻: `<table class="Payout_Detail_Table">` **2개**
      테이블1: 単勝 · 複勝 · 枠連 · 馬連
      테이블2: ワイド · 馬単 · **3連複** · **3連単**
    ⚠ 🔴 **`三連複` 이 아니라 `3連複`(아라비아 숫자)** 다. 한자로 찾으면 **못 찾는다**(실제로 놓쳤다).
  · 🔴 **천 단위 콤마**: `1,110円` · `2,550円` · `20,550円` — 제거 필수.
  · 결과 미확정 경주: 착순 **0행** · 払戻 테이블은 있으나 **내용 비어 있음** → 이것으로 분기한다.

■ 우리 스키마 매핑 (⚠ 이름이 헷갈린다 — 여기 고정한다)
    우리 '복승'  = 馬連   (1·2착 순서무관)  → payouts.quinella
    우리 '쌍승'  = 馬単   (순서 있음)       → payouts.exacta
    우리 '삼복승'= 3連複                    → payouts.trio
    단승        = 単勝                     → payouts.win

사용: python tools/parse_jra_result.py --ids 202601010301,202601010305,202601010308
"""
import argparse
import gzip
import json
import os
import re
import sys
import time
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
HDR = {"User-Agent": UA, "Accept": "text/html,*/*", "Accept-Language": "ja", "Accept-Encoding": "gzip"}
RESULT_URL = "https://race.netkeiba.com/race/result.html?race_id=%s"

# 🔴 표기 변형을 전부 넣는다. `三連複`(한자)·`3連複`(아라비아) 둘 다 실재할 수 있다.
_PAY_MAP = {
    "単勝": "win", "馬連": "quinella", "馬単": "exacta",
    "3連複": "trio", "三連複": "trio", "３連複": "trio",
    "3連単": "trifecta", "三連単": "trifecta", "３連単": "trifecta",
    "複勝": "place", "ワイド": "wide", "枠連": "bracket",
}


def _fetch(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=HDR), timeout=15) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        return raw.decode("utf-8", "replace")


def _yen(s):
    """`"2,550円"` → 2550. 🔴 **천 단위 콤마 제거가 핵심**(오즈 확정배당도 같은 특성)."""
    m = re.search(r"([\d,]+)", str(s or ""))
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", ""))
    except Exception:
        return None


def _cells(tr):
    out = [re.sub(r"<[^>]+>", " ", c) for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr, re.S)]
    return [re.sub(r"\s+", " ", c).strip() for c in out]


def parse_result(html):
    """→ {ok, finished, order:[...], top3:[..], payouts:{...}, payouts_raw:{...}}"""
    out = {"ok": True, "finished": False, "order": [], "top3": [], "payouts": {}, "payouts_raw": {}}
    rows = re.findall(r'<tr[^>]*class="[^"]*HorseList[^"]*"[^>]*>(.*?)</tr>', html, re.S)
    for r in rows:
        c = _cells(r)
        if len(c) < 4:
            continue
        try:
            rank = int(re.sub(r"\D", "", c[0]) or 0)
            umaban = int(re.sub(r"\D", "", c[2]) or 0)      # 0=착순 1=枠 2=마번
        except Exception:
            continue
        if not rank or not umaban:
            continue                                        # 除外·中止 행은 착순 숫자가 없다
        out["order"].append({"rank": rank, "no": umaban, "name": c[3] if len(c) > 3 else ""})
    out["order"].sort(key=lambda x: x["rank"])
    out["top3"] = [x["no"] for x in out["order"][:3]]

    for tbl in re.findall(r'<table[^>]*class="Payout_Detail_Table"[^>]*>(.*?)</table>', html, re.S):
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", tbl, re.S):
            c = _cells(tr)
            if len(c) < 3 or c[0] not in _PAY_MAP:
                continue
            key = _PAY_MAP[c[0]]
            out["payouts_raw"][c[0]] = {"combo": c[1], "yen": c[2],
                                        "pop": c[3] if len(c) > 3 else ""}
            # 대표값 1건(첫 조합)만 payouts 에 넣는다 — 複勝·ワイド 는 다건이라 raw 로만 보존
            if key in ("win", "quinella", "exacta", "trio", "trifecta"):
                out["payouts"][key] = _yen(c[2].split(" ")[0])
    out["finished"] = bool(out["order"]) and bool(out["payouts"])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", required=True)
    a = ap.parse_args()
    for rid in [x.strip() for x in a.ids.split(",") if x.strip()]:
        t0 = time.time()
        try:
            r = parse_result(_fetch(RESULT_URL % rid))
        except Exception as e:
            print("%s  🔴 실패: %s" % (rid, str(e)[:100]))
            continue
        print("=" * 74)
        print("race_id=%s  %.2f초  finished=%s  착순 %d두" % (rid, time.time() - t0, r["finished"], len(r["order"])))
        if not r["finished"]:
            print("   ⏳ 결과 미확정 — 착순 %d행 · 払戻 %d건 (분기 정상)" % (len(r["order"]), len(r["payouts"])))
            continue
        print("   top3 = %s" % r["top3"])
        print("   payouts(우리 스키마) = %s" % json.dumps(r["payouts"], ensure_ascii=False))
        print("   payouts_raw = %s" % json.dumps(r["payouts_raw"], ensure_ascii=False)[:300])
    return 0


if __name__ == "__main__":
    sys.exit(main())
