# -*- coding: utf-8 -*-
"""[날짜 없는 glob 매칭 금지 (2026-07-31 신설)] — 원칙 16 의 자동 검증.

■ 왜 회귀 테스트인가 (체크리스트가 아니라)
  🔴 "같은 경기장이 여러 날 개최되는 것" 자체는 **정상**이다.
     그걸 매일 감시 항목으로 두면 **영원히 빨간불**이고, 항상 빨간 항목은 무시하게 된다
     (Gemini WARNING 99.5% 가 무의미했던 것과 같은 구조).
  ⇒ 매일 볼 것이 아니라 **커밋할 때 걸려야 하는 것**이다.

■ 무엇을 잡는가
  `glob("*_<경기장>_<N>경주.json")` 처럼 **특정 경주를 날짜 없이** 찾는 패턴.
  2026-07-31 실사고: A일 확정배당과 B일 배당판이 짝지어져
  모든 회수율이 **+10~25%p 부풀려졌다**(현행 95.6% → 실제 71.8%).

■ 무엇을 안 잡는가
  `glob("*.json")` 전수 스캔은 **특정 경주 매칭이 아니므로 무해**하다.

사용: python tests/run_glob_safety.py [--json]
"""
import argparse
import glob as _glob
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 검사 대상 — app.py(본 로직) 포함. 새 파일이 생겨도 자동으로 들어온다.
SCAN = ["app.py", "review_engine.py", "gemini_forecast.py"]
SCAN_DIRS = ["tools", "tests"]

# 🔴 특정 경주를 가리키는 신호: 경주/raceKey/slug/venue 를 파일명에 끼워 넣는 패턴
_RACE_HINT = re.compile(r"(경주|raceKey|slug|venue|rk\b)")
# 날짜가 매칭 키에 들어갔다는 신호
_DATE_HINT = re.compile(r"%Y|\d{4}_\d{2}_\d{2}|ymd|_pfx|strftime|date\b|day\b")
_GLOB = re.compile(r"glob\.glob\(|(?<![\w.])glob\(")

# 여기에 넣으면 검사에서 빠진다 — **넣기 전에 이유를 남길 것.**
ALLOW = {
    # (파일, 줄내용 일부): 사유
}


def targets():
    out = [os.path.join(BASE, f) for f in SCAN if os.path.exists(os.path.join(BASE, f))]
    for d in SCAN_DIRS:
        out += sorted(_glob.glob(os.path.join(BASE, d, "*.py")))
    return out


def scan():
    bad = []
    for p in targets():
        rel = os.path.relpath(p, BASE).replace("\\", "/")
        if rel.endswith("tests/run_glob_safety.py"):
            continue                                    # 자기 자신(설명 문자열)
        try:
            lines = open(p, encoding="utf-8", errors="replace").read().split("\n")
        except Exception:
            continue
        for i, l in enumerate(lines, 1):
            if not _GLOB.search(l):
                continue
            s = l.strip()
            if s.startswith("#"):
                continue
            # 와일드카드로 시작하는 파일명 매칭이면서
            if "*_" not in s and "_*" not in s and "*." not in s:
                continue
            # 🔴 특정 경주를 가리키는데 날짜가 없으면 위반
            if _RACE_HINT.search(s) and not _DATE_HINT.search(s):
                if any(k[0] == rel and k[1] in s for k in ALLOW):
                    continue
                bad.append({"file": rel, "line": i, "code": s[:120]})
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    bad = scan()
    n = len(targets())
    if a.json:
        print(json.dumps({"scanned": n, "violations": bad}, ensure_ascii=False, indent=1))
        return 1 if bad else 0
    print("=" * 78)
    print("날짜 없는 glob 매칭 검사 (원칙 16)  ⚠ 통과가 정답이다")
    print("=" * 78)
    print("검사 대상 %d파일 (app.py 포함 · tools/ · tests/ 자동)" % n)
    if not bad:
        print("\n✅ 위반 0건")
        print("   ⚠ `glob(\"*.json\")` 전수 스캔은 특정 경주 매칭이 아니므로 검사하지 않는다.")
        return 0
    print("\n🔴 위반 %d건 — 특정 경주를 날짜 없이 매칭한다" % len(bad))
    for b in bad:
        print("   %s:%d" % (b["file"], b["line"]))
        print("     %s" % b["code"])
    print("\n   ⇒ 파일명에 `YYYY_MM_DD_` 접두사를 포함하도록 고칠 것.")
    print("      같은 경기장이 여러 날 개최되므로 다른 날 데이터가 섞인다.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
