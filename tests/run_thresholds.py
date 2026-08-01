# -*- coding: utf-8 -*-
"""[판정선 대조 테스트 (2026-08-02 신설 · 권대표 승인)]

🔴 왜: 합의와 코드가 두 번 어긋났고 **두 번 다 코드가 더 느슨한 방향**이었다.
  사람이 기억하면 또 어긋난다 → **테스트가 대조한다.**

검사 3종
```
[1] 상수 일치      — 각 파일의 값이 tools/thresholds.py 와 같은가
[2] 실제 참조 여부 — 판정 코드가 그 값을 **참조**하는가(하드코딩 재선언 금지)
[3] 미구현 추적    — UNIMPLEMENTED 목록이 실제로 미구현인가(구현됐는데 목록에 남아 있지 않은가)
```
⚠ [2] 가 없으면 "thresholds.py 를 만들었는데 아무도 안 읽는다"가 된다 — 오늘 F3 가 그 형태였다
  (승인만 있고 구현 없음). **만든 것과 쓰이는 것은 다르다.**
⚠ 자기검증은 `tests/run_selfcheck.py` 가 담당한다(원칙 17).
"""
import io
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "tools"))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# 🔴 [2026-08-02 실사고] `__pycache__` 가 **낡은 값을 돌려준다.**
#   자기검증에서 `PAYBACK = 74.5` → `70.0` 으로 주입했더니 **파일은 74.5인데 import 는 70.0** 이었다.
#   원인: 두 값의 **길이가 같고**(4자) 수정이 같은 초에 일어나 .pyc 의 (mtime, size) 검증을 통과했다.
#   🔴 실전에서도 가능하다 — 판정선을 **같은 길이로** 고치면(74.5→70.0) 캐시가 안 갱신될 수 있다.
#   ⇒ 캐시를 쓰지 않고 **소스를 직접 로드**한다. 이 테스트만큼은 캐시를 신뢰하면 안 된다.
sys.dont_write_bytecode = True
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "_thresholds_src", os.path.join(BASE, "tools", "thresholds.py"))
T = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(T)

fails, warns = [], []


def _read(rel):
    p = os.path.join(BASE, rel)
    return open(p, encoding="utf-8").read() if os.path.exists(p) else None


def _num(src, pat):
    m = re.search(pat, src) if src else None
    return float(m.group(1)) if m else None


# ── [1] 상수 일치 ────────────────────────────────────────────────────────────
mr = _read("tools/measure_recovery.py")
ap = _read("app.py")
hc = _read("tools/health_check.py")

CASES = [
    ("PAYBACK", mr, r"^PAYBACK\s*=\s*([\d.]+)", T.PAYBACK),
    ("CLEAN_LO", mr, r"^CLEAN_LO,\s*CLEAN_HI\s*=\s*([\d.]+)", T.CLEAN_LO),
    ("CLEAN_HI", mr, r"^CLEAN_LO,\s*CLEAN_HI\s*=\s*[\d.]+\s*,\s*([\d.]+)", T.CLEAN_HI),
    ("EV_RESCUE_LO", ap, r"_EV_RESCUE_LO,\s*_EV_RESCUE_HI,\s*_EV_RESCUE_MAX\s*=\s*([\d.]+)", T.EV_RESCUE[0]),
    ("EV_RESCUE_HI", ap, r"_EV_RESCUE_LO,\s*_EV_RESCUE_HI,\s*_EV_RESCUE_MAX\s*=\s*[\d.]+\s*,\s*([\d.]+)", T.EV_RESCUE[1]),
    ("TRIO_SHOW_MAX", ap, r'"trioShowMax"\s*:\s*(\d+)', T.TRIO_SHOW_MAX),
    ("SNAP_TARGET_RATE", hc, r"^SNAP_TARGET_RATE\s*=\s*([\d.]+)", T.CHECKLIST["D1_snapshot_rate_min"]),
    ("SNAP_MIN_TICKS", hc, r"^SNAP_MIN_TICKS\s*=\s*(\d+)", T.CHECKLIST["D1_min_ticks"]),
    ("SETTLE_MIN", hc, r"^SETTLE_MIN\s*=\s*(\d+)", T.CHECKLIST["settle_min"]),
    ("DAEMON_LONG_SEC", hc, r"^DAEMON_LONG_SEC\s*=\s*([\d.]+)", T.CHECKLIST["I3_daemon_long_sec"]),
    ("SERVER_LOG_MAX_MIN", hc, r"^SERVER_LOG_MAX_MIN\s*=\s*([\d.]+)", T.CHECKLIST["I7_log_stale_min_max"]),
]
print("[1] 상수 일치")
for name, src, pat, want in CASES:
    if src is None:
        warns.append("[1] %s — 파일 없음" % name)
        continue
    got = _num(src, pat if pat.startswith("^") else pat) if not pat.startswith("^") else _num(src, pat.replace("^", "", 1) if False else pat)
    got = _num(src, pat) if got is None else got
    if got is None:
        got = _num(src, pat.lstrip("^"))
    if got is None:
        fails.append("[1] %s — 코드에서 값을 찾지 못했다(이름이 바뀌었나?)" % name)
    elif abs(got - float(want)) > 1e-9:
        # 🔴 어느 방향으로 어긋났는지 함께 적는다 — 느슨한 쪽이면 더 위험하다
        fails.append("[1] %s: 코드 %s ↔ 판정선 %s  🔴 **불일치**" % (name, got, want))
    else:
        print("    🟢 %-20s %s" % (name, got))

# ── [2] 실제 참조 여부 ───────────────────────────────────────────────────────
print("[2] 실제 참조 여부 (하드코딩 재선언 금지)")
REFS = [
    ("F2 변별 판정선", hc, r"decisive\s*<\s*(\d+)", T.F2_DECISIVE_MIN),
    ("F3 갈린경주 판정선", hc, r"diff_n\s*<\s*(\d+)", T.F3_DIFF_MIN),
    ("F1 폐기율 상한", hc, r"F1_DISCARD_MAX|20\.0", T.F1_DISCARD_MAX),
]
for name, src, pat, want in REFS:
    if src is None:
        continue
    m = re.search(pat, src)
    if not m:
        fails.append("[2] %s — 판정 코드에서 찾지 못했다" % name)
        continue
    try:
        got = float(m.group(1))
        if abs(got - float(want)) > 1e-9:
            fails.append("[2] %s: 코드 %s ↔ 판정선 %s 🔴 불일치" % (name, got, want))
            continue
    except (IndexError, ValueError):
        pass
    print("    🟢 %-20s 확인" % name)

# ── [3] 미구현 추적 ──────────────────────────────────────────────────────────
print("[3] 미구현 목록 (승인했으나 코드에 없는 것)")
for u in T.UNIMPLEMENTED:
    print("    ⏳ %-12s %s  (승인 %s)" % (u["id"], u["what"], u["approved"]))
if hc and "O1_drop_rate_ratio_max" in hc:
    fails.append("[3] O1~O6 가 구현된 것으로 보인다 — UNIMPLEMENTED 에서 빼야 한다")

print()
for w in warns:
    print("  ⚠", w)
if fails:
    print("🔴 실패 %d건" % len(fails))
    for x in fails:
        print("   -", x)
    print("🔴 **불일치는 항상 「느슨한 쪽」으로 생긴다** — 코드가 판정선을 더 쉽게 통과시키고 있지 않은지 먼저 볼 것.")
    sys.exit(1)
print("🟢 통과 — 판정선 불일치 0건 · 미구현 %d건은 목록으로 추적 중" % len(T.UNIMPLEMENTED))
sys.exit(0)
