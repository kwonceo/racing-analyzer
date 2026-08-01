# -*- coding: utf-8 -*-
"""[안전 종료 (2026-08-01 신설)] — **서버를 실수로 죽이지 않게 한다.**

■ 왜 만들었나
  2026-08-01 하루에 서버가 **3번** 죽었다. 그중 1회는 **내가 직접 죽였다** —
  `Get-Process python` 으로 잡은 PID 목록에서 "아까 확인한 서버 PID"를 제외했는데,
  그 사이 재기동이 있어 **PID 가 이미 바뀌어 있었다.**
  🔴 **"아까 확인한 PID"는 재기동이 있었으면 낡은 값이다.** 사람 절차는 잊는다 → 스크립트로 만든다.

■ 무엇을 하는가
  ① `netstat` 으로 **지금 이 순간** 8011 을 LISTEN 하는 PID 를 구한다(그리고 그 부모까지).
  ② 그 PID 는 **무조건 종료 대상에서 제외**한다.
  ③ 나머지 python 프로세스 중 **이름/커맨드라인이 일치하는 것만** 종료한다.
  ⚠ `--dry` 가 기본. `--apply` 를 붙여야 실제로 종료한다.

사용:
  python tools/kill_safe.py --match probe_jra_odds            (무엇이 죽을지 본다)
  python tools/kill_safe.py --match probe_jra_odds --apply    (실제 종료)
  python tools/kill_safe.py --list                            (현재 python 프로세스와 서버 PID)
"""
import argparse
import os
import re
import subprocess
import sys

SERVER_PORT = 8011


def server_pids(port=SERVER_PORT):
    """지금 이 순간 그 포트를 LISTEN 하는 PID 집합. ⚠ 캐시하지 않는다 — 매번 새로 구한다."""
    out = set()
    try:
        r = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=20)
        for line in (r.stdout or "").splitlines():
            if "LISTENING" not in line or (":%d" % port) not in line:
                continue
            m = re.search(r"(\d+)\s*$", line.strip())
            if m:
                out.add(int(m.group(1)))
    except Exception as e:
        print("⚠ netstat 실패: %s — 안전을 위해 아무것도 죽이지 않는다." % e)
        return None                      # None = 판정 불가 → 호출부가 중단한다
    return out


def python_procs():
    """python.exe 프로세스 (PID, 커맨드라인)."""
    rows = []
    try:
        r = subprocess.run(
            ["wmic", "process", "where", "name='python.exe'", "get", "ProcessId,CommandLine", "/format:list"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=25)
        cur = {}
        for line in (r.stdout or "").splitlines():
            line = line.strip()
            if not line:
                if cur.get("ProcessId"):
                    rows.append((int(cur["ProcessId"]), cur.get("CommandLine", "")))
                cur = {}
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                cur[k.strip()] = v.strip()
        if cur.get("ProcessId"):
            rows.append((int(cur["ProcessId"]), cur.get("CommandLine", "")))
    except Exception as e:
        print("⚠ 프로세스 조회 실패:", e)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--match", help="커맨드라인에 이 문자열이 든 프로세스만 대상")
    ap.add_argument("--apply", action="store_true", help="실제로 종료(기본은 미실행)")
    ap.add_argument("--list", action="store_true", help="현황만 출력")
    a = ap.parse_args()

    sp = server_pids()
    if sp is None:
        return 1
    procs = python_procs()

    print("=" * 74)
    print("안전 종료  %s" % ("[APPLY]" if a.apply else "[DRY-RUN]"))
    print("=" * 74)
    print("🟢 지금 %d 포트를 LISTEN 하는 PID(=서버·**절대 종료 안 함**): %s"
          % (SERVER_PORT, sorted(sp) if sp else "**없음 — 서버가 안 떠 있다**"))
    if not sp:
        print("⚠ 서버가 안 떠 있다. 종료 작업 전에 이 사실을 먼저 확인할 것.")
    print("\npython 프로세스 %d개:" % len(procs))
    for pid, cmd in procs:
        tag = "🟢 서버(제외)" if pid in sp else "  "
        print("   %-7d %s %s" % (pid, tag, (cmd or "")[:100]))

    if a.list:
        return 0
    if not a.match:
        print("\n⚠ `--match` 가 없다. 무엇을 죽일지 지정하지 않으면 **아무것도 하지 않는다.**")
        return 0

    targets = [(p, c) for p, c in procs if p not in sp and a.match in (c or "")]
    print("\n🔴 종료 대상 %d개 (match=%r · 서버 PID 제외 후):" % (len(targets), a.match))
    for pid, cmd in targets:
        print("   %-7d %s" % (pid, (cmd or "")[:100]))
    if not targets:
        print("   (없음)")
        return 0
    if not a.apply:
        print("\n⚠ DRY-RUN 이다. 실제 종료는 `--apply`.")
        return 0

    for pid, _ in targets:
        if pid in server_pids():                      # ⚠ 종료 직전 **한 번 더** 확인(그 사이 재기동 대비)
            print("   ⏭ %d 는 지금 서버다 — 건너뛴다" % pid)
            continue
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=15)
            print("   ✅ 종료 %d" % pid)
        except Exception as e:
            print("   ❌ 실패 %d: %s" % (pid, e))
    print("\n🟢 종료 후 서버 PID: %s" % (sorted(server_pids() or []) or "**없음 — 즉시 재기동할 것**"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
