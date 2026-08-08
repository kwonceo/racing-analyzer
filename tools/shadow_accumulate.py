# -*- coding: utf-8 -*-
"""섀도우 축적 — 안을 고르지 말고 **결과만 매일 기록**한다.

🔴 [2026-08-09 대표 지시] 아흐레 측정 결론: 한 달 표본으로는 어떤 안도 못 정한다.
  회수율 월간 변동 경마 18.4%p · 경륜 12.2%p · 95%CI 폭 ±28%p.
  세 안이 전부 「한 달은 되고 한 달은 안 됐다」.
⇒ **지금 고르지 않는다.** 3~4개월 뒤 신뢰구간이 절반이 되면 그때 고른다.

이 도구가 하는 일
  · `measure_recovery.py` 를 그대로 호출해 출력을 받아 적는다. **계산을 다시 쓰지 않는다**
    (원칙 15 — 회수율 측정 창구는 하나다. 즉석 코드 금지).
  · `docs/shadow/<날짜>.md` 에 누적. 같은 날 두 번 돌리면 덮어쓴다(멱등).
  · 🔴 **실전 경로에 일절 개입하지 않는다.** app.py 무변경 · 읽기 전용.

사용
  python tools/shadow_accumulate.py                 # 오늘 · 이번 달 패턴
  python tools/shadow_accumulate.py --pattern 2026_07_*
"""
import os
import io
import sys
import time
import argparse
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "docs", "shadow")
TOOL = os.path.join(ROOT, "tools", "measure_recovery.py")


def run(sport, pattern):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    try:
        p = subprocess.run([sys.executable, TOOL, "--sport", sport, "--pattern", pattern],
                           capture_output=True, text=True, encoding="utf-8",
                           env=env, cwd=ROOT, timeout=900)
        return p.stdout or ""
    except Exception as e:
        return "실행 실패: %s" % e


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pattern", default=time.strftime("%Y_%m_*"))
    ap.add_argument("--sports", default="horse,cycle")
    a = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    day = time.strftime("%Y-%m-%d")
    buf = ["# 섀도우 축적 %s" % day,
           "",
           "⚠ 안을 **고르지 않는다.** 결과만 쌓는다. 판정은 신뢰구간이 절반이 된 뒤에.",
           "⚠ 분모 패턴: `%s`" % a.pattern,
           ""]
    for sp in [x.strip() for x in a.sports.split(",") if x.strip()]:
        buf.append("## %s" % sp)
        buf.append("```")
        out = run(sp, a.pattern)
        # 표 부분만 — 안 이름과 수치 줄
        for ln in out.splitlines():
            if ("분모" in ln) or ("%" in ln and "배" in ln) or ("95%CI" in ln):
                buf.append(ln.rstrip())
        buf.append("```")
        buf.append("")
    path = os.path.join(OUT_DIR, "%s.md" % day)
    io.open(path, "w", encoding="utf-8").write("\n".join(buf))
    print("[섀도우] %s · 패턴 %s → %s" % (day, a.pattern, os.path.relpath(path, ROOT)))
    print("⚠ 이 파일은 기록이다. 여기 숫자로 안을 고르지 말 것(한 달 표본은 ±28%p 흔들린다).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
