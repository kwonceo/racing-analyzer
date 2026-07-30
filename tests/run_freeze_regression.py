# -*- coding: utf-8 -*-
"""[마감 시점 동결 회귀 테스트 (2026-07-30 신설)] — 고정 Fixture 기반·라이브 무의존.

■ 이게 무엇인가 (코드 용어 없이)
  확인하는 것: **마감 시점에 확정된 추천이 마감 후에도 그대로 남아 있는가.**
  왜 필요한가: 2026-07-30 나고야 3R 에서 마감 확정은 `[9,3,8]` 이었는데 나중에 화면을 열자
    `[9,3,5]` 로 바뀌어 있었다. 정답(9-5-3)과 우연히 맞아 보였고 그것을 근거로
    케이스를 **3번 잘못 규정**했다.
  쉽게 말하면: 시험 답안지를 낸 뒤 누군가 정답을 보고 답을 고쳐 쓰는 것.
    채점하면 잘 맞은 것처럼 보이지만 실제로 그렇게 쓴 게 아니다.

■ 외부 앵커 원칙 (이 파일의 존재 이유)
  🔴 **파이프라인 내부의 두 값을 대조하는 것은 증명이 아니다.**
     "안전하다"는 결론은 **독립된 외부 기준**으로만 낼 수 있다.
     여기서 외부 기준 = **회원이 실제로 받은 카카오 원문**(`data/kakao_queue.jsonl` → Fixture 동봉).
     내부 저장값끼리 비교하면 둘 다 오염됐을 때 "일치"가 나와 통과해 버린다.

■ ⚠ 이 테스트들은 **지금 실패해야 정답이다.**
  마감 시점 동결이 아직 구현되지 않았기 때문이다(다음 세션 목표).
  **통과한다면 기대값이 느슨한 것이므로 다시 설계해야 한다.**
  동결 구현 후 `EXPECTED_FAIL` 에서 해제하고, 그때부터 실패 시 커밋을 막는다.

사용: python tests/run_freeze_regression.py [--json]
"""
import argparse
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FX = os.path.join(BASE, "tests", "fixtures")

# 동결 미구현 상태에서 실패가 예견된 케이스 — 커밋을 막지 않는다(작업 3 파이프라인이 참조).
EXPECTED_FAIL = {"① 카나자와 7R", "② 모리오카 3R", "③ 나고야 3R", "④ 나고야 9R"}


def _fx(name):
    return json.load(open(os.path.join(FX, name + ".json"), encoding="utf-8"))


def _combos_from_text(text):
    """카카오 원문에서 조합만 뽑는다. 예 '복승: 1+12 (5.4배) ★★★' → {(1,12)}
    ⚠ 원문 파싱이므로 '배' 앞 숫자(배당)를 조합으로 오인하지 않도록 `숫자+숫자` 형태만 잡는다."""
    out = set()
    for m in re.finditer(r"(\d+(?:\+\d+)+)", str(text or "")):
        nums = tuple(sorted(int(x) for x in m.group(1).split("+")))
        if 2 <= len(nums) <= 3:
            out.add(nums)
    return out


def _norm(lst):
    out = set()
    for c in (lst or []):
        if isinstance(c, dict):
            c = c.get("combo")
        if c:
            out.add(tuple(sorted(int(x) for x in c)))
    return out


def case1_kanazawa():
    """① 카나자와 7R — **카톡으로 보낸 조합이 최종 저장값에 그대로 있는가.**

    왜 이 기대값이 정답인가(외부 앵커):
      회원은 T-7·T-5 카톡으로 특정 조합을 받았다. 그 원문이 유일한 외부 증거다.
      최종 저장(`displayedCombos` = 적중 판정 명단)이 그 조합을 **포함하지 않으면**,
      회원이 산 조합과 시스템이 채점하는 조합이 다르다는 뜻이다.
    """
    d = _fx("kanazawa_7r")
    sent = set()
    for a in d["kakaoAnchors"]:
        sent |= _combos_from_text(a.get("text"))
    final = _norm((d["A_확정"].get("displayedCombos") or {}).get("quinellas"))
    missing = sent - final
    return (not missing), {
        "카톡 발송 조합": sorted("+".join(map(str, c)) for c in sent),
        "최종 확정 명단": sorted("+".join(map(str, c)) for c in final),
        "보낸 뒤 사라진 조합": sorted("+".join(map(str, c)) for c in missing),
    }


def case2_morioka():
    """② 모리오카 3R — **마감 전 동결값이 마감 후에 변형되지 않았는가.**

    왜 이 기대값이 정답인가(외부 앵커):
      카톡 원문(T-5)의 조합이 최종 확정 명단에 남아 있어야 한다.
      추가로 A(마감 확정)와 B(마감 후 재분석)의 유력마가 같아야 '변형 없음'이다.
      ⚠ 이 경주는 2026-07-28 에 보호 **조건** 결함이 수정된 건이라 A 는 정상일 것으로 본다.
        그럼에도 B 가 다르면 **표시 계층은 여전히 뚫려 있다**는 뜻이다.
    """
    d = _fx("morioka_3r")
    sent = set()
    for a in d["kakaoAnchors"]:
        if a.get("phase") == "T-5":
            sent |= _combos_from_text(a.get("text"))
    final = _norm((d["A_확정"].get("displayedCombos") or {}).get("quinellas"))
    kh_a, kh_b = d["A_확정"].get("keyHorses"), d["B_마감후"].get("keyHorses")
    ok = (not (sent - final)) and (kh_a == kh_b)
    return ok, {
        "T-5 카톡 조합": sorted("+".join(map(str, c)) for c in sent),
        "최종 확정 명단": sorted("+".join(map(str, c)) for c in final),
        "유력마 A(확정)": kh_a, "유력마 B(마감후)": kh_b,
        "유력마 변형됨": kh_a != kh_b,
    }


def case3_nagoya3():
    """③ 나고야 3R — **경주 등급이 마감 시점 값 그대로인가.**

    왜 이 기대값이 정답인가:
      등급은 `신호 개수`와 `확신도`로 정해진다. 같은 파일 안에 **그 근거가 함께 저장**돼 있으므로
      등급 표기(`raceGradeBasis`)의 신호 수와 실제 저장된 신호 수(`strongSignalsCount`)가
      **어긋나면 둘 중 하나는 마감 후 값**이다 — 즉 동결이 깨진 증거다.
      추가로 동결이 걸렸다면 `locked` 플래그가 남아야 하는데 실측 **0건/805** 이었다.
    """
    d = _fx("nagoya_3r")
    a = d["A_확정"]
    m = re.search(r"신호\s*(\d+)", str(a.get("raceGradeBasis") or ""))
    basis_n = int(m.group(1)) if m else None
    saved_n = a.get("strongSignalsCount")
    consistent = (basis_n is not None and saved_n is not None and basis_n == saved_n)
    locked = bool((a.get("displayedCombos") or {}).get("locked")) or bool(a.get("gradeLocked"))
    return (consistent and locked), {
        "등급": a.get("raceGrade"), "등급 근거(표기)": a.get("raceGradeBasis"),
        "표기된 신호 수": basis_n, "실제 저장된 신호 수": saved_n,
        "근거 일치": consistent, "동결 플래그(locked)": locked,
    }


def case4_nagoya9():
    """④ 나고야 9R — **회원이 받은 조합과 채점 대상이 같은가.** (잠정 기대값)

    왜 이 기대값이 정답인가(외부 앵커):
      회원은 T-7·T-5 카톡으로 **`1+12` 하나만** 받았다(원문 확인). 그런데 적중 판정 명단은
      `2+11`·`1+12` **2조합**이다. 회원이 사지 않은 조합으로 채점되면 성적이 부풀려진다.
      → **집합이 정확히 같아야** 통과다(포함이 아니라 일치).
    ⚠ **잠정**: 작업 1(발송 앵커 `data/kakao_sent/`)이 쌓이기 전이라
      `kakao_queue.jsonl`(sent 여부 불명) 원문을 기준으로 삼았다.
      앵커가 쌓이면 그것으로 교체하고 '잠정' 표기를 뗀다.
    """
    d = _fx("nagoya_9r")
    sent = set()
    for a in d["kakaoAnchors"]:
        sent |= _combos_from_text(a.get("text"))
    final = _norm((d["A_확정"].get("displayedCombos") or {}).get("quinellas"))
    return (sent == final), {
        "카톡으로 받은 조합": sorted("+".join(map(str, c)) for c in sent),
        "채점 대상 명단": sorted("+".join(map(str, c)) for c in final),
        "회원이 못 받은 채점 조합": sorted("+".join(map(str, c)) for c in (final - sent)),
        "받았는데 빠진 조합": sorted("+".join(map(str, c)) for c in (sent - final)),
    }


CASES = [("① 카나자와 7R", "카톡 발송 조합이 최종 저장값에 남아 있는가", case1_kanazawa),
         ("② 모리오카 3R", "마감 전 동결값이 마감 후 변형되지 않았는가", case2_morioka),
         ("③ 나고야 3R", "경주 등급이 마감 시점 값 그대로인가", case3_nagoya3),
         ("④ 나고야 9R", "회원이 받은 조합과 채점 대상이 같은가(잠정)", case4_nagoya9)]


def run():
    rows = []
    for name, what, fn in CASES:
        try:
            ok, detail = fn()
            err = None
        except Exception as e:
            ok, detail, err = False, {}, "%s: %s" % (type(e).__name__, e)
        rows.append({"case": name, "what": what, "pass": bool(ok),
                     "expectedFail": name in EXPECTED_FAIL, "detail": detail, "error": err})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    rows = run()
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0
    print("=" * 78)
    print("마감 시점 동결 회귀 테스트 — ⚠ 지금은 '실패'가 정답이다(동결 미구현)")
    print("=" * 78)
    surprises = []
    for r in rows:
        mark = "✅ 통과" if r["pass"] else "❌ 실패"
        note = ""
        if r["expectedFail"]:
            note = "  ← 예견된 실패(정상)" if not r["pass"] else "  🔴 예상 밖 통과 — 기대값이 느슨하다"
            if r["pass"]:
                surprises.append(r["case"])
        print("\n%s  %s" % (mark, r["case"]) + note)
        print("   확인: %s" % r["what"])
        if r["error"]:
            print("   ⚠ 오류: %s" % r["error"])
        for k, v in (r["detail"] or {}).items():
            print("     · %-22s %s" % (k, v))
    npass = sum(1 for r in rows if r["pass"])
    print("\n" + "=" * 78)
    print("통과 %d / 실패 %d  (예견된 실패 %d건)"
          % (npass, len(rows) - npass, sum(1 for r in rows if r["expectedFail"])))
    if surprises:
        print("🔴 예상 밖 통과: %s → 기대값 재설계 필요" % ", ".join(surprises))
    print("=" * 78)
    return 0        # expected_fail 단계이므로 실패해도 exit 0 (커밋 차단하지 않음)


if __name__ == "__main__":
    sys.exit(main())
