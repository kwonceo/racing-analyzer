# -*- coding: utf-8 -*-
"""[회귀 테스트 자기검증 (2026-08-01 신설)] — 원칙 17 의 자동 적용.

■ 왜 필요한가
  🔴 **통과만 확인하면 아무것도 안 재는 테스트가 통과한다.**
  2026-08-01 실사고: `run_glob_safety.py` 가 위반을 잡았는데
  **내가 단 주석 한 줄**(`# date-filtered below`)로 통과됐다.
  그리고 한 줄 단위 검사라 `_pat` 선언과 `glob()` 호출을 **변수로 분리하면 빠져나갔다.**

■ 무엇을 하는가
  각 회귀 테스트에 **일부러 위험/오염을 주입**하고 그때 `rc=1` 로 실패하는지 본다.
  주입 → 검증 → **반드시 원복**. 원복 실패 시 즉시 중단하고 크게 알린다.

■ ⚠ 이 테스트는 **통과가 정답이다.** 실패하면 그 회귀 테스트가 아무것도 안 재고 있다는 뜻이다.

사용: python tests/run_selfcheck.py [--json]
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable


def _run(rel, args=()):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    try:
        r = subprocess.run([PY, os.path.join(BASE, rel)] + list(args), cwd=BASE, env=env,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=300)
        return r.returncode
    except Exception:
        return -1


def _run_json(rel):
    """🔴 [2026-08-01] rc 만 보면 **기존 위반이 이미 있을 때 주입 없이도 rc=1** 이라
    자기검증이 공짜로 통과한다(=아무것도 안 잰다). ⇒ **주입한 파일이 목록에 실제로 뜨는지**까지 본다.
    반환: (rc, 위반목록) · 실패 시 (rc, None)."""
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    try:
        r = subprocess.run([PY, os.path.join(BASE, rel), "--json"], cwd=BASE, env=env,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=300)
        try:
            return r.returncode, (json.loads(r.stdout) or {}).get("violations")
        except Exception:
            return r.returncode, None
    except Exception:
        return -1, None


# ── 케이스 ①  run_glob_safety : 날짜 없는 특정 경주 매칭을 주입 ──────────────
def case_glob_safety():
    p = os.path.join(BASE, "tools", "_selfcheck_tmp.py")
    # SELFCHECK-INJECT-BEGIN  ⚠ 아래는 **일부러 위험한** 코드다. glob 안전 검사에서 제외된다.
    src = ('import glob, os\nBASE = "."\n\n'
           'def _bad(rk):\n'
           '    slug = rk.replace(" ", "_")\n'
           '    pat = "*_%s.json" % slug\n'
           '    return sorted(glob.glob(os.path.join(BASE, "data", "odds_history", pat)))\n')
    # SELFCHECK-INJECT-END
    open(p, "w", encoding="utf-8").write(src)
    try:
        rc, viol = _run_json("tests/run_glob_safety.py")
    finally:
        try:
            os.remove(p)
        except Exception:
            return None, "🔴 원복 실패 — tools/_selfcheck_tmp.py 를 직접 지울 것"
    hit = [v for v in (viol or []) if v.get("file") == "tools/_selfcheck_tmp.py"]
    return (rc == 1 and bool(hit)), "rc=%s · 주입파일 적발 %d건 (기대 rc=1·1건+)" % (rc, len(hit))


# ── 케이스 ⑤  run_glob_safety : 🔴 **os.listdir** 로 날짜 없이 특정 경주 지목 ──
#   ⚠ 실사고 재현 — `_snapshot_index()` 가 `glob` 이 아니라 `os.listdir` 을 써서
#     검사 대상 밖이었고, 7/31 카드에 7/14 스냅샷이 떴다.
def case_glob_safety_listdir():
    p = os.path.join(BASE, "tools", "_selfcheck_tmp_listdir.py")
    # SELFCHECK-INJECT-BEGIN  ⚠ 아래는 **일부러 위험한** 코드다. glob 안전 검사에서 제외된다.
    src = ('import json, os\nSNAP = "."\n\n'
           'def _bad_index():\n'
           '    m = {}\n'
           '    for fn in os.listdir(SNAP):\n'
           '        if not fn.endswith(".png"):\n'
           '            continue\n'
           '        meta = json.load(open(os.path.join(SNAP, fn[:-4] + ".json")))\n'
           '        rk = (meta.get("raceKey") or "").strip()\n'
           '        m.setdefault(rk, fn)\n'
           '    return m\n')
    # SELFCHECK-INJECT-END
    open(p, "w", encoding="utf-8").write(src)
    try:
        rc, viol = _run_json("tests/run_glob_safety.py")
    finally:
        try:
            os.remove(p)
        except Exception:
            return None, "🔴 원복 실패 — tools/_selfcheck_tmp_listdir.py 를 직접 지울 것"
    hit = [v for v in (viol or []) if v.get("file") == "tools/_selfcheck_tmp_listdir.py"]
    return (rc == 1 and bool(hit)), "rc=%s · 주입파일 적발 %d건 (기대 rc=1·1건+)" % (rc, len(hit))


# ── 케이스 ⑥  run_glob_safety : 🔴 **무해한** 전수 스캔은 잡지 **않아야** 한다 ──
#   ⚠ 원칙 17 의 뒷면 — "다 잡는 테스트"는 영원한 빨간불이라 결국 무시된다.
#     `os.listdir` 52곳을 전부 위반으로 세면 진짜 5곳이 묻힌다.
def case_glob_safety_noise():
    p = os.path.join(BASE, "tools", "_selfcheck_tmp_safe.py")
    # SELFCHECK-INJECT-BEGIN  ⚠ 아래는 **무해한** 코드다(전수 스캔). 잡히면 안 된다.
    src = ('import json, os\nD = "."\n\n'
           'def _ok_list():\n'
           '    keys = set()\n'
           '    for fn in os.listdir(D):\n'
           '        if not fn.endswith(".json"):\n'
           '            continue\n'
           '        doc = json.load(open(os.path.join(D, fn)))\n'
           '        if doc.get("raceKey"):\n'
           '            keys.add(doc["raceKey"])\n'
           '    return sorted(keys)\n')
    # SELFCHECK-INJECT-END
    open(p, "w", encoding="utf-8").write(src)
    try:
        rc, viol = _run_json("tests/run_glob_safety.py")
    finally:
        try:
            os.remove(p)
        except Exception:
            return None, "🔴 원복 실패 — tools/_selfcheck_tmp_safe.py 를 직접 지울 것"
    if viol is None:
        return None, "--json 파싱 실패"
    hit = [v for v in viol if v.get("file") == "tools/_selfcheck_tmp_safe.py"]
    return (not hit), "무해코드 오탐 %d건 (기대 0건)" % len(hit)


# ── 케이스 ②  run_smoke_render : 프롬프트에 `%` 포맷 충돌을 주입 ──────────────
def case_smoke_render():
    p = os.path.join(BASE, "gemini_forecast.py")
    bak = p + ".selfcheck.bak"
    src = open(p, encoding="utf-8").read()
    anchor = "당신은 경륜/경마 전개 분석가입니다."
    if anchor not in src:
        return None, "앵커 문자열 없음 — 케이스 갱신 필요"
    shutil.copy2(p, bak)
    try:
        # 🔴 실사고 재현: `%` 포맷 문자열 안의 `100%)` → ValueError
        open(p, "w", encoding="utf-8").write(
            src.replace(anchor, anchor + " (폐기율 100%)", 1))
        rc = _run("tests/run_smoke_render.py")
    finally:
        try:
            shutil.copy2(bak, p)
            os.remove(bak)
        except Exception:
            return None, "🔴 원복 실패 — %s 를 %s 로 복원할 것" % (bak, p)
    return rc == 1, "주입 시 rc=%s (기대 1)" % rc


# ── 케이스 ③  run_freeze_behavior : 복원 함수를 무력화 ────────────────────
def case_freeze_behavior():
    p = os.path.join(BASE, "app.py")
    bak = p + ".selfcheck.bak"
    src = open(p, encoding="utf-8").read()
    anchor = "def _frozen_capture(rk, doc, rec_rows):"
    if anchor not in src:
        return None, "앵커 없음 — 케이스 갱신 필요"
    shutil.copy2(p, bak)
    try:
        # 복원이 항상 실패하게 만든다 → 행위 테스트가 잡아야 한다
        open(p, "w", encoding="utf-8").write(
            src.replace(anchor, anchor + '\n    return None, "selfcheck"', 1))
        rc = _run("tests/run_freeze_behavior.py")
    finally:
        try:
            shutil.copy2(bak, p)
            os.remove(bak)
        except Exception:
            return None, "🔴 원복 실패 — %s 를 %s 로 복원할 것" % (bak, p)
    return rc == 1, "주입 시 rc=%s (기대 1)" % rc


# ── 케이스 ④  run_precommit : 문법 오류를 주입 ────────────────────────────
def case_precommit():
    p = os.path.join(BASE, "tools", "_selfcheck_syntax.py")
    open(p, "w", encoding="utf-8").write("def broken(:\n    pass\n")
    try:
        rc = _run("tests/run_precommit.py")
    finally:
        try:
            os.remove(p)
        except Exception:
            return None, "🔴 원복 실패 — tools/_selfcheck_syntax.py 를 직접 지울 것"
    return rc == 1, "주입 시 rc=%s (기대 1)" % rc


def case_hook_crash():
    """[2026-08-01 신설] **훅 자체가 죽는 상황**을 주입한다.

    🔴 왜: 오늘 커밋 훅이 cp949 콘솔에서 `UnicodeEncodeError` 로 죽어 rc=1 이 됐다.
      **테스트는 전부 통과했는데 커밋이 막혔다.** 화면에는 "커밋 차단"만 떠서
      내용을 안 보면 "위반이 있나 보다" 하고 `--no-verify` 로 넘기게 된다.
      ⇒ 훅은 **"정당한 차단"과 "게이트 고장"을 갈라서 출력**해야 한다. 그것을 여기서 검증한다.

    통과 조건 3가지 — 셋 다 만족해야 한다:
      ① 커밋을 **막는다**(고장 났다고 통과시키면 게이트가 없는 것과 같다)
      ② 출력에 **"게이트 오류"** 가 있다
      ③ **"커밋 차단(정당)"** 으로 오분류하지 않는다
    """
    hook = os.path.join(BASE, ".git", "hooks", "pre-commit")
    if not os.path.exists(hook):
        return None, "⏭ .git/hooks/pre-commit 미설치 — scripts/install_hooks.bat 실행 필요"
    p = os.path.join(BASE, "tests", "run_precommit.py")
    bak = p + ".bak_selfcheck"
    shutil.copy2(p, bak)
    try:
        s = open(p, encoding="utf-8").read()
        s = s.replace("def main():",
                      'def main():\n    raise RuntimeError("SELFCHECK: 게이트 고장 주입")', 1)
        open(p, "w", encoding="utf-8").write(s)
        r = subprocess.run(["sh", hook], cwd=BASE, capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        out = (r.stdout or "") + (r.stderr or "")
    finally:
        try:
            shutil.move(bak, p)
        except Exception:
            return None, "🔴 원복 실패 — %s 를 %s 로 되돌릴 것" % (bak, p)
    ok = (r.returncode != 0) and ("게이트 오류" in out) and ("커밋 차단(정당)" not in out)
    return ok, "rc=%s · 고장분류=%s · 정당오분류=%s" % (
        r.returncode, "게이트 오류" in out, "커밋 차단(정당)" in out)


CASES = [
    ("pre-commit(고장구분)", "게이트 스크립트 자체 예외", case_hook_crash),
    ("run_glob_safety", "날짜 없는 특정 경주 매칭", case_glob_safety),
    ("run_glob_safety(listdir)", "os.listdir 날짜 없는 지목", case_glob_safety_listdir),
    ("run_glob_safety(오탐)", "무해한 전수 스캔(잡히면 실패)", case_glob_safety_noise),
    ("run_smoke_render", "% 포맷 충돌(실사고 재현)", case_smoke_render),
    ("run_freeze_behavior", "복원 함수 무력화", case_freeze_behavior),
    ("run_precommit", "문법 오류 주입", case_precommit),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    res = []
    for name, what, fn in CASES:
        try:
            ok, detail = fn()
        except Exception as e:
            ok, detail = False, "예외: %s" % str(e)[:90]
        res.append({"test": name, "injected": what, "ok": ok, "detail": detail})
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0 if all(x["ok"] for x in res) else 1
    print("=" * 82)
    print("회귀 테스트 자기검증 (원칙 17)  ⚠ 통과가 정답이다")
    print("=" * 82)
    print("각 테스트에 위험을 **일부러 주입**해 실패(rc=1)하는지 본다.")
    print("실패하면 그 테스트는 **아무것도 안 재고 있다**는 뜻이다.\n")
    for r in res:
        m = "✅" if r["ok"] else ("⏭" if r["ok"] is None else "❌")
        print("  %s %-26s %-28s %s" % (m, r["test"], r["injected"], r["detail"]))
    bad = [x for x in res if x["ok"] is False]
    skip = [x for x in res if x["ok"] is None]
    print("\n" + "=" * 82)
    print("통과 %d / 실패 %d / 건너뜀 %d" % (len(res) - len(bad) - len(skip), len(bad), len(skip)))
    if bad:
        print("🔴 위 테스트는 위험을 주입해도 통과했다 — 재설계 필요")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
