# -*- coding: utf-8 -*-
"""[읽기 전용] 카톡 「어떻게 봤나」 근거줄이 실제로 붙는가 — 종목·전적별.

🔴 왜 이 도구가 있나 (2026-09-01)
  근거줄이 T-5·T-7 발송 119건 중 19건(16%)에서 빠졌고, 그 16건이 **전적 없는 경주**였다.
  원인은 `an["form"]` 이 None 이면 `an["horses"]`(dict) 로 폴백해 int 키를 순회하다 죽는 것이었고,
  `except` 가 삼켜 **아무 표시 없이 블록만 빠졌다**(하루 148건).
  ⇒ 고친 뒤 **실발동을 눈으로 확인**해야 하는데, 그날 남은 경주가 전부 전적O 라 조건에 도달하지 못했다.
    사람 기억에 맡기지 않으려고 도구로 만든다.

🔴 분모 규약 (원칙 8-C·26 — 오늘 이것으로 한 번 틀렸다)
  분모는 **T-5·T-7 발송**만이다. 「즉시변경」은 변경 통보라 근거줄이 **없는 것이 설계**다.
  섞으면 누락이 19건 → 33건으로 부풀어 보인다.

⚠ 판정하지 않는다 — 숫자를 내고 조건 도달 여부만 밝힌다(원칙 4·23).
   전적X 경주가 0건이면 「고쳐졌다」가 아니라 **「조건이 안 왔다」**이다.

실행:
  python tools/check_kakao_why.py            오늘
  python tools/check_kakao_why.py 20260901   날짜 지정
"""
import io
import os
import sys
import json
import glob
import time
import collections

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PHASES = ("T-5", "T-7")          # 🔴 즉시변경은 분모에서 뺀다(설계상 근거줄 없음)
MARK = "어떻게 봤나"
MIN_N = 10                       # 원칙 1 계열 — 표본이 얇으면 판정하지 않는다


def _load(p):
    try:
        with io.open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def sends(ymd):
    """카톡 발송 이력 — 파일명이 두 형식이라 둘 다 본다."""
    d1 = "%s-%s-%s" % (ymd[:4], ymd[4:6], ymd[6:8])
    out = []
    for p in (os.path.join(BASE, "data", "kakao_sent", ymd + ".json"),
              os.path.join(BASE, "data", "kakao_sent", d1 + ".json")):
        d = _load(p)
        if isinstance(d, list):
            out += d
        elif isinstance(d, dict):
            out += list(d.values())
    return [r for r in out if isinstance(r, dict)]


def logs(ymd):
    """분석 로그 → {경주명: (전적보유, 종목)}.
    ⚠ 전적 보유는 `horses[].gait` 로 본다 — 마감 후 재계산에 덮이므로
      **저장 시점 상태**이지 T-5 시점 상태가 아니다(2026-09-01 기록 참조)."""
    out = {}
    pat = "%s_%s_%s_*.json" % (ymd[:4], ymd[4:6], ymd[6:8])
    for f in glob.glob(os.path.join(BASE, "data", "analysis_log", pat)):
        d = _load(f)
        if not isinstance(d, dict):
            continue
        nm = os.path.basename(f)[:-5].split("_", 3)[3].replace("_", " ")
        hs = d.get("horses") or []
        out[nm] = (any(h.get("gait") is not None for h in hs) if hs else None, d.get("sport"))
    return out


def main():
    ymd = (sys.argv[1] if len(sys.argv) > 1 else time.strftime("%Y%m%d")).replace("-", "")
    rows = [r for r in sends(ymd) if str(r.get("phase") or "") in PHASES]
    lg = logs(ymd)
    print("▣ 카톡 「어떻게 봤나」 근거줄 — %s" % ymd)
    print("   ⚠ 분모 = **T-5·T-7 발송만** (즉시변경 제외 — 설계상 근거줄 없음)")
    if not rows:
        print("   발송 없음 — 개최일이 아니거나 이력이 아직 없다.")
        return 0

    def txt(r):
        return str(r.get("text") or r.get("body") or r.get("message") or "")

    b = collections.defaultdict(lambda: [0, 0])
    miss = []
    for r in rows:
        rk = str(r.get("raceKey") or "")
        g, sp = lg.get(rk, (None, "?"))
        key = (sp or "?", {True: "전적O", False: "전적X", None: "로그없음"}.get(g))
        has = MARK in txt(r)
        b[key][0] += 1
        b[key][1] += 1 if has else 0
        if not has:
            miss.append((rk, sp, g, r.get("sentAt") or ""))

    tot = sum(v[0] for v in b.values())
    hit = sum(v[1] for v in b.values())
    print("   발송 %d건 · 근거줄 %d건 (%.1f%%)\n" % (tot, hit, 100.0 * hit / max(tot, 1)))
    print("   %-20s %5s %8s %s" % ("종목 / 전적", "발송", "근거줄", "비율"))
    for k in sorted(b):
        n, h = b[k]
        print("   %-20s %5d %8d  %3.0f%%%s"
              % ("%s / %s" % k, n, h, 100.0 * h / max(n, 1), "  ⚠n<%d" % MIN_N if n < MIN_N else ""))

    # 🔴 핵심 — 전적X 경주가 왔는가(조건 도달), 왔다면 근거줄이 붙었는가(실발동)
    xn = sum(n for (sp, gl), (n, h) in b.items() if gl == "전적X")
    xh = sum(h for (sp, gl), (n, h) in b.items() if gl == "전적X")
    print()
    if xn == 0:
        print("   🔴 **조건 미도달** — 전적X 경주 발송이 0건이다.")
        print("      「고쳐졌다」로 읽지 말 것(원칙 4·23 — 도달과 발동은 다르다).")
    else:
        print("   🟢 조건 도달 — 전적X 경주 발송 %d건 · 근거줄 %d건 (%.0f%%)"
              % (xn, xh, 100.0 * xh / xn))
        if xh == 0:
            print("      🔴 **여전히 0% 다 — 수정이 안 먹었거나 다른 원인이다.**")
        elif xn < MIN_N:
            print("      ⚠ n=%d < %d — 방향만 본다(원칙 1)." % (xn, MIN_N))
        else:
            print("      🟢 실발동 확인.")

    if miss:
        print("\n   근거줄 없는 %d건:" % len(miss))
        for rk, sp, g, at in miss[:15]:
            print("      %-16s %-6s 전적=%-4s %s"
                  % (rk, sp, {True: "O", False: "X", None: "?"}.get(g), str(at)[11:16]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
