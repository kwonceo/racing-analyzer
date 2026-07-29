# -*- coding: utf-8 -*-
"""[읽기전용] 파서 출력 키 ↔ 저장행 키 자동 대조 — '파싱은 되는데 저장에서 탈락'하는 필드 전수 탐지.

배경: 같은 유형의 소실이 4번 나왔다(distance/surface/trackCond · corners 계열 · kimarite · declaredStyle).
      전부 파서는 뽑는데 저장행 구성에서 빠지고, **에러가 안 나서 아무도 모른다.**
방법: app.py 를 AST 로 읽어 ⓐ파서 함수가 만드는 dict 리터럴 키 ⓑ저장행 함수가 만드는 dict 리터럴 키를
      추출해 차집합을 낸다. 실행하지 않으므로 부작용 없음.
"""
import ast, os, collections

BASE = r"C:\Users\Administrator\Desktop\경마분석서버"
SRC = open(os.path.join(BASE, "app.py"), encoding="utf-8").read()
TREE = ast.parse(SRC)

# (파서 함수, 저장행/소비 함수, 라벨)
PAIRS = [
    ("_keiba_parse_shutsuba", "_keiba_starter_store_row", "경마 출주표(oddspark) → 전적 저장행"),
    ("_keiba_build_form", "_keiba_starter_store_row", "경마 전적 산출 → 전적 저장행"),
    ("_nar_parse_deba", "_keiba_starter_store_row", "南関東 DebaTable → 전적 저장행"),
    ("_keirin_parse_card", "_keirin_analyze", "경륜 출마표 → 분석"),
]


def func_node(name):
    for n in ast.walk(TREE):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    return None


def dict_keys_in(node):
    """함수 안 dict 리터럴의 문자열 키 전부(중첩 포함)."""
    out = collections.Counter()
    if node is None:
        return out
    for n in ast.walk(node):
        if isinstance(n, ast.Dict):
            for k in n.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    out[k.value] += 1
        elif isinstance(n, ast.Call):
            # d["k"] = v / d.setdefault("k", ...) 형태
            if isinstance(n.func, ast.Attribute) and n.func.attr == "setdefault" and n.args:
                a = n.args[0]
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    out[a.value] += 1
        elif isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Constant):
            if isinstance(n.slice.value, str):
                out[n.slice.value] += 1
    return out


def get_keys(node):
    """함수가 .get("x") 로 읽는 키 — 소비 측이 실제로 쓰는 키."""
    out = set()
    if node is None:
        return out
    for n in ast.walk(node):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "get" and n.args):
            a = n.args[0]
            if isinstance(a, ast.Constant) and isinstance(a.value, str):
                out.add(a.value)
    return out


NOISE = {"combo", "odds", "error", "ok", "raceKey", "t", "source", "horses", "riders",
         "quinella", "exacta", "trio", "win", "reason", "type", "text", "time", "no",
         "name", "label", "count", "status", "detail", "date", "race", "sport", "category"}

print("=" * 78)
print("파서 출력 ↔ 저장/소비 키 차집합 (탈락 후보)")
print("=" * 78)
for pf, sf, label in PAIRS:
    pn, sn = func_node(pf), func_node(sf)
    if pn is None or sn is None:
        print("\n[%s] ⚠ 함수 미발견: %s / %s" % (label, pf if pn is None else "", sf if sn is None else ""))
        continue
    produced = set(dict_keys_in(pn))
    consumed = set(dict_keys_in(sn)) | get_keys(sn)
    lost = sorted(k for k in (produced - consumed) if k not in NOISE and not k.startswith("_"))
    print("\n[%s]" % label)
    print("  %s 생성 키 %d · %s 소비/저장 키 %d" % (pf, len(produced), sf, len(consumed)))
    print("  ⚠ 탈락 후보 %d개: %s" % (len(lost), lost if lost else "없음"))

print("\n" + "=" * 78)
print("경륜: 파서 → analyze → 저장행 3단 추적")
print("=" * 78)
card = set(dict_keys_in(func_node("_keirin_parse_card")))
anal = set(dict_keys_in(func_node("_keirin_analyze"))) | get_keys(func_node("_keirin_analyze"))
# 경륜 저장행은 _keirin_autocollect_form 안 dict comprehension
auto = func_node("_keirin_autocollect_form")
store = set(dict_keys_in(auto)) | get_keys(auto)
print("  card %d키 · analyze %d키 · autocollect(저장) %d키" % (len(card), len(anal), len(store)))
lost2 = sorted(k for k in (card - store) if k not in NOISE and not k.startswith("_"))
print("  ⚠ card 에서 저장까지 못 간 키 %d개: %s" % (len(lost2), lost2))

print("\n" + "=" * 78)
print("실데이터 대조: starters_store 실제 저장 키")
print("=" * 78)
import json
try:
    sd = json.load(open(os.path.join(BASE, "starters_store.json"), encoding="utf-8"))
    for src in ("oddspark", "keirin", "keiba_nar", "korea", "jra"):
        rows = [v for v in sd.values() if v.get("source") == src]
        if not rows:
            print("  %-10s 저장 0건" % src)
            continue
        rec_keys = sorted(set(k for r in rows for k in r.keys()))
        h_keys = sorted(set(k for r in rows for h in (r.get("horses") or [])[:1] for k in h.keys()))
        print("  %-10s %3d경주 · 레코드키 %s" % (src, len(rows), rec_keys))
        print("             말/선수키 %s" % h_keys)
except Exception as e:
    print("  실데이터 확인 실패:", e)
