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

■ 🔴 [2026-08-01 확대] `os.listdir` 계열도 본다 — 원칙 17 의 두 번째 실증
  스냅샷 오배정 실사고(`7/31 로 검색했는데 7/14 이미지가 나옴`)를 이 테스트가 못 잡았다.
  이유는 단순하다 — `_snapshot_index()` 는 **`glob` 을 안 쓰고 `os.listdir` 을 쓴다.**
  ⇒ **검사 대상 밖이었다.** "통과"는 "그 방식으로는 안 걸린다"는 뜻일 뿐이었다.
  ⚠ 다만 `os.listdir` 자체는 무해하다. `app.py` 안 70개 함수가 쓰고 있고
    그중 33개가 경주를 다룬다 — **전부 위반으로 잡으면 그것도 영원한 빨간불**이다.
    위험한 것은 디렉터리를 훑으면서 **날짜 없이 특정 경주 1건을 지목**하는 경우뿐이다.

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
# 🔴 [2026-08-01 수정] 종전 정규식은 **주석의 `date` 단어까지 날짜 힌트로 인정**해
#   `_pat = "*_%s.json" % slug   # date-filtered below` 를 통과시켰다.
#   ⇒ 실제 안전성이 아니라 **주석을 재고 있었다.** 어제 배운 원칙 그대로다:
#     "테스트 통과가 곧 정상은 아니다. 테스트가 의도한 것을 재는지 눈으로 확인한다."
#   이제 **코드 부분만**(주석 제거 후) 검사하고, 날짜 힌트도 **실제 포맷 토큰**만 인정한다.
# ⚠ [2026-08-01] `\d{4}_\d{2}_\d{2}` 만 봐서 `"^\d{4}-\d{2}-\d{2}$"`(대시 표기)를 못 알아봤다.
#   날짜로 고친 코드를 계속 위반으로 부르면 그것도 영원한 빨간불이다. 두 표기 다 인정한다.
_DATE_HINT = re.compile(r"%Y|\d{4}[-_]\d{2}[-_]\d{2}|\bymd\b|_pfx|strftime")
_GLOB = re.compile(r"glob\.glob\(|(?<![\w.])glob\(")

# ── 🔴 [2026-08-01 확대] `os.listdir` 경로 (정규식 3개) ────────────────────────
# ① 디렉터리 나열 자체. 이것만으로는 위반이 아니다(전수 스캔은 무해).
_LISTDIR = re.compile(r"os\.(?:listdir|scandir|walk)\(|(?<![\w.])(?:listdir|scandir)\(")
# ② 날짜가 들어있지 않은 **경주 식별자** 변수/필드 이름.
#    `raceKey`("나고야 11경주")·`slug`·`race_id` 는 전부 날짜가 없다 — 이것이 사고의 뿌리다.
_RK_TOKEN = r"(?<![A-Za-z0-9])_?(?:rk|race_?key|race_?id|slug)\b"
# ③ 🔴 그 식별자로 **1건을 지목**하는 신호. 이게 있어야 위반이다.
#    ⚠ 여기가 오탐/미탐의 분기점이다 — `for rk in keys:`(전수 순회)나
#      `keys.add(rk)`(집합 수집)는 특정 경주를 찾는 것이 아니므로 **일부러 뺐다**.
_PICK_ONE = re.compile("|".join([
    r"(?:!=|==)\s*\(?\s*" + _RK_TOKEN,          # meta["raceKey"] != rk
    _RK_TOKEN + r"\s*\)?\s*(?:!=|==)",          # rk == ...
    r"\[\s*" + _RK_TOKEN + r"\s*\]\s*=",        # out[rk] = ...   ← 날짜 없는 조회표를 만든다
    r"\.setdefault\(\s*" + _RK_TOKEN,           # m.setdefault(rk, {})
    r"\.get\(\s*" + _RK_TOKEN,                  # idx.get(rk)
    r"\.startswith\(\s*" + _RK_TOKEN,           # fn.startswith(slug)
    r"(?<!for )" + _RK_TOKEN + r"\s+in\s+",     # rk in fn  (for 루프 순회는 제외)
]), re.IGNORECASE)

# 여기에 넣으면 검사에서 빠진다 — **넣기 전에 이유를 남길 것.**
ALLOW = {
    # (파일, 줄내용 일부): 사유
}

# 🔴 [2026-08-01] 파일 단위 제외 — **화면에 반드시 출력**한다(조용히 빠지면 원칙 17 위반).
#   `tools/_sync_app*.py` 는 2026-07-20 시점 `app.py` 의 **죽은 사본**이다
#   (1.38MB 통짜 복사본 · `import` 하는 곳 0곳 · 서버가 로드하지 않음).
#   같은 위반이 app.py 와 중복 계상돼 "진짜 고칠 곳"이 묻힌다.
#   ⚠ 살아 있는 코드는 절대 여기 넣지 말 것.
SKIP_FILES = {
    "tools/_sync_app.py": "app.py 구버전 사본(2026-07-20) · import 0곳",
    "tools/_sync_app3.py": "app.py 구버전 사본(2026-07-20) · import 0곳",
}

# 🔴 [2026-08-01] **승인 대기 기지선(baseline)** — 지우는 목록이 아니라 **갚아야 할 목록**이다.
#   왜 필요한가: `os.listdir` 확대로 위반 5건이 새로 드러났고 그중 2건(스냅샷 카드 경로)은 고쳤다.
#   남은 3건은 **API 계약 변경·판정 경로**라 대표 승인 없이 손대면 안 된다(CLAUDE.md 「승인 필요」).
#   그렇다고 rc=1 로 두면 **커밋이 전부 막혀** 무관한 작업까지 멈춘다.
#   ⇒ 여기 적힌 것만 통과시키고, **여기 없는 위반이 하나라도 생기면 즉시 rc=1** 이다.
#   ⚠ 절대 규칙 3가지:
#     ① 목록은 **매 실행 화면에 전부 출력**한다 — 조용히 빠지는 것은 없다.
#     ② **늘리지 않는다.** 새 위반은 여기 추가하지 말고 코드를 고친다.
#     ③ 해소되면 화면이 "KNOWN 에서 제거할 것"이라고 알린다 — 목록이 줄어드는 것이 진행이다.
KNOWN = {
    ("app.py", "_snapshot_metas_for_race"):
        "승인대기 · /api/snapshot/compare?raceKey= 가 날짜를 안 받는다(회원 공개 API 계약 변경)",
    ("app.py", "_rr_load_by_key"):
        "승인대기 · race_results 결과·판정 데이터 경로 → 추천에 닿는다",
    ("app.py", "_gemini_latest_map"):
        "승인대기 · {raceKey: 리뷰} 조회표 · 날짜 키 전환 시 카드·목록 동시 영향",
}


def targets():
    out = [os.path.join(BASE, f) for f in SCAN if os.path.exists(os.path.join(BASE, f))]
    for d in SCAN_DIRS:
        out += sorted(_glob.glob(os.path.join(BASE, d, "*.py")))
    return out


def scan():
    """🔴 [2026-08-01 재설계] **함수 단위**로 본다.

    종전은 한 줄 단위라 `_pat = "*_%s.json" % slug` / `glob.glob(_pat)` 로
    **변수를 분리하면 그대로 빠져나갔다**(`regrade_market.py` 가 실제로 그랬다).
    ⇒ 함수 본문 전체에서 `glob` 호출 + 경주 힌트 + 와일드카드가 **함께** 나오면
      날짜 힌트가 있는지 본다. 주석은 미리 제거한다.
    """
    import ast as _ast
    bad = []
    for p in targets():
        rel = os.path.relpath(p, BASE).replace("\\", "/")
        if rel == "tests/run_glob_safety.py":
            continue                                    # 자기 자신(설명 문자열)
        if rel in SKIP_FILES:
            continue                                    # ⚠ 사유는 SKIP_FILES 참조 · 화면에 출력됨
        try:
            src = open(p, encoding="utf-8", errors="replace").read()
            tree = _ast.parse(src)
        except Exception:
            continue
        lines = src.split("\n")
        # 🔴 [제외 범위 좁힘 (2026-08-01)] 종전에는 `run_selfcheck.py` 를 **파일 통째로** 제외했다.
        #   그러면 그 파일에 **진짜 위험한 코드**가 들어가도 영영 안 잡힌다.
        #   ⇒ `# SELFCHECK-INJECT-BEGIN/END` 마커 사이 **주입 블록만** 비운다.
        #   ⚠ 원칙 17 계열 — **제외 목록이 테스트를 무력화하지 않는지** 본다.
        _mask = False
        for _i, _l in enumerate(lines):
            if "SELFCHECK-INJECT-BEGIN" in _l:
                _mask = True
            elif "SELFCHECK-INJECT-END" in _l:
                _mask = False
            elif _mask:
                lines[_i] = ""                          # 주입 블록만 지운다
        for fn in _ast.walk(tree):
            if not isinstance(fn, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                continue
            a, b = fn.lineno - 1, (fn.end_lineno or fn.lineno)
            body = "\n".join(re.sub(r"#.*$", "", x) for x in lines[a:b])
            if any(k[0] == rel and k[1] in body for k in ALLOW):
                continue

            # ── ⓐ glob 경로 (종전) ─────────────────────────────────────────
            if _GLOB.search(body):
                # 🔴 [오탐 방지] `glob("*.json")` 같은 **전수 스캔은 무해**하다.
                #   위험한 것은 파일명에 **변수를 끼워 넣어 특정 경주를 지목**하는 패턴이다.
                #   → `"*_%s..." % x` 또는 `f"*_{x}..."` 처럼 **와일드카드 + 포맷**이 함께 있어야 한다.
                _named = re.search(r'["\'][^"\']*\*_[^"\']*["\']\s*%|f["\'][^"\']*\*_\{', body)
                if _named and _RACE_HINT.search(body) and not _DATE_HINT.search(body):
                    first = next((a + 1 + i for i, x in enumerate(lines[a:b]) if _GLOB.search(x)),
                                 fn.lineno)
                    bad.append({"kind": "glob", "file": rel, "line": first, "func": fn.name,
                                "code": lines[first - 1].strip()[:110]})
                    continue                             # 같은 함수를 두 번 세지 않는다

            # ── ⓑ 🔴 os.listdir 경로 (2026-08-01 확대) ──────────────────────
            #   ⚠ `os.listdir` 이 있다고 위반이 아니다. 디렉터리 전수 스캔(`*_list` 목록 API 등)은
            #     **무해**하며 실제로 대부분이 그것이다. 세 조건이 **동시에** 성립해야 위반이다:
            #       ① 디렉터리를 나열하고  ② 날짜 없는 경주 식별자로 1건을 지목하며
            #       ③ 어디에도 날짜 힌트가 없다
            if _LISTDIR.search(body):
                _pick = _PICK_ONE.search(body)
                if _pick and not _DATE_HINT.search(body):
                    first = next((a + 1 + i for i, x in enumerate(lines[a:b]) if _LISTDIR.search(x)),
                                 fn.lineno)
                    bad.append({"kind": "listdir", "file": rel, "line": first, "func": fn.name,
                                "code": lines[first - 1].strip()[:110],
                                "pick": _pick.group(0).strip()[:40]})
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    bad = scan()
    n = len(targets())
    # 🔴 기지선 분리 — `known` 은 갚아야 할 빚(승인 대기), `new` 만 커밋을 막는다.
    known = [b for b in bad if (b["file"], b["func"]) in KNOWN]
    new = [b for b in bad if (b["file"], b["func"]) not in KNOWN]
    fixed = [k for k in KNOWN if k not in {(b["file"], b["func"]) for b in bad}]
    if a.json:
        print(json.dumps({"scanned": n, "violations": bad, "new": new,
                          "known": known, "knownFixed": ["%s %s()" % k for k in fixed]},
                         ensure_ascii=False, indent=1))
        return 1 if new else 0
    print("=" * 78)
    print("날짜 없는 glob 매칭 검사 (원칙 16)  ⚠ 통과가 정답이다")
    print("=" * 78)
    print("검사 대상 %d파일 (app.py 포함 · tools/ · tests/ 자동)" % n)
    print("검사 방식 2종: glob 패턴 · os.listdir/scandir/walk + 날짜 없는 경주 지목")
    for _f, _why in sorted(SKIP_FILES.items()):
        print("  ⏭ 제외 %-24s — %s" % (_f, _why))

    # ── 🔴 승인 대기 기지선 — **항상 전부 출력한다**(조용히 빠지는 것 없음) ──
    if KNOWN:
        print("\n📌 승인 대기 %d건 — **갚아야 할 빚**이다(늘리지 말 것 · 줄어드는 것이 진행)"
              % len(KNOWN))
        for (f, fn), why in sorted(KNOWN.items()):
            state = "✅ 해소됨 → KNOWN 에서 제거할 것" if (f, fn) in fixed else "⏳ 미해소"
            print("   %s  %s %s()" % (state, f, fn))
            print("        %s" % why)

    def _dump(title, rows):
        _g = [x for x in rows if x.get("kind") != "listdir"]
        _l = [x for x in rows if x.get("kind") == "listdir"]
        print("\n%s %d건 (glob %d · listdir %d)" % (title, len(rows), len(_g), len(_l)))
        for b in rows:
            print("   [%s] %s:%d  %s()%s"
                  % (b.get("kind", "glob"), b["file"], b["line"], b["func"],
                     ("   지목=" + b["pick"]) if b.get("pick") else ""))
            print("     %s" % b["code"])

    if known:
        _dump("⏳ 기지선 위반(승인 대기 · 커밋 차단 안 함)", known)
    if not new:
        print("\n✅ **신규 위반 0건**")
        print("   ⚠ `glob(\"*.json\")` · `os.listdir` 전수 스캔은 특정 경주 매칭이 아니므로 검사하지 않는다.")
        if known:
            print("   ⚠ 위 승인 대기 %d건은 **아직 남아 있다** — 통과가 '전부 안전'을 뜻하지 않는다." % len(known))
        return 0
    _dump("🔴 신규 위반 — 특정 경주를 날짜 없이 매칭한다", new)
    print("\n   ⇒ 파일명에 `YYYY_MM_DD_` 접두사를 포함하도록 고칠 것.")
    print("      같은 경기장이 여러 날 개최되므로 다른 날 데이터가 섞인다.")
    print("   ⇒ listdir 유형은 **조회 키에 날짜를 넣는 것**이 정석이다(`YYYY-MM-DD|raceKey`).")
    print("   ⚠ KNOWN 에 추가해서 넘기지 말 것 — 그건 완료선을 사후에 낮추는 것이다.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
