# -*- coding: utf-8 -*-
"""한국 PDF 세션의 경주번호를 **발주시각(fitz)** 으로 교정한다.

🔴 [2026-08-07 실사고] Vision 이 제주 1R(13:15)을 **6R 로**, 제주 3R(14:55)을 **5R 로** 읽었다.
  fitzVerify 는 이것을 '제주 1·3 누락'으로 보고했는데 **해석이 틀렸다** —
  없는 게 아니라 **틀린 자리에 있었다.** 그대로 두면 제주 1R 명단이 6R 에 붙어
  회원이 다른 경주 말을 본다. "명단 없음"보다 나쁘다(조용히 틀린다).

⚠ app.py 의 `_korea_verify_fitz` 안에도 같은 교정을 넣었지만 **그 시점에는 postTime 이 아직
  없어서** 걸리지 않았다(검증이 postTime 부여보다 앞선다). 그래서 완료된 세션을 고치는
  이 도구가 따로 필요하다. 🔴 app.py 쪽 교정은 지우지 않는다 — 순서가 바뀌면 그쪽이 잡는다.

원칙
  · 🔴 발주시각이 **정확히 일치할 때만** 바꾼다. 안 맞으면 아무것도 안 바꾼다.
  · 🔴 같은 자리가 이미 차 있으면 바꾸지 않는다(덮어쓰기 금지).
  · --dry 가 기본. --apply 로만 저장하고, 저장 전 백업한다.
  · 명단(horses)은 **손대지 않는다.** 번호표만 고친다.

사용
  python tools/fix_korea_renum.py --dry
  python tools/fix_korea_renum.py --apply
"""
import os
import re
import io
import sys
import json
import time
import shutil
import argparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SESS = os.path.join(ROOT, "data", "korea_session.json")
PDF = os.path.join(ROOT, "data", "korea_last.pdf")


def fitz_map():
    """PDF → {발주시각: (경기장, 경주번호)}. fitz 는 결정적이라 두 번 돌려도 같다."""
    import fitz
    out = {}
    d = fitz.open(PDF)
    for i in range(d.page_count):
        m = re.search(r"(부산|제주|서울)경마\s*(\d+)경주.*?일반경주\((\d{2}:\d{2})\)",
                      d[i].get_text(), re.S)
        if m:
            out.setdefault(m.group(3), (m.group(1), int(m.group(2))))
    d.close()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    fx = fitz_map()
    print("[fitz] 경주 %d개" % len(fx))
    sess = json.load(io.open(SESS, encoding="utf-8"))
    races = sess.get("races") or []

    taken = set()
    for r in races:                       # 이미 맞는 것부터 자리를 잡는다
        p = r.get("postTime")
        if p and fx.get(p) == (r.get("venue"), int(r.get("raceNo") or 0)):
            taken.add(fx[p])

    plan = []
    for r in races:
        p = r.get("postTime")
        tgt = fx.get(p) if p else None
        cur = (r.get("venue"), int(r.get("raceNo") or 0))
        if not tgt:
            print("  ⚠ %s %s경주 · 발주시각 없음/불일치 → 손대지 않음" % cur)
            continue
        if cur == tgt or tgt in taken:
            continue
        plan.append((r, cur, tgt, p))
        taken.add(tgt)

    if not plan:
        print("교정 대상 없음")
        return 0
    print("\n교정 계획 %d건" % len(plan))
    for _r, cur, tgt, p in plan:
        print("  🔴 %s %s경주 → %s %s경주 (발주 %s · 말 %d두)"
              % (cur[0], cur[1], tgt[0], tgt[1], p, len(_r.get("horses") or [])))

    if not a.apply:
        print("\n(--dry) 저장하지 않았다. 적용하려면 --apply")
        return 0

    bak = os.path.join(ROOT, "backups", "korea_renum_%s" % time.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(bak, exist_ok=True)
    shutil.copy2(SESS, os.path.join(bak, "korea_session.json"))
    for r, cur, tgt, p in plan:
        r["venue"], r["raceNo"] = tgt[0], tgt[1]
        r["renumFrom"] = "%s %s경주" % cur          # 🔴 무엇을 무엇으로 바꿨는지 남긴다
        r["renumBy"] = "fitz 발주시각 %s" % p
    tmp = SESS + ".tmp"
    io.open(tmp, "w", encoding="utf-8").write(json.dumps(sess, ensure_ascii=False))
    os.replace(tmp, SESS)
    print("\n🟢 적용 %d건 · 백업 %s" % (len(plan), bak))
    return 0


if __name__ == "__main__":
    sys.exit(main())
