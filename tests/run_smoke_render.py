# -*- coding: utf-8 -*-
"""[문자열 렌더 스모크 테스트 (2026-07-31 신설)]

■ 왜 필요한가 (🔴 오늘 실제로 걸렸다)
  프롬프트 주석에 `100%)` 를 넣었더니 `%` 포맷과 충돌해
  `ValueError: unsupported format character ')'` 로 **프롬프트 생성이 통째로 죽었다.**
  🔴 **문법 검사(`ast.parse`)로는 절대 안 잡힌다** — 문법은 완벽히 맞고,
     **실행해야만** 드러나는 유형이다.

■ 무엇을 하는가
  `%` 포맷·f-string 을 쓰는 **문자열 생성 함수를 실제로 1회 호출**해 본다.
  ⚠ **API 는 호출하지 않는다.** 문자열이 만들어지는 데까지만 확인한다.

■ ⚠ 이 테스트는 **통과해야 정답이다.** 실패하면 커밋을 막는다.

사용: python tests/run_smoke_render.py [--json]
"""
import argparse
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

# 최소 입력 — 실제 필드가 없어도 렌더 자체는 되어야 한다(빈 값 방어 확인 겸용).
_SNAP = {
    "raceKey": "테스트 1경주", "fieldSize": 7, "wind": None,
    "horses": [{"no": i, "name": "A%d" % i, "gait": "선행" if i < 3 else "추입",
                "recentPlacings": [1, 2, 3]} for i in range(1, 8)],
    "composition": {"leadCount": 2, "leadHorses": [1, 2],
                    "closeCount": 5, "closeHorses": [3, 4, 5, 6, 7]},
    "lines": [], "paceLabel": None, "comment": "",
    "appliedRulesAvailable": [], "_fields_included": [], "_fields_omitted": [],
    "_not_provided": ["날씨", "바람(풍향·풍속)"], "_excluded": ["odds"],
}
_WIND = {"venue": "히라츠카", "풍속": 6.2, "풍향": "남남서", "풍향코드": 9,
         "기온": 27.4, "강수1h": 0.0, "관측시각": "2026-07-31T04:00:00+09:00",
         "지점": "辻堂", "지점거리km": 9.5}
_GRADED = {
    "predicted_top3": [1, 2, 3], "predicted_style": "선행", "predicted_pace": "빠른",
    "confidence": 3, "key_factors": ["a", "b"], "exception_note": "통상",
    "input_snapshot": _SNAP, "graded": True,
    "grading": {"actual": [5, 1, 9], "hit": [1], "hit_count": 1, "missed": [5, 9],
                "missed_info": [{"no": 5, "gait": "추입", "recentPlacings": [1, 3],
                                 "line": "A", "grade": "S"}],
                "payout_quinella": 46.3, "is_high_odds": True},
}


def cases():
    """(이름, 호출가능객체) 목록. 새 렌더 함수가 생기면 여기에 추가한다."""
    out = []
    try:
        import gemini_forecast as G
        out.append(("예측 프롬프트(바람 없음)", lambda: G._build_prompt(_SNAP)))
        _s2 = dict(_SNAP)
        _s2["wind"] = _WIND
        _s2["_not_provided"] = ["날씨"]
        out.append(("예측 프롬프트(바람 있음)", lambda: G._build_prompt(_s2)))
        out.append(("복기 프롬프트", lambda: G._build_review_prompt(_GRADED)))
    except Exception as e:
        out.append(("gemini_forecast import", lambda e=e: (_ for _ in ()).throw(e)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    res = []
    for name, fn in cases():
        try:
            s = fn()
            ok = isinstance(s, str) and len(s) > 200
            res.append({"name": name, "ok": ok,
                        "detail": "%d자" % len(s) if isinstance(s, str) else "문자열 아님"})
        except Exception as e:
            res.append({"name": name, "ok": False,
                        "detail": "%s: %s" % (type(e).__name__, str(e)[:120])})
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        print("=" * 74)
        print("문자열 렌더 스모크 — ⚠ 문법 검사로는 안 잡히는 유형을 잡는다")
        print("=" * 74)
        for r in res:
            print("  %s %-26s %s" % ("✅" if r["ok"] else "❌", r["name"], r["detail"]))
        bad = [r for r in res if not r["ok"]]
        print("=" * 74)
        print("통과 %d / 실패 %d" % (len(res) - len(bad), len(bad)))
    return 0 if all(r["ok"] for r in res) else 1


if __name__ == "__main__":
    sys.exit(main())
