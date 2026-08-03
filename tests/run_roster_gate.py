# -*- coding: utf-8 -*-
"""[출주 명단 밖 마번 차단 게이트 회귀 테스트 (2026-08-02 신설)] — 원칙 17.

🔴 **실사고 재현 테스트다.** 2026-08-02 호후 3경주(7명)에 확장(private)이 11두짜리 다른 경주
  배당을 보내 **없는 마번 7·11 이 추천되고 카카오로 회원에게 나갔다.**
  전수: 발송 301건 중 **13건(4.3%)** · 7/30~8/2 **매일** 나가고 있었다.

⚠ **네트워크·서버를 쓰지 않는다.** 게이트 로직을 그대로 옮겨 판정만 검사한다.
⚠ 이 테스트는 **통과가 정답**이다. 실패하면 없는 말이 다시 회원에게 나간다.

사용: python tests/run_roster_gate.py
"""
import io
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass


def gate(fp, roster):
    """app.py 의 게이트와 **같은 규칙**. 출마표가 2두 미만이면 판정하지 않는다(추측 금지)."""
    if len(roster) < 2:
        return fp, []
    out, drops = {}, []
    for k in ("quinellas", "trifectas", "bmedSpecial"):
        keep = []
        for it in (fp.get(k) or []):
            try:
                ok = all(int(x) in roster for x in (it.get("combo") or []))
            except Exception:
                ok = True
            (keep if ok else drops).append(it) if ok else drops
            if ok:
                pass
            else:
                continue
        out[k] = [it for it in (fp.get(k) or [])
                  if all(int(x) in roster for x in (it.get("combo") or []))]
        drops += [it for it in (fp.get(k) or [])
                  if not all(int(x) in roster for x in (it.get("combo") or []))]
    return out, drops


def main():
    fails = []

    # ① 🔴 실사고 재현 — 호후 3경주(출마표 1~6)에 7·11 조합
    roster = {1, 2, 3, 4, 5, 6}
    fp = {"quinellas": [{"combo": [7, 11], "odds": 5.3}, {"combo": [3, 7], "odds": 9.2}],
          "trifectas": [{"combo": [3, 7, 11], "odds": 6.7}], "bmedSpecial": []}
    out, drops = gate(fp, roster)
    r1 = (not out["quinellas"]) and (not out["trifectas"]) and len(drops) == 3
    print("  %s ① 실사고 재현(호후 3R)  남은 복승 %d · 삼복승 %d · 제외 %d (기대 0·0·3)"
          % ("✅" if r1 else "🔴", len(out["quinellas"]), len(out["trifectas"]), len(drops)))
    if not r1:
        fails.append("① 호후 3경주 실사고가 차단되지 않는다")

    # ② 정상 조합은 **하나도 자르지 않는다**(오탐 0)
    fp2 = {"quinellas": [{"combo": [4, 6]}, {"combo": [4, 7]}],
           "trifectas": [{"combo": [1, 4, 7]}], "bmedSpecial": [{"combo": [2, 5]}]}
    out2, drops2 = gate(fp2, {1, 2, 3, 4, 5, 6, 7})
    r2 = len(drops2) == 0 and len(out2["quinellas"]) == 2
    print("  %s ② 오탐 없음(호후 4R)    제외 %d (기대 0)" % ("✅" if r2 else "🔴", len(drops2)))
    if not r2:
        fails.append("② 정상 조합을 자른다 — 오탐")

    # ③ 🔴 출마표가 없으면 **판정하지 않는다**(추측 금지 · 종전대로 통과)
    out3, drops3 = gate(fp, set())
    r3 = len(drops3) == 0
    print("  %s ③ 출마표 없음→미판정    제외 %d (기대 0)" % ("✅" if r3 else "🔴", len(drops3)))
    if not r3:
        fails.append("③ 출마표가 없는데 판정한다")

    # ④ 🔴 app.py 에 게이트가 **실제로 배선**돼 있는가(빠지면 ①~③이 무의미하다)
    src = open(os.path.join(BASE, "app.py"), encoding="utf-8").read()
    r4a = "rosterNos" in src and "출주명단 게이트" in src
    r4b = ("카카오 명단검사" in src or "카카오 발송 보류" in src)
    print("  %s ④ app.py 배선           추천단 %s · 발송단 %s" % ("✅" if (r4a and r4b) else "🔴", r4a, r4b))
    if not (r4a and r4b):
        fails.append("④ app.py 에 게이트가 배선돼 있지 않다")

    # ⑤ 발송단 차단이 **추천단 뒤**에 있는가(순서가 뒤집히면 오염이 새 나간다)
    r5 = src.find("출주명단 게이트") < src.find("카카오 명단검사")
    print("  %s ⑤ 순서(추천 → 발송)" % ("✅" if r5 else "🔴"))
    if not r5:
        fails.append("⑤ 발송단 검사가 추천단보다 앞에 있다")

    # ⑥⑦ 🔴 과거 실데이터 소급 — **발동 규모 + 오탐률**을 함께 잰다.
    #   🔴 [2026-08-02 교훈] 오탐을 안 재고 켠 가드가 실제로 수집을 죽였다(netkeiba guard).
    #     그리고 이 게이트도 **출마표만 쓰던 첫 판은 오탐 83.3%** 였다.
    #     ⇒ **오탐률을 재지 않은 가드는 켜지 않는다.**
    def _nos(q):
        s = set()
        for k in (q or {}):
            for x in str(k).replace("-", "+").split("+"):
                if x.isdigit():
                    s.add(int(x))
        return s
    try:
        import glob
        applied = cutn = fpn = 0
        for f in sorted(glob.glob(os.path.join(BASE, "data", "analysis_log", "2026_0*.json"))):
            try:
                d = json.load(open(f, encoding="utf-8"))
            except Exception:
                continue
            cp = d.get("corePicks") or {}
            combos = []
            for k in ("finalQuinellas", "finalTrifectas", "bmedSpecial"):
                for it in (cp.get(k) or []):
                    if it.get("combo"):
                        combos.append([int(x) for x in it["combo"]])
            if not combos:
                continue
            rs = set()
            for e in ((d.get("raw_profile") or {}).get("entries") or []):
                try:
                    rs.add(int(e["no"]))
                except Exception:
                    pass
            srv = set()
            try:
                h = json.load(open(f.replace("analysis_log", "odds_history"), encoding="utf-8"))
                for s in (h.get("snapshots") or []):
                    if str(s.get("src") or "").startswith(("oddspark", "netkeiba")):
                        srv |= _nos(s.get("quinella"))
            except Exception:
                pass
            rr = rs | srv                      # 🔴 출마표 ∪ 서버수집(private 제외)
            if len(rr) < 2:
                continue
            applied += 1
            for c in combos:
                if not all(x in rr for x in c):
                    cutn += 1
                    if srv and all(x in srv for x in c):
                        fpn += 1               # 서버 배당에 실재 = 살 수 있는 조합 = **오탐**
        rate = 100.0 * fpn / max(cutn, 1)
        r6 = applied >= 100 and cutn > 0
        print("  %s ⑥ 소급 발동 규모        적용 %d경주 · 차단 %d조합 (0 이면 검사가 무의미)"
              % ("✅" if r6 else "🔴", applied, cutn))
        r7 = (fpn == 0)
        print("  %s ⑦ 🔴 오탐률             %d/%d = %.1f%% (기대 0%% · 오탐이 더 위험하다)"
              % ("✅" if r7 else "🔴", fpn, cutn, rate))
        if not r6:
            fails.append("⑥ 과거 데이터에서 발동이 0 — 검사가 아무것도 안 잡는다")
        if not r7:
            fails.append("⑦ 🔴 오탐 %d건 — 정상 조합을 자른다. 켜면 안 된다" % fpn)
    except Exception as e:
        print("  ⚠ ⑥⑦ 과거 대조 실패(건너뜀):", str(e)[:80])

    print()
    if fails:
        print("🔴 실패 %d건" % len(fails))
        for f in fails:
            print("   -", f)
        return 1
    print("🟢 통과 — 없는 마번은 추천에도 발송에도 나가지 않는다")
    return 0


if __name__ == "__main__":
    sys.exit(main())
