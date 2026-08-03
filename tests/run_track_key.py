# -*- coding: utf-8 -*-
"""[표준키 diff 대조 (2026-08-01 신설 · 3단계)] — **완전 읽기 전용**.

■ 이 테스트가 하는 일
  `tools/track_key.py`(표준키 모듈)에서 **파생한 목록**과 `app.py` 에 흩어진 **기존 6벌**을 대조해
  **차이를 전부 출력**한다.

■ 🔴 목표는 "diff 0" 이 아니다
  차이에는 두 종류가 있고 **갈라야 한다**:
    · 🔴 **구멍** — 있어야 하는데 없는 것(예: `_JP_TRACKS` 에 JRA 10곳 전무 → 전적 오염 33건)
    · 🟢 **의도적 차이** — 설계상 일부러 뺀 것
      (예: `_KEIRIN_ONLY_RE` 는 **경륜 전용 지명만** 담고 이중소속(코치·나고야·카와사키)은 **일부러 제외**한다.
       그걸 "구멍"이라 부르고 채우면 경마가 경륜으로 오분류된다.)
  ⇒ 이 테스트는 **차이를 드러내는 것**이 목적이다. 아래 `EXPECTED` 에 **사유와 함께** 적힌 차이만
     통과시키고, 그 밖의 차이가 새로 생기면 **실패**한다.

■ 무엇을 막는가
  누군가 새 경기장·새 표기를 추가하면서 **한 목록에만 넣으면** 여기서 잡힌다.
  오늘 네 번 난 사고가 전부 그 유형이었다.

■ ⚠ 이 테스트는 app.py 를 고치지 않는다. 4단계(조회 계층 배선)는 **별도 승인**이다.
"""
import ast
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "tools"))
import track_key as TK   # noqa: E402

APP = os.environ.get("TRACK_KEY_APP") or os.path.join(BASE, "app.py")
SRC = open(APP, encoding="utf-8").read()

# 🔴 **알고 있는 차이**. 반드시 사유를 적는다 — 사유 없는 항목은 아래에서 실패 처리한다.
#   (`폐기: 사유 필수` 규약과 같은 취지 — "실수인지 의도인지" 구분이 안 되면 그게 다음 사고다.)
EXPECTED = {
    ("_KEIRIN_ONLY_RE", "코치"): "이중소속(경마장+경륜장) — 일부러 제외. 넣으면 지방경마가 경륜으로 오분류된다",
    ("_KEIRIN_ONLY_RE", "나고야"): "이중소속 — 동상",
    ("_KEIRIN_ONLY_RE", "카와사키"): "이중소속 — 동상",
    ("_KEIRIN_ONLY_RE", "고쿠라"): "이중소속(JRA 고쿠라 + 小倉競輪) — 동상",
    ("_KEIRIN_ONLY_RE", "코쿠라"): "고쿠라 이표기 — 이중소속이라 동일하게 제외",
    ("_KEIRIN_ONLY_RE", "에도가와"): "🔴 경정장(江戸川競艇)이다. 경륜 전용 목록에 넣으면 boat 가 cycle 로 오분류된다",
    # 🔴 아래 4건은 **알고 있는 미보강분**(CLAUDE.md 보류 목록 '_KEIRIN_ONLY_RE 4개 지명 보강').
    #   의도적 제외가 **아니다** — 넣어야 하는데 아직 승인 전이다. 사유에 그렇게 적어 구분한다.
    ("_KEIRIN_ONLY_RE", "호후"): "⏸ 미보강(보류 목록) — 경륜 전용이 맞다. 승인 후 추가 대상",
    ("_KEIRIN_ONLY_RE", "히라츠카"): "⏸ 미보강(보류 목록) — 동상",
    ("_KEIRIN_ONLY_RE", "다케오"): "⏸ 미보강(보류 목록) — 동상",
    ("_KEIRIN_ONLY_RE", "나라"): "⏸ 미보강 — 경륜 전용(奈良競輪) 확인 필요",
    ("_KEIRIN_ONLY_RE", "도리데"): "⏸ 미보강 — 경륜 전용(取手競輪) 확인 필요",
    ("_KEIRIN_ONLY_RE", "마쓰야마"): "⏸ 미보강 — 경륜 전용(松山競輪) 확인 필요",
    # `_JRA_TRACKS` 는 `중경`·`주쿄` 는 담고 있으나 `추쿄` 표기가 없다.
    #   🔴 의도가 아니라 **구멍**이다 — 실데이터에 세 표기가 모두 나타난다(오늘 집계가 갈렸다).
    #   ⚠ 그래서 EXPECTED 에 넣지 않는다. **실패로 남겨 4단계에서 고친다.**
}


def _const(name):
    for n in ast.walk(ast.parse(SRC)):
        if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == name:
            try:
                return ast.literal_eval(n.value)
            except Exception:
                return None
    return None


def _regex_words(name):
    """⚠ `_KEIRIN_ONLY_RE` 는 여러 줄 문자열 연결 + 주석이 섞여 있어 한 줄 정규식으로는 못 읽는다.
    (처음에 그렇게 짰다가 **빈 집합이 나와 33건이 전부 누락으로 잡히는 오탐**을 냈다 — 원칙 8-D 사례.)
    ⇒ 정의 블록 전체를 잘라 **문자열 리터럴 안의 토큰만** 모은다."""
    i = SRC.find(name + " = re.compile(")
    if i < 0:
        return set()
    j = SRC.find("\n_", i + 10)          # 다음 모듈 레벨 정의 직전까지
    block = SRC[i:j if j > 0 else i + 4000]
    words = set()
    for lit in re.findall(r'r"([^"]*)"', block):
        for w in lit.split("|"):
            w = w.strip("()^$ ")
            if w:
                words.add(w)
    return words


def main():
    fails, warns = [], []
    print("=" * 84)
    print("표준키 diff 대조 (3단계)   ⚠ 목표는 'diff 0' 이 아니라 **차이를 드러내는 것**")
    print("=" * 84)

    # [0] 모듈이 얹은 별칭 = app.py 의 구멍
    miss = TK.missing_aliases()
    print("\n[0] app.py `_TRACK_GROUPS` 에 없는 별칭(모듈이 얹은 것) : %d건" % len(miss))
    for std, a in miss:
        print("    🔴 %s ← %s   (app.py 에 넣어야 한다 · 4단계)" % (std, a))
    if miss:
        warns.append("_TRACK_GROUPS 별칭 부족 %d건" % len(miss))

    # [1] _JP_TRACKS ↔ 모듈이 보는 '일본 경마장'(JRA+NAR)
    jp = set(_const("_JP_TRACKS") or ())
    # ⚠ 로마자 별칭(sapporo·obi 등)은 비교 대상이 아니다 — raceKey 는 한글/한자로 저장된다.
    #   처음에 전부 비교해 **45건 오탐**을 냈다. 한글·한자만 본다.
    def _cjk(x):
        return bool(re.search(r"[가-힣぀-ヿ一-鿿]", str(x)))
    want = set()
    for k in list(TK._JRA) + list(TK._NAR):
        if _cjk(k):
            want.add(k)
        for a in (TK._groups().get(k) or []):
            if _cjk(a):
                want.add(a)
    lack = {w for w in want if not any(t in w or w in t for t in jp)}
    print("\n[1] `_JP_TRACKS` — 일본 경마장(JRA+NAR) 표기 중 **누락** : %d건" % len(lack))
    for w in sorted(lack):
        print("    🔴 %s" % w)
    if lack:
        fails.append("_JP_TRACKS 누락 %d건 — 한국 전적 오매칭이 다시 뚫린다" % len(lack))

    # [2] _JRA_TRACKS ↔ 모듈 JRA 10곳
    jra = set(_const("_JRA_TRACKS") or ())
    lack2 = {k for k in TK._JRA if not any(k in t or t in k for t in jra)}
    print("\n[2] `_JRA_TRACKS` — JRA 10곳 중 **누락** : %d건" % len(lack2))
    for w in sorted(lack2):
        print("    🔴 %s" % w)
    if lack2:
        fails.append("_JRA_TRACKS 누락 %d건" % len(lack2))

    # [3] _KEIRIN_ONLY_RE ↔ 모듈이 보는 경륜 표준키(이중소속 제외가 정상)
    kw = _regex_words("_KEIRIN_ONLY_RE")
    keirin = {k for k in TK._groups() if TK.track_region(k) == "KEIRIN_ETC"}
    lack3, expected_hit = [], []
    for k in sorted(keirin):
        alts = {a for a in (set(TK._groups().get(k) or []) | {k})
                if re.search(r"[가-힣぀-ヿ一-鿿]", str(a))}
        if any(a in kw for a in alts):
            continue
        (expected_hit if ("_KEIRIN_ONLY_RE", k) in EXPECTED else lack3).append(k)
    print("\n[3] `_KEIRIN_ONLY_RE` — 경륜 표준키 중 정규식에 없음 : %d건 (알려진 차이 %d건)"
          % (len(lack3), len(expected_hit)))
    for k in expected_hit:
        print("    🟢 %-10s 의도적 차이 — %s" % (k, EXPECTED[("_KEIRIN_ONLY_RE", k)]))
    for k in lack3:
        print("    🔴 %s" % k)
    if lack3:
        warns.append("_KEIRIN_ONLY_RE 미등록 %d건(=종목 오분류 위험)" % len(lack3))

    # [4] 사유 없는 EXPECTED 는 금지
    for key, why in EXPECTED.items():
        if not str(why).strip():
            fails.append("EXPECTED %s 에 사유가 없다 — 실수인지 의도인지 구분 불가" % (key,))

    # ── [5] 🔴 수신 시점 표준화 **오탐률** (2026-08-03 · 원칙 20) ──────────────────
    #   왜: 2026-08-03 에 `triple_ingest` 진입부에서 경기장명을 표준키로 바꾸기 시작했다.
    #     이건 **모든 수집의 입구**라 잘못 바꾸면 전 종목이 엉뚱한 키로 저장된다.
    #   오탐 = "합쳐지면 안 되는 경기장이 합쳐진 것". 실제 저장된 고유 경기장 토큰 전수에
    #     `_track_norm` 을 돌려, **승인된 통합(EXPECTED_MERGE) 밖의 변경**이 있으면 실패다.
    #   ⚠ 이 항목이 0% 가 아니면 rc=1 — "발동은 하는데 오탐률을 안 잰" 상태를 만들지 않는다.
    #   🔴 검사 대상은 `_track_norm` 전체가 아니라 **수신 정규화가 실제로 적용하는 범위**다.
    #     app.py 의 `_INGEST_NORM_ALLOW` 를 **소스에서 직접 읽어** 대조한다 —
    #     목록을 테스트에 따로 적으면 두 벌이 되어 오늘 네 번 본 '목록 갈림' 사고가 재발한다.
    _srcA = open(os.path.join(BASE, "app.py"), encoding="utf-8").read()
    _mAllow = re.search(r"^_INGEST_NORM_ALLOW\s*=\s*\{.*?^\}", _srcA, re.S | re.M)
    _nsA = {}
    exec(_mAllow.group(0), _nsA)
    ALLOW = _nsA["_INGEST_NORM_ALLOW"]
    for _k, _why in ALLOW.items():
        if not str(_why).strip():
            fails.append("_INGEST_NORM_ALLOW['%s'] 에 사유가 없다 — 실수인지 의도인지 구분 불가" % _k)
    EXPECTED_MERGE = {k: (None, v) for k, v in ALLOW.items()}   # 표준키는 _track_norm 결과를 따른다
    # 🔴 `tools/track_key.py`(대용 모듈)가 아니라 **app.py 가 실제로 쓰는 `_track_norm`** 을 잰다.
    #   대용으로 재면 "테스트는 통과하는데 운영은 다른 것"이 된다(원칙 8-D).
    import glob as _glob
    _srcA = open(os.path.join(BASE, "app.py"), encoding="utf-8").read()
    _ns = {}
    exec("import re", _ns)
    _mA = re.search(r"^_TRACK_ALIAS\s*=\s*\{[^}]*\}", _srcA, re.M)
    exec(_mA.group(0), _ns)
    _i = _srcA.find("_TRACK_GROUPS = {")
    exec(_srcA[_i:_srcA.find("\ndef ", _i)], _ns)
    exec(re.search(r"^def _track_norm.*?(?=\n(?:def |@|\Z))", _srcA, re.S | re.M).group(0), _ns)
    norm = _ns["_track_norm"]
    toks = set()
    _vre = re.compile(r"^(.+?)\s*\d+경주")
    for pat in ("data/analysis_log/*.json", "data/odds_history/*.json"):
        for f in _glob.glob(os.path.join(BASE, pat)):
            b = re.sub(r"^\d{4}_\d{2}_\d{2}_", "", os.path.basename(f)[:-5]).replace("_", " ")
            m = _vre.match(b)
            if m:
                toks.add(m.group(1).strip())
    changed, bad, guarded = [], [], []
    for t in sorted(toks):
        try:
            out = norm(t)
        except Exception as e:
            fails.append("_track_norm('%s') 예외: %s" % (t, str(e)[:60]))
            continue
        if not out or out == t:
            continue
        if t in ALLOW:
            changed.append((t, out))            # 수신 정규화가 실제로 바꾸는 것
        else:
            guarded.append((t, out))            # `_track_norm` 은 바꾸지만 **범위 밖이라 안 바꾼다**
    print("\n[5] 수신 표준화 오탐률 — 저장된 경기장 토큰 %d개" % len(toks))
    print("    승인 범위(_INGEST_NORM_ALLOW) 적용 %d건:" % len(changed))
    for t, o in changed:
        print("       🟢 '%s' → '%s'   %s" % (t, o, ALLOW.get(t, "")))
    print("    🔒 범위 밖이라 **원문 유지** %d건(=_track_norm 은 바꾸지만 수신은 안 바꾼다):" % len(guarded))
    print("       %s" % ", ".join("%s(→%s)" % (t, o) for t, o in guarded[:20]))
    for t, o in changed:
        if t not in ALLOW:
            bad.append((t, o))
    rate = (len(bad) / len(changed) * 100) if changed else 0.0
    print("    ⇒ 오탐률 %.1f%% (승인 밖 적용 %d / 적용 %d)" % (rate, len(bad), len(changed)))
    if bad:
        fails.append("수신 표준화 오탐 %d건 — 승인되지 않은 경기장 통합" % len(bad))

    print("\n" + "=" * 84)
    if fails:
        print("🔴 실패 %d건" % len(fails))
        for f in fails:
            print("   · %s" % f)
    if warns:
        print("🟡 경고 %d건 (4단계에서 처리)" % len(warns))
        for w in warns:
            print("   · %s" % w)
    if not fails:
        print("✅ 새로 생긴 미승인 차이 없음")
        print("⚠ 위 🟡 경고는 **남아 있는 차이**다 — 통과가 '전부 일치'를 뜻하지 않는다.")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
