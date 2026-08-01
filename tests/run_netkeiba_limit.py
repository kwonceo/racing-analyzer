# -*- coding: utf-8 -*-
"""[netkeiba 요청 제한 회귀 테스트 (2026-08-02 신설)] — 원칙 17 적용.

🔴 **제한을 넣었다는 것과 제한이 실제로 거는 것은 다르다.**
  2026-08-02 에 소급 수집이 4.4 req/s 로 나가 **IP 가 차단**됐다. 같은 일이 또 나면
  IP 를 계속 태우게 된다. ⇒ **막히는지 매번 확인한다.**

⚠ **네트워크를 쓰지 않는다.** 상태 파일도 임시 경로로 갈아끼워 **운영 카운터를 건드리지 않는다.**

사용: python tests/run_netkeiba_limit.py
"""
import importlib
import io
import os
import sys
import tempfile
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass


def _fresh(tmpdir):
    """운영 상태 파일과 **완전히 분리된** 새 인스턴스."""
    import netkeiba_guard as g
    importlib.reload(g)
    g.STATE = os.path.join(tmpdir, "_q.json")
    g._MEM = None
    return g


def main():
    fails = []
    tmp = tempfile.mkdtemp(prefix="nkguard_")

    # ① 최소 간격 — 연속 두 번째가 막혀야 한다
    g = _fresh(tmp)
    ok1, _ = g.allow("live")
    g.record(True)
    ok2, why2 = g.allow("live")
    r1 = ok1 and (not ok2) and "간격" in why2
    print("  %s ① 최소 간격      첫 요청 %s · 즉시 두 번째 %s (%s)"
          % ("✅" if r1 else "🔴", ok1, ok2, why2))
    if not r1:
        fails.append("① 최소 간격이 안 걸린다")

    # ② 소급(backfill)이 실시간보다 **느려야** 한다
    r2 = g.MIN_INTERVAL["backfill"] > g.MIN_INTERVAL["live"]
    print("  %s ② 소급이 더 느림   live %.1f초 < backfill %.1f초"
          % ("✅" if r2 else "🔴", g.MIN_INTERVAL["live"], g.MIN_INTERVAL["backfill"]))
    if not r2:
        fails.append("② 소급 간격이 실시간보다 느리지 않다")

    # ③ 🔴 상한을 1 로 낮추면 **두 번째 요청이 막혀야 한다**(대표 지시한 자기검증)
    g = _fresh(tempfile.mkdtemp(prefix="nkguard_"))
    g.MIN_INTERVAL = {"live": 0.0, "backfill": 0.0}     # 간격 요인을 제거해 상한만 본다
    g.LIMIT_MIN = 1
    ok1, _ = g.allow("live")
    g.record(True)
    ok2, why2 = g.allow("live")
    r3 = ok1 and (not ok2) and "분당" in why2
    print("  %s ③ 분당 상한(=1)    첫 %s · 두 번째 %s (%s)"
          % ("✅" if r3 else "🔴", ok1, ok2, why2))
    if not r3:
        fails.append("③ 분당 상한이 안 걸린다 — 제한이 아무것도 안 재고 있다")

    # ④ 400 이 연속 3회면 **그날 중단** + 이후 전부 거부
    g = _fresh(tempfile.mkdtemp(prefix="nkguard_"))
    g.MIN_INTERVAL = {"live": 0.0, "backfill": 0.0}
    for _ in range(g.FAIL_STREAK_STOP):
        g.record(False, 400)
    okA, whyA = g.allow("live")
    r4 = (not okA) and "중단" in whyA
    print("  %s ④ 400 연속 중단    %d회 후 allow=%s (%s)"
          % ("✅" if r4 else "🔴", g.FAIL_STREAK_STOP, okA, whyA))
    if not r4:
        fails.append("④ 400 연속 3회 중단이 안 걸린다")

    # ⑤ 🔴 중단은 **사람이 켤 때만** 풀린다(자동 해제가 없어야 한다)
    okB, _ = g.allow("live")
    g.reset_stop("test")
    okC, _ = g.allow("live")
    r5 = (not okB) and okC
    print("  %s ⑤ 자동 해제 없음   중단 유지 %s · reset_stop 후 %s"
          % ("✅" if r5 else "🔴", not okB, okC))
    if not r5:
        fails.append("⑤ 중단이 자동으로 풀린다")

    # ⑥ 정상 운영 속도(하루 1,080요청 · 피크 분당 6)가 상한에 **걸리지 않아야** 한다
    g2 = _fresh(tempfile.mkdtemp(prefix="nkguard_"))
    r6 = (g2.LIMIT_MIN > 6) and (g2.LIMIT_DAY > 1080)
    print("  %s ⑥ 정상운영 여유    분당 %d(>6) · 일일 %d(>1080)"
          % ("✅" if r6 else "🔴", g2.LIMIT_MIN, g2.LIMIT_DAY))
    if not r6:
        fails.append("⑥ 상한이 정상 운영을 막는다")

    # ⑦ app.py 가 실제로 이 관문을 쓰는지 (배선이 빠지면 위 전부가 무의미하다)
    src = open(os.path.join(BASE, "app.py"), encoding="utf-8").read()
    r7 = ("netkeiba_guard.allow" in src) and ("netkeiba_guard.record" in src)
    print("  %s ⑦ app.py 배선     allow=%s · record=%s"
          % ("✅" if r7 else "🔴", "netkeiba_guard.allow" in src, "netkeiba_guard.record" in src))
    if not r7:
        fails.append("⑦ app.py 가 요청 제한을 부르지 않는다")

    print()
    if fails:
        print("🔴 실패 %d건" % len(fails))
        for f in fails:
            print("   -", f)
        return 1
    print("🟢 통과 7 / 실패 0 — 제한이 실제로 건다")
    return 0


if __name__ == "__main__":
    sys.exit(main())
