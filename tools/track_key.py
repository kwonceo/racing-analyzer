# -*- coding: utf-8 -*-
"""[경기장 표준키 단일 모듈 (2026-08-01 신설)] — **완전 읽기 전용 · app.py 무변경**.

■ 왜 (같은 문제가 2026-08-01 하루에 네 번)
  | | 목록 | 구멍 | 실피해 |
  |---|---|---|---|
  | ① | `_JP_TRACKS` | `가와사키`↔실제 `카와사키` · `고치`↔`코치` · **JRA 10곳 전무** | 🔴 전적 오염 33건 |
  | ② | `_JRA_TRACKS` | `중경` 누락(07-31 보강) | 중앙 판정 누락 |
  | ③ | `_KEIRIN_ONLY_RE` | 이와키타이라·武雄·히라츠카·호후 누락 | `sport` 오분류 4건 |
  | ④ | 집계 | **`추쿄`와 `주쿄`가 따로 세어짐**(같은 中京) | 통계 분산 |
  ⇒ 목록이 **6벌로 흩어져** 있고 서로 표기가 다르다. 새 경기장·새 표기가 나올 때마다 또 뚫린다.

■ 🔴 이 파일이 하지 않는 것 (중요)
  · **`app.py` 를 고치지 않는다.** `_TRACK_GROUPS` 도 건드리지 않는다.
    ⚠ `_track_norm` 은 **38곳**에서 쓰인다. 별칭을 하나 추가하면 그 38곳의 동작이 즉시 바뀐다
      (예: `중경`→`추쿄` 로 정규화되면 `중경 7경주` 로 저장된 과거 데이터를 못 찾을 수 있다).
    ⇒ **3단계(diff 대조)까지가 이번 범위**다. 목록 교체·조회 계층 배선(4단계)은 **별도 승인**.
  · **저장 표기를 바꾸지 않는다.** 소급 정규화는 하지 않는다(2026-07-31 결정).

■ 원본
  `app.py` 의 `_TRACK_GROUPS`(57 표준키)를 **런타임 파싱**해 유일 원본으로 삼는다.
  거기에 **아직 app.py 에 없는 별칭**만 `_OVERLAY` 로 얹는다 — 얹은 것은 `missing_aliases()` 로
  **전부 드러난다**(조용히 채우지 않는다).

■ 공개 함수
  · `track_key(name)`    → 표준키(모르면 입력 그대로)
  · `track_region(name)` → "KRA" | "JRA" | "NAR" | "KEIRIN_ETC" | None
  · `track_sport(name)`  → "horse" | "cycle" | None  (이중소속은 None — 판정 불가를 숨기지 않는다)
"""
import ast
import os
import re

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# ⚠ 자기검증(원칙 17)용 오버라이드. 라이브 app.py 를 건드리지 않고 **사본**으로 시험하기 위한 것이다.
#   (서버가 app.py 를 감시하므로 원본을 잠깐이라도 고치면 리로더가 죽을 수 있다 — 오늘 3회 사망 이력)
APP = os.environ.get("TRACK_KEY_APP") or os.path.join(BASE, "app.py")

# 🔴 app.py `_TRACK_GROUPS` 에 **아직 없는** 별칭. 여기 있는 것은 `missing_aliases()` 로 보고된다.
#   ⚠ 여기에 넣는다고 app.py 동작이 바뀌지 않는다. "app.py 에 넣어야 할 목록"이라는 뜻이다.
_OVERLAY = {
    # 中京 = 추쿄 = 주쿄 = 중경. 실데이터에 셋 다 나타나 집계가 셋으로 갈라진다.
    "추쿄": ["주쿄", "중경"],
}

# 지역 분류. ⚠ 이중소속(경마장+경륜장)은 아래 `_DUAL` 로 따로 뺀다.
_KRA = ("서울", "부산", "부경", "제주", "과천")
_JRA = ("삿포로", "하코다테", "후쿠시마", "니가타", "도쿄", "나카야마",
        "추쿄", "쿄토", "한신", "고쿠라")
_NAR = ("후나바시", "오이", "나고야", "소노다", "코치", "오비히로", "몬베츠", "모리오카",
        "미즈사와", "우라와", "카와사키", "카나자와", "카사마츠", "히메지", "사가")
# 🔴 이중소속 — 같은 지명이 경마장이자 경륜장이다. `track_sport` 는 **None** 을 돌려준다.
#   ⚠ 억지로 하나로 정하면 7/25·7/26 코치 오분류 같은 사고가 난다. **판정 불가를 숨기지 않는다.**
_DUAL = ("코치", "나고야", "카와사키", "고쿠라")

_CACHE = {}


def _groups():
    if "g" in _CACHE:
        return _CACHE["g"]
    src = open(APP, encoding="utf-8").read()
    g = {}
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "_TRACK_GROUPS":
            g = ast.literal_eval(n.value)
            break
    if not g:
        raise SystemExit("app.py 에서 _TRACK_GROUPS 를 찾지 못했다")
    _CACHE["g"] = g
    return g


def _reverse():
    if "r" in _CACHE:
        return _CACHE["r"]
    rev = {}
    for std, alts in _groups().items():
        rev[std] = std
        for a in (alts or []):
            rev[str(a)] = std
    for std, alts in _OVERLAY.items():
        rev.setdefault(std, std)
        for a in alts:
            rev[a] = std
    # 🔴 한국 경마장은 `_TRACK_GROUPS`(일본 표기 통일용)에 **애초에 없다**.
    #   넣지 않으면 `서울 5경주` 가 region=None 이 되어 **한국경마가 "모르는 경기장"으로 떨어진다** —
    #   그건 오늘 오염 사고와 정확히 같은 유형의 구멍이다. 별칭도 함께 넣는다.
    for a, std in (("서울", "서울"), ("부산", "부경"), ("부경", "부경"), ("부산경남", "부경"),
                   ("제주", "제주"), ("과천", "서울")):
        rev.setdefault(a, std)
    _CACHE["r"] = rev
    return rev


def missing_aliases():
    """`_OVERLAY` 중 app.py `_TRACK_GROUPS` 에 아직 없는 것 → [(표준키, 별칭)].
    🔴 비어 있지 않으면 **app.py 에 그만큼 구멍이 있다는 뜻**이다."""
    g = _groups()
    out = []
    for std, alts in _OVERLAY.items():
        have = set(g.get(std) or []) | {std}
        for a in alts:
            if a not in have and a not in g:
                out.append((std, a))
    return out


def track_key(name):
    """표기 변형 → 표준키. 모르면 **입력을 그대로** 돌려준다(추측하지 않는다)."""
    s = str(name or "").strip()
    if not s:
        return s
    rev = _reverse()
    if s in rev:
        return rev[s]
    # raceKey('삿포로 7경주') 처럼 뒤에 경주번호가 붙은 형태 대응
    head = re.split(r"[\s0-9]", s, 1)[0]
    return rev.get(head, s)


def track_region(name):
    k = track_key(name)
    if k in _KRA:
        return "KRA"
    if k in _JRA:
        return "JRA"
    if k in _NAR:
        return "NAR"
    if k in _groups() or k in _OVERLAY:
        return "KEIRIN_ETC"
    return None


def track_sport(name):
    """🔴 이중소속은 **None**. '모른다'를 '경마다'로 바꾸지 않는다."""
    k = track_key(name)
    if k in _DUAL:
        return None
    r = track_region(k)
    if r in ("KRA", "JRA", "NAR"):
        return "horse"
    if r == "KEIRIN_ETC":
        return "cycle"
    return None


if __name__ == "__main__":
    print("표준키 %d개 · 역인덱스 %d항목" % (len(_groups()), len(_reverse())))
    m = missing_aliases()
    print("🔴 app.py 에 없는 별칭 %d건: %s" % (len(m), m))
    for s in ("중경", "주쿄", "추쿄 7경주", "카와사키 3경주", "코치 2경주", "삿포로 9경주",
              "서울 5경주", "防府", "いわき平", "平塚"):
        print("  %-14s → key=%-10s region=%-10s sport=%s"
              % (s, track_key(s), track_region(s), track_sport(s)))
