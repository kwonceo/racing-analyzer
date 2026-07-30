# -*- coding: utf-8 -*-
"""[Gemini 독립 예측 · Phase A/B (2026-07-31 신설)] 예측 기록 + 기계 채점.

■ 무엇인가
  Gemini 를 **코드 리뷰가 아니라 경주 예측 엔진**으로 쓴다.
  통계 테이블(`flow_table`)은 '빠른|7두 추입+추입' 같은 **셀 단위**로만 보므로,
  "같은 라인에 선행형이 둘이라 내부 경쟁이 난다" 같은 건 표현되지 않는다.
  → **"이 경주가 통상적인가, 예외로 볼 이유가 있나"** 를 묻고 그 답을 저장·채점한다.

■ 🔴 절대 원칙
  · **저장 전용.** 기존 추천 경로(`_final_picks`·EV필터·`corePicks`)에 **일절 개입하지 않는다.**
  · **3대 금지 입력**(Overfitting 방지) — 프롬프트에 넣지 않는다:
      ① 배당(odds)·인기순위(pop)·시장 최저 조합
      ② 우리 시스템 추천(finalQuinellas·keyHorses·axis)
      ③ paceBonus 가 **가산된** 점수(record_score·totalScore) → **가산 전 `paceBonusBase` 만**
    넣으면 시장이나 우리 답안을 베끼게 되고, 잘 맞아도 **새 정보가 아니다**.
    (2026-07-30 "마감 후 유력마가 잘 들어온다"가 정확히 그 함정이었다.)
  · **형식 검증 실패 = 통째 폐기.** 부분 채택 금지. `except: pass` 금지 — 반드시 로그를 남긴다.
  · `GEMINI_REVIEW_ENABLED`(코드 리뷰)는 **끈 채로 둔다.** 이 모듈은 별도 플래그를 쓴다.

■ 시장 대조군 (Phase B 필수)
  Gemini 성적만 재면 의미가 없다. **마감 시점(T-5) 스냅샷**의 단승 최저 3두를 `market_top3` 로 잡아
  나란히 채점한다. ⚠ **마감 후 배당 사용 금지** — 시장이 유리해져 비교가 성립하지 않는다.
"""
import json
import logging
import sys
import os
import threading
import time

try:
    import requests
except ImportError:
    requests = None

_LOG = logging.getLogger("gemini_forecast")
# 🔴 [2026-07-31] 핸들러가 없으면 `logging.lastResort` 가 **stderr** 로 보낸다.
#   서버는 stdout 만 파일로 받으므로 경고가 어디로 갔는지 알 수 없게 된다.
#   ⇒ stdout 핸들러를 직접 붙인다. **기존 print 는 그대로 둔다**(중복 출력은 감수).
#   ⚠ 이 파일이 다른 곳에서 import 돼도 핸들러가 두 번 붙지 않게 검사한다.
if not _LOG.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter("[예측·경고] %(message)s"))
    _LOG.addHandler(_h)
    _LOG.setLevel(logging.WARNING)
    _LOG.propagate = False
BASE = os.path.dirname(os.path.abspath(__file__))
FORECAST_DIR = os.path.join(BASE, "logs", "forecast")
RULES_MD = os.path.join(BASE, "docs", "learned_rules.md")

_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent"
_MODELS = ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash"]
_TIMEOUT = 12

# 통계(집계용) — 침묵 실패 방지. 2026-07-30 에 침묵 실패가 3번 났다.
STATS = {"called": 0, "ok": 0, "discarded": 0, "failed": 0, "skipped": 0}
_STATS_LOCK = threading.Lock()
_CALLED = {}                      # raceKey → 마지막 호출 시각(경주당 1회)
_CALL_ONCE_SEC = 3600


def _flag(name, default="0"):
    return str(os.environ.get(name, default)).strip().lower() in ("1", "true", "yes", "on")


def forecast_enabled():
    """[Phase A] 예측 호출 허용 여부(기본 꺼짐). ⚠ GEMINI_REVIEW_ENABLED 와 **별개**."""
    return _flag("GEMINI_FORECAST_ENABLED", "0")


def _api_key():
    return (os.environ.get("GEMINI_API_KEY") or "").strip()


def _mask(t, key):
    s = str(t)
    return s.replace(key, "<KEY>") if key else s


def _bump(k):
    with _STATS_LOCK:
        STATS[k] = STATS.get(k, 0) + 1


def _slug(rk):
    import re
    return re.sub(r"[^\w가-힣]+", "_", str(rk or "race")).strip("_")


def _load_rules():
    """docs/learned_rules.md 의 규칙 목록. 없으면 빈 리스트(필드는 유지)."""
    if not os.path.exists(RULES_MD):
        return []
    out = []
    try:
        import re
        for line in open(RULES_MD, encoding="utf-8"):
            m = re.match(r"^\s*[-*]?\s*\[?R?(\d+)\]?[.)]?\s+(.+)$", line.strip())
            if m and len(m.group(2)) > 4:
                out.append({"id": int(m.group(1)), "text": m.group(2).strip()[:200]})
    except Exception as e:
        _LOG.warning("[예측] 규칙 파일 읽기 실패(빈 목록으로 진행): %s", e)
    return out


# ══════════════ Phase A — 입력 구성 ══════════════
# ── [확장 재료 자동 포함 (2026-07-31)] ────────────────────────────────────────
#  🔴 **재료가 늘면 자동으로 프롬프트에 들어가게** 한다(매번 코드를 고치지 않기 위함).
#     여기 목록에만 추가하면 되고, **보유율이 임계 미만이면 자동 제외**된다.
#  ⚠ 포함 여부는 `input_snapshot._fields_included` / `_fields_omitted` 에 **반드시 기록**한다.
#     나중에 "그때 무엇을 보고 예측했나"를 알아야 하기 때문이다.
FIELD_INCLUDE_THRESHOLD = 0.70          # 보유율 70% 이상이면 자동 포함
OPTIONAL_FIELDS = [
    # (스냅샷 키, starters 원본 키 후보들, 사람이 읽는 이름)
    ("gear",     ["gear"],                         "기어비"),
    ("recent",   ["recent"],                       "금개최 성적"),
    ("prev1",    ["prev1"],                        "전개최 성적"),
    ("prev2",    ["prev2"],                        "전전개최 성적"),
    ("chaku",    ["chaku"],                        "착순분포"),
    ("declared", ["declaredStyleLabel", "declaredStyle"], "표기 각질"),
    ("weight",   ["weight"],                       "부담중량"),
    ("bodyWt",   ["bodyWeight"],                   "마체중"),
    ("jockey",   ["jockey"],                       "기수명"),
    ("jockeyRate", ["jockeyRate"],                 "기수 승률"),
    ("distApt",  ["distAptitude"],                 "거리 적성"),
]
# 🔴 아직 확보되지 않은 항목 — 프롬프트에 "추측하지 마시오"로 **명시**한다.
#    없는 정보를 안 알려주면 Gemini 가 지어낸다("주로가 무거워서" 같은 문장).
NOT_PROVIDED_ALWAYS = ["주로 상태(습도·경중)", "날씨", "바람(풍향·풍속)", "조교 평가"]


def build_input_snapshot(rk, starters, pace_analysis=None, line_info=None, comment=None,
                         distance=None, surface=None, track_cond=None, wind=None):
    """프롬프트에 주입할 **원본 데이터**를 만든다. ⚠ 3대 금지 항목을 여기서 차단한다.

    starters: [{no, name, gait/styleType, recentPlacings, grade, paceBonusBase, weeks, ...}]
    ⚠ `odds`·`pop`·`record_score`·`totalScore` 는 **의도적으로 제외**한다(넣으면 안 된다).
    ⚠ 선택 항목(`OPTIONAL_FIELDS`)은 **보유율 70% 이상일 때만 자동 포함**되고,
      포함/제외 결과가 `_fields_included`/`_fields_omitted` 에 남는다.
    """
    src = [h for h in (starters or []) if h.get("no") is not None]
    n = len(src) or 1
    # 보유율 계산 → 임계 이상만 채택
    included, omitted = [], []
    for key, cands, label in OPTIONAL_FIELDS:
        have = sum(1 for h in src
                   if any(h.get(c) not in (None, "", []) for c in cands))
        (included if have / n >= FIELD_INCLUDE_THRESHOLD else omitted).append(
            {"key": key, "label": label, "rate": round(100.0 * have / n, 1)})
    inc_keys = {x["key"] for x in included}
    horses = []
    for h in src:
        try:
            no = int(h.get("no"))
        except (TypeError, ValueError):
            continue
        row = {
            "no": no,
            "name": h.get("name") or "",
            "gait": h.get("gait") or h.get("styleType") or h.get("declaredStyleLabel"),
            "recentPlacings": h.get("recentPlacings") or [],
            "grade": h.get("absGrade") or h.get("grade"),
            # ⚠ paceBonus 가산 **전** 값만. record_score/totalScore 는 넣지 않는다.
            "paceBonusBase": h.get("paceBonusBase"),
            "weeksInARow": h.get("weeksInARow") or h.get("renzoku"),
        }
        for key, cands, _lb in OPTIONAL_FIELDS:
            if key not in inc_keys:
                continue
            for c in cands:
                if h.get(c) not in (None, "", []):
                    row[key] = h.get(c)
                    break
        horses.append(row)
    lead = [h["no"] for h in horses if str(h.get("gait") or "").startswith(("선행", "逃"))]
    close = [h["no"] for h in horses if str(h.get("gait") or "").startswith(("추입", "追", "差"))]
    not_provided = list(NOT_PROVIDED_ALWAYS)
    if distance:
        not_provided = [x for x in not_provided if not x.startswith("주로")] if (surface or track_cond) else not_provided
    if surface or track_cond:
        not_provided = [x for x in not_provided if not x.startswith("주로")]
    # [아메다스 바람 (2026-07-31)] 실측값이 들어온 경주만 "제공되지 않는 정보"에서 뺀다.
    #   ⚠ 20km 초과·한국 경마장·수집 실패는 값이 없으므로 종전대로 "추측하지 마시오"에 남는다 —
    #     **틀린 바람보다 없는 게 낫다.** 부재를 명시하지 않으면 Gemini 가 지어낸다.
    if wind and wind.get("풍속") is not None:
        not_provided = [x for x in not_provided if not x.startswith("바람")]
    not_provided += ["%s(보유율 %.0f%%로 미달)" % (x["label"], x["rate"]) for x in omitted]
    return {
        "raceKey": rk,
        "wind": wind or None,
        "fieldSize": len(horses),
        "distance": distance,
        "surface": surface,
        "trackCond": track_cond,
        "horses": horses,
        "composition": {"leadCount": len(lead), "leadHorses": lead,
                        "closeCount": len(close), "closeHorses": close},
        "lines": line_info or [],
        "paceLabel": (pace_analysis or {}).get("pace") if isinstance(pace_analysis, dict) else None,
        "comment": (comment or "")[:600],
        "appliedRulesAvailable": _load_rules(),
        # ⚠ 재현용 — "그때 무엇을 보고 예측했나"를 나중에 알아야 한다.
        "_fields_included": included,
        "_fields_omitted": omitted,
        "_not_provided": not_provided,
        "_excluded": ["odds", "pop", "marketLowest", "finalQuinellas", "keyHorses",
                      "axis", "record_score", "totalScore"],
    }


def _build_prompt(snap):
    rules = snap.get("appliedRulesAvailable") or []
    rules_txt = ("\n".join("R%d. %s" % (r["id"], r["text"]) for r in rules)
                 if rules else "(아직 없음 — applied_rules 는 빈 배열로 두세요)")
    # 🔴 "제공되지 않는 정보" — 없는 것을 명시하지 않으면 Gemini 가 지어낸다.
    _np = snap.get("_not_provided") or []
    _np_txt = "\n".join("   · " + str(x) for x in _np) if _np else "   (없음)"
    # [아메다스 바람 (2026-07-31)] 바람이 **실측으로 들어온 경주에만** 안내를 붙인다.
    #   ⚠ 값이 없으면 이 블록 자체가 비고, 바람은 "제공되지 않는 정보"에 남는다 —
    #     그래야 "맞바람이라" 같은 문장이 근거 없이 나오지 않는다.
    _w = snap.get("wind") or {}
    if _w.get("풍속") is not None:
        _wind_txt = ("""
🌬 [바람 — 실측값이 제공됩니다]
   %s %s m/s · 기온 %s℃ · 강수1h %s mm
   관측 %s (%s 관측소 · 경기장에서 %s km)
   ⚠ 경륜에서 **맞바람이면 선행이 불리**하고 뒷바람이면 선행이 버티기 쉽습니다.
      다만 **주로의 방향(직선 주로가 어느 방위인지)은 제공되지 않습니다.**
      방위를 모르는 채로 "맞바람이다"라고 단정하지 마시오. 풍속이 충분히 강할 때
      (대략 5 m/s 이상) **전개에 영향을 줄 수 있다**는 정도로만 다루십시오.
   ⚠ 풍속이 약하면(2 m/s 미만) 바람을 근거로 삼지 마시오.
"""
                     % (_w.get("풍향"), _w.get("풍속"), _w.get("기온"), _w.get("강수1h"),
                        _w.get("관측시각"), _w.get("지점"), _w.get("지점거리km")))
    else:
        _wind_txt = ""
    return """당신은 경륜/경마 전개 분석가입니다.

🔴 [2026-07-31 수정] 종전 프롬프트는 *"누가 1착일까를 맞히는 것이 목적이 아니다"* 로 시작했고,
   그 결과 `predicted_top3` 가 빈 배열로 와서 **전량 폐기**됐다(폐기율 100퍼센트).
   지시를 따른 쪽이 옳았고 **검증과 프롬프트가 모순이었다.** 이제 **둘 다** 요구한다.

당신이 할 일은 두 가지이며, **둘 다 반드시** 해야 합니다.

**① 1·2·3착을 예측한다 — `predicted_top3` 에 반드시 3개를 채우십시오.**
   비워두거나 2개만 쓰면 그 답변은 통째로 버려집니다.

**② 그 순위의 근거가 "통계 평균"이 아니라 "이 경주의 특수성"이어야 합니다.**
   `exception_note` 에 이 경주가 통상적인지, 예외로 볼 이유가 있는지 쓰십시오.

즉 **순위를 찍되, 왜 그 순위인지가 이 경주에만 해당하는 사정이어야 합니다.**
"각질 좋은 말이 앞선다" 같은 근거로 순위를 찍으면 통계 표와 같은 답이 됩니다.

예외의 예: 같은 라인에 선행형이 둘이라 내부 경쟁이 발생 / 연속 출전 피로 /
총평의 자연어 단서 / 두수 대비 각질 구성의 특수성 / 라인 구도의 불균형.
예외로 볼 이유가 없으면 `exception_note` 에 '통상' 이라고 쓰고, 순위는 그래도 채우십시오.

⚠ 아래 정보에는 **배당·인기·타 시스템 추천이 의도적으로 빠져 있습니다.**
   시장 판단을 베끼지 말고, 주어진 구도와 전적만으로 판단하세요.

🔴 [제공되지 않는 정보 — 추측하지 마시오]
%s
   위 항목을 근거로 삼는 문장은 **쓰지 마시오.**
   목록에 있는 항목을 근거로 든 예측은 폐기됩니다.
   모르는 것은 모른다고 두고, **주어진 데이터로만** 판단하십시오.
%s
⚠ 일반론 금지: "선행이 유리하다" 처럼 **어느 경주에나 해당하는 문장**은 쓰지 마시오.
   그런 답은 통계 표로도 나옵니다. **이 경주에만 해당하는 사정**을 쓰십시오.

[경주 데이터]
%s

[적용 가능한 학습 규칙]
%s

반드시 아래 JSON만 출력하세요(설명·마크다운 금지):
{
  "predicted_top3": [정수, 정수, 정수],   ← 🔴 반드시 3개. 비우면 통째 폐기됨
  "predicted_style": "선행|추입|혼합 중 하나",
  "predicted_pace": "빠른|보통|느린 중 하나",
  "line_winner": [정수, ...],
  "confidence": 1~5 정수,
  "reason_short": "80자 이내",
  "key_factors": ["...", "..."],
  "exception_note": "예외로 볼 이유. 통상적이면 '통상'",
  "applied_rules": [사용한 규칙 번호(위 목록에 없으면 빈 배열)]
}""" % (_np_txt, _wind_txt, json.dumps(snap, ensure_ascii=False, indent=1)[:9000], rules_txt)


# ══════════════ Phase A — 형식 검증 (부분 채택 금지) ══════════════
def validate(result, valid_nos):
    """반환 (ok, 사유). 하나라도 어긋나면 **통째 폐기**한다."""
    if not isinstance(result, dict):
        return False, "JSON 객체가 아님"
    need = ["predicted_top3", "predicted_style", "predicted_pace", "line_winner",
            "confidence", "reason_short", "key_factors", "exception_note", "applied_rules"]
    miss = [k for k in need if k not in result]
    if miss:
        return False, "키 누락: %s" % ",".join(miss)
    t3 = result.get("predicted_top3")
    if not isinstance(t3, list) or len(t3) != 3:
        return False, "predicted_top3 가 3개가 아님(%s)" % t3
    try:
        t3i = [int(x) for x in t3]
    except (TypeError, ValueError):
        return False, "predicted_top3 에 정수가 아닌 값"
    if len(set(t3i)) != 3:
        return False, "predicted_top3 중복"
    vs = set(int(x) for x in (valid_nos or []))
    if vs:
        bad = [x for x in t3i if x not in vs]
        if bad:
            return False, "출전 명단에 없는 번호: %s (명단 %s)" % (bad, sorted(vs))
    try:
        c = int(result.get("confidence"))
    except (TypeError, ValueError):
        return False, "confidence 가 정수가 아님"
    if not (1 <= c <= 5):
        return False, "confidence 범위 이탈(%s)" % c
    if not isinstance(result.get("applied_rules"), list):
        return False, "applied_rules 가 배열이 아님"
    return True, ""


def _save(rk, doc):
    os.makedirs(FORECAST_DIR, exist_ok=True)
    p = os.path.join(FORECAST_DIR, "%s_%s.json" % (time.strftime("%Y%m%d"), _slug(rk)))
    tmp = "%s.tmp%d_%d" % (p, os.getpid(), threading.get_ident())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)
    return p


def forecast_once(rk, snap, valid_nos):
    """[Phase A] 1회 예측·저장. 반환 저장 경로 또는 None. **완전 방어적**."""
    if not forecast_enabled():
        _bump("skipped")
        return None
    if requests is None:
        _LOG.warning("[예측] requests 미설치 — 건너뜀")
        _bump("failed")
        return None
    key = _api_key()
    if not key:
        _LOG.warning("[예측] GEMINI_API_KEY 없음 — 건너뜀")
        _bump("failed")
        return None
    now = time.time()
    if now - _CALLED.get(rk, 0) < _CALL_ONCE_SEC:
        _bump("skipped")
        return None
    _CALLED[rk] = now
    _bump("called")
    prompt = _build_prompt(snap)
    payload = {"contents": [{"parts": [{"text": prompt}]}],
               "generationConfig": {"maxOutputTokens": 800, "responseMimeType": "application/json",
                                    "thinkingConfig": {"thinkingBudget": 0}}}
    headers = {"x-goog-api-key": key, "Content-Type": "application/json"}
    result, last = None, ""
    for m in _MODELS:
        try:
            r = requests.post(_GEMINI_BASE % m, headers=headers, json=payload, timeout=_TIMEOUT)
            if r.status_code != 200:
                last = "%s %s" % (m, _mask(r.text, key)[:140])
                continue
            cand = (r.json().get("candidates") or [{}])[0]
            parts = (cand.get("content") or {}).get("parts") or []
            if not parts:
                last = "%s parts 없음(%s)" % (m, cand.get("finishReason"))
                continue
            raw = parts[0].get("text", "").replace("```json", "").replace("```", "").strip()
            result = json.loads(raw)
            break
        except Exception as e:
            last = "%s %s" % (m, _mask(e, key)[:140])
            continue
    if result is None:
        _LOG.warning("[예측] %s: 호출 실패 — %s", rk, last)
        _bump("failed")
        return None
    ok, why = validate(result, valid_nos)
    if not ok:
        # 🔴 부분 채택 금지 — 통째 폐기하고 반드시 기록한다.
        _LOG.warning("[예측] %s: 형식 검증 실패 → 통째 폐기 (%s) · 원문 %s",
                     rk, why, json.dumps(result, ensure_ascii=False)[:200])
        print("⚠ [예측 폐기] %s: %s" % (rk, why))
        _bump("discarded")
        return None
    doc = dict(result)
    doc.update({"raceKey": rk, "forecastAt": time.strftime("%Y-%m-%d %H:%M:%S"),
                "forecastEpoch": time.time(), "readonly": True,
                "input_snapshot": snap})       # ⚠ 재현용 — 마감 후 덮어쓰기 대비
    p = _save(rk, doc)
    _bump("ok")
    print("🔮 [예측] %s: top3=%s conf=%s · %s" % (rk, doc.get("predicted_top3"),
                                                doc.get("confidence"), doc.get("exception_note", "")[:40]))
    return p


# ══════════════ Phase B — 기계 채점 ══════════════
def _market_top3(snapshots, deadline_epoch):
    """⚠ **마감 시점(T-5) 스냅샷**의 단승 최저 3두. 마감 후 배당 사용 금지."""
    if not snapshots or not deadline_epoch:
        return None, None
    best, bt = None, None
    for s in snapshots:
        t = s.get("t")
        if not t:
            continue
        mb = (float(t) - float(deadline_epoch)) / 60.0
        if mb > 0:
            continue                       # 마감 후 제외
        if mb < -8:
            continue                       # T-8분보다 이른 것도 제외(T-5 근방만)
        if bt is None or mb > bt:           # 마감에 가장 가까운 마감 전 스냅샷
            bt, best = mb, s
    if not best:
        return None, None
    win = best.get("win") or best.get("single") or {}
    if not isinstance(win, dict) or not win:
        return None, round(bt, 1) if bt is not None else None
    try:
        items = sorted(((int(k), float(v)) for k, v in win.items() if float(v) > 0), key=lambda x: x[1])
    except (TypeError, ValueError):
        return None, round(bt, 1)
    return [n for n, _ in items[:3]], round(bt, 1)


def grade(rk, result_top3, starters=None, snapshots=None, deadline_epoch=None,
          payout_quinella=None):
    """[Phase B] 예측 파일에 채점 필드를 **append**. 기존 예측 필드는 불변.
    반환 채점 dict 또는 None(예측 파일 없음)."""
    p = os.path.join(FORECAST_DIR, "%s_%s.json" % (time.strftime("%Y%m%d"), _slug(rk)))
    if not os.path.exists(p):
        return None
    try:
        doc = json.load(open(p, encoding="utf-8"))
    except Exception as e:
        _LOG.warning("[채점] %s: 예측 파일 파싱 실패 — %s", rk, e)
        return None
    if doc.get("graded"):
        return doc.get("grading")
    try:
        actual = [int(x) for x in result_top3][:3]
    except (TypeError, ValueError):
        _LOG.warning("[채점] %s: 착순 형식 오류 %s", rk, result_top3)
        return None
    pred = [int(x) for x in (doc.get("predicted_top3") or [])]
    hit = [n for n in pred if n in actual]
    missed = [n for n in actual if n not in pred]
    by = {}
    for h in (starters or []):
        try:
            by[int(h.get("no"))] = h
        except (TypeError, ValueError):
            continue
    missed_info = [{"no": n,
                    "gait": (by.get(n) or {}).get("gait") or (by.get(n) or {}).get("styleType"),
                    "recentPlacings": (by.get(n) or {}).get("recentPlacings"),
                    "line": (by.get(n) or {}).get("line"),
                    "grade": (by.get(n) or {}).get("absGrade") or (by.get(n) or {}).get("grade")}
                   for n in missed]
    mt3, msnap_mb = _market_top3(snapshots, deadline_epoch)
    g = {"actual": actual, "hit_count": len(hit), "hit": hit,
         "missed": missed, "missed_info": missed_info,
         "payout_quinella": payout_quinella,
         "is_high_odds": bool(payout_quinella and float(payout_quinella) >= 20.0),
         "market_top3": mt3,
         "market_hit_count": (len([n for n in (mt3 or []) if n in actual]) if mt3 else None),
         "market_snapshot_mb": msnap_mb,
         "gradedAt": time.strftime("%Y-%m-%d %H:%M:%S")}
    doc["grading"] = g
    doc["graded"] = True
    _save(rk, doc)
    print("📊 [채점] %s: Gemini %d/3 ↔ 시장 %s/3 · 놓침 %s"
          % (rk, g["hit_count"], g["market_hit_count"], missed))
    return g


# ══════════════ Phase C — 복기 (2026-07-31 신설) ══════════════
#  🔴 채점(Phase B)은 "몇 개 맞았나"만 센다. **왜 못 봤는지**는 아무도 안 묻는다.
#     복기가 없으면 규칙이 늘지 않고, 규칙이 없으면 예측은 매번 처음부터 시작한다.
#  ⚠ **예측 필드는 불변** — 복기는 `review` 키에만 append 한다. 예측을 고치면 채점이 무의미해진다.
#  ⚠ **복기에는 배당을 넣어도 된다** — 결과 확정 후이고, '고배당을 놓쳤다'는 인식이 필요하다.
#     단 **예측 단계(Phase A)에는 여전히 금지**다(3대 금지 원칙 유지).
#  ⚠ 복기 실패도 조용히 넘기지 않는다 — `STATS["review_failed"]` 로 집계한다.
STATS.update({"review_ok": 0, "review_failed": 0, "review_skipped": 0})
REVIEW_MIN_MISS = 1          # 놓친 말이 이만큼 이상일 때만 복기(전부 맞았으면 물을 게 없다)


def _build_review_prompt(doc):
    g = doc.get("grading") or {}
    snap = doc.get("input_snapshot") or {}
    miss = g.get("missed_info") or []
    miss_txt = "\n".join(
        "   · %s번 — 각질 %s · 최근착순 %s · 라인 %s · 등급 %s"
        % (m.get("no"), m.get("gait") or "?", m.get("recentPlacings") or "?",
           m.get("line") or "?", m.get("grade") or "?") for m in miss) or "   (없음)"
    payout = g.get("payout_quinella")
    payout_txt = ("복승 배당 %s배%s" % (payout, " — 🔴 고배당입니다" if g.get("is_high_odds") else "")
                  if payout else "복승 배당 정보 없음")
    return """당신은 방금 끝난 경주의 **예측을 복기**합니다.

⚠ 맞히지 못한 것을 변명하지 마십시오. **무엇을 놓쳤는지**만 쓰십시오.
⚠ "운이 나빴다" · "이변이었다" 같은 문장은 쓰지 마십시오. 그런 답은 아무 쓸모가 없습니다.

[내가 예측한 것]
  상위 3: %s / 전개: %s / 페이스: %s
  확신도: %s
  근거로 든 것: %s
  예외 판단: %s

[실제 결과]
  착순 상위 3: %s
  맞힌 말: %s (%s/3)
  🔴 놓친 말:
%s
  %s

[예측 당시 내가 본 데이터]
%s

반드시 아래 JSON만 출력하세요(설명·마크다운 금지):
{
  "miss_reason": "왜 못 봤는지. 어떤 정보를 놓쳤는지. 120자 이내. 놓친 말이 없으면 '해당 없음'",
  "new_rule": "다음에 같은 상황에서 무엇을 볼지 **한 문장 규칙**으로. 80자 이내. 일반론이면 '없음'",
  "rule_confidence": 1~5 정수
}""" % (doc.get("predicted_top3"), doc.get("predicted_style"), doc.get("predicted_pace"),
        doc.get("confidence"), doc.get("key_factors"), doc.get("exception_note"),
        g.get("actual"), g.get("hit"), g.get("hit_count"), miss_txt, payout_txt,
        json.dumps(snap, ensure_ascii=False, indent=1)[:4000])


def _review_validate(r):
    if not isinstance(r, dict):
        return False, "JSON 객체가 아님"
    for k in ("miss_reason", "new_rule", "rule_confidence"):
        if k not in r:
            return False, "키 누락: %s" % k
    try:
        c = int(r.get("rule_confidence"))
    except (TypeError, ValueError):
        return False, "rule_confidence 가 정수가 아님"
    if not (1 <= c <= 5):
        return False, "rule_confidence 범위 이탈(%s)" % c
    return True, ""


def review(rk):
    """[Phase C] 채점 완료된 예측을 복기. 반환 review dict 또는 None. **완전 방어적**.

    ⚠ 호출 시점 = 결과 확정 + 채점(Phase B) 완료 후. 채점 전에는 아무것도 하지 않는다.
    """
    if not forecast_enabled():
        _bump("review_skipped")
        return None
    p = os.path.join(FORECAST_DIR, "%s_%s.json" % (time.strftime("%Y%m%d"), _slug(rk)))
    if not os.path.exists(p):
        _bump("review_skipped")
        return None
    try:
        doc = json.load(open(p, encoding="utf-8"))
    except Exception as e:
        _LOG.warning("[복기] %s: 예측 파일 파싱 실패 — %s", rk, e)
        _bump("review_failed")
        return None
    if not doc.get("graded"):
        _bump("review_skipped")           # 채점 전 — 다음 기회에
        return None
    if doc.get("reviewed"):
        return doc.get("review")          # 멱등
    g = doc.get("grading") or {}
    if len(g.get("missed") or []) < REVIEW_MIN_MISS:
        doc["reviewed"] = True
        doc["review"] = {"miss_reason": "해당 없음(전부 적중)", "new_rule": "없음",
                         "rule_confidence": 1, "reviewedAt": time.strftime("%Y-%m-%d %H:%M:%S")}
        _save(rk, doc)
        _bump("review_skipped")
        return doc["review"]
    key = _api_key()
    if requests is None or not key:
        _LOG.warning("[복기] %s: requests 미설치 또는 API 키 없음", rk)
        _bump("review_failed")
        return None
    payload = {"contents": [{"parts": [{"text": _build_review_prompt(doc)}]}],
               "generationConfig": {"maxOutputTokens": 500, "responseMimeType": "application/json",
                                    "thinkingConfig": {"thinkingBudget": 0}}}
    headers = {"x-goog-api-key": key, "Content-Type": "application/json"}
    result, last = None, ""
    for m in _MODELS:
        try:
            r = requests.post(_GEMINI_BASE % m, headers=headers, json=payload, timeout=_TIMEOUT)
            if r.status_code != 200:
                last = "%s %s" % (m, _mask(r.text, key)[:140])
                continue
            parts = ((r.json().get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
            if not parts:
                last = "%s parts 없음" % m
                continue
            result = json.loads(parts[0].get("text", "").replace("```json", "").replace("```", "").strip())
            break
        except Exception as e:
            last = "%s %s" % (m, _mask(e, key)[:140])
            continue
    if result is None:
        _LOG.warning("[복기] %s: 호출 실패 — %s", rk, last)
        print("⚠ [복기 실패] %s: %s" % (rk, last[:90]))
        _bump("review_failed")
        return None
    ok, why = _review_validate(result)
    if not ok:
        _LOG.warning("[복기] %s: 형식 검증 실패 → 폐기 (%s)", rk, why)
        print("⚠ [복기 폐기] %s: %s" % (rk, why))
        _bump("review_failed")
        return None
    rv = dict(result)
    rv["reviewedAt"] = time.strftime("%Y-%m-%d %H:%M:%S")
    doc["review"] = rv                     # ⚠ 예측 필드는 건드리지 않는다
    doc["reviewed"] = True
    _save(rk, doc)
    _bump("review_ok")
    print("📝 [복기] %s: %s → 규칙(%s): %s"
          % (rk, str(rv.get("miss_reason"))[:50], rv.get("rule_confidence"),
             str(rv.get("new_rule"))[:60]))
    return rv


def stats_summary():
    with _STATS_LOCK:
        s = dict(STATS)
    tot = s.get("called", 0)
    s["discardRate"] = round(100.0 * s.get("discarded", 0) / tot, 1) if tot else None
    s["failRate"] = round(100.0 * s.get("failed", 0) / tot, 1) if tot else None
    return s
