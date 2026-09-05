# -*- coding: utf-8 -*-
"""운영 폴더에 커밋을 반영한다 (git pull) — 🔴 경주 중이면 스스로 거부한다.

왜 스크립트인가: 리로더가 켜져 있어 **pull = 즉시 재기동**이다.
  경주 수집 창(발주 10분전~2분후)에 재기동하면 그 경주의 배당 틱이 끊긴다.
  자정에 손으로 판단하게 하면 실수한다 → 도구가 판단한다.

사용:  python tools/apply_update.py            # 안전하면 pull, 아니면 거부
       python tools/apply_update.py --force    # 판단을 무시하고 강행(권장하지 않음)
"""
import io, json, os, subprocess, sys, time

TARGET = r"C:\Users\Administrator\Desktop\경마분석서버"
WIN_BEFORE, WIN_AFTER, SETTLE = 600, 120, 2400   # 발주 10분전 ~ 2분후 · 마지막+40분


def sched(root):
    p = os.path.join(root, "data", "today_schedule.json")
    if not os.path.exists(p):
        return None
    try:
        d = json.load(io.open(p, encoding="utf-8"))
    except Exception:
        return None
    eps = []
    for t in (d.get("tracks") or []):
        for r in (t.get("races") or []):
            pe = r.get("postEpoch") or r.get("post_epoch")
            if pe:
                eps.append(float(pe))
    return sorted(eps) or None


def hhmm(e):
    return time.strftime("%H:%M", time.localtime(e))


def main():
    force = "--force" in sys.argv
    root = TARGET
    for a in sys.argv[1:]:
        if not a.startswith("--"):
            root = a
    print("대상 폴더: %s" % root)
    if not os.path.isdir(os.path.join(root, ".git")):
        print("🔴 git 저장소가 아니다. 경로를 확인할 것."); return 2

    eps = sched(root)
    now = time.time()
    if eps:
        inwin = [e for e in eps if -WIN_AFTER <= (e - now) <= WIN_BEFORE]
        rem = [e for e in eps if e > now]
        print("  마지막 발주 %s · 남은 경주 %d" % (hhmm(eps[-1]), len(rem)))
        if inwin:
            print("🔴 지금 수집 창에 %d경주가 있다 (다음 발주 %s)." % (len(inwin), hhmm(inwin[0])))
            print("   pull 하면 서버가 재기동돼 그 경주의 배당 틱이 끊긴다.")
            print("   🟢 안전 시각: %s 이후 (마지막 발주 + 40분)" % hhmm(eps[-1] + SETTLE))
            if not force:
                print("\n   지금은 하지 않는다. 안전 시각 뒤에 다시 실행할 것.")
                return 1
            print("   ⚠ --force 로 강행한다.")
        elif rem:
            gap = rem[0] - now
            print("🟡 지금은 창 밖이지만 다음 발주까지 %d분뿐이다." % int(gap / 60))
            if gap < WIN_BEFORE + 60 and not force:
                print("   여유가 적다. 안전 시각 %s 이후를 권한다." % hhmm(eps[-1] + SETTLE))
                return 1
        else:
            print("🟢 오늘 경주 종료.")
    else:
        print("  ⚠ 스케줄을 못 읽었다 — 경주 여부를 판단할 수 없다.")
        if not force:
            print("   안전을 위해 중단한다. 확실하면 --force."); return 1

    print("\n=== git pull ===")
    r = subprocess.run(["git", "-c", "safe.directory=*", "-C", root, "pull", "--no-rebase", "origin", "master"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    print((r.stdout or "").strip()[-1200:])
    if r.returncode != 0:
        print("🔴 pull 실패:\n" + (r.stderr or "").strip()[-800:]); return 3

    print("\n=== 서버 생존 확인 (재기동 대기 8초) ===")
    time.sleep(8)
    ok = False
    for i in range(6):
        try:
            import urllib.request
            with urllib.request.urlopen("http://127.0.0.1:8011/api/multi/schedule", timeout=5) as f:
                if f.status == 200:
                    ok = True; break
        except Exception:
            time.sleep(4)
    print("  HTTP 200 🟢" if ok else "  🔴 응답 없음 — 서버 로그를 볼 것 (logs/server_stdout.log.err)")

    print("\n=== CLAUDE.md 크기 ===")
    subprocess.run([sys.executable, os.path.join(root, "tools", "archive_claudemd.py"), "--check"])
    print("\n=== 훅 설치 (이 PC 1회) ===")
    subprocess.run([sys.executable, os.path.join(root, "tools", "install_claude_hook.py")])
    return 0 if ok else 4


if __name__ == "__main__":
    sys.exit(main())
