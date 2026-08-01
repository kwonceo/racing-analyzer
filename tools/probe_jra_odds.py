# -*- coding: utf-8 -*-
"""[netkeiba 배당 갱신 주기 실측 (2026-08-01 신설)] — **완전 읽기 전용 · 서버 무관**.

■ 왜 재는가
  BMED 는 **배당의 움직임**을 읽는다. 값이 아니라 변화가 신호다.
  30초마다 불러도 값이 안 바뀌면 "수집은 되는데 신호는 안 나온다".
  ⇒ **배선하기 전에 갱신 주기부터 잰다.** 이 측정이 배선 가치를 결정한다.

■ 무엇을 하는가
  지정한 race_id 들을 N초 간격으로 M회 호출해 **원문 응답을 그대로 파일에 남긴다.**
  나중에 대조할 수 있도록 JSONL 로 append 한다(요청 시각·응답 본문·소요 시간).

■ ⚠ 이 도구는 아무것도 바꾸지 않는다. 서버(app.py)와도 무관하다.

사용: python tools/probe_jra_odds.py --ids 202604020305,202601010305 --interval 30 --rounds 26
"""
import argparse
import gzip
import json
import os
import sys
import time
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(BASE, "logs", "jra_odds_probe")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
HDR = {"User-Agent": UA, "Accept": "*/*", "Accept-Language": "ja", "Accept-Encoding": "gzip"}
# 🔴 BMED 가 실제로 쓰는 것만 받는다 — type=4(馬連=복승) · type=7(三連複=삼복승).
#   type=1(단승)·type=6(馬単=쌍승)은 중앙경마 분석에 안 쓰므로 요청하지 않는다(부하 절반).
TYPES = {4: "복승(馬連)", 7: "삼복승(三連複)"}
# 🔴 [2026-08-01 실측] `action` 파라미터가 **결정적**이다.
#   (없음)          → 발주 전에는 `{"status":"middle","data":"","reason":"result odds empty"}` — **빈 응답**.
#                     처음에 성공한 것은 **이미 끝난 경주의 확정배당**이었다(status=result).
#   `action=init`   → 페이지 최초 로드분(오래된 스냅샷)
#   🔴 `action=update` → **실시간 오즈**. 발주 전 경주에서도 값이 오고 `official_datetime` 이 갱신된다.
#   ⇒ BMED 가 필요한 것은 **마감 전 배당 변화**이므로 반드시 `action=update` 를 써야 한다.
URL = "https://race.netkeiba.com/api/api_get_jra_odds.html?race_id=%s&type=%d&action=update"


def fetch(url):
    t0 = time.time()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=HDR), timeout=12) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return r.status, raw.decode("utf-8", "replace"), time.time() - t0
    except Exception as e:
        return -1, "%s: %s" % (type(e).__name__, str(e)[:120]), time.time() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", required=True, help="race_id 쉼표 구분")
    ap.add_argument("--interval", type=int, default=30)
    ap.add_argument("--rounds", type=int, default=26)
    a = ap.parse_args()
    ids = [x.strip() for x in a.ids.split(",") if x.strip()]
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, time.strftime("%Y%m%d_%H%M%S") + ".jsonl")
    print("실측 시작 — race %d개 × type %d종 × %d회 (간격 %d초)"
          % (len(ids), len(TYPES), a.rounds, a.interval))
    print("기록: %s" % os.path.relpath(path, BASE))
    with open(path, "a", encoding="utf-8") as f:
        for i in range(a.rounds):
            for rid in ids:
                for t in TYPES:
                    st, body, el = fetch(URL % (rid, t))
                    dt = None
                    try:
                        dt = (json.loads(body).get("data") or {}).get("official_datetime")
                    except Exception:
                        pass
                    f.write(json.dumps({
                        "round": i, "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "race_id": rid, "type": t, "status": st,
                        "elapsed": round(el, 3), "official_datetime": dt,
                        "body": body,                      # ⚠ 원문 그대로 — 나중에 대조용
                    }, ensure_ascii=False) + "\n")
                    f.flush()
            print("  round %2d/%d  %s" % (i + 1, a.rounds, time.strftime("%H:%M:%S")))
            if i < a.rounds - 1:
                time.sleep(a.interval)
    print("완료:", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
