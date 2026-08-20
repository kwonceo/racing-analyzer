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
import time


def _parent_of(pid):
    """PID 의 부모 PID(없으면 None).

    🔴 [2026-08-03] 오늘 `netstat` 스냅샷만 보고 7148 을 '고아'로 오판했다.
      실제로는 7148(부모)+24384(자식)의 **Flask 리로더 정상 구조**였다.
      ⇒ **프로세스를 판정할 때는 부모-자식 관계를 먼저 확인한다.**
    """
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_Process -Filter \"ProcessId=%d\").ParentProcessId" % int(pid)],
            capture_output=True, text=True, timeout=15)
        s = (out.stdout or "").strip()
        return int(s) if s.isdigit() else None
    except Exception:
        return None

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
    # 🔴 [2026-08-20] Windows 11 에서 `wmic` 이 제거돼 **명령줄을 못 읽고 대상 0개**가 됐다.
    #   0개가 나오면 「죽일 게 없다」와 「못 읽었다」가 구분되지 않는다 — 조용히 틀리는 유형이다.
    #   ⇒ PowerShell Get-CimInstance 를 **먼저** 쓰고, 실패하면 종전 wmic 으로 폴백한다.
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
             "ForEach-Object { \"ProcessId=\" + $_.ProcessId; \"CommandLine=\" + $_.CommandLine; \"\" }"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
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
        print("⚠ PowerShell 조회 실패(무시·wmic 폴백):", str(e)[:80])
    if rows:
        _me = os.getpid()
        return [(pid, cl) for pid, cl in rows if pid != _me]
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
    # 🔴 [ⓐ 2026-08-03 승인] **서버를 일부러 끄는 모드**(추가만 · 기존 동작 무변경).
    #   배경: 이 도구는 원래 "서버를 실수로 죽이지 않기" 위해 만들어져 8011 LISTEN PID 를
    #     **무조건 제외**한다. 그래서 **끄는 용도로 그대로 부르면 서버가 안 죽는다.**
    #   ⚠ 대표가 창을 닫아도 안 죽는 이유는 2026-07-30 에 `Start-Process -WindowStyle Hidden`
    #     (detached)로 띄웠기 때문이다 — 창과 프로세스가 분리된다.
    #   🔴 이 모드는 **서버 PID 와 그 자식(리로더)까지** 대상으로 삼는다. 반드시 확인을 받는다.
    ap.add_argument("--server", action="store_true",
                    help="🔴 서버(8011 LISTEN)와 그 자식까지 종료 대상으로 삼는다 — 끄는 용도")
    ap.add_argument("--yes", action="store_true", help="확인 프롬프트 생략(배치에서 이미 확인받은 경우)")
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
    # ── [ⓐ] 서버 종료 모드 ──────────────────────────────────────────────
    if a.server:
        if not sp:
            print("\n🟢 8011 을 LISTEN 하는 프로세스가 없다 — **이미 꺼져 있다.** 아무것도 하지 않는다.")
            return 0
        # 🔴 서버 PID 의 **부모·자식**까지 모은다(Flask 리로더는 부모+자식 2벌 구조다).
        #   오늘(2026-08-03) 7148(부모)+24384(자식)를 2벌로 오판한 사례가 있어 관계를 명시한다.
        fam = set(sp)
        for pid, _c in procs:
            try:
                if _parent_of(pid) in sp:
                    fam.add(pid)
            except Exception:
                pass
        for pid in list(sp):
            try:
                pp = _parent_of(pid)
                if pp and any(pp == q for q, _ in procs):
                    fam.add(pp)
            except Exception:
                pass
        tg = [(p, c) for p, c in procs if p in fam]
        print("\n🔴 종료 대상 %d개 (서버 + 리로더 자식/부모):" % len(tg))
        for pid, cmd in tg:
            role = "LISTEN(서버)" if pid in sp else "리로더 가족"
            print("   %-7d [%s] %s" % (pid, role, (cmd or "")[:90]))
        if not a.apply:
            print("\n⚠ DRY-RUN 이다. 실제로 끄려면 `--server --apply` 를 붙인다.")
            return 0
        if not a.yes:
            try:
                ans = input("\n🔴 위 %d개를 정말 종료합니까? (y/N) " % len(tg)).strip().lower()
            except Exception:
                ans = "n"
            if ans != "y":
                print("취소했다. 아무것도 종료하지 않았다.")
                return 0
        for pid, _c in tg:
            try:
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                               capture_output=True, text=True)
                print("   종료 요청: %d" % pid)
            except Exception as e:
                print("   🔴 실패 %d: %s" % (pid, str(e)[:60]))
        time.sleep(1.5)
        left = server_pids()                     # 🔴 정말 죽었는지 그 순간 다시 확인
        if left:
            print("\n🔴 아직 %d 포트를 LISTEN 하는 PID 가 있다: %s — **종료 실패**" % (SERVER_PORT, sorted(left)))
            return 1
        print("\n🟢 종료 완료 — %d 포트를 LISTEN 하는 프로세스가 없다." % SERVER_PORT)
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
