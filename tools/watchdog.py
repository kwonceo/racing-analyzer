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


def _app_procs():
    """지금 떠 있는 `python … app.py` 프로세스 [(pid, 나이초)]. 조회 실패면 None(모른다 — 빈 목록과 구분한다 · 원칙 24)."""
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*app.py*' } | "
          "ForEach-Object { '{0} {1}' -f $_.ProcessId, [int]((Get-Date) - $_.CreationDate).TotalSeconds }")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=30).stdout
    except Exception as e:
        print("[watchdog] 프로세스 조회 실패:", str(e)[:100])
        return None
    rows = []
    for line in (out or "").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].lstrip("-").isdigit():
            rows.append((int(parts[0]), int(parts[1])))
    # ⚠ 다른 계정(관리자 콘솔 등)이 띄운 프로세스는 CommandLine 이 안 읽힌다(9/14·9/18 실측 — 빈 목록).
    #   그래서 8011 을 LISTEN 중인 소유 프로세스도 「있는 프로세스」로 합친다(나이는 CIM 으로 다시 읽는다).
    try:
        out2 = subprocess.run(["powershell", "-NoProfile", "-Command",
                               "Get-NetTCPConnection -LocalPort 8011 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { $p = Get-CimInstance Win32_Process -Filter (\"ProcessId=\" + $_.OwningProcess); '{0} {1}' -f $_.OwningProcess, [int]((Get-Date) - $p.CreationDate).TotalSeconds }"],
                              capture_output=True, text=True, timeout=30).stdout
        seen = {p for p, _ in rows}
        for line in (out2 or "").splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[0].isdigit() and parts[1].lstrip("-").isdigit() and int(parts[0]) not in seen:
                rows.append((int(parts[0]), int(parts[1])))
    except Exception:
        pass
    return rows


AUTOSTART_LOG = os.path.join(LOG_DIR, "autostart.log")


def _recent_start_attempt(within_sec):
    """시작 bat 이 남기는 「[YYYY-MM-DD HH:MM:SS.ff] START attempt」가 within_sec 안에 있으면 그 초 전 값. 없으면 None.
    프로세스 명령줄이 안 읽히는 계정에서도 「지금 뜨는 중」을 알 수 있는 유일한 흔적이다."""
    try:
        if not os.path.exists(AUTOSTART_LOG):
            return None
        last = None
        with open(AUTOSTART_LOG, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if "START attempt" in line and line.startswith("["):
                    last = line[1:20].strip()
        if not last:
            return None
        t = time.mktime(time.strptime(last.replace("  ", " 0") if last[11] == " " else last, "%Y-%m-%d %H:%M:%S"))
        age = time.time() - t
        return age if 0 <= age <= within_sec else None
    except Exception:
        return None


def _uptime_sec():
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "[int]((Get-Date) - (Get-CimInstance Win32_OperatingSystem).LastBootUpTime).TotalSeconds"],
                             capture_output=True, text=True, timeout=30).stdout.strip()
        return int(out) if out.lstrip("-").isdigit() else None
    except Exception:
        return None


# [2026-09-18 실사고] 9/16 12:04 부팅 → 시작프로그램 bat 이 서버를 띄우는 중(바인딩까지 ~40초)에 5분 주기 워치독이
#   HTTP 미응답을 「죽음」으로 보고 두 번째 서버를 띄웠다 → SO_REUSEADDR 로 8011 에 두 벌 LISTEN(카톡 2회 · 틱 중복).
#   9/14 재부팅 때도 같은 무늬(autostart START 2회). ⇒ 「프로세스는 있는데 HTTP 만 안 되는」 상태를 죽음과 구분한다.
START_GRACE_SEC = 240      # app.py 프로세스가 이 나이 안이면 「뜨는 중」 — 재기동하지 않는다
BOOT_GRACE_SEC = 300       # 부팅 뒤 이 시간 안에는 시작프로그램에 맡긴다


def main():
    if _alive():
        # 직전이 down 이었을 때만 한 줄 남긴다.
        rows = _recent_rows(RESTART_WINDOW_MIN)
        if rows:
            last = rows[-1].get("ev")
            if last in ("down", "restart", "giveup", "starting", "hung"):
                _append("recovered")
        print("[watchdog] alive")
        return 0

    # ---- HTTP 는 안 온다. 그런데 프로세스가 있나? (죽음 ↔ 뜨는 중 ↔ 멈춤을 가른다) ----
    procs = _app_procs()
    up = _uptime_sec()
    sa = _recent_start_attempt(START_GRACE_SEC)
    if sa is not None:
        _append("starting", procs=procs, uptime=up, start_attempt_age=int(sa))
        print("[watchdog] 시작 bat 이 %d초 전에 기동 시도 — 재기동하지 않는다" % sa)
        return 0
    if procs:
        young = [p for p in procs if p[1] < START_GRACE_SEC]
        if young or (up is not None and up < BOOT_GRACE_SEC):
            _append("starting", procs=procs, uptime=up)
            print("[watchdog] 서버가 뜨는 중(app.py %d개 · 부팅 %s초) — 재기동하지 않는다" % (len(procs), up))
            return 0
        # 프로세스는 오래 살아 있는데 HTTP 가 죽었다 = 멈춤. 두 벌을 만들지 않고 사람에게 남긴다.
        _append("hung", procs=procs, uptime=up)
        print("[watchdog] app.py 프로세스 %d개가 있는데 HTTP 무응답 — 두 벌 방지로 재기동 보류(tools/kill_safe.py 로 정리 후)" % len(procs))
        return 7
    if procs is None:
        _append("giveup", reason="프로세스 조회 실패 — 두 벌 위험이라 재기동 보류")
        return 8
    if up is not None and up < BOOT_GRACE_SEC:
        _append("starting", procs=[], uptime=up)
        print("[watchdog] 부팅 %d초 — 시작프로그램에 맡긴다" % up)
        return 0

    # ---- 여기부터 죽어 있다(프로세스도 없다) ----
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
