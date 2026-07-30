# -*- coding: utf-8 -*-
"""[커밋 전 검증 파이프라인 (2026-07-30 신설)] — 단계적 차단.

■ 이게 무엇인가
  확인하는 것: **커밋해도 되는 상태인가.**
  왜 필요한가: 2026-07-30 에 편집 중 `except` 누락으로 서버가 내려간 적이 있고,
    문법 검증을 안 돌린 채 커밋한 이력이 있다.
  쉽게 말하면: 문을 나서기 전 가스·불·문단속을 한 번에 확인하는 체크리스트.

■ 두 등급
  🔴 **차단(blocking)** — 실패하면 `exit 1`. 커밋하면 안 되는 상태다.
     문법 검증 + 기존 테스트(run_report·run_formula·run_flow).
  🟡 **예견된 실패(expected_fail)** — 실패해도 커밋을 막지 않는다.
     아직 고치지 않은 결함을 **재현**하는 테스트라 지금은 실패가 정답이다.
     ⚠ 반대로 **통과하면 경고**를 낸다 — 기대값이 느슨하다는 뜻이기 때문이다.
     동결 구현 후 `EXPECTED_FAIL_SUITES` 에서 빼면 그때부터 차단으로 승격된다.

사용: python tests/run_precommit.py [--json]
"""
import argparse
import ast
import json
import os
import subprocess
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SYNTAX_TARGETS = ["app.py", "gemini_reviewer.py", "review_engine.py",
                  "tools/health_check.py", "tools/dedupe_odds_snapshots.py",
                  "tools/build_amedas_map.py",
                  "tests/run_freeze_regression.py", "tests/run_freeze_behavior.py"]

# 🔴 [2026-07-31] `run_freeze_behavior.py` 를 차단 등급으로 승격.
#    마감 시점 동결이 구현됐으므로 이제부터 복원 경로가 끊기면 커밋을 막는다.
BLOCKING_SUITES = ["tests/run_report.py", "tests/run_formula.py", "tests/run_flow.py",
                   "tests/run_freeze_behavior.py"]

# 🟡 아직 고치지 않은 결함을 재현하는 테스트 — 실패해도 커밋을 막지 않는다.
#
# 🔴 [2026-07-31 발견] `run_freeze_regression.py` 는 **여기서 뺄 수 없다.**
#    그 파일은 **고정 Fixture 만 읽는다** — Fixture 는 *이미 오염된 과거를 찍어둔 사진*이라
#    코드를 고쳐도 **영원히 초록이 되지 않는다.** '역사 기록'이지 '동작 검증'이 아니다.
#    ⇒ 그 파일은 외부 앵커(회원이 받은 카톡 원문)로서 **그대로 보존**하고,
#      동결이 실제로 되는지는 `run_freeze_behavior.py`(위 차단 목록)가 잰다.
EXPECTED_FAIL_SUITES = ["tests/run_freeze_regression.py"]


def _syntax():
    out = []
    for rel in SYNTAX_TARGETS:
        p = os.path.join(BASE, rel)
        if not os.path.exists(p):
            out.append({"target": rel, "ok": None, "msg": "파일 없음(건너뜀)"})
            continue
        try:
            ast.parse(open(p, encoding="utf-8").read())
            out.append({"target": rel, "ok": True, "msg": "문법 정상"})
        except Exception as e:
            out.append({"target": rel, "ok": False, "msg": "%s: %s" % (type(e).__name__, e)})
    return out


def _run(rel):
    p = os.path.join(BASE, rel)
    if not os.path.exists(p):
        return {"suite": rel, "ok": None, "code": None, "msg": "파일 없음(건너뜀)"}
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    try:
        # ⚠ Windows 기본 로케일(cp949)로 디코드하면 한글 출력에서 UnicodeDecodeError 가 난다.
        #   하위 프로세스가 UTF-8 로 찍으므로 여기서도 UTF-8 을 명시한다.
        r = subprocess.run([sys.executable, p], cwd=BASE, env=env,
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=300)
        tail = (r.stdout or "").strip().split("\n")[-1][:120]
        return {"suite": rel, "ok": (r.returncode == 0), "code": r.returncode,
                "msg": tail or (r.stderr or "").strip().split("\n")[-1][:120],
                "stdout": r.stdout}
    except Exception as e:
        return {"suite": rel, "ok": False, "code": -1, "msg": "%s: %s" % (type(e).__name__, e)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    syn = _syntax()
    blocking = [_run(s) for s in BLOCKING_SUITES]
    expected = []
    for s in EXPECTED_FAIL_SUITES:
        r = _run(s)
        # 예견된 실패 스위트는 내부에서 exit 0 을 내므로, 개별 케이스 통과 수로 판정한다.
        loose = False
        if "run_freeze_regression" in s and r.get("stdout"):
            loose = "예상 밖 통과" in r["stdout"]
        r["looseExpectation"] = loose
        expected.append(r)

    syn_bad = [x for x in syn if x["ok"] is False]
    blk_bad = [x for x in blocking if x["ok"] is False]
    loose = [x for x in expected if x.get("looseExpectation")]
    blocked = bool(syn_bad or blk_bad)

    if a.json:
        print(json.dumps({"syntax": syn, "blocking": blocking, "expectedFail": expected,
                          "blocked": blocked}, ensure_ascii=False, indent=1))
        return 1 if blocked else 0

    print("=" * 74)
    print("커밋 전 검증 파이프라인")
    print("=" * 74)
    print("\n[1] 문법 검증  🔴 차단")
    for x in syn:
        m = "✅" if x["ok"] else ("⏭" if x["ok"] is None else "❌")
        print("   %s %-38s %s" % (m, x["target"], x["msg"]))
    print("\n[2] 기존 테스트  🔴 차단")
    for x in blocking:
        m = "✅" if x["ok"] else ("⏭" if x["ok"] is None else "❌")
        print("   %s %-38s %s" % (m, x["suite"], x["msg"]))
    print("\n[3] 예견된 실패  🟡 차단 안 함 (아직 안 고친 결함을 재현하는 테스트)")
    for x in expected:
        if x.get("looseExpectation"):
            print("   🔴 %-38s 예상 밖 통과 — 기대값이 느슨하다. 재설계 필요" % x["suite"])
        else:
            print("   🟡 %-38s 예견대로 실패(정상)" % x["suite"])
    print("\n" + "=" * 74)
    if blocked:
        print("🔴 커밋 차단 — 문법 %d건 · 테스트 %d건 실패" % (len(syn_bad), len(blk_bad)))
    else:
        print("✅ 커밋 가능")
    if loose:
        print("⚠ 경고: 예견된 실패 스위트가 통과했다 → 기대값 재설계 필요")
    print("=" * 74)
    return 1 if blocked else 0


if __name__ == "__main__":
    sys.exit(main())
