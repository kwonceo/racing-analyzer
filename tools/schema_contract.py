# -*- coding: utf-8 -*-
"""[schema contract test] 파서가 만든 키가 저장행에서 **조용히 사라지는 것**을 막는다.

🔴 왜 필요한가 (CLAUDE.md 2026-07-30 설계안)
  같은 유형의 소실이 네 번 반복됐다 — `distance`·`surface`·`trackCond` / `corners` 계열 /
  `kimarite` / `declaredStyle`. 전부 **파서는 뽑는데 저장행에서 빠지고, 예외가 안 나서 아무도 몰랐다.**
  ⇒ 파서 생성 키 ↔ 저장행 키의 **차집합**을 계약으로 고정하고, 계약에 없는 신규 탈락이 생기면 실패시킨다.

🔴 「폐기」에는 **사유가 필수**다(권대표 지시 2026-07-30).
  사유 없이 폐기로 적으면 나중에 **「실수인지 의도인지」 구분이 안 된다** — 네 번의 소실이 정확히
  그 상태에서 생겼다. 사유가 비면 **계약 파일 자체가 테스트 실패**다.
  · `폐기: True`  = 의도적 제외(중복·재구성 가능·다른 곳에 저장됨)
  · `폐기: False` = **알고 있는 미배선** — 통과시키되 아래 `pending()` 목록에 노출한다

⚠ 이 파일은 **읽기 전용 검사**다. app.py 를 수정하지 않는다.
실행: python tools/schema_contract.py      (위반이 있으면 rc=1)
"""
import ast
import io
import json
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(BASE, "app.py")

CONTRACT = {
    "_keiba_starter_store_row": {
        "파서": "_keiba_parse_shutsuba",
        "설명": "경마 出走表 파서 → starters_store 말 행",
        "필수": ["no", "name", "totalScore", "recentPlacings", "styleType",
                 "corners", "fieldSizes", "pastDistances", "last3fList", "pastPlacings"],
        "선택": ["jockey", "grade", "bodyWeight", "distAptitude", "jockeyRate",
                 "weight", "sexAge", "winOdds", "pop", "career", "detail",
                 "lineageNb", "pastGrades", "pastPops", "bodyWeightBonus",
                 "distAptitudeRate", "jockeyDistRate", "pastClassGrades", "pastClassPlacings"],
        "폐기": {
            "detailUrl":  {"폐기": True,  "사유": "재조회용 링크 · 경주 종료 후 무효라 보존 가치 없음"},
            "venue":      {"폐기": True,  "사유": "raceKey·파일명에 이미 포함(중복)"},
            "raceNo":     {"폐기": True,  "사유": "raceKey 에 이미 포함(중복)"},
            "nameSrc":    {"폐기": True,  "사유": "마명 출처 태그 — 진단용이고 점수·판정에 안 쓰인다"},
            "surface":    {"폐기": True,  "사유": "말 행이 아니라 **경주 최상위**에 저장된다(2026-08-24 D4 확인 · 보유 100%)"},
            "trackCond":  {"폐기": True,  "사유": "동상 — 경주 최상위 저장"},
            "raceGrade":  {"폐기": True,  "사유": "경주 단위 값 — 말 행에 담을 것이 아니다"},
            "horses":     {"폐기": True,  "사유": "파서의 반환 컨테이너 키(말 행이 아니다)"},
            "other":      {"폐기": True,  "사유": "동상 — 파서 내부 분기 키"},
            "win":        {"폐기": True,  "사유": "동상 — 단승표 컨테이너"},
            "second":     {"폐기": True,  "사유": "동상 — 결과 파싱용"},
            "third":      {"폐기": True,  "사유": "동상 — 결과 파싱용"},
        },
    },
    "_nar_parse_deba": {
        "파서": None,
        "설명": "南関東 DebaTable 파서(자체 반환)",
        "필수": ["no", "name"],
        "선택": ["jockey", "recentPlacings", "corners", "last3fList",
                 "pastDistances", "bodyWeight", "fieldSizes"],
        "폐기": {},
    },
}

# 🔴 「알고 있는 미배선」 — 폐기가 아니다. 통과시키되 매일 목록에 노출한다.
#   D4(스키마 드리프트)가 세는 것과 같은 축이다. 값이 채워지면 여기서 뺀다.
PENDING = {
    "winOdds": "저장행엔 있는데 **값이 안 채워진다**(D4 보유율 미달) — 발주 시점 인기, 종료 후 소실",
    "pop":     "동상",
    "weight":  "동상 — 부담중량",
    "rank":    "미배선 — 점수 분해(D5). 공식이 바뀌면 과거 재현이 불가해진다",
    "baseScore": "동상",
}


def _keys(node):
    """함수 안에서 쓰인 문자열 키 전부(dict 리터럴 · 첨자 · .get)."""
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Dict):
            for k in n.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    out.add(k.value)
        elif isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Constant) \
                and isinstance(n.slice.value, str):
            out.add(n.slice.value)
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "get":
            if n.args and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str):
                out.add(n.args[0].value)
    return out


def _funcs():
    tree = ast.parse(io.open(APP, encoding="utf-8").read())
    return {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}


def check():
    """계약 위반 목록을 돌려준다. 빈 리스트면 통과."""
    bad = []
    fs = _funcs()
    for store, spec in CONTRACT.items():
        node = fs.get(store)
        if node is None:
            bad.append("저장행 함수 없음: %s" % store)
            continue
        skeys = _keys(node)
        # ① 필수 키가 저장행에 있나
        for k in spec["필수"]:
            if k not in skeys:
                bad.append("🔴 필수 키 누락: %s.%s" % (store, k))
        # ② 폐기 항목에 사유가 있나
        for k, meta in (spec.get("폐기") or {}).items():
            if not str(meta.get("사유") or "").strip():
                bad.append("🔴 폐기 사유 없음: %s.%s (사유 없이 폐기로 적지 않는다)" % (store, k))
        # ③ 파서에만 있고 저장행에 없는 **신규** 탈락
        pf = spec.get("파서")
        if pf and fs.get(pf) is not None:
            known = set(spec["필수"]) | set(spec["선택"]) | set((spec.get("폐기") or {}).keys())
            for k in sorted(_keys(fs[pf]) - skeys):
                if k not in known:
                    bad.append("🔴 신규 탈락(계약에 없음): %s → %s 에서 사라짐" % (pf, k))
    return bad


def pending():
    """「알고 있는 미배선」 목록 — 통과는 시키되 숨기지 않는다."""
    return dict(PENDING)


def main():
    bad = check()
    print("=" * 78)
    print("schema contract test — 계약 대상 %d개" % len(CONTRACT))
    print("=" * 78)
    if bad:
        for b in bad:
            print("  " + b)
    else:
        print("  🟢 위반 없음")
    p = pending()
    print("\n  ⚠ 알고 있는 미배선 %d건(통과시키되 숨기지 않는다):" % len(p))
    for k, v in p.items():
        print("     %-12s %s" % (k, v))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
