# -*- coding: utf-8 -*-
"""[netkeiba 경주결과(착순·払戻) 실물 확인 (2026-08-01 신설)] — **완전 읽기 전용 · 서버 무관**.

■ 왜
  중앙경마는 확정배당·착순이 **0건**이라 성적을 못 재고 있다.
  배당 API(`api_get_jra_odds`)에 착순이 없는 것은 당연하다 — **배당 API** 이므로.
  착순·払戻는 `race/result.html` 에 있다(2026-08-01 확인). 그 실물을 먼저 본다.

■ 무엇을 하는가
  지정 race_id 의 `result.html` 을 받아 **원문을 그대로 파일에 남기고**, 구조를 요약 출력한다.
  ⚠ 파싱 결과가 아니라 **원문**을 남긴다 — 나중에 파서를 고칠 때 재현 대조가 필요하다.

사용: python tools/probe_jra_result.py --ids 202601010301,202601010302
"""
import argparse
import gzip
import os
import re
import sys
import time
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(BASE, "logs", "jra_result_probe")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
HDR = {"User-Agent": UA, "Accept": "text/html,*/*", "Accept-Language": "ja", "Accept-Encoding": "gzip"}
URL = "https://race.netkeiba.com/race/result.html?race_id=%s"


def fetch(url):
    t0 = time.time()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=HDR), timeout=15) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return r.status, raw.decode("utf-8", "replace"), time.time() - t0
    except Exception as e:
        return -1, "%s: %s" % (type(e).__name__, str(e)[:150]), time.time() - t0


def summarize(html):
    """무엇이 들어 있는지 나열."""
    out = {}
    txt = re.sub(r"<[^>]+>", " ", html)
    out["로그인요구"] = ("ログイン" in html and "パスワード" in html)
    # 착순 테이블
    rows = re.findall(r'<tr[^>]*class="[^"]*HorseList[^"]*"[^>]*>(.*?)</tr>', html, re.S)
    out["착순행수"] = len(rows)
    ranks = []
    for r in rows[:3]:
        cells = [re.sub(r"<[^>]+>", " ", c).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", r, re.S)]
        ranks.append([c for c in cells if c][:6])
    out["상위3행"] = ranks
    # 払戻
    out["払戻섹션"] = bool(re.search(r"Payout|払戻", html))
    pays = {}
    for m in re.finditer(r'<tr[^>]*class="([^"]*)"[^>]*>\s*<th[^>]*>(.*?)</th>(.*?)</tr>', html, re.S):
        cls, th, rest = m.groups()
        name = re.sub(r"<[^>]+>", "", th).strip()
        if name in ("単勝", "複勝", "枠連", "馬連", "ワイド", "馬単", "三連複", "三連単"):
            nums = re.sub(r"<[^>]+>", " ", rest)
            pays[name] = re.sub(r"\s+", " ", nums).strip()[:120]
    out["払戻항목"] = pays
    for k in ("通過", "上り", "馬体重", "タイム", "着差", "人気"):
        out["필드_" + k] = (k in txt)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", required=True)
    a = ap.parse_args()
    os.makedirs(OUT_DIR, exist_ok=True)
    for rid in [x.strip() for x in a.ids.split(",") if x.strip()]:
        st, body, el = fetch(URL % rid)
        p = os.path.join(OUT_DIR, "%s_%s.html" % (time.strftime("%Y%m%d_%H%M%S"), rid))
        open(p, "w", encoding="utf-8").write(body)
        print("=" * 76)
        print("race_id=%s  HTTP %s  %.2f초  len=%d" % (rid, st, el, len(body)))
        print("원문 저장: %s" % os.path.relpath(p, BASE))
        if st != 200:
            print("  ", body[:200])
            continue
        s = summarize(body)
        for k, v in s.items():
            print("   %-12s %s" % (k, v))
    return 0


if __name__ == "__main__":
    sys.exit(main())
