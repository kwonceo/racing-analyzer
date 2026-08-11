# -*- coding: utf-8 -*-
"""[코드맵 자동 생성 (2026-07-30 신설)] AST 로 `app.py` 구조를 기계 추출 → `docs/codemap/`.

■ 왜 만드나
  2026-07-30 사고 6건이 전부 "28,000줄 중 어디에 무엇이 있는지 몰라서" 났다
  (`key_horses` 스코프 오판 · `os.replace` 락 누락 18곳 · 빈값 폴백 62곳 ·
   CLAUDE.md 함수명 오기로 12시간 소모 · 1623↔1780 상충).
■ 원칙 — 자동/수동 분리
  · **코드가 진실인 것**(무엇이 어디에) → 이 스크립트가 자동 생성. 손으로 쓰지 않는다.
  · **사람이 진실인 것**(왜 그렇게 했나 · 무엇을 기각했나) → CLAUDE.md 수동 기록.
  손으로 쓴 구조 문서는 반드시 낡는다(`_scenario_combos` 오기가 증거).
■ ⚠ 한 줄 설명은 **코드에서 기계적으로만** 뽑는다. 지어내면 그것도 낡는다.
■ 완전 읽기 전용 — 소스를 수정하지 않는다.

사용: python tools/build_codemap.py [--check] [--json]
      --check : 재생성하지 않고 신선도만 판정(exit 0=최신, 1=낡음)
"""
import argparse
import ast
import hashlib
import json
import os
import re
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "docs", "codemap")
STATE = os.path.join(OUT, ".codemap_state.json")
SOURCES = ["app.py", "gemini_reviewer.py", "review_engine.py",
           "tools/health_check.py", "tools/dedupe_odds_snapshots.py"]

SECTIONS = [("01_수집", "수집 — fetch·파서·수집 진입점"),
            ("02_저장", "저장 — 저장 함수·tmp 패턴·락"),
            ("03_분석", "분석 — 점수 계산·추천 산출"),
            ("04_필터", "필터 — 게이트·면제·상한"),
            ("05_출력", "출력 — 화면 API·카카오"),
            ("06_검증", "검증 — 테스트·가드·체크리스트"),
            ("99_미분류", "미분류 — 분류 규칙에 걸리지 않은 함수")]

# ── 분류 규칙 ──────────────────────────────────────────────────────────
#  🔴 [수정 1 반영 2026-07-30] **이름 기반 판정을 본문 기반보다 먼저** 적용한다.
#     종전 설계(본문에 os.replace/json.dump 가 있으면 02_저장)는 `_triple_analyze` 처럼
#     **본문에서 저장을 호출할 뿐인 분석 총괄 함수를 저장 섹션으로 보낸다.**
#     분석의 심장이 '저장' 서랍에 들어가면 지도를 읽을 수 없다.
#     → ① 데코레이터 → ② 이름 → ③ 본문(단 **직접 `os.replace` 호출만**) 순서.
NAME_RULES = [
    ("05_출력", r"(kakao|_page$|_api$|notify|send_)"),
    ("06_검증", r"(^check|^test|_test$|guard|_review|audit|health|verify|sanity)"),
    ("02_저장", r"(_save$|_save_|_append$|_record$|_record_|_log$|_atomic|_store_|_persist|_backup)"),
    ("01_수집", r"(_fetch|_parse|_collect|_ingest|_url$|_urls$|_scrape|_load$|_read)"),
    ("04_필터", r"(_filter|_exempt|_gate|_cut$|_suspect|_norm$|_dedupe|_prune|_limit|_cap$)"),
    ("03_분석", r"(_analyze|_score|_picks|_strategy|_confidence|_grade|_rank|_predict|_learn|_stats)"),
]


def _src_line(lines, node):
    return lines[node.lineno - 1] if 0 < node.lineno <= len(lines) else ""


def _oneline(node, lines):
    """한 줄 설명 — ①docstring ②def 위 주석 ③함수 안 첫 주석 ④없음(그대로 노출).
    ⚠ AI 가 지어내지 않는다. 없으면 '설명 없음'을 남겨 개선 대상으로 드러낸다."""
    doc = ast.get_docstring(node)
    if doc:
        first = doc.strip().split("\n")[0].strip()
        if first:
            return first[:150], "docstring"
    i = node.lineno - 2
    buf = []
    while i >= 0:
        s = lines[i].strip()
        if s.startswith("#"):
            buf.append(s.lstrip("#").strip())
            i -= 1
            continue
        break
    if buf:
        return buf[-1][:150], "주석(def 위)"
    for j in range(node.lineno, min(node.end_lineno or node.lineno, node.lineno + 6)):
        if j < len(lines) and lines[j].strip().startswith("#"):
            return lines[j].strip().lstrip("#").strip()[:150], "주석(본문)"
    return "⚠ 설명 없음 (docstring 추가 필요)", None


def _sig(node):
    a = node.args
    parts = [x.arg for x in a.args]
    if a.vararg:
        parts.append("*" + a.vararg.arg)
    parts += [x.arg for x in a.kwonlyargs]
    if a.kwarg:
        parts.append("**" + a.kwarg.arg)
    return "%s(%s)" % (node.name, ", ".join(parts))


def _decorators(node, lines):
    return [_src_line(lines, d).strip() for d in node.decorator_list]


def _calls(node):
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name):
                out.add(f.id)
            elif isinstance(f, ast.Attribute):
                base = f.value.id if isinstance(f.value, ast.Name) else ""
                out.add((base + "." + f.attr) if base else f.attr)
    return out


def classify(fn, lines):
    """① 데코레이터 → ② 이름 → ③ 본문(직접 os.replace 만). 첫 매치에서 확정."""
    decs = " ".join(_decorators(fn, lines))
    if "@app.route" in decs:
        return "05_출력", "데코레이터 @app.route"
    for sec, pat in NAME_RULES:
        if re.search(pat, fn.name):
            return sec, "이름 규칙 %s" % pat
    # ③ 본문 — ⚠ '직접 os.replace 호출'만. json.dump·_json_atomic 호출은 제외(수정 1).
    for n in ast.walk(fn):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                and n.func.attr == "replace" and isinstance(n.func.value, ast.Name) \
                and n.func.value.id == "os":
            return "02_저장", "본문에서 os.replace 직접 호출"
    return "99_미분류", "규칙 미매치"


def collect(rel):
    path = os.path.join(BASE, rel)
    if not os.path.exists(path):
        return [], []
    src = open(path, encoding="utf-8").read()
    lines = src.split("\n")
    tree = ast.parse(src)
    funcs = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            desc, how = _oneline(node, lines)
            sec, why = classify(node, lines)
            funcs.append({"file": rel, "name": node.name, "line": node.lineno,
                          "end": node.end_lineno, "sig": _sig(node),
                          "desc": desc, "descFrom": how, "section": sec,
                          "why": why, "calls": sorted(_calls(node)),
                          "decorators": _decorators(node, lines)})
    return funcs, lines


# ══════════════ 위험목록 룰 ══════════════
DANGER_HEADERS = {
    "①": "⚠ 오늘 사고: 7/30 14:09 와카야마 8R — 스냅샷 20틱 + archive 23건이 1틱/10건으로 초기화. "
         "`except: doc={\"snapshots\":[]}` 뒤 그대로 저장했다.",
    "②": "⚠ 오늘 사고: 단일 프로세스 9분에 WinError 32/5 26건. tmp 이름이 PID 만 달라 "
         "같은 프로세스의 두 스레드가 같은 tmp 를 공유했다.",
    "③": "⚠ 오늘 사고: winOdds·pop·weight 가 파싱은 되는데 저장행에서 탈락(오늘만 5번째 유형). "
         "⚠ 단 horses[].odds 는 '저장행 탈락'이 아니라 '애초에 미수집'이었다 — 유형을 구분할 것.",
    "④": "⚠ 오늘 사고: _EST_CAL=2.0 이 감으로 박힌 값인데 실측은 2.35(배당대별 2.70↔1.40).",
    "⑤": "⚠ 오늘 사고: _kra_backfill_loop 가 sleep(1200)을 '먼저' 자서 개발 중 리로드로 타이머가 계속 리셋 "
         "→ 7/30 하루 백필 0회 실행(데몬 시작 6회 ↔ 실행 0건).",
    "⑥": "⚠ 오늘 사고: readonly 가 '마감 후 첫 저장'에 걸려 그 저장에 이미 마감 후 값이 들어간 채 굳었다. "
         "즉 readonly 는 두 번째 오염부터 막고 첫 오염은 통과시킨다.",
}


def _key_drift():
    """[③] `tools/audit_key_drift.py` 를 **그대로 재사용**해 파서↔저장행 차집합을 얻는다.

    🔴 로직을 베끼지 않는다 — 같은 규칙을 두 곳에 두면 갈린다(원칙 계열).
    ⚠ 실패하면 사유를 그대로 돌려준다. 빈 목록으로 덮지 않는다.
    """
    try:
        import importlib.util
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audit_key_drift.py")
        if not os.path.exists(p):
            return {"error": "tools/audit_key_drift.py 가 없다"}
        spec = importlib.util.spec_from_file_location("_akd", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        rows = []
        for parser, store, label in getattr(mod, "PAIRS", []):
            try:
                # ⚠ `get_keys` 는 **AST 노드**를 받는다(이름 문자열이 아니다).
                #   실측으로 잡았다: 이름을 넘겨 4쌍 전부 「'str' object has no attribute '_fields'」였다.
                a = mod.get_keys(mod.func_node(parser))
                b = mod.get_keys(mod.func_node(store))
            except Exception as e:
                rows.append({"parser": parser, "store": store,
                             "missing": ["조회 실패: %s" % str(e)[:60]]})
                continue
            if not a:
                continue
            miss = sorted(set(a) - set(b or []))
            if miss:
                rows.append({"parser": parser, "store": store, "missing": miss})
        return {"rows": rows}
    except Exception as e:
        return {"error": str(e)[:160]}


def _is_empty_lit(n):
    if isinstance(n, ast.Dict):
        if not n.keys:
            return True
        return any((isinstance(v, (ast.List, ast.Dict, ast.Set))
                    and not (getattr(v, "elts", None) or getattr(v, "keys", None)))
                   or (isinstance(v, ast.Constant) and v.value is None) for v in n.values)
    if isinstance(n, ast.List):
        return not n.elts
    return False


SAVE_FUNCS = {"_json_atomic", "json.dump", "os.replace", "_triple_save",
              "_multi_store_save", "_starters_save", "_kakao_sent_save"}


def danger_fallback(rel, lines):
    src = "\n".join(lines)
    tree = ast.parse(src)
    out = []
    for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        saves = _calls(fn) & SAVE_FUNCS
        for node in ast.walk(fn):
            if not isinstance(node, ast.Try):
                continue
            body = "\n".join(lines[node.body[0].lineno - 1: node.body[-1].end_lineno])
            if "json.load" not in body:
                continue
            for h in node.handlers:
                for st in h.body:
                    tgt = val = None
                    if isinstance(st, ast.Assign):
                        val, tgt = st.value, ast.unparse(st.targets[0])
                    elif isinstance(st, ast.Return) and st.value is not None:
                        val, tgt = st.value, "<return>"
                    if val is not None and _is_empty_lit(val):
                        hot = bool(saves) and tgt != "<return>"
                        out.append({"file": rel, "line": h.lineno, "func": fn.name,
                                    "target": tgt, "value": ast.unparse(val)[:60],
                                    "level": "🔴 핫경로(같은 함수에서 저장)" if hot else "🟡 반환만",
                                    "saves": sorted(saves)})
    return out


def danger_replace(rel, lines):
    out = []
    for i, l in enumerate(lines, 1):
        if not re.search(r"\bos\.replace\(", l) or l.strip().startswith("#"):
            continue
        ctx = "\n".join(lines[max(0, i - 16):i])
        m = re.findall(r'(?:tmp|_tmp\w*|_rp_path)\s*=\s*(.+)', ctx)
        tmp = (m[-1].strip() if m else "?")
        if ".tmp" not in tmp:
            m2 = re.findall(r'"([^"]*\.tmp[^"]*)"', ctx + l)
            tmp = m2[-1] if m2 else tmp
        out.append({"file": rel, "line": i, "tmp": tmp[:60],
                    "pid": ("getpid" in tmp or "getpid" in ctx),
                    "tid": ("get_ident" in tmp or "get_ident" in ctx),
                    "lock": bool(re.search(r"with\s+\w*(lock|LOCK|_lk)\w*\s*:", ctx)),
                    "retry": bool(re.search(r"for\s+_?i\s+in\s+range", ctx))})
    return out


def danger_sleep_daemon(rel, lines):
    tree = ast.parse("\n".join(lines))
    out = []
    for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        for w in [n for n in ast.walk(fn) if isinstance(n, ast.While)]:
            if not (isinstance(w.test, ast.Constant) and w.test.value is True):
                continue
            body_src = "\n".join(lines[w.lineno - 1: (w.end_lineno or w.lineno)])
            m = re.search(r"time\.sleep\(\s*([^\)]+)\)", body_src)
            if not m:
                continue
            first = None
            for st in w.body:
                s = ast.unparse(st) if hasattr(ast, "unparse") else ""
                if "time.sleep" in s:
                    first = True
                    break
                first = False
            out.append({"file": rel, "func": fn.name, "line": w.lineno,
                        "interval": m.group(1).strip()[:40],
                        "sleepFirst": bool(first),
                        "persistLast": ("last" in body_src and "json" in body_src)})
    return out


def danger_consts(rel, lines):
    tree = ast.parse("\n".join(lines))
    out = []
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for t in node.targets:
            nm = getattr(t, "id", "")
            if not nm or not re.match(r"^_?[A-Z][A-Z0-9_]*$", nm):
                continue
            if not isinstance(node.value, ast.Constant):
                continue
            out.append({"file": rel, "name": nm, "line": node.lineno,
                        "value": repr(node.value.value)[:40]})
    src = "\n".join(lines)
    for c in out:
        c["refs"] = len(re.findall(r"\b%s\b" % re.escape(c["name"]), src)) - 1
    return out


def danger_guards(rel, lines):
    src = "\n".join(lines)
    out = []
    for m in re.finditer(r'^\s*(if\s+.*\b(readonly|afterClose|_corrupt|locked|trioShadow)\b.*):', src, re.M):
        ln = src[:m.start()].count("\n") + 1
        out.append({"file": rel, "line": ln, "cond": m.group(1).strip()[:110]})
    return out


def _hashes():
    h = {}
    for rel in SOURCES:
        p = os.path.join(BASE, rel)
        if os.path.exists(p):
            b = open(p, "rb").read()
            h[rel] = {"sha256": hashlib.sha256(b).hexdigest()[:16], "lines": b.count(b"\n") + 1}
    return h


def check_fresh():
    if not os.path.exists(STATE):
        return False, "코드맵이 아직 생성되지 않았습니다"
    try:
        st = json.load(open(STATE, encoding="utf-8"))
    except Exception as e:
        return False, "상태 파일 손상: %s" % e
    cur = _hashes()
    bad = [k for k in cur if (st.get("sources") or {}).get(k, {}).get("sha256") != cur[k]["sha256"]]
    if bad:
        return False, "변경된 소스: %s" % ", ".join(bad)
    return True, "최신 (생성 %s)" % st.get("generatedAt")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="재생성 없이 신선도만 판정")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.check:
        ok, msg = check_fresh()
        print(("✅ 코드맵 최신 — " if ok else "🔴 코드맵이 낡았습니다 — ") + msg)
        if not ok:
            print("   재생성: python tools/build_codemap.py")
        return 0 if ok else 1

    os.makedirs(OUT, exist_ok=True)
    all_funcs = []
    per_lines = {}
    for rel in SOURCES:
        fs, lines = collect(rel)
        all_funcs += fs
        per_lines[rel] = lines

    # Caller 역인덱스
    byname = {}
    for f in all_funcs:
        byname.setdefault(f["name"], []).append(f)
    callers = {}
    for f in all_funcs:
        for c in f["calls"]:
            callers.setdefault(c, []).append(f)

    # ── 위험목록 (수정 2: 여기 걸린 함수는 Caller 전수 나열) ──
    D = {"①": [], "②": [], "③": [], "④": [], "⑤": [], "⑥": []}
    for rel, lines in per_lines.items():
        if not lines:
            continue
        D["①"] += danger_fallback(rel, lines)
        D["②"] += danger_replace(rel, lines)
        D["④"] += danger_consts(rel, lines)
        D["⑤"] += danger_sleep_daemon(rel, lines)
        D["⑥"] += danger_guards(rel, lines)
    danger_names = {x["func"] for x in D["①"]} | {x["func"] for x in D["⑤"]} | {"_json_atomic"}

    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    # ── 섹션 파일 ──
    for sec, title in SECTIONS:
        fs = sorted([f for f in all_funcs if f["section"] == sec], key=lambda x: (x["file"], x["line"]))
        L = ["# %s" % title, "",
             "> 🤖 **자동 생성** — `python tools/build_codemap.py` · %s" % ts,
             "> ⚠ 손으로 고치지 마세요. 다음 생성 때 덮어써집니다.",
             "> ⚠ **AST 한계**: `getattr()` 동적 호출 · `importlib` 경유 · 문자열 디스패치는 잡지 못합니다",
             ">   (`gemini_reviewer`·`health_check` 가 실제로 `importlib` 경유입니다).", "",
             "총 **%d개** 함수", ""]
        L[-2] = L[-2] % len(fs)
        for f in fs:
            cs = callers.get(f["name"], [])
            full = f["name"] in danger_names          # 수정 2
            names = ["`%s`(%d)" % (c["name"], c["line"]) for c in cs]
            if full or len(names) <= 6:
                cl = " · ".join(names) if names else "—"
            else:
                cl = " · ".join(names[:6]) + " … [+%d]" % (len(names) - 6)
            L += ["### `%s`" % f["sig"],
                  "- **위치**: `%s:%d` (%d~%d)" % (f["file"], f["line"], f["line"], f["end"] or f["line"]),
                  "- **설명**: %s%s" % (f["desc"], "" if not f["descFrom"] else " *(%s)*" % f["descFrom"]),
                  "- **분류 근거**: %s" % f["why"],
                  "- **호출됨(Caller · %d)**: %s%s" % (len(cs), cl,
                                                     "  ⚠ 위험목록 함수 → 전수 나열" if full else ""),
                  ""]
        open(os.path.join(OUT, sec + ".md"), "w", encoding="utf-8").write("\n".join(L))

    # ── 위험목록.md ──
    R = ["# 위험목록 (자동 추출)", "",
         "> 🤖 자동 생성 · %s" % ts,
         "> 🔴 **코드 수정 전 이 파일을 먼저 읽으세요.** 고치려는 함수가 여기 있으면 그 사고 사례부터 확인합니다.", ""]
    R += ["## ① 빈값 폴백 재저장", "", DANGER_HEADERS["①"], "",
          "총 **%d곳** (🔴 핫경로 %d · 🟡 반환만 %d)" % (
              len(D["①"]), sum(1 for x in D["①"] if x["level"].startswith("🔴")),
              sum(1 for x in D["①"] if x["level"].startswith("🟡"))), "",
          "| 파일:줄 | 함수 | 대상 | 기본값 | 등급 |", "|---|---|---|---|---|"]
    for x in sorted(D["①"], key=lambda v: (v["level"], v["file"], v["line"])):
        R.append("| `%s:%d` | `%s` | `%s` | `%s` | %s |"
                 % (x["file"], x["line"], x["func"], x["target"], x["value"], x["level"]))
    R += ["", "## ② os.replace — tmp 이름과 락", "", DANGER_HEADERS["②"], "",
          "총 **%d곳** (락 있음 %d · 없음 %d)" % (
              len(D["②"]), sum(1 for x in D["②"] if x["lock"]), sum(1 for x in D["②"] if not x["lock"])), "",
          "| 파일:줄 | tmp 표현식 | PID | TID | 락 | 재시도 |", "|---|---|---|---|---|---|"]
    for x in D["②"]:
        R.append("| `%s:%d` | `%s` | %s | %s | %s | %s |"
                 % (x["file"], x["line"], x["tmp"], "✅" if x["pid"] else "❌",
                    "✅" if x["tid"] else "❌", "✅" if x["lock"] else "🔴",
                    "✅" if x["retry"] else "—"))
    # 🔴 [③ 실측 적재 (2026-08-12)] 종전에는 "다른 도구를 돌려라"는 안내 한 줄뿐이라
    #   위험목록만 봐서는 **무엇이 빠졌는지 알 수 없었다**(00_INDEX 요약에도 ③이 없었다).
    #   ⇒ `tools/audit_key_drift.py` 를 **그대로 import 해서** 결과를 여기에 싣는다.
    #   ⚠ 로직을 베끼지 않는다 — 같은 규칙을 두 곳에 두면 갈린다.
    #   ⚠ 실패하면 사유를 그대로 남긴다. 빈 목록으로 덮지 않는다(원칙 9).
    R += ["", "## ③ 파서 → 저장행 키 차집합", "", DANGER_HEADERS["③"], ""]
    _d3 = _key_drift()
    if _d3.get("error"):
        R += ["🔴 자동 추출 실패: %s" % _d3["error"],
              "⚠ 수동 확인: `python tools/audit_key_drift.py`", ""]
    elif not _d3.get("rows"):
        R += ["차집합 없음(또는 짝을 찾지 못함).",
              "⚠ 수동 확인: `python tools/audit_key_drift.py`", ""]
    else:
        R += ["총 **%d쌍**" % len(_d3["rows"]), "",
              "| 파서 | 저장행 | 탈락 키 |", "|---|---|---|"]
        for x in _d3["rows"]:
            R.append("| `%s` | `%s` | %s |"
                     % (x["parser"], x["store"],
                        ", ".join("`%s`" % k for k in x["missing"][:12])
                        + (" …(%d개)" % len(x["missing"]) if len(x["missing"]) > 12 else "")))
        R.append("")
    R += ["## ④ 전역 상수", "", DANGER_HEADERS["④"], "",
          "총 **%d개**" % len(D["④"]), "", "| 파일:줄 | 이름 | 값 | 참조 수 |", "|---|---|---|---|"]
    for x in sorted(D["④"], key=lambda v: -v["refs"])[:60]:
        R.append("| `%s:%d` | `%s` | `%s` | %d |" % (x["file"], x["line"], x["name"], x["value"], x["refs"]))
    R += ["", "## ⑤ while True + time.sleep 데몬", "", DANGER_HEADERS["⑤"], "",
          "총 **%d곳**" % len(D["⑤"]), "",
          "| 파일:줄 | 함수 | 주기 | sleep이 먼저? | 마지막 실행 영속화 |", "|---|---|---|---|---|"]
    for x in D["⑤"]:
        R.append("| `%s:%d` | `%s` | `%s` | %s | %s |"
                 % (x["file"], x["line"], x["func"], x["interval"],
                    "🔴 예(리로드마다 리셋)" if x["sleepFirst"] else "아니오",
                    "✅" if x["persistLast"] else "🔴 없음"))
    R += ["", "## ⑥ 데이터 가드 조건", "", DANGER_HEADERS["⑥"], "",
          "총 **%d곳**" % len(D["⑥"]), "", "| 파일:줄 | 조건 |", "|---|---|"]
    for x in D["⑥"][:80]:
        R.append("| `%s:%d` | `%s` |" % (x["file"], x["line"], x["cond"]))
    open(os.path.join(OUT, "위험목록.md"), "w", encoding="utf-8").write("\n".join(R))

    # ── INDEX ──
    nodesc = sum(1 for f in all_funcs if not f["descFrom"])
    # 🔴 [신선도 배너 (2026-08-12)] 소스 sha256 을 직전 상태와 대조해 **낡음을 맨 위에 알린다.**
    #   ⚠ **차단하지 않는다** — 라이브 중 커밋을 막으면 안 된다. 알리기만 한다.
    _stale = []
    try:
        if os.path.exists(STATE):
            _old = (json.load(open(STATE, encoding="utf-8")) or {}).get("sources") or {}
            _now = _hashes()
            for _f, _v in _now.items():
                _o = _old.get(_f) or {}
                if _o and _o.get("sha256") != _v.get("sha256"):
                    _stale.append("%s (%d → %d줄)" % (_f, _o.get("lines") or 0, _v.get("lines") or 0))
    except Exception as _se:
        _stale = ["신선도 대조 실패: %s" % str(_se)[:80]]
    I = ["# 코드맵 색인", ""]
    if _stale:
        I += ["> 🔴 **직전 생성 이후 바뀐 소스 %d개** — 아래 목록은 **이번 실행 기준**이다."
              % len(_stale),
              "> " + " · ".join(_stale[:6]) + (" 외" if len(_stale) > 6 else ""),
              "> ⚠ 차단하지 않는다. 낡았으면 `python tools/build_codemap.py` 를 다시 돌린다.", ""]
    else:
        I += ["> 🟢 직전 생성 이후 소스 변경 없음", ""]
    I += ["> 🤖 자동 생성 · %s" % ts, "",
          "| 섹션 | 함수 수 |", "|---|---|"]
    for sec, title in SECTIONS:
        I.append("| [%s](%s.md) — %s | %d |" % (sec, sec, title.split(" — ")[1],
                                                sum(1 for f in all_funcs if f["section"] == sec)))
    I += ["| [위험목록](위험목록.md) | ① %d · ② %d · ③ %d · ④ %d · ⑤ %d · ⑥ %d |"
          % (len(D["①"]), len(D["②"]), len(_d3.get("rows") or []),
             len(D["④"]), len(D["⑤"]), len(D["⑥"])), "",
          "**총 함수 %d개** · 설명 자동 추출 **%d개 (%.1f%%)** · ⚠ 설명 없음 %d개"
          % (len(all_funcs), len(all_funcs) - nodesc,
             100.0 * (len(all_funcs) - nodesc) / len(all_funcs) if all_funcs else 0, nodesc)]
    open(os.path.join(OUT, "00_INDEX.md"), "w", encoding="utf-8").write("\n".join(I))

    json.dump({"generatedAt": ts, "sources": _hashes(), "funcCount": len(all_funcs)},
              open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    res = {"funcs": len(all_funcs), "noDesc": nodesc,
           "descRate": round(100.0 * (len(all_funcs) - nodesc) / len(all_funcs), 1) if all_funcs else 0,
           "sections": {s: sum(1 for f in all_funcs if f["section"] == s) for s, _ in SECTIONS},
           "danger": {k: len(v) for k, v in D.items()},
           "replaceNoLock": sum(1 for x in D["②"] if not x["lock"]),
           "fallbackHot": sum(1 for x in D["①"] if x["level"].startswith("🔴")),
           "probe": {n: next(((f["section"], f["line"]) for f in all_funcs if f["name"] == n), None)
                     for n in ["_triple_analyze", "_final_picks", "_history_save_analysis", "_json_atomic"]}}
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        print("코드맵 생성 완료 → docs/codemap/")
        print("  함수 %d개 · 설명 추출률 %.1f%% (설명 없음 %d)" % (res["funcs"], res["descRate"], res["noDesc"]))
        print("  섹션:", res["sections"])
        print("  위험:", res["danger"], "· replace 락없음", res["replaceNoLock"], "· 폴백 핫경로", res["fallbackHot"])
        print("  [수정1 확인] 네 함수의 섹션:")
        for k, v in res["probe"].items():
            print("     %-24s %s" % (k, v))
    return 0


if __name__ == "__main__":
    sys.exit(main())
