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

# 🔴 [2026-08-06 대표 지적 ③] 원문에 없는 **심리·성격 추정**을 막는다.
#   실제 사고: 통산 3착이 많다는 이유로 '승부욕이 부족하거나 막판 뒷심이 아쉽다'고 썼는데
#   그 말이 1착했다(소노다 5R 8번). 숫자에서 성격을 읽어내는 것은 지어내는 것이다.
#   ⚠ 프롬프트가 1차 방어이고 이것은 2차다 — 폐기로 막기보다 안 틀리게 하는 것이 먼저다.
_MIND_WORDS = ["승부욕", "근성", "뒷심", "집중력", "의욕", "기질"]


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
    # 중앙(JRA·netkeiba 馬柱): '… | 2026年8月2日 札幌1R レース情報(JRA) - netkeiba'
    #   ⚠ 여기엔 '競馬' 글자가 없어 위 정규식으로는 안 잡힌다(그래서 인덱스에서 통째로 빠져 있었다).
    m = re.search(r"日\s*([^\s｜|0-9]+?)(\d+)R\s*レース情報", t)
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
5. 🔴🔴 **빼는 판단을 하지 않는다.** '1착이 없어서' '공백이 길어서' '마체중이 줄어서'
   '나이가 많아서' 같은 이유로 말을 제외하지 않는다.
   ⚠ 2026-08-06 에 그 판단이 **전부 틀렸다** — 뺀 말이 1착·2착으로 들어왔다.
   ⑤ 항목은 '제외'가 아니라 **'판단을 보류한 말과 그 이유'** 로 쓴다. 근거가 없으면 비워도 된다.
   ⑦(주의점)은 최소 2개를 채운다. 비워 두면 그 분석문은 폐기된다.
5-B. 🔴 **출주한 모든 말을 ④에서 한 번씩 다룬다.** 두수가 많아도 빠뜨리지 않는다.
   ⚠ 실제로 한 말을 통째로 빠뜨렸고 그 말이 2착이었다(2026-08-06).
6. 한국어로 쓴다. 말·선수 이름은 원문 표기 그대로 둔다.
   🔴 **등급 표기도 원문 그대로 쓴다** — 'Ｃ３一' 을 'C3일'·'C3-1' 로 바꾸지 않는다.
     조 한자(一二三)를 한국어 발음으로 옮기면 '일(1조)'인지 '일(日)'인지 읽는 사람이 헷갈린다.
7. 숫자를 직접 계산하지 않는다. 승률·연대율·확률을 스스로 나눗셈해 만들지 않는다.
   원문에 적혀 있는 숫자만 쓴다. 원문에 '4-11-7-74' 라고 있으면 그 표기 그대로 인용한다.
8. 🔴 숫자를 **반올림하거나 근사하지 않는다.** '11.5' 를 '11.0' 이나 '11초대' 로 바꾸면
   그 분석문은 폐기된다. 구간을 말하고 싶으면 '11.5 와 12.4' 처럼 원문 값을 그대로 나열한다.
   ⚠ 실제로 이 유형(경륜 타임 반올림)으로 두 번 연속 폐기됐다(2026-08-06).
9. 🔴 **경륜의 주회 타임(11.5·12.4 같은 값)은 문장에 인용하지 않는다.**
   그 숫자는 반올림 사고의 원인이고, 경륜 판단의 축도 아니다(결정수·라인·급별이 축이다).
   타임을 말하고 싶으면 숫자 없이 '직전 착순이 좋았다' 처럼 착순·순위로만 적는다.
10. 🔴 **말의 성격·의지를 추정하지 않는다.** 다음과 같은 표현을 쓰지 않는다:
   승부욕 / 근성 / 뒷심 / 집중력 / 의욕 / 기질 / 마음
   원문에는 그런 정보가 없다. 착순·타임·통산 숫자에서 성격을 읽어내는 것은 **지어내는 것**이다.
   ⚠ 실제 사고: 통산 3착이 많다는 이유로 '승부욕이 부족하거나 막판 뒷심이 아쉽다'고 썼는데
     그 말이 1착했다(소노다 5R 8번 · 2026-08-06). 숫자는 숫자로만 적는다.
11. 🔴 **중앙(JRA)의 등급은 조건 클래스로만 판정한다** — 未勝利 / 1勝 / 2勝 / 3勝 / OP / L / G3 / G2 / G1.
   '２歳未勝利 → ３歳未勝利' 는 **나이가 한 살 먹은 것**이지 승급이 아니다. 등급은 그대로 未勝利다.
   나이 표기(２歳·３歳·４歳以上)의 변화를 등급 변화로 적지 않는다.

# 분석 관점(가장 중요)
A. 🔴 축은 **넷 이상** 세운다. 축 이름은 조건별 실적으로 잡는다 —
   당거리(距) 실적 / 당 경마장(場) 실적 / 통산(全) 성적 / 최고타임 / 등급 변화 / 휴양 복귀.
   배당 순서를 그대로 옮겨 적는 것은 분석이 아니다.
A-0. 🔴🔴 **시장 상위(1~2인기)를 끌어내리지 않는다.**
   시장이 위로 본 말은 그대로 위에 둔다. 그 말을 '거품' '위험' 으로 몰지 않는다.
   ⚠ 실제로 시장 1~2인기를 낮춰 본 판단이 **세 번 다 틀렸다**(2026-08-06).
   🔴 저평가 후보는 **축을 밀어내는 자리가 아니라 상대·복병 자리**에 놓는다.
     즉 "시장 1인기 대신 이 말" 이 아니라 "시장 1인기 + 이 말" 이다.
A-2. 🔴 **휴양 복귀를 반드시 본다.** 기본표에 '몇 주 공백'이 적혀 있다.
   8주 이상 쉬었으면 그 사실을 적고, 최근 착순이 좋더라도 그것이 **몇 달 전 성적**임을 밝힌다.
   ⚠ 실제 사고: 직전이 넉 달 전인데 '최근 2연승의 상승세'로만 써서 그 말이 3착 밖이었다.
B. 🔴 경마라면 **등급(클래스) 변화**를 반드시 다룬다.
   🔴 직전 한 전만 보지 않는다. **최근 5전 안에서 등급이 바뀐 지점**을 찾아 적는다.
     직전이 이번과 같아도 그 앞에서 내려왔으면 그것이 강급이다.
     (실제 사례: 직전 Ｃ３一 이라 '유지'로 봤는데 그 앞 4전이 전부 Ｃ２一 = Ｃ２→Ｃ３ 강급이었다)
   🔴 **급 단위(Ｃ２→Ｃ３)와 조 단위(Ｃ３一→Ｃ３二)를 반드시 구분해 적는다.**
     둘은 성격이 다르다. 급이 내려온 것을 조 이동처럼 적으면 안 된다.
   ⚠ 등급 순서는 Ａ>Ｂ>Ｃ>Ｄ, 같은 급 안에서는 1>2>3, 조는 一>二>三 이다.
   ⚠ 아래 '# 등급 이력' 이 주어지면 그 값을 그대로 쓴다. 직접 다시 읽어 계산하지 않는다.
A-3. 🔴🔴 **저평가 후보를 반드시 하나 이상 지목한다.**
   '# 조건별 실적 · 시장 괴리' 표가 주어지면 거기 적힌 **괴리가 큰 말**(조건 상위인데 시장 하위)을
   축 하나로 세우고, ④에서 그 말의 근거를 조건별 실적으로 적는다.
   ⚠ 표가 없으면(oddspark·경륜) 등급 강급·통산 성적의 모양으로 대신 찾는다.
   🔴 시장 상위 3두를 그대로 옮겨 적고 끝내면 그것은 분석이 아니다.
     "이 말은 조건에서는 상위인데 시장은 N인기로 낮게 본다" 를 **한 문장으로 명시**한다.
   ⚠ 저평가 후보가 반드시 온다고 말하지 않는다. **왜 그렇게 볼 여지가 있는지**만 적는다.
A-4. 🔴 조건별 실적을 말별로 적는다 — 당거리(距)·당경마장(場)·통산(全)·최고타임·부담중량.
   ⚠ 距 는 NAR 원문에만 있다. 표에 없으면 **없다고 적고 지어내지 않는다.**
B-2. 🔴 위 '# 등급 이력' 이 **주어지지 않은 경주**(NAR·중앙·경륜)는 등급 축을 억지로 만들지 않는다.
   NAR(船橋 등)은 레이스명이 'Ａ２以下'·'Ｂ１選抜馬' 형태로 조(組)가 없고, 과거 착순과의 짝짓기가
   원문에서 확실하지 않다 — 등급 변화를 추측해 쓰면 조용히 틀린다.
   ⇒ 그 대신 **통산(全)·좌우·경마장(場)·거리(距)별 성적**과 부담중량을 축으로 쓴다. 원문에 다 있다.
C. 통산 성적('全 4-11-7-74' 형태 = 1착-2착-3착-착외)은 **사실로만 적는다.**
   🔴 다음 축은 **쓰지 않는다** — 2026-08-06 측정에서 전부 무너졌다(배수를 함께 적는다):
     · 3착이 많은 말('3착 전문') — 통산 3착 횟수로 재면 **0.87**(우위 없음 · 자기상관까지 있다)
     · 승급한 말을 낮추는 것 — **0.98**(기저선과 같다)
     · 부담중량·감량 기수 — **0.72**(오히려 나쁘다)
     · 최근 5전 착순이 우상향이라는 이유 — **0.93 / 0.87**(우위 없음)
     · 당거리 **연대율** — **0.86**(우위 없음). 🔴 당거리는 **승수**로 본다. 연대율이 아니다.
       (몬베츠 4R 9번은 距 63전 5승으로 승수 1위인데 연대율은 9.5%% 로 하위였다)
   ⚠ 이 넷을 근거로 순위를 올리거나 내리지 않는다. 사실로 적는 것까지만 허용한다.
D. 🔴 대신 쓰는 축은 **조건별 실적**이다 — 당거리(距) 승수 · 당 경마장(場) 승수 ·
   최고타임 · 등급 낙차(급 단위 / 조 단위). 이것이 오늘 실측에서 살아남은 축이다.
F. 🔴 위 축들이 서로 어긋날 때 어느 쪽을 왜 택했는지 적는다. 그것이 이 분석문의 값어치다.
G. 🔴 **④에서 긍정적으로 쓴 말은 반드시 ⑥ 조합 후보에 넣는다.**
   '가능성을 보여주었다' '경쟁력을 발휘할 여지가 있다' 처럼 좋게 써놓고 조합에서 빼면
   그 분석문은 스스로 모순된 것이다. 좋게 볼 수 없으면 ④에서 좋게 쓰지 않는다.
   ⚠ 실제 사고: 8번·9번을 ④에서 좋게 써놓고 조합은 7+5·7+2·5+6 으로 냈는데 결과가 8-7-9 였다.
   ⚠ 조합을 3개까지만 낼 수 있다면, 좋게 본 말이 넷이면 **④의 표현을 줄이든지 조합을 바꾸든지**
     둘 중 하나를 해야 한다. 좋게 본 말과 조합이 어긋난 채로 두지 않는다.

# 출력 형식
아래 JSON 하나만 출력한다. 다른 말은 붙이지 않는다.
raceCharacter 와 structure 는 원문을 그대로 옮기지 말고 **한국어 설명 문장**으로 쓴다.
{
  "raceCharacter": "문자열",
  "structure": "문자열",
  "axisRanks": [{"axis": "축 이름", "order": "상위 순서와 근거"}],
  "horses": [{"no": 마번(정수), "facts": "🔴 **마명으로 시작**한 뒤 원문에서 그대로 가져온 사실",
              "classMove": "🔴 최근 5전 안 등급 변화 지점. 급 단위인지 조 단위인지 밝힌다. 경륜이면 급별·라인",
              "view": "그에 대한 우리 판단"}],
  "excluded": [{"no": 마번(정수), "why": "뺀 이유"}],
  "combos": [{"combo": [마번, 마번], "why": "근거"}],
  "cautions": ["주의점", "주의점"]
}

# 경주
%(head)s

%(cls)s
# 원문
%(body)s
"""


def build_prompt(rec):
    body = prepare(rec)
    head = "%s / %s %s경주 (%s)" % (rec["title"][:80], rec["venue"], rec["rno"], rec["kind"])
    cls = class_history(rec["html"], rec["kind"], rec.get("date"))   # 실패하면 "" — 종전과 같아진다
    cls += market_table(rec["html"], rec["kind"])                    # NAR 조건별 실적·괴리(없으면 "")
    # 🔴 검증 대조본에 기본표를 **더한다**. 기본표의 숫자(경과 주수·통산)는 코드가 원문에서
    #   기계 추출·계산한 값이라 정당한데, 대조본이 원문뿐이면 그것이 환각으로 잡혀 폐기된다.
    #   실제로 '19주 공백'의 19 가 잡혀 정상 분석문이 폐기됐다(2026-08-06 · 오탐 3번째).
    return _PROMPT % {"head": head, "body": body, "cls": cls}, (body + "\n" + cls)


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

    # ③ 단정 표현 · 심리 추정
    for w in _ASSERT_WORDS:
        if w in text:
            problems.append("단정 표현: %s" % w)
    for w in _MIND_WORDS:
        if w in text:
            problems.append("원문에 없는 심리 추정: %s" % w)

    # ④ 필수 항목
    #   🔴 [2026-08-06 대표 지시] ⑤(뺀 말) 강제를 **뺀다** — "빼는 판단을 아예 하지 않는다".
    #     오늘 그 판단이 전부 틀렸다(뺀 말이 1·2착으로 들어왔다). 강제하면 없는 근거를 지어낸다.
    #     ⚠ 항목 자체는 남긴다(기능 삭제 금지) — '판단 보류'로 쓰이면 그대로 실린다.
    if not (doc.get("cautions") or []):
        problems.append("⑦ 주의점이 비었다")

    # ⑥ 🔴 [2026-08-06] 저평가 검사 — 표가 지목한 저평가 후보가 **조합에 하나도 없으면** 경고.
    #   ⚠ 폐기하지 않는다(조합은 판단이고 숫자 오류가 아니다). 다만 조용히 넘기지 않는다.
    #   후보 마번은 대조본(body + 기본표)의 '🔴 저평가 후보' 줄에서 읽는다.
    und = []
    mline = re.search(r"저평가 후보[^\n]*", body or "")
    if mline:
        und = [int(x) for x in re.findall(r"(\d+)번\(", mline.group(0))]
    if und:
        inc = set()
        for c in (doc.get("combos") or []):
            for x in (c.get("combo") or []):
                inc.add(int(x))
        if not (set(und) & inc):
            problems.append("⚠경고(폐기 아님) 저평가 후보 %s 가 조합에 하나도 없다" % und)

    # ⑤ 🔴 [2026-08-06] 다루지 않은 말 — 한 말을 통째로 빠뜨렸고 그 말이 2착이었다.
    #   ⚠ 숫자 오류가 아니라 **누락**이므로 폐기하지 않고 사유에만 남긴다(경고 성격).
    if valid_nos:
        seen = set(int(h["no"]) for h in (doc.get("horses") or []) if h.get("no") is not None)
        miss = sorted(set(int(x) for x in valid_nos) - seen)
        if miss:
            problems.append("⚠경고(폐기 아님) 다루지 않은 마번: %s" % miss)

    # 🔴 '⚠경고' 로 시작하는 것은 **폐기 사유가 아니다**(누락처럼 숫자 오류가 아닌 것).
    #   숫자·마번·단정어는 종전대로 하나만 걸려도 폐기한다 — 그 원칙은 그대로다.
    fatal = [p for p in problems if not p.startswith("⚠경고")]
    return (len(fatal) == 0), problems


def valid_nos_of(html, kind):
    """출전 번호 범위를 **원문의 '나의 예상' 마크 선택기 id** 로 읽는다.
    oddspark 출주표는 출전마(선수)마다 `id="fill_select_myYoso_<마번>"` 을 정확히 1개씩 둔다.

    🔴 [2026-08-06 정정 2회차] 판정 근거를 두 번 바꿨다. 이력을 남긴다.
      1차: 정제본의 'N番' 텍스트 → 소노다 6R 에서 '5番' 하나만 걸려 두수 5로 오판,
           6~10번을 전부 유령으로 보고 **정상 분석문 12건을 통째로 폐기**했다(전부 오탐).
      2차: 상세 링크(lineageNb) 개수 → 🔴 **경마에서 항상 +1 이 나온다.**
           과거 전적표의 **1착마 링크**가 같은 `HorseDetail.do?lineageNb=` 형태라 함께 잡힌다.
           실측(8/6 소노다 1~6R): lineageNb 는 두수보다 정확히 1 크고, 그 1건은
           "2020/03/11 園田 ダ1400 マイタイザン" 처럼 **과거 경주 행**에 있었다.
           ⇒ 유령 마번 검사가 매 경주 1번씩 느슨했다(정상을 막지는 않았으나 검출력이 샜다).
      3차(현재): `fill_select_myYoso_<마번>` 의 최댓값. 경마·경륜 공통이고 마번과 1:1 이다.
           교차검증(경륜 8/6 벳푸 1~6R): playerCd 개수와 **전부 일치**. 경마는 lineageNb-1 과 일치.
    ⚠ NAR(keiba.go.jp DebaTable)은 화면 구조가 아예 다르다 — `fill_select_myYoso_` 가 0건이다.
      그쪽은 마번 셀 `class="horseNum"` 의 값을 쓴다(8/6 후나바시 1~12R 전부 1..N 연속 확인).
    ⚠ 못 읽으면 None 을 주고 마번 검사를 건너뛴다. 추측으로 막지 않는다."""
    if kind == "nar":
        nos = sorted(set(int(x) for x in
                         re.findall(r'class="horseNum"[^>]*>\s*(\d+)', html)))
    elif kind == "jra":
        # 중앙(netkeiba 馬柱)은 `<td class="Waku1">枠</td><td class="Waku">馬番</td>`.
        # ⚠ `<tr class="HorseList">` 개수는 다른 테이블 행이 섞여 +7 정도 크다 — 쓰지 않는다.
        nos = sorted(set(int(x) for x in re.findall(r'class="Waku">\s*(\d+)', html)))
    else:
        nos = sorted(set(int(x) for x in re.findall(r"fill_select_myYoso_(\d+)", html)))
    n = max(nos) if nos else 0
    if not (2 <= n <= 18) or len(nos) != n:
        return None                      # 결번·비연속이면 판정하지 않는다(추측 금지)
    return nos


# ── 등급 표기 정규화 ──────────────────────────────────────────────────────
#   🔴 [2026-08-06] LLM 이 전각 등급 'Ｃ３一' 을 'C3일'·'C2이'·'C3삼' 으로 옮겨 쓴다.
#     프롬프트로 두 번 막으려 했으나 계속 나온다(모델 성향) → **저장 직전에 되돌린다.**
#     '일(1조)'인지 '일(日)'인지 대표가 읽을 때 헷갈리는 것이 실제 문제다.
#   ⚠ 검증(숫자·마번·단정어) 을 **통과한 뒤에** 적용한다. 검증을 우회하지 않는다.
#     등급의 급 숫자는 1자리라 애초에 숫자 검증 대상이 아니다(2자리+ 와 소수만 본다).
_KUMI_KR = {"일": "一", "이": "二", "삼": "三", "사": "四", "오": "五", "육": "六"}
_GRADE_KR_RE = re.compile(r"([A-DＡ-Ｄ])\s*([1-6１-６])\s*(일|이|삼|사|오|육)(?![0-9일])")
_FW_NUM = {"1": "１", "2": "２", "3": "３", "4": "４", "5": "５", "6": "６"}
_FW_CLS = {"A": "Ａ", "B": "Ｂ", "C": "Ｃ", "D": "Ｄ"}


def _grade_fix(s):
    if not isinstance(s, str):
        return s
    def rep(m):
        c, n, k = m.group(1), m.group(2), m.group(3)
        return _FW_CLS.get(c, c) + _FW_NUM.get(n, n) + _KUMI_KR[k]
    return _GRADE_KR_RE.sub(rep, s)


def normalize_grades(doc):
    """생성문 전체의 등급 표기를 원문 전각으로 되돌린다(문자열 값만 · 구조 무변경)."""
    if isinstance(doc, dict):
        return {k: normalize_grades(v) for k, v in doc.items()}
    if isinstance(doc, list):
        return [normalize_grades(v) for v in doc]
    return _grade_fix(doc)


# ── 등급(클래스) 이력 — 최근 5전 안에서 바뀐 지점을 찾는다 ────────────────
#   🔴 [2026-08-06] 왜 필요한가: 직전 한 전만 보면 놓친다.
#     소노다 5R 8번은 직전이 Ｃ３一(유지)이라 LLM 이 "등급 유지"로 봤는데,
#     그 앞 4전이 전부 Ｃ２一 이었다 = **최근 5전 안에 Ｃ２→Ｃ３ 급 강급 지점이 있다.**
#     강급은 오늘 측정에서 유일하게 지지된 축이다(같은 급 내림 배수 1.21 ↔ 완전동일 0.86).
#   🔴 급 단위(Ｃ２→Ｃ３)와 조 단위(Ｃ３一→Ｃ３二)를 반드시 구분한다. 성격이 다르다.
_APP_PY = os.path.join(ROOT, "app.py")


def _load_class_fns():
    """app.py 의 `_CLASS_KUMI`~`_class_grade_rank` 블록만 잘라 실행해 재사용한다.

    🔴 목록을 두 곳에 두지 않는다(`tools/track_key.py` 선례와 같은 방식).
      조(組) 한자는 유니코드 연속이 아니라 범위 정규식이 조용히 틀린다 — 그 지식이 app.py 에 있다.
    ⚠ app.py 를 import 하지 않는다. import 하면 서버 초기화 부작용이 돈다
      (build_review.py 가 top-level 에서 sys.stdout 을 덮던 사고와 같은 계열)."""
    try:
        src = io.open(_APP_PY, encoding="utf-8").read()
        i = src.find("_CLASS_KUMI = ")
        j = src.find("def _keiba_parse_shutsuba")
        if i < 0 or j <= i:
            return None, None
        ns = {"re": re}
        exec(src[i:j], ns)                       # noqa: S102 — 잘라낸 상수·순수함수 2개뿐
        return ns.get("_class_grade_pick"), ns.get("_class_grade_rank")
    except Exception as e:
        print("[등급이력] app.py 재사용 실패(등급 요약 생략):", str(e)[:80])
        return None, None


def _move_kind(old, new, rank):
    """과거 등급 old → 새 등급 new 의 변화 종류. (라벨, 급단위여부) 또는 None(변화 없음)."""
    if not old or not new or old == new:
        return None
    ro, rn = rank(old), rank(new)
    if ro is None or rn is None:
        return None
    cls_move = old[:2] != new[:2]                # 'Ｃ２' vs 'Ｃ３' — 급 자체가 바뀌었나
    if rn > ro:
        return ("급 강급" if cls_move else "조 내림"), cls_move
    return ("급 승급" if cls_move else "조 올림"), cls_move


def _weeks_since(ymd_race, past_yymmdd):
    """이번 경주일(YYYYMMDD)과 과거 경주일('26.03.26') 사이 경과 주수. 실패하면 None.
    🔴 [2026-08-06 대표 지적] 5번은 직전이 3/26 인데 이번이 8/6 로 넉 달 공백이었다.
      그런데 분석문은 '최근 2연승의 상승세'로만 썼고 그 말은 3착 밖이었다.
      **원문에 날짜가 다 있는데 LLM 이 간격을 계산하지 않았다** — 코드가 계산해 준다."""
    try:
        import datetime as _dt
        y, m, d = past_yymmdd.split(".")
        p = _dt.date(2000 + int(y), int(m), int(d))
        r = _dt.date(int(ymd_race[:4]), int(ymd_race[4:6]), int(ymd_race[6:8]))
        return max(0, (r - p).days) // 7
    except Exception:
        return None


def _career_of(blk):
    """말 블록의 통산 표(table.ent2) → '全 3-3-3-11 · 場 2-1-2-4 · 他 … · 重 … · 連対 .300 · 3連 .450'.
    ⚠ oddspark 경마에는 **거리별(距) 칸이 없다** — 全(통산)·場(당 경마장)·他(타 경마장)·重(중마장)뿐이다.
      없는 축을 있다고 적으면 LLM 이 지어낸다."""
    m = re.search(r'<table class="ent2">(.*?)</table>', blk, re.S)
    if not m:
        return ""
    txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(1))).strip()
    out = []
    for lab in ("全", "場", "他", "重"):
        mm = re.search(lab + r"\s*(\d+)\s*-\s*(\d+)\s*-\s*(\d+)\s*-\s*(\d+)", txt)
        if mm:
            out.append("%s %s-%s-%s-%s" % ((lab,) + mm.groups()))
    for lab in ("連対", "3連"):
        mm = re.search(lab + r"\s*(\.?\d+(?:\.\d+)?)", txt)
        if mm:
            out.append("%s %s" % (lab, mm.group(1)))
    return " · ".join(out)


def class_history(html, kind, ymd=None):
    """원문 → 말별 기본표(마명·부마·통산·경과주수·등급 이력). 경마(oddspark)만. 실패하면 "".

    말 블록 경계는 `<tr>` 이 아니라 **HorseDetail 등장 위치**다(app.py 실측 교훈).
    ⚠ 과거 등급 리스트는 **최신이 앞**이다(직전 = index 0).
    🔴 [2026-08-06 대표 지적 ①] **마명을 부마 이름으로 쓰던 사고**를 여기서 막는다.
      원문 구조: `<small>父</small> <a href=HorseDetail><strong>마명</strong></a> 母`
      LLM 은 앞에 오는 `<small>`(父)을 마명으로 읽었다(소노다 5R 1번을 '월드에이스'로 표기 ·
      실제 마명은 スマートエミネンス). ⚠ app.py 파서도 같은 오류가 있다(별건·미수정)."""
    if kind != "oddspark" or "HorseDetail" not in html:
        return ""
    pick, rank = _load_class_fns()
    if not pick:
        return ""
    cur = pick(re.sub(r"\s+", " ", _title_of(html)))
    pos = [m.start() for m in re.finditer(r"HorseDetail", html)] + [len(html)]
    lines = []
    for i in range(len(pos) - 1):
        blk = html[pos[i]:pos[i + 1]]
        rn = re.findall(r'racename-small"\s+title="(.*?)"', blk, re.S)
        pl = re.findall(r"bg-(\d+)chaku", blk)
        gs = [pick(x) for x in rn]
        if not any(gs):
            continue                              # 과거 전적표 1착마 링크 블록 등 — 건너뛴다
        no = i + 1
        # ── 마명·부마 (블록 시작 = HorseDetail 링크 직전이므로 앞 300자에서 <small> 을 본다)
        head = html[max(0, pos[i] - 300):pos[i] + 400]
        mnm = re.search(r"<strong>(.*?)</strong>", head, re.S)
        msr = re.findall(r"<small>(.*?)</small>", head, re.S)
        nm = re.sub(r"<[^>]+>|\s+", "", mnm.group(1)) if mnm else ""
        sire = re.sub(r"<[^>]+>|\s+", "", msr[-1]) if msr else ""
        # ── 통산 · 경과 주수
        car = _career_of(blk)
        dts = re.findall(r"(\d{2}\.\d{1,2}\.\d{1,2})", blk)
        wk = _weeks_since(ymd, dts[0]) if (ymd and dts) else None
        seq = []
        for k, g in enumerate(gs):
            seq.append("%s(%s착)" % (g or "?", pl[k] if k < len(pl) else "?"))
        # 변화 지점: 이번 경주 ← 직전 ← 그 앞 …  (최신이 앞이므로 gs[k] 가 gs[k+1] 보다 최신)
        moves = []
        mv = _move_kind(gs[0], cur, rank) if gs else None
        if mv:
            moves.append("직전 %s → 이번 %s: %s" % (gs[0], cur, mv[0]))
        _lab = lambda k: "직전" if k == 0 else "%d전 전" % (k + 1)   # noqa: E731
        for k in range(len(gs) - 1):
            mv = _move_kind(gs[k + 1], gs[k], rank)
            if mv:
                moves.append("%s %s → %s %s: %s"
                             % (_lab(k + 1), gs[k + 1], _lab(k), gs[k], mv[0]))
        hd = "%d번 %s" % (no, nm or "(마명 미확인)")
        if sire:
            hd += " (父 %s)" % sire
        rest = []
        if wk is not None:
            rest.append("직전 경주 %s → 이번까지 **%d주** 공백" % (dts[0], wk))
        if car:
            rest.append("통산 " + car)
        rest.append("최근5전(최신순) " + " ← ".join(seq))
        rest.append(" / ".join(moves) if moves else "최근 5전 안 등급 변화 없음")
        lines.append(hd + "\n      " + "\n      ".join(rest))
    if not lines:
        return ""
    return ("# 말별 기본표(원문에서 기계 추출 — 🔴 이 값을 그대로 쓴다. 다시 읽거나 계산하지 않는다)\n"
            "이번 경주 등급: %s\n"
            "⚠ 급 단위(Ｃ２→Ｃ３)와 조 단위(Ｃ３一→Ｃ３二)는 성격이 다르다. 구분해 적는다.\n"
            "⚠ 마명은 여기 적힌 것만 쓴다. `父` 는 아버지 말이므로 **그 말의 이름이 아니다**.\n"
            "⚠ 통산은 全(전체)·場(당 경마장)·他(타 경마장)·重(중마장) 순서다. 거리별 칸은 원문에 없다.\n%s\n"
            % (cur or "(단일 등급 아님)", "\n".join(lines)))


# ── 조건별 실적 + 시장 괴리 (NAR) ─────────────────────────────────────────
#   🔴 [2026-08-06 대표 지시] "오늘 저평가를 네 번 짚은 근거가 전부 이 넷인데 분석문에 하나도 없다."
#     당거리(距)·당경마장(場)·최고타임·등급 이동 + **시장 인기 순위를 나란히** 넣는다.
#   ⚠ NAR 원문에는 距·最高タイム 이 다 있다. oddspark 에는 距 칸이 없으니 있는 것만 쓴다.
#   🔴 이 표는 **조건 축에 배당·인기를 넣지 않는다.** 인기는 대조(괴리 계산)로만 쓴다.
def market_table(html, kind):
    """NAR 원문 → 조건별 실적·시장 인기·괴리 표. 실패하면 "".

    괴리 = 시장 인기 순위 − 조건 축 순위. **양수가 크면 조건 상위인데 시장 하위** = 저평가 후보.
    ⚠ 실측 근거(NAR 31경주): 괴리 +4 이상에서 통산연대율 1.85 · 최고타임 1.73 · 당거리승수 1.82.
      단 n=27~41 이라 **방향까지**다. 이 표는 근거를 보여줄 뿐 성적을 보장하지 않는다."""
    if kind != "nar":
        return ""
    try:
        import measure_undervalued as MU
    except Exception:
        return ""
    try:
        race = MU.parse_race(html)
    except Exception:
        return ""
    hs = race.get("horses") or []
    if len(hs) < 4:
        return ""
    ar = MU.axis_ranks(race)
    mr = MU.market_rank(race)
    if len(mr) < 4:
        return ""
    lines = []
    gaps = []
    for h in sorted(hs, key=lambda x: x["no"]):
        no = h["no"]
        parts = []
        for lab, key in (("全", "all"), ("場", "place"), ("距", "dist")):
            v = h.get(key)
            if v:
                parts.append("%s %d-%d-%d-%d" % ((lab,) + tuple(v)))
        if h.get("best"):
            parts.append("최고타임 %s" % h["best"])
        if h.get("weight"):
            parts.append("부담 %.1f" % h["weight"])
        rk = []
        best_gap = None
        for lab in ("距승", "場승", "全연", "타임"):
            r = (ar.get(lab) or {}).get(no)
            if r:
                rk.append("%s %d위" % (lab, r))
                g = (mr.get(no) or 99) - r
                if best_gap is None or g > best_gap:
                    best_gap = g
        mk = mr.get(no)
        head = "%d번 %s" % (no, h.get("name") or "")
        if mk:
            head += " · 시장 %d인기" % mk
        if best_gap is not None and mk:
            head += " · 괴리 %+d" % best_gap
            gaps.append((best_gap, no, h.get("name") or "", mk))
        lines.append("%s\n      %s\n      조건순위: %s"
                     % (head, " · ".join(parts) or "원문에 조건별 실적 없음",
                        " / ".join(rk) or "판정 가능한 조건 축 없음"))
    # 🔴 [2026-08-06] 임계를 임의로 정하지 않는다 — **분포를 먼저 쟀다**(NAR 4일 470두):
    #   괴리 +3 이상 42% · **+4 이상 32%** · +5 이상 23%. 원칙 18(발동 5~30%)에 맞는 것은 +4~+5.
    #   ⇒ **+4** 를 택했다. 상위 3두 제한이 함께 걸려 실질 발동은 그보다 낮다.
    #   ⚠ +5 로 하면 대표 실증 3건 중 후나바시 9R 3번(괴리 +4)이 탈락한다.
    #     그것이 +4 를 고른 이유이므로 **사후 최적화 위험이 있다**는 사실을 여기 남긴다.
    #     표본이 쌓이면 다시 재고, 그때 임계를 **낮추는 방향으로는 바꾸지 않는다.**
    GAP_MIN = 4
    gaps.sort(key=lambda x: -x[0])
    top = [g for g in gaps if g[0] >= GAP_MIN][:3]
    tail = ""
    if top:
        tail = ("\n🔴 저평가 후보(조건 상위인데 시장 하위): %s\n"
                % " · ".join("%d번(%s · 시장 %d인기 · 괴리 %+d)" % (n, nm, mk, g)
                             for g, n, nm, mk in top))
    return ("# 조건별 실적 · 시장 괴리(원문에서 기계 추출 — 🔴 이 값을 그대로 쓴다)\n"
            "⚠ 全=통산 · 場=당 경마장 · 距=당거리 (1착-2착-3착-착외). 距 는 NAR 원문에만 있다.\n"
            "⚠ 괴리 = 시장 인기 순위 − 조건 축 순위. **양수가 크면 조건 상위인데 시장 하위**다.\n%s%s\n"
            % ("\n".join(lines), tail))


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
    doc = normalize_grades(doc)          # 🔴 검증 통과 후에만 — 표기만 되돌린다(값 무변경)
    p = save(rec, doc, meta)
    _bump("published")
    print("  🟢 통과 · 저장:", os.path.basename(p))
    return doc


# ── 범위 배선 — 강력승부 등급만 자동 생성 ─────────────────────────────────
#   🔴 [2026-08-06 승인] 좁게 시작한다. 판정 로직은 **읽기만** 한다(등급을 만들지 않는다).
#   실측 건수(8/1~8/5 analysis_log): 강력승부 하루 3~11건(중앙 7) · 추천까지 넣으면 12~24건.
#     ⇒ 강력승부만이면 경주당 약 17원 기준 **하루 약 120원**, 폐기 재시도 1.4배로 봐도 약 170원.
#   ⚠ raceGrade 는 마감 후 재분석으로 흔들린 이력이 있다(일치율 30.4%). 여기서는 **범위 선정에만**
#     쓰고 판정에는 쓰지 않으므로, 흔들려도 "몇 건 만드느냐"만 달라진다. 상한이 그것을 막는다.
AUTO_GRADES = ("강력승부",)          # 여기를 넓히면 대상이 는다. 넓힐 때는 건수부터 다시 잰다.
AUTO_MAX_PER_DAY = 20                 # 🔴 비용 상한. 넘으면 그날은 더 만들지 않는다.


def auto_targets(date):
    """오늘 원문이 있고 시스템 등급이 강력승부인 경주 목록. (완전 읽기 전용)"""
    idx = index_raw(date)
    out = []
    for key in sorted(idx, key=lambda x: (x[0], x[1])):
        rec = dict(idx[key]); rec["venue"], rec["rno"] = key
        sp = system_picks(rec)
        lab = (sp or {}).get("raceGrade") or ""
        if any(g in lab for g in AUTO_GRADES):
            out.append((key, rec, lab))
    return out


def run_auto(date, dry=False):
    made = skipped = discarded = 0
    done_today = len(glob.glob(os.path.join(OUT_DIR, "%s_*.json" % date)))
    tg = auto_targets(date)
    print("[자동] %s · 강력승부 %d경주 (이미 생성 %d · 상한 %d)"
          % (date, len(tg), done_today, AUTO_MAX_PER_DAY))
    for (key, rec, lab) in tg:
        p = os.path.join(OUT_DIR, "%s_%s_%sR.json" % (date, key[0], key[1]))
        if os.path.exists(p):
            skipped += 1
            continue
        if done_today + made >= AUTO_MAX_PER_DAY:
            print("  🔴 하루 상한 %d 도달 — 중단" % AUTO_MAX_PER_DAY)
            _bump("auto_capped")
            break
        print("\n=== [자동] %s %sR · %s ===" % (key[0], key[1], lab))
        if dry:
            made += 1
            continue
        if run_one(rec, dry=False):
            made += 1
        else:
            discarded += 1
    if not dry:                      # ⚠ --dry 는 실제로 만들지 않는다 → 계수기도 올리지 않는다
        _bump("auto_made", made)
        _bump("auto_discard", discarded)
    print("\n[자동] 생성 %d · 폐기 %d · 이미있음 %d" % (made, discarded, skipped))
    return made, discarded, skipped


# ── 사람이 읽는 형태로 내보내기(완전 읽기 전용) ───────────────────────────
def export_txt(date, out_path):
    """저장된 분석문 JSON → 대표가 읽을 텍스트 1개 파일. 판정·저장 경로 무개입."""
    files = sorted(glob.glob(os.path.join(OUT_DIR, "%s_*.json" % date)))
    if not files:
        print("[내보내기] %s 분석문 없음" % date)
        return None
    L = []
    L.append("전적 분석문 — %s (%d경주)" % (date, len(files)))
    L.append("※ 전적 원문만 읽고 만든 분석입니다. 시스템 추천과 다를 수 있습니다.")
    L.append("=" * 74)
    for f in files:
        d = normalize_grades(json.load(io.open(f, encoding="utf-8")))
        # ⚠ 정규화를 여기서도 한 번 더 돈다 — 정규화 배선 **이전에 저장된 파일**도
        #   대표가 읽을 때는 올바른 표기로 나오게 하기 위해서다(재생성 비용 0).
        b, m = d.get("brief") or {}, d.get("meta") or {}
        L.append("")
        L.append("■ %s %sR  (%s · %s)" % (d.get("venue"), d.get("raceNo"), d.get("kind"),
                                          m.get("at", "")))
        L.append("-" * 74)
        L.append("[1] 경주 성격\n  " + (b.get("raceCharacter") or "").strip())
        L.append("[2] 구조\n  " + (b.get("structure") or "").strip())
        L.append("[3] 축별 순위")
        for x in (b.get("axisRanks") or []):
            L.append("  · %s\n      %s" % (x.get("axis"), (x.get("order") or "").strip()))
        L.append("[4] 말(선수)별")
        for h in (b.get("horses") or []):
            L.append("  %s번" % h.get("no"))
            L.append("    사실 : " + (h.get("facts") or "").strip())
            if h.get("classMove"):
                L.append("    등급 : " + h["classMove"].strip())
            L.append("    판단 : " + (h.get("view") or "").strip())
        L.append("[5] 뺀 말")
        for e in (b.get("excluded") or []):
            L.append("  %s번 — %s" % (e.get("no"), (e.get("why") or "").strip()))
        L.append("[6] 조합")
        for c in (b.get("combos") or []):
            L.append("  %s — %s" % ("+".join(str(x) for x in (c.get("combo") or [])),
                                    (c.get("why") or "").strip()))
        L.append("[7] 주의점")
        for c in (b.get("cautions") or []):
            L.append("  · " + str(c).strip())
        ag, sp = d.get("agreement") or {}, d.get("systemPicks") or {}
        L.append("[참고] 시스템 추천 대조 — %s" % ag.get("status"))
        L.append("  우리 조합   : %s" % ["+".join(map(str, c)) for c in (ag.get("mine") or [])])
        L.append("  시스템 복승 : %s" % ["+".join(map(str, c)) for c in (ag.get("system") or [])])
        L.append("  시스템 등급 : %s" % sp.get("raceGrade"))
        L.append("=" * 74)
    txt = "\n".join(L)
    with io.open(out_path, "w", encoding="utf-8") as fp:
        fp.write(txt)
    print("[내보내기] %d경주 → %s (%d자)" % (len(files), out_path, len(txt)))
    return out_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=time.strftime("%Y%m%d"))
    ap.add_argument("--race", default="")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--export", default="", help="저장된 분석문을 텍스트 파일로 내보낸다")
    ap.add_argument("--auto", action="store_true", help="강력승부 등급 경주만 자동 생성")
    a = ap.parse_args()

    if a.export:
        return 0 if export_txt(a.date, a.export) else 1
    if a.auto:
        run_auto(a.date, dry=a.dry)
        return 0

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
