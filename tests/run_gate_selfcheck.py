# -*- coding: utf-8 -*-
"""[배당 오염 안전장치 — **행위** 회귀 테스트 (2026-08-05 신설)] — 원칙 17

■ 무엇을 재는가 (코드 용어 없이)
  ① 계수기가 **파일에 실제로 남는가**       — 2026-08-04 에 977번 조용히 실패했다
  ② 발동 기록이 **덮어쓰기 없는 로그**에 남는가
  ③ 정상 배당을 **막지 않는가**             — 🔴 정상을 막는 쪽이 오염보다 나쁘다
  ④ 오염 배당을 **막는가**
  ⑤ 한국은 **막지 않고 경고만** 하는가       — 2026-08-05 승인 완화안
  ⑥ 명단이 얇으면 **판정하지 않는가**        — 추측 금지
  ⑦ 중간점검이 이상을 **이상이라 말하는가**

■ 🔴 왜 함수를 그대로 떼어 쓰는가 — **로직을 베끼면 아무것도 못 잡는다**
  `run_roster_gate.py` 는 게이트 규칙을 테스트 안에 **복사**해 뒀다. 그러면 app.py 가
  바뀌어도 테스트는 옛 규칙을 계속 통과시킨다. 여기서는 app.py 소스를 **잘라 실행**한다.

■ 🔴🔴 왜 import 문까지 app.py 에서 가져오는가 — **`io` 미import 사고를 재발 차단한다**
  2026-08-04: `_gate_hit` 이 `io.open` 을 쓰는데 app.py 에 `import io` 가 없었다.
  `except Exception` 이 NameError 를 삼켜 **계수기가 죽은 줄도 모르고 977번 돌았다.**
  ⇒ 테스트 namespace 에 io 를 넣어 주면 **그 사고를 영원히 못 잡는다.**
     그래서 app.py 의 최상단 import 문을 그대로 실행해 **app.py 가 가진 것만** 쓴다.
  ⇒ 검사는 예외를 보지 않고 **결과(파일이 생겼는가)** 로 한다. 삼켜져도 잡힌다.

⚠ **네트워크·실전 데이터를 쓰지 않는다.** DATA_DIR·LOGS_DIR 을 임시 디렉터리로 갈아끼운다.
⚠ 이 테스트는 **통과가 정답**이다. 실패하면 오염이 회원 화면에 나가거나 정상이 막힌다.

사용: python tests/run_gate_selfcheck.py [--json]
     GATE_APP_SRC=<사본경로>  ← 자기검증(주입)이 사본을 넘길 때. 🔴 라이브 app.py 를 고치지 않는다.
"""
import io
import json
import os
import re
import shutil
import sys
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

# 잘라 쓸 구간 — 앵커 문자열로 잡는다(줄 번호는 수정할 때마다 밀린다).
_GATE_FROM = "# ══════════ [3층 · 배당 오염 표시 차단"
_GATE_TO = "def _oddspark_mapping_suspect("
_MID_FROM = "\nMIDCHECK_ENABLED = True"   # ⚠ 주석 줄에 먼저 걸리지 않게 개행부터 잡는다
_MID_TO = "def _midcheck_run("


def _slice(src, a, b, what):
    i = src.find(a)
    j = src.find(b)
    if i < 0 or j < 0 or j <= i:
        raise RuntimeError("app.py 에서 %s 구간을 못 찾았다 (앵커가 바뀌었나)" % what)
    return src[i:j]


def load_ns(tmp):
    """app.py 의 게이트·중간점검 구간을 임시 디렉터리 위에서 실행한다."""
    path = os.environ.get("GATE_APP_SRC") or os.path.join(BASE, "app.py")
    src = io.open(path, encoding="utf-8").read()

    # ① 🔴 app.py 자신의 import 문만 실행한다 — 없는 모듈은 없는 채로 둔다(io 사고 검출).
    imports = []
    for line in src.split("\n")[:200]:
        if re.match(r"^(import |from )\w", line) and "app" not in line.split()[1]:
            imports.append(line)
    ns = {"__name__": "gate_test", "__file__": os.path.join(BASE, "app.py")}
    for line in imports:
        try:
            exec(compile(line, "<imports>", "exec"), ns)
        except Exception:
            pass                      # 선택 의존(anthropic 등)은 없어도 된다

    # ② 실전 경로를 절대 안 보게 한다.
    ns["DATA_DIR"] = os.path.join(tmp, "data")
    ns["LOGS_DIR"] = os.path.join(tmp, "logs")
    os.makedirs(ns["DATA_DIR"], exist_ok=True)
    os.makedirs(ns["LOGS_DIR"], exist_ok=True)

    def _atomic(p, obj, **kw):
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        with io.open(p, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
    ns["_json_atomic"] = _atomic
    ns["_json_load_guard"] = lambda p, d, tag=None: (d, False)
    # 🔴 1층은 `_starters_load()` 로 출마표를 읽는다. **실전 파일을 읽지 않도록** 여기서 갈아끼운다.
    #   테스트가 `ns["_STARTERS"]` 에 넣은 것만 명단이 된다.
    ns["_STARTERS"] = {}
    ns["_starters_load"] = lambda: ns["_STARTERS"]

    # ③ 종목·경마장 판정 정규식은 원본 그대로 가져온다(테스트가 다시 쓰면 의미가 없다).
    # ⚠ 정규식 상수가 **여러 줄에 걸쳐** 있는 것이 있다(`_KEIRIN_ONLY_RE`).
    #   한 줄만 잘라 쓰면 괄호가 안 닫혀 통째로 실패한다 → 컴파일될 때까지 줄을 이어 붙인다.
    lines = src.split("\n")
    for name in ("_KRA_TRACK_RE", "_KEIRIN_ONLY_RE"):
        for i, line in enumerate(lines):
            if not line.startswith(name + " = re.compile("):
                continue
            for j in range(i, min(i + 40, len(lines))):
                chunk = "\n".join(lines[i:j + 1])
                try:
                    exec(compile(chunk, "<re>", "exec"), ns)
                    break
                except SyntaxError:
                    continue
            break

    exec(compile(_slice(src, _GATE_FROM, _GATE_TO, "게이트"), "<gate>", "exec"), ns)
    try:
        exec(compile(_slice(src, _MID_FROM, _MID_TO, "중간점검"), "<mid>", "exec"), ns)
    except Exception as e:
        ns["_MID_ERR"] = str(e)
    return ns


def _q(n):
    """n두 완전 그리드 = C(n,2) 조합."""
    return [{"combo": [a, b], "odds": 10.0}
            for a in range(1, n + 1) for b in range(a + 1, n + 1)]


def main():
    as_json = "--json" in sys.argv
    tmp = tempfile.mkdtemp(prefix="gate_selfcheck_")
    fails, notes = [], []
    try:
        ns = load_ns(tmp)
        ghost = ns["_ingest_ghost_verdict"]
        suspect = ns["_odds_suspect_verdict"]
        hit = ns["_gate_hit"]
        hits_file = ns["_GATE_HITS_FILE"]

        # ══════ ① 계수기가 파일에 실제로 남는가 (io 미import 회귀 차단) ══════
        hit("selftest_reach", reach_only=True)
        if not os.path.exists(hits_file):
            fails.append("① 계수기 파일이 안 생겼다 — `import io` 누락 등으로 조용히 죽었다"
                         " (2026-08-04 실사고 재현)")
        else:
            d = json.load(io.open(hits_file, encoding="utf-8"))
            e = d.get("selftest_reach") or {}
            if int(e.get("reach") or 0) < 1:
                fails.append("① 도달이 안 세어졌다: %r" % e)
            if int(e.get("fire") or 0) != 0:
                fails.append("① reach_only 인데 발동이 셌다: %r" % e)

        # ══════ ② 발동이 append 로그에 남는가 ══════
        hit("selftest_fire", rk="검증 1경주", reason="테스트")
        gf = os.path.join(ns["LOGS_DIR"], "gate_fire")
        rows = []
        if os.path.isdir(gf):
            for fn in os.listdir(gf):
                for line in io.open(os.path.join(gf, fn), encoding="utf-8"):
                    if line.strip():
                        rows.append(json.loads(line))
        if not any(r.get("gate") == "selftest_fire" for r in rows):
            fails.append("② 발동이 append 로그(logs/gate_fire)에 안 남았다")

        # ══════ 1층 — 명단은 출마표(6두)로 넣는다 ══════
        for _rk in ("일본 1경주", "후나바시 1경주", "부산 1경주", "다케오 1경주"):
            ns["_STARTERS"][_rk] = {"horses": [{"no": i} for i in range(1, 7)]}

        # ③ 정상: 명단 6두 · 15조합 = C(6,2)  → 막으면 안 된다
        if ghost("일본 1경주", _q(6), "horse") is not None:
            fails.append("③ 🔴 정상 배당을 1층이 막았다 (정상을 막는 쪽이 오염보다 나쁘다)")

        # ④ 오염(일본): 명단 6두 · 36조합 = C(9,2) → 막아야 한다
        v_jp = ghost("후나바시 1경주", _q(9), "horse")
        if not v_jp:
            fails.append("④ 오염 배당(36조합=C(9,2) · 명단 6두)을 1층이 안 막았다")

        # ⑤ 한국 완화 ②: 같은 오염이라도 한국은 막지 않는다
        v_kr = ghost("부산 1경주", _q(9), "horse")
        if v_kr:
            fails.append("⑤ 🔴 한국인데 1층이 막았다 (완화 ② 미적용): %s" % v_kr)

        # ⑥ 명단이 얇으면 판정하지 않는다
        if ghost("미지 1경주", _q(9), "horse") is not None:
            fails.append("⑥ 명단이 없는데 1층이 판정했다 (추측 금지 위반)")

        # ⑦ 경륜은 부분수집 예외가 없다 — 오염이면 막는다
        if not ghost("다케오 1경주", _q(9), "cycle"):
            fails.append("⑦ 경륜 오염을 1층이 안 막았다")

        # 🔴 명단 확장(출마표 ∪ oddspark) — 원칙 22. oddspark 가 9두를 봤으면 오염이 아니다.
        ns["_STARTERS"]["모리오카 1경주"] = {"horses": [{"no": i} for i in range(1, 7)]}
        ns["_oddspark_seen_note"]("모리오카 1경주", _q(9))
        if ghost("모리오카 1경주", _q(9), "horse") is not None:
            fails.append("⑦-2 🔴 oddspark 가 본 마번인데 1층이 막았다 (원칙 22 위반)")

        # ══════ 3층 — 표시 차단 판정 (배당 키는 `quinella` 다) ══════
        an_ok = {"form": [{"no": i} for i in range(1, 7)],
                 "corePicks": {"finalQuinellas": [{"combo": [1, 2]}]},
                 "quinella": _q(6)}
        if suspect("일본 2경주", an_ok) is not None:
            fails.append("⑧ 🔴 정상 경주를 3층이 가렸다")

        an_bad = dict(an_ok, quinella=_q(9))
        if not suspect("후나바시 2경주", an_bad):
            fails.append("⑨ 오염 경주를 3층이 안 가렸다")

        an_none = {"form": [], "corePicks": {"finalQuinellas": [{"combo": [1, 2]}]},
                   "quinella": _q(9)}
        if not suspect("후나바시 3경주", an_none):
            notes.append("⑩ 명단 없음이 3층에 안 걸린다 — 규칙이 바뀌었는지 확인할 것")

        # ══════ ⑪ 중간점검이 이상을 이상이라 말하는가 ══════
        if "_midcheck_abnormal" not in ns:
            fails.append("⑪ 중간점검 함수를 못 읽었다: %s" % ns.get("_MID_ERR", "구간 없음"))
        else:
            ab = ns["_midcheck_abnormal"]
            normal = {"procs": 2, "saveFailDelta": 0, "gapDelta": 0, "corruptDelta": 0,
                      "diverge": 0, "integBad": [], "gate": {"counterAgeMin": 1}}
            if ab(dict(normal)):
                fails.append("⑪ 정상인데 이상이라 말한다: %s" % ab(dict(normal)))
            if not ab(dict(normal, procs=5)):
                fails.append("⑪ 서버 5벌인데 정상이라 말한다")
            if not ab(dict(normal, saveFailDelta=999)):
                fails.append("⑪ 저장 실패 폭주인데 정상이라 말한다")
            if not ab(dict(normal, gate={"counterAgeMin": 9999})):
                fails.append("⑪ 계수기가 멎었는데 정상이라 말한다")

        # ══════ ⑫ 실전 경로를 안 봤는가 ══════
        if not str(hits_file).startswith(tmp):
            fails.append("⑫ 🔴 계수기가 실전 경로를 봤다: %s" % hits_file)
    except Exception as e:
        fails.append("실행 예외: %s: %s" % (type(e).__name__, e))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if as_json:
        print(json.dumps({"ok": not fails, "fails": fails, "notes": notes}, ensure_ascii=False))
    else:
        for n in notes:
            print("⚠ %s" % n)
        if fails:
            print("🔴 실패 %d건" % len(fails))
            for f in fails:
                print("   - %s" % f)
        else:
            print("🟢 통과 — 계수기 2 · 1층 5 · 3층 3 · 중간점검 4 · 격리 1")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
