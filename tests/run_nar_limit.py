# -*- coding: utf-8 -*-
"""[keiba.go.jp 요청 제한 자기검증 (2026-08-03 신설)] — 네트워크 미사용.

🔴 원칙 17: 테스트를 만들면 **통과만이 아니라 실패도 확인한다.**
  그래서 각 케이스는 "막아야 할 때 실제로 막히는가"를 본다. 안 막히면 rc=1.

⚠ 운영 카운터(`data/_nar_quota.json`)를 건드리지 않는다 — 임시 경로로 갈아끼운다.
  실행 후 운영 파일이 생성/변경되지 않았음을 마지막에 확인한다.
"""
import importlib.util
import os
import sys
import tempfile
import time

sys.dont_write_bytecode = True
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8")          # cp949 콘솔에서 거짓 실패 방지
except Exception:
    pass

FAIL = []


def _ok(name, cond, note=""):
    print(("   ✅ " if cond else "   ❌ ") + name + (("  — " + note) if note else ""))
    if not cond:
        FAIL.append(name)


def _fresh(tmpdir, **over):
    """모듈을 **소스에서 새로 로드**한다(캐시 우회 — 2026-08-02 __pycache__ 사고 대응)."""
    path = os.path.join(BASE, "nar_guard.py")
    spec = importlib.util.spec_from_file_location("nar_guard_t%d" % time.time_ns(), path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.STATE = os.path.join(tmpdir, "quota_%d.json" % time.time_ns())
    m._MEM = None
    for k, v in over.items():
        setattr(m, k, v)
    return m


def main():
    print("=" * 74)
    print("keiba.go.jp 요청 제한(nar_guard) 자기검증 — 네트워크 미사용")
    print("=" * 74)
    prod = os.path.join(BASE, "data", "_nar_quota.json")
    prod_before = (os.path.getmtime(prod) if os.path.exists(prod) else None)

    with tempfile.TemporaryDirectory() as td:
        print("\n[1] 최소 간격 — 연속 요청은 막혀야 한다")
        g = _fresh(td)
        g.record(ok=True)
        ok, why = g.allow("live")
        _ok("간격 미달이 차단된다", (not ok) and ("간격" in why), why)

        print("\n[2] 선수집은 실시간보다 느려야 한다")
        g = _fresh(td)
        _ok("preload 간격 >= live 간격",
            g.MIN_INTERVAL["preload"] >= g.MIN_INTERVAL["live"],
            "live=%.1f preload=%.1f" % (g.MIN_INTERVAL["live"], g.MIN_INTERVAL["preload"]))

        print("\n[3] 🔴 분당 상한을 1 로 낮추면 두 번째가 막혀야 한다")
        g = _fresh(td, LIMIT_MIN=1, MIN_INTERVAL={"live": 0.0, "preload": 0.0})
        ok1, _ = g.allow("live")
        g.record(ok=True)
        ok2, why2 = g.allow("live")
        _ok("첫 요청 통과", ok1)
        _ok("두 번째가 분당 상한으로 막힌다", (not ok2) and ("분당" in why2), why2)

        print("\n[4] 차단코드 연속 3회 → 그날 중단")
        g = _fresh(td, MIN_INTERVAL={"live": 0.0, "preload": 0.0})
        for _ in range(3):
            g.record(ok=False, code=403)
        ok, why = g.allow("live")
        _ok("연속 차단 후 중단된다", (not ok) and ("중단" in why), why)

        print("\n[5] 중단은 reset_stop 전까지 자동 해제되지 않는다")
        ok, _ = g.allow("live")
        _ok("여전히 막혀 있다", not ok)
        g.reset_stop("selfcheck")
        ok, why = g.allow("live")
        _ok("사람이 풀면 재개된다", ok, why)

        print("\n[6] 정상 운영량(선수집 70요청)은 상한에 안 걸린다")
        g = _fresh(td, MIN_INTERVAL={"live": 0.0, "preload": 0.0})
        blocked = 0
        for i in range(70):
            ok, _ = g.allow("preload")
            if not ok:
                blocked += 1
            else:
                g.record(ok=True)
            if (i + 1) % 25 == 0:                     # 분이 바뀐 상황을 흉내
                g._MEM["minute"] = "reset"
        _ok("70요청이 통과한다", blocked == 0, "막힌 요청 %d건" % blocked)

        print("\n[7] 🔴 wait_allow 는 간격이면 기다리고, 상한이면 즉시 포기한다")
        g = _fresh(td, MIN_INTERVAL={"live": 0.3, "preload": 0.3})
        g.record(ok=True)
        t0 = time.time()
        ok, why = g.wait_allow("live", max_wait=3.0)
        _ok("간격은 기다렸다가 통과한다", ok and (time.time() - t0) >= 0.2,
            "%.2f초 대기" % (time.time() - t0))
        g = _fresh(td, LIMIT_DAY=0, MIN_INTERVAL={"live": 0.0, "preload": 0.0})
        t0 = time.time()
        ok, why = g.wait_allow("live", max_wait=3.0)
        _ok("상한은 기다리지 않고 즉시 False", (not ok) and (time.time() - t0) < 1.0, why)

        print("\n[8] 🔴 app.py 가 실제로 이 관문을 부르는가 (배선이 빠지면 1~7이 전부 무의미하다)")
        src = open(os.path.join(BASE, "app.py"), encoding="utf-8").read()
        _ok("import nar_guard 가 있다", "import nar_guard" in src)
        _ok("폴백이 wait_allow 를 부른다", "nar_guard.wait_allow" in src)
        _ok("선수집이 record 를 부른다", "nar_guard.record" in src)
        _ok("_nar_form_fallback 이 배선돼 있다", "_nar_form_fallback(key" in src)
        _ok("선수집 스케줄러가 기동에 등록돼 있다", "_start_nar_preload_scheduler()" in src)

    print("\n[9] 운영 카운터 무변경 확인")
    prod_after = (os.path.getmtime(prod) if os.path.exists(prod) else None)
    _ok("data/_nar_quota.json 을 건드리지 않았다", prod_before == prod_after,
        "before=%s after=%s" % (prod_before, prod_after))

    print("\n" + "=" * 74)
    if FAIL:
        print("🔴 실패 %d건: %s" % (len(FAIL), " · ".join(FAIL)))
        return 1
    print("✅ 전부 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
