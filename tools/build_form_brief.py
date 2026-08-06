# -*- coding: utf-8 -*-
"""전적 분석문 생성 — 원문(logs/form_raw)을 그대로 LLM 에 넘겨 회원용 분석문을 만든다.

🔴 대표 지시(2026-08-06): "논리적으로 여러 상황을 고려한 분석이 목적이다. 실패해도 상관없다."
🔴 지름길: 파싱을 기다리지 않는다. 등급·통산·부담중량이 파싱 0% 여도 **원문에는 다 있다**.

원칙(CLAUDE.md)
  · 판정 로직을 한 줄도 건드리지 않는다. 이 도구는 완전 독립이고 추천·학습에 개입하지 않는다.
  · 사실은 원문에서 그대로 쓴다. LLM 이 기억으로 쓰지 못하게 프롬프트로 못 박고, 생성 후 숫자를 대조한다.
  · 🔴 숫자가 원문과 하나라도 어긋나면 그 경주는 분석문을 **내지 않는다**(폐기).
  · 🔴 "옵니다" 같은 단정 금지. "우리는 이렇게 봅니다" 로만.
  · 원문이 없는 경주는 만들지 않는다.

사용
  python tools/build_form_brief.py --list                       원문 인덱스만 출력
  python tools/build_form_brief.py --race 園田:5 --dry           프롬프트·토큰만(호출 안 함)
  python tools/build_form_brief.py --race 園田:5                 실제 생성
  python tools/build_form_brief.py --race 園田:5,園田:6,小松島:5,小松島:6
"""
import os
import re
import io
import sys
import json
import gzip
import glob
import time
import argparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_DIR = os.path.join(ROOT, "logs", "form_raw")
OUT_DIR = os.path.join(ROOT, "logs", "form_brief")
STAT_FILE = os.path.join(ROOT, "data", "form_brief_stats.json")

_MODELS = ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash"]
_BASE = "https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent"

# 🔴 단정 표현 금지어 — 회원에게 "온다"고 말하지 않는다.
#   ⚠ [2026-08-06] "반드시" 는 목록에서 뺐다. 첫 실측에서 "반드시 배당판을 확인하세요"(주의 문맥)가
#     걸렸는데, 그건 결과 단정이 아니라 오히려 넣어야 할 문장이다. 금지의 취지는
#     **결과를 단정하지 말라**이므로 결과 단정에 쓰이는 낱말만 남긴다(완화가 아니라 오탐 제거).
#     프롬프트에서는 "반드시"도 계속 금지한다.
_ASSERT_WORDS = ["옵니다", "온다", "확실", "틀림없", "무조건", "장담",
                 "100%", "필승", "확정적", "보장", "승리합니다", "이깁니다"]


# ── 원문 인덱스 ────────────────────────────────────────────────────────────
def _title_of(html):
    m = re.search(r"<title>(.*?)</title>", html, re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", m.group(1))).strip() if m else ""


def _identify(html):
    """원문 → (경기장, 경주번호). 실패하면 (None, None).
    oddspark 경마/경륜은 <title> 에 '園田競馬 5R' · NAR 은 <h4> 에 '船 橋 第12競走'."""
    t = _title_of(html)
    m = re.search(r"([^\s｜]+?)(?:競馬|競輪)\s*(\d+)R", t)
    if m:
        return m.group(1), int(m.group(2))
    h4 = re.search(r"<h4[^>]*>(.*?)</h4>", html, re.S)
    if h4:
        x = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", h4.group(1)))
        m2 = re.search(r"([一-鿿]\s?[一-鿿]*)\s*第(\d+)競走", x)
        if m2:
            return m2.group(1).replace(" ", ""), int(m2.group(2))
    return None, None


def index_raw(date=None):
    """logs/form_raw/<날짜>/ → {(경기장, 경주번호): {file, kind, mtime, html}} (같은 경주는 최신만).
    ⚠ 파일명에 경주 식별자가 없다(<종목>_<HHMMSS>_<해시>). 반드시 내용으로 식별해야 한다."""
    date = date or time.strftime("%Y%m%d")
    out = {}
    for f in sorted(glob.glob(os.path.join(RAW_DIR, date, "*.html.gz"))):
        try:
            html = gzip.open(f, "rt", encoding="utf-8", errors="ignore").read()
        except Exception:
            continue
        ven, rno = _identify(html)
        if not ven:
            continue
        kind = os.path.basename(f).split("_")[0]
        prev = out.get((ven, rno))
        if prev and prev["file"] >= f:      # 파일명에 시각이 들어 있어 사전순 = 시간순
            continue
        out[(ven, rno)] = {"file": f, "kind": kind, "html": html,
                           "title": _title_of(html), "date": date}
    return out


# ── 정제(파싱이 아니다 — 숫자·문자를 바꾸지 않는다) ────────────────────────
def clean_html(html):
    h = re.sub(r"(?is)<script.*?</script>", " ", html)
    h = re.sub(r"(?is)<style.*?</style>", " ", h)
    h = re.sub(r"(?is)<!--.*?-->", " ", h)
    h = re.sub(r"(?is)<(head|nav|footer|select|form)\b.*?</\1>", " ", h)
    h = re.sub(r"<br\s*/?>", "\n", h)
    h = re.sub(r"</(tr|div|p|li|h\d|table)>", "\n", h)
    h = re.sub(r"<[^>]+>", " ", h)
    h = (h.replace("&nbsp;", " ").replace("&amp;", "&")
          .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"'))
    h = re.sub(r"[ \t　]+", " ", h)
    h = re.sub(r"\n[ \t]*", "\n", h)
    h = re.sub(r"\n{2,}", "\n", h)
    return h.strip()


def trim_body(text, kind):
    """머리말(네비게이션)을 잘라 토큰을 줄인다. 못 찾으면 통째로 쓴다(원문 손실보다 낫다)."""
    marks = ["レース別出走表", "レースプログラム", "出走表", "出馬表", "DebaTable"]
    for m in marks:
        i = text.find(m)
        if 0 < i < len(text) * 0.4:
            return text[i:]
    return text


def prepare(rec):
    return trim_body(clean_html(rec["html"]), rec["kind"])


def est_tokens(s):
    """일본어·한국어는 문자당 약 0.6~1.0 토큰. 보수적으로 문자수를 상한으로 본다."""
    return len(s)


# ── 프롬프트(7단 구조 고정) ────────────────────────────────────────────────
_SECTIONS = [
    ("raceCharacter", "① 경주 성격 — 거리·주로·등급 조건이 이 경주를 어떤 판으로 만드는지"),
    ("structure", "② 구조 — 경륜이면 라인 구도, 경마면 등급 조건과 출전 등급 분포"),
    ("axisRanks", "③ 축별 순위 — 전적·등급·통산 같은 축마다 상위 몇 두를 각각 따로"),
    ("horses", "④ 말(선수)별 사실과 판단 — 사실은 원문 그대로, 판단은 그와 분리해서"),
    ("excluded", "⑤ 뺀 말과 이유 — 왜 후보에서 제외했는지"),
    ("combos", "⑥ 조합과 근거"),
    ("cautions", "⑦ 주의점 — 이 분석이 빗나갈 수 있는 지점"),
]

_PROMPT = u"""당신은 일본 지방경마·경륜 전적을 읽고 회원용 분석문을 쓰는 분석가다.

아래는 그 경주의 공식 출주표 원문(HTML 태그만 제거한 것)이다.

# 절대 규칙
1. 숫자·이름·등급·배당은 반드시 아래 원문에 있는 값만 쓴다. 기억이나 일반 지식으로 쓰지 않는다.
2. 원문에 없는 정보는 "원문에 없다"고 적는다. 지어내지 않는다.
3. 결과를 단정하지 않는다. 다음 낱말을 쓰지 않는다:
   옵니다 / 온다 / 확실 / 반드시 / 무조건 / 틀림없 / 장담 / 필승 / 보장 / 100%%
   (⚠ 이 문자열은 %% 포맷으로 조립된다 — 퍼센트는 반드시 %%%% 로 적는다. 2026-08-04 같은 사고 재발 방지)
   대신 "우리는 이렇게 봅니다" "그렇게 볼 여지가 있습니다" 처럼 판단임을 드러낸다.
4. 사실과 판단을 문장 안에서 섞지 않는다. 사실을 먼저 적고 판단을 뒤에 붙인다.
5. ⑤(뺀 말과 이유)는 최소 1두 이상, ⑦(주의점)은 최소 2개를 채운다. 비워 두면 그 분석문은 폐기된다.
   후보에서 뺀 말이 없다고 생각되더라도, 가장 근거가 약한 말을 골라 왜 약한지 적는다.
6. 한국어로 쓴다. 말·선수 이름은 원문 표기 그대로 둔다.
7. 숫자를 직접 계산하지 않는다. 승률·연대율·확률을 스스로 나눗셈해 만들지 않는다.
   원문에 적혀 있는 숫자만 쓴다. 원문에 '4-11-7-74' 라고 있으면 그 표기 그대로 인용한다.

# 분석 관점(가장 중요)
A. 🔴 배당·인기는 축으로 최대 1개만 쓴다. 나머지 축은 배당과 무관한 것으로 잡는다.
   배당 순서를 그대로 옮겨 적는 것은 분석이 아니다. 시장이 이미 아는 것을 되풀이하지 않는다.
B. 🔴 경마라면 **등급(클래스) 변화**를 반드시 다룬다.
   원문의 과거 경주명에 'Ｃ２一' 'Ｃ３二' 같은 등급이 들어 있다. 이번 경주 등급과 비교해
   그 말이 위 조에서 내려왔는지(강급) 아래 조에서 올라왔는지(승급)를 말별로 적는다.
   ⚠ 등급 순서는 Ａ>Ｂ>Ｃ>Ｄ, 같은 급 안에서는 1>2>3, 조는 一>二>三 이다.
C. 🔴 통산 성적('全 4-11-7-74' 형태 = 1착-2착-3착-착외)을 본다.
   3착이 유난히 많은 말, 2착만 많고 1착이 없는 말처럼 **모양이 치우친 말**을 짚는다.
D. 부담중량과 감량 표기(▲·△·☆)를 본다. 직전보다 늘었는지 줄었는지 적는다.
E. 최근 5전의 착순 흐름이 오르는지 내리는지, 그 사이 등급이 바뀌었는지 함께 본다.
F. 🔴 위 축들이 서로 어긋날 때 어느 쪽을 왜 택했는지 적는다. 그것이 이 분석문의 값어치다.

# 출력 형식
아래 JSON 하나만 출력한다. 다른 말은 붙이지 않는다.
raceCharacter 와 structure 는 원문을 그대로 옮기지 말고 **한국어 설명 문장**으로 쓴다.
{
  "raceCharacter": "문자열",
  "structure": "문자열",
  "axisRanks": [{"axis": "축 이름", "order": "상위 순서와 근거"}],
  "horses": [{"no": 마번(정수), "facts": "원문에서 그대로 가져온 사실",
              "classMove": "직전 등급 -> 이번 등급 (강급/승급/유지 중 하나와 근거). 경륜이면 급별·라인",
              "view": "그에 대한 우리 판단"}],
  "excluded": [{"no": 마번(정수), "why": "뺀 이유"}],
  "combos": [{"combo": [마번, 마번], "why": "근거"}],
  "cautions": ["주의점", "주의점"]
}

# 경주
%(head)s

# 원문
%(body)s
"""


def build_prompt(rec):
    body = prepare(rec)
    head = "%s / %s %s경주 (%s)" % (rec["title"][:80], rec["venue"], rec["rno"], rec["kind"])
    return _PROMPT % {"head": head, "body": body}, body


# ── 숫자 검증 ──────────────────────────────────────────────────────────────
_NUM_RE = re.compile(r"\d+\.\d+|\d{2,}")


def _numbers(text):
    """검증 대상 숫자 — 소수(배당·타임)와 2자리 이상 정수.
    ⚠ 1자리 정수(마번·착순)는 원문에 거의 전부 있어 검증력이 없다 → 마번은 따로 본다."""
    return set(_NUM_RE.findall(text or ""))


def verify(doc, body, valid_nos):
    """생성문의 숫자를 원문과 대조. 반환 (ok, 사유목록).
    🔴 하나라도 어긋나면 그 경주는 내지 않는다."""
    problems = []
    flat = []

    def walk(v):
        if isinstance(v, str):
            flat.append(v)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    walk(doc)
    text = "\n".join(flat)

    # ① 숫자 대조 — 원문에 없는 소수/2자리+ 숫자는 환각으로 본다.
    body_nums = _numbers(body)
    # 원문에 '10.0' 이 있으면 '10' 도 사실상 있다고 본다(표기 차이까지 잡으면 오탐이 는다).
    body_loose = set(body_nums)
    for n in list(body_nums):
        if "." in n:
            body_loose.add(n.split(".")[0])
    # 🔴 [2026-08-06 정정] 원문의 연대율은 앞 0을 생략한다('連対 .059'). LLM 은 '0.059' 로 쓴다.
    #   정규식 \d+\.\d+ 로는 '.059' 를 못 잡아 정상 인용이 '원문에 없는 숫자'로 폐기됐다(오탐 2번째).
    for x in re.findall(r"(?<!\d)\.(\d+)", body):
        body_loose.add("0." + x)
        body_loose.add("." + x)
    for n in sorted(_numbers(text)):
        if n not in body_loose:
            problems.append("원문에 없는 숫자: %s" % n)

    # ② 마번 — 유령 마번 방지(과거 실사고). 출전 명단 밖 번호를 추천하면 폐기.
    if valid_nos:
        vs = set(int(x) for x in valid_nos)
        for h in (doc.get("horses") or []):
            if h.get("no") is not None and int(h["no"]) not in vs:
                problems.append("출전 명단 밖 마번(horses): %s" % h["no"])
        for e in (doc.get("excluded") or []):
            if e.get("no") is not None and int(e["no"]) not in vs:
                problems.append("출전 명단 밖 마번(excluded): %s" % e["no"])
        for c in (doc.get("combos") or []):
            for x in (c.get("combo") or []):
                if int(x) not in vs:
                    problems.append("출전 명단 밖 마번(combo): %s" % x)

    # ③ 단정 표현
    for w in _ASSERT_WORDS:
        if w in text:
            problems.append("단정 표현: %s" % w)

    # ④ 필수 항목 — ⑤⑦ 은 비울 수 없다(지금 시스템에 없고 신뢰를 만든다).
    if not (doc.get("excluded") or []):
        problems.append("⑤ 뺀 말이 비었다")
    if not (doc.get("cautions") or []):
        problems.append("⑦ 주의점이 비었다")

    return (len(problems) == 0), problems


def valid_nos_of(html, kind):
    """출전 번호 범위를 **원문 HTML 의 상세 링크 개수**로 읽는다. 경마·경륜 모두 1번부터 연속이다.

    🔴 [2026-08-06 정정] 처음에는 정제본에서 'N番' 텍스트를 세었는데 **오탐이 났다** —
      소노다 6R 에서 '5番' 하나만 걸려 두수를 5로 보고 6~10번을 전부 유령으로 판정,
      정상 분석문을 통째로 폐기했다(검증 12건 실패는 전부 오탐이었다).
      가드가 정상을 막는 쪽이 더 나쁘다(원칙 20) → 텍스트 추정을 버리고 링크 개수로 바꾼다.
    ⚠ 못 읽으면 None 을 주고 마번 검사를 건너뛴다. 추측으로 막지 않는다."""
    if kind == "keirin":
        n = len(set(re.findall(r"PlayerDetail[^\"']*?playerCd=(\d+)", html)))
    else:
        n = len(set(re.findall(r"lineageNb=(\d+)", html)))
    if not (2 <= n <= 18):
        return None
    return list(range(1, n + 1))


# ── LLM 호출 ───────────────────────────────────────────────────────────────
def _api_key():
    k = os.environ.get("GEMINI_API_KEY")
    if k:
        return k
    p = os.path.join(ROOT, ".env")
    if os.path.exists(p):
        for ln in io.open(p, encoding="utf-8", errors="ignore"):
            if ln.strip().startswith("GEMINI_API_KEY"):
                return ln.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def call_llm(prompt):
    import requests
    key = _api_key()
    if not key:
        return None, "GEMINI_API_KEY 없음"
    payload = {"contents": [{"parts": [{"text": prompt}]}],
               # ⚠ classMove 를 넣은 뒤 4096 에서 출력이 잘려 JSON 이 깨졌다(2026-08-06). 8192 로 올린다.
               "generationConfig": {"maxOutputTokens": 8192, "temperature": 0.4,
                                    "responseMimeType": "application/json",
                                    "thinkingConfig": {"thinkingBudget": 0}}}
    last = "no model"
    for m in _MODELS:
        try:
            r = requests.post(_BASE % m, json=payload, timeout=90,
                              headers={"x-goog-api-key": key, "Content-Type": "application/json"})
            if r.status_code != 200:
                last = "%s HTTP %s" % (m, r.status_code)
                continue
            j = r.json()
            txt = j["candidates"][0]["content"]["parts"][0]["text"]
            usage = j.get("usageMetadata") or {}
            return {"text": txt, "model": m, "usage": usage}, None
        except Exception as e:
            last = "%s %s" % (m, str(e)[:80])
    return None, last


# ── 계수기·저장 ────────────────────────────────────────────────────────────
def _bump(key, n=1):
    try:
        os.makedirs(os.path.dirname(STAT_FILE), exist_ok=True)
        d = {}
        if os.path.exists(STAT_FILE):
            d = json.load(io.open(STAT_FILE, encoding="utf-8"))
        day = time.strftime("%Y-%m-%d")
        d.setdefault(day, {})
        d[day][key] = int(d[day].get(key, 0)) + n
        io.open(STAT_FILE, "w", encoding="utf-8").write(json.dumps(d, ensure_ascii=False, indent=1))
    except Exception:
        pass


def system_picks(rec):
    """같은 경주의 시스템 추천을 찾아 함께 담는다(병행 표시용·읽기 전용).
    🔴 판정 로직을 건드리지 않는다. analysis_log 를 읽기만 한다.
    ⚠ 경기장명이 원문은 일본어(園田)·우리 키는 한글(소노다)이라 표준키로 맞춘다
      (`tools/track_key` 재사용 — 목록을 두 곳에 두지 않는다)."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import track_key as tk
        std = tk.track_key(rec["venue"]) or rec["venue"]
    except Exception:
        std = rec["venue"]
    d = rec["date"]
    # 🔴 [2026-08-06] analysis_log 파일명은 `YYYY_MM_DD_<경기장>_<N>경주.json` 이다.
    #   처음에 경주번호를 빼고 조립해(`..._소노다경주.json`) 항상 빗나갔고 glob 폴백으로 갔다.
    #   회귀 테스트(run_glob_safety)가 그 glob 을 잡아 준 덕에 발견했다 — 날짜 접두를 반드시 넣는다(원칙 16).
    date_prefix = "%s_%s_%s" % (d[:4], d[4:6], d[6:8])
    pat = os.path.join(ROOT, "data", "analysis_log",
                       "%s_%s_%s경주.json" % (date_prefix, std, rec["rno"]))
    if not os.path.exists(pat):
        return None
    try:
        a = json.load(io.open(pat, encoding="utf-8"))
    except Exception:
        return None
    cp = a.get("corePicks") or {}
    dc = cp.get("displayedCombos") or {}
    return {"file": os.path.basename(pat), "keyHorses": cp.get("keyHorses"),
            "quinellas": dc.get("quinellas"), "trifectas": dc.get("trifectas"),
            "raceGrade": (cp.get("raceGrade") or {}).get("label") if isinstance(cp.get("raceGrade"), dict) else None}


def _compare(doc, sysp):
    """전적 분석 조합과 시스템 추천이 같은지 — 나중에 어느 쪽이 나은지 세기 위해 기록만 한다."""
    if not sysp:
        return {"status": "시스템 추천 없음"}
    mine = set(tuple(sorted(int(x) for x in (c.get("combo") or []))) for c in (doc.get("combos") or []))
    theirs = set(tuple(sorted(int(x) for x in c)) for c in (sysp.get("quinellas") or []) if c)
    if not mine or not theirs:
        return {"status": "비교 불가", "mine": sorted(mine), "system": sorted(theirs)}
    inter = mine & theirs
    return {"status": ("완전 일치" if mine == theirs else ("일부 겹침" if inter else "완전히 다름")),
            "mine": sorted(mine), "system": sorted(theirs), "overlap": sorted(inter)}


def save(rec, doc, meta):
    os.makedirs(OUT_DIR, exist_ok=True)
    p = os.path.join(OUT_DIR, "%s_%s_%sR.json" % (rec["date"], rec["venue"], rec["rno"]))
    sysp = system_picks(rec)
    io.open(p, "w", encoding="utf-8").write(json.dumps(
        {"venue": rec["venue"], "raceNo": rec["rno"], "date": rec["date"],
         "kind": rec["kind"], "sourceFile": os.path.basename(rec["file"]),
         "brief": doc, "meta": meta,
         "systemPicks": sysp, "agreement": _compare(doc, sysp),
         "notice": "전적 분석이며 시스템 추천과 다를 수 있습니다."},
        ensure_ascii=False, indent=1))
    return p


# ── 실행 ───────────────────────────────────────────────────────────────────
def run_one(rec, dry=False):
    prompt, body = build_prompt(rec)
    tk = est_tokens(prompt)
    print("  프롬프트 %d자 (입력 토큰 상한 약 %d)" % (len(prompt), tk))
    if dry:
        return None
    _bump("attempt")
    res, err = call_llm(prompt)
    if not res:
        print("  🔴 호출 실패:", err)
        _bump("call_fail")
        return None
    try:
        doc = json.loads(res["text"])
    except Exception as e:
        print("  🔴 JSON 파싱 실패:", str(e)[:80])
        _bump("parse_fail")
        return None
    vn = valid_nos_of(rec["html"], rec["kind"])
    ok, probs = verify(doc, body, vn)
    u = res.get("usage") or {}
    meta = {"model": res["model"], "promptTokens": u.get("promptTokenCount"),
            "outputTokens": u.get("candidatesTokenCount"), "totalTokens": u.get("totalTokenCount"),
            "verified": ok, "problems": probs, "validNos": vn,
            "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    print("  모델 %s · 토큰 입력 %s / 출력 %s" % (res["model"], u.get("promptTokenCount"), u.get("candidatesTokenCount")))
    if not ok:
        print("  🔴 폐기 — 검증 실패 %d건" % len(probs))
        for p in probs[:8]:
            print("     -", p)
        _bump("discard")
        return None
    p = save(rec, doc, meta)
    _bump("published")
    print("  🟢 통과 · 저장:", os.path.basename(p))
    return doc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=time.strftime("%Y%m%d"))
    ap.add_argument("--race", default="")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    idx = index_raw(a.date)
    if a.list or not a.race:
        print("[원문 인덱스] %s · %d경주" % (a.date, len(idx)))
        for (v, n) in sorted(idx, key=lambda x: (x[0], x[1])):
            r = idx[(v, n)]
            print("  %-8s %2dR  %-9s %s" % (v, n, r["kind"], os.path.basename(r["file"])))
        return 0

    for spec in [s for s in a.race.split(",") if s.strip()]:
        ven, _, rno = spec.partition(":")
        key = (ven.strip(), int(rno))
        print("\n=== %s %sR ===" % key)
        rec = idx.get(key)
        if not rec:
            print("  원문 없음 — 분석문을 만들지 않는다")
            _bump("no_raw")
            continue
        rec["venue"], rec["rno"] = key
        run_one(rec, dry=a.dry)
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.exit(main())
