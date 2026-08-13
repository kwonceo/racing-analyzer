# -*- coding: utf-8 -*-
"""서버 감시 — 죽어 있으면 다시 띄운다.

2026-08-14 신설. 윈도우 업데이트 재부팅으로 01:59~04:05 서버가 없었고
두 시간 동안 아무도 몰랐던 사고 대응.

설계
  · 5분 간격으로 작업 스케줄러가 부른다.
  · HTTP 가 응답하면 아무것도 하지 않는다(로그도 안 남긴다 — 도배 방지).
    직전이 down 이었을 때만 recovered 한 줄을 남긴다.
  · 응답이 없으면 재기동. 단 최근 60분 재기동이 3회 이상이면 멈추고 기록만 한다.
    🔴 코드가 깨져 계속 죽는 상황에서 무한 재시작이 되면 로그만 쌓이고
       진짜 원인을 못 본다. 그래서 상한을 둔다.

기록은 logs/watchdog.jsonl 에 append 만 한다.
🔴 읽기 실패를 빈 값으로 덮어쓰지 않는다(원칙 9). append 전용이라 구조적으로 안전하다.

이 스크립트는 서버를 읽기만 한다. 추천·수집·판정 경로에 개입하지 않는다.
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(BASE_DIR, "logs")
LOG_PATH = os.path.join(LOG_DIR, "watchdog.jsonl")
STARTER = os.path.join(BASE_DIR, "scripts", "start_server_only.bat")

HEALTH_URL = "http://127.0.0.1:8011/api/auto/status"
TIMEOUT_SEC = 8

# 최근 이 시간(분) 안에 재기동이 MAX_RESTARTS 회 이상이면 더 시도하지 않는다.
RESTART_WINDOW_MIN = 60
MAX_RESTARTS = 3


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _append(ev, **kw):
    """append 전용. 실패해도 감시 자체는 계속 간다."""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        row = {"at": _now(), "t": time.time(), "ev": ev}
        row.update(kw)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as e:
        print("[watchdog] 기록 실패(무시):", str(e)[:100])


def _recent_rows(minutes):
    """최근 N분 기록. 파일이 없으면 빈 리스트가 정상(첫 실행)."""
    rows = []
    if not os.path.exists(LOG_PATH):
        return rows
    cut = time.time() - minutes * 60
    try:
        with open(LOG_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if float(d.get("t") or 0) >= cut:
                    rows.append(d)
    except Exception as e:
        # 🔴 읽기 실패를 "재기동 0회"로 읽으면 상한이 무력화된다.
        #    실패를 명시해 상한 쪽으로 안전하게 기울인다.
        print("[watchdog] 이력 읽기 실패:", str(e)[:100])
        return None
    return rows


def _alive():
    try:
        req = urllib.request.Request(HEALTH_URL, headers={"Accept": "*/*"})
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as r:
            return 200 <= r.getcode() < 400
    except Exception:
        return False


def main():
    if _alive():
        # 직전이 down 이었을 때만 한 줄 남긴다.
        rows = _recent_rows(RESTART_WINDOW_MIN)
        if rows:
            last = rows[-1].get("ev")
            if last in ("down", "restart", "giveup"):
                _append("recovered")
        print("[watchdog] alive")
        return 0

    # ---- 여기부터 죽어 있다 ----
    rows = _recent_rows(RESTART_WINDOW_MIN)
    if rows is None:
        _append("giveup", reason="이력 읽기 실패 — 상한 판정 불가")
        print("[watchdog] 이력을 못 읽어 재기동을 보류했다")
        return 2

    n_restart = sum(1 for d in rows if d.get("ev") == "restart")
    _append("down", restarts_in_window=n_restart)

    if n_restart >= MAX_RESTARTS:
        _append("giveup",
                reason="최근 %d분 재기동 %d회 — 상한 도달" % (RESTART_WINDOW_MIN, n_restart))
        print("[watchdog] 상한 도달. 재기동하지 않는다 (%d회)" % n_restart)
        return 3

    if not os.path.exists(STARTER):
        _append("giveup", reason="기동 스크립트 없음: %s" % STARTER)
        print("[watchdog] 기동 스크립트가 없다:", STARTER)
        return 4

    try:
        subprocess.run(["cmd", "/c", STARTER], cwd=BASE_DIR, timeout=120)
    except Exception as e:
        _append("giveup", reason="기동 실행 실패: %s" % str(e)[:150])
        print("[watchdog] 기동 실행 실패:", str(e)[:150])
        return 5

    ok = _alive()
    _append("restart", ok=ok)
    print("[watchdog] 재기동 %s" % ("성공" if ok else "실패"))
    return 0 if ok else 6


if __name__ == "__main__":
    sys.exit(main())
