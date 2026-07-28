import os, json, threading, time, requests
from datetime import datetime

_GEMINI_CALLED = {}
_GEMINI_LOCK = threading.Lock()
_CALL_INTERVAL = 300
_LOG_DIR = os.path.join(os.path.dirname(__file__), "logs", "gemini_review")
os.makedirs(_LOG_DIR, exist_ok=True)
_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent"
# gemini-2.0-flash 는 2026-07 기준 서비스 종료("no longer available", 404) → 2.5 계열로 교체.
#   순서대로 시도(앞이 실패하면 다음). 기존 2.0-flash 도 삭제하지 않고 최후 폴백으로 유지.
_GEMINI_MODELS = ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash"]
_GEMINI_URL = _GEMINI_BASE % _GEMINI_MODELS[0]   # 하위호환(기존 상수명 유지)


def _gemini_api_key():
    return (os.environ.get("GEMINI_API_KEY") or "").strip()


def _mask(text, key):
    """로그/예외 문자열에 API 키가 노출되지 않도록 마스킹."""
    s = str(text)
    return s.replace(key, "<KEY>") if key else s

def _fmt_combo(lst):
    if not lst:
        return "없음"
    parts = []
    for item in lst[:4]:
        combo = "+".join(str(x) for x in (item.get("combo") or []))
        odds = item.get("odds")
        reason = (item.get("reason") or "")[:15]
        parts.append(combo + (f"({odds}배)" if odds else "") + (f"[{reason}]" if reason else ""))
    return ", ".join(parts)

def _build_prompt(rk, final_q, final_t, special_q, line_pairs, strong_signals, fav_axis, strong_axis, drops, cur_mb):
    drop_txt = "없음"
    if drops:
        drop_txt = ", ".join(
            str(d.get("combo", d.get("no", "?"))) + " " + str(round(d.get("pct", 0), 1)) + "%"
            for d in sorted(drops, key=lambda x: x.get("pct", 0))[:3]
        )
    prompt = (
        "너는 BMED 경마/경륜 시스템의 수석 로직 검수관이다. 반드시 JSON만 출력해라.\n"
        "[경주] " + str(rk) + " [마감까지] " + str(cur_mb) + "분\n"
        "[왕축] " + str(fav_axis) + " strongAxis=" + str(strong_axis) + "\n"
        "[강신호] " + str(len(strong_signals or [])) + "건 [급락] " + drop_txt + "\n"
        "[라인] " + json.dumps(line_pairs or [], ensure_ascii=False) + "\n"
        "[복승] " + _fmt_combo(final_q) + "\n"
        "[삼복승] " + _fmt_combo(final_t) + "\n"
        "[보조] " + _fmt_combo(special_q or []) + "\n"
        "진단 항목(해당하는 것만): 1)맹목적왕축 2)B라인누락 3)라인교차 4)급락미반영\n"
        "규칙:\n"
        "- issues 에는 위 데이터에서 '실제로 확인되는' 문제만 넣어라. 항목명을 그대로 나열하지 마라.\n"
        "- 근거가 없으면 status=SAFE, issues=[] 로 답하라. 대부분의 경주는 SAFE 가 정상이다.\n"
        "- 확신이 없으면 WARNING 대신 SAFE 를 택하라(오경보가 미탐보다 해롭다).\n"
        "- 각 issue 는 '항목명: 근거가 된 마번/수치' 형식으로 한 구절씩 쓴다.\n"
        '출력형식(JSON만): {"status":"SAFE" or "WARNING","issues":[],"summary":"한줄","q_suggest":"","t_suggest":""}'
    )
    return prompt

# ─────────────────────────────────────────────────────────────────────────────
# [전체자료 모드 2026-07-28] 정확한 로직 분석이 목적이므로 배당판·시계열·전적·판단근거를
#   '자르지 않고' 전부 보낸다. 기존 요약 프롬프트(_build_prompt)는 폴백으로 그대로 보존.
#   비용보다 정확도 우선(권대표 지시). gemini-2.5-flash 는 1M 토큰 입력을 받는다.
# ─────────────────────────────────────────────────────────────────────────────
_MAX_PROMPT_CHARS = 400000     # 안전 상한(1M 토큰 대비 충분히 여유). 초과 시 시계열 오래된 순으로만 축약.


def _pair_key(k):
    """조합 키를 '1+2' 문자열로 정규화((1,2) 튜플·'1+2'·[1,2] 모두 허용)."""
    if isinstance(k, (tuple, list)):
        return "+".join(str(int(x)) for x in k)
    return str(k).replace(" ", "")


def _as_odds_items(m):
    """[형식 방어 2026-07-28] 배당 자료가 dict 와 list 두 형식으로 들어온다.
      · dict : {(1,2): 3.4} 또는 {"1+2": 3.4}   (curQ·curWin 등 _parse_combo_map 산출)
      · list : [{"combo": [1,2], "odds": 3.4}]  (rec 원본 — exacta 는 이 형식이다)
    실사고: exa(list)를 dict 로 가정해 .items() 를 호출 →
      "[Gemini] 카나자와 10경주: 에러(무시) — 'list' object has no attribute 'items'"
      로 전체자료 모드가 매 경주 실패하고 있었다(로그 0건의 진짜 원인).
    어느 형식이든 [(키, 배당)] 목록으로 정규화한다."""
    if not m:
        return []
    if isinstance(m, dict):
        return list(m.items())
    out = []
    if isinstance(m, (list, tuple)):
        for e in m:
            if isinstance(e, dict):
                k = e.get("combo") or e.get("pair") or e.get("no")
                v = e.get("odds") if e.get("odds") is not None else e.get("odd")
                if k is not None and v is not None:
                    out.append((k, v))
            elif isinstance(e, (list, tuple)) and len(e) == 2:
                out.append((e[0], e[1]))
    return out


def _fmt_odds_map(m, limit=None, asc=True):
    """배당 맵/목록 → '1+2:3.4 1+3:5.6 ...' (배당 오름차순). limit=None 이면 전량."""
    items = _as_odds_items(m)
    if not items:
        return "없음"
    try:
        items = sorted(items, key=lambda kv: (kv[1] is None, kv[1]), reverse=not asc)
    except Exception:
        pass
    if limit:
        items = items[:limit]
    return " ".join("%s:%s" % (_pair_key(k), v) for k, v in items)


def _fmt_json_block(obj, title):
    """구조가 제각각인 분석 산출물은 JSON 원문 그대로 전달(정보 손실 0)."""
    if obj in (None, [], {}, ""):
        return "[%s] 없음\n" % title
    try:
        return "[%s]\n%s\n" % (title, json.dumps(obj, ensure_ascii=False, default=str))
    except Exception:
        return "[%s] %s\n" % (title, str(obj))


def _fmt_timeline(tl):
    """배당 시계열 전량 — 스냅샷마다 전 조합 배당. 급락 판정의 원재료라 절대 요약하지 않는다."""
    if not tl:
        return "[배당 시계열] 없음\n"
    out = ["[배당 시계열] %d개 스냅샷 (오래된→최신, T-분 = 마감까지 남은 분)" % len(tl)]
    for s in tl:
        mb = s.get("minutes_before")
        q = s.get("quinella") or {}
        out.append("T-%s (%s) %s" % (mb, s.get("time"), _fmt_odds_map(q)))
    return "\n".join(out) + "\n"


# ── 로직 소스 전송 (2026-07-28 권대표 지시: "로직 소스도 보내게 해줘") ──────────────────
#   데이터만 주면 "결과가 이상하다"까지밖에 못 간다. 판단을 만든 '실제 코드'를 함께 줘야
#   "어느 함수의 어느 조건이 잘못됐다"는 로직 레벨 분석이 가능하다.
#   ⚠ app.py 는 읽기만 한다. mtime 캐시라 코드 수정 시 자동 갱신된다.
_APP_PATH = os.path.join(os.path.dirname(__file__), "app.py")
_MAX_SOURCE_CHARS = 300000
_LOGIC_CACHE = {"mtime": None, "text": "", "meta": ""}

# 이 경주의 판단을 실제로 만들어낸 함수들(선정 → 제거 → 신호 → 확정 → 전략 순).
_LOGIC_FNS = [
    # 신호·이상감지
    "_excess_drop_analysis", "_mass_drop_detect", "_win_exacta_reversal", "_quinella_mismatch",
    "_signal_confidence", "_inverse_arrangement", "_compression_pattern", "_strong_signals",
    "_advanced_anomaly",
    # 말 선정·제거·등급
    "_form_from_starters", "_elimination", "_elim_score",
    "_integrated_grades", "_learned_integrated_weights", "_integrated_adaptive",
    "_signal_situation", "_combo_signal_quality",
    # 조합 확정
    "_confidence_picks", "_final_picks", "_third_place_hunt", "_reversal_backing_bets",
    "_trio_est",
    # 전략·후처리
    "_bmed_strategy", "_apply_profit_strategy", "_apply_mass_drop_strategy", "_compare_recommend",
]

_FORMULA_SPEC = """[설계 의도 — 공식과 임계값 (문서 기준)]
1. 초과급락 = 말N 평균급락 − 전체평균급락. 절대 10%+ 급락은 노이즈가 아니라 집중신호(ABS_STRONG=-10).
   5%p+ = 강함(🔴) / 0~5%p = 약함(🟡) / 그 외 노이즈 제거.
2. 역전비율 = 쌍승(B→A) / 쌍승(A→B). <0.95 역전신호 / <0.80 강한 / <0.60 압도적.
3. 불일치점수 = 예상최저복승 / 실제최저복승. 1.2+ 주의 / 1.5+ 강한 / 2.0+ 압도적.
4. 종합 신뢰도 = 초과급락 40% + 쌍승역전 35% + 복승불일치 25%. 70+ 🔴 / 40~69 🟡.
5. 통합 점수 = 이상감지(배당) 60% + 전적 40%. 50경주+ 누적 시 비교학습으로 ±15%p 자동 조정
   (이상감지 가중치는 0.45~0.75 범위).
6. 배당 급락 경고: 30%↑ 🟠 / 50%↑ 🔴. A/B/C/D 등급 상위 비율 45:28:17:10.
7. 상황별 가중(_signal_situation): 일반 50:50 / 이상감지다수 40:60 / 대규모 30:70 / 대규모+집중 20:80.
8. 마감 후 급락은 추천에 반영하지 않는다(참고만). 첫 수집 1틱은 워밍업으로 급락 계산 보류.
※ 위는 '의도'다. 아래 실제 소스가 이 의도대로 구현돼 있는지도 검증 대상이다."""


def _logic_source():
    """app.py 에서 판단 로직 함수들의 실제 소스를 추출(줄번호 포함). 실패해도 분석은 계속된다."""
    try:
        mt = os.path.getmtime(_APP_PATH)
    except Exception:
        return "", "app.py 접근 불가"
    if _LOGIC_CACHE["mtime"] == mt and _LOGIC_CACHE["text"]:
        return _LOGIC_CACHE["text"], _LOGIC_CACHE["meta"]
    try:
        import ast
        with open(_APP_PATH, encoding="utf-8") as f:
            src = f.read()
        lines = src.splitlines()
        spans = {}
        for n in ast.walk(ast.parse(src)):
            if isinstance(n, ast.FunctionDef):
                spans.setdefault(n.name, (n.lineno, getattr(n, "end_lineno", n.lineno)))
        out, used, got, missing = [], 0, [], []
        for fn in _LOGIC_FNS:
            sp = spans.get(fn)
            if not sp:
                missing.append(fn)
                continue
            a, b = sp
            body = "\n".join(lines[a - 1:b])
            if used + len(body) > _MAX_SOURCE_CHARS:
                out.append("# … 크기 상한(%d자) 도달 — 이후 함수 생략: %s"
                           % (_MAX_SOURCE_CHARS, ", ".join(_LOGIC_FNS[_LOGIC_FNS.index(fn):])))
                break
            out.append("# ────── app.py:%d-%d  def %s ──────\n%s" % (a, b, fn, body))
            used += len(body)
            got.append("%s(%d줄)" % (fn, b - a + 1))
        text = "\n\n".join(out)
        meta = "%d개 함수 %d자 | 미발견: %s" % (len(got), used, ", ".join(missing) or "없음")
        _LOGIC_CACHE.update(mtime=mt, text=text, meta=meta)
        return text, meta
    except Exception as e:
        return "", "로직 소스 추출 실패: %s" % e


def _build_full_prompt(ctx):
    """[전체자료] 로직 소스 + 배당판 전량 + 시계열 전량 + 전적 + 판단근거 + 최종추천(절단 없음)."""
    g = ctx.get
    P = []
    P.append("너는 BMED 경마·경륜 베팅 시스템의 수석 로직 검수관이다.")
    P.append("아래는 이 경주에 대해 시스템이 보유한 '전체' 데이터와, 시스템이 내린 최종 판단이다.")
    P.append("아래 순서로 제공된다: ①판단 로직의 설계 의도와 실제 소스 ②이 경주의 전체 데이터")
    P.append("③시스템이 실제로 내린 판단. 셋을 대조해 로직 결함을 근거와 함께 지적하라.\n")

    _src, _srcmeta = _logic_source()
    P.append("═══ 0. 판단 로직 (설계 의도 + 실제 소스) ═══")
    P.append(_FORMULA_SPEC)
    if _src:
        P.append("\n[실제 구현 소스 — app.py 발췌 · %s]" % _srcmeta)
        P.append("```python")
        P.append(_src)
        P.append("```")
    else:
        P.append("\n[실제 구현 소스] 첨부 실패(%s) — 의도 명세만으로 판단하라." % _srcmeta)
    P.append("")

    P.append("═══ 1. 경주 기본 ═══")
    P.append("경주: %s | 종목: %s/%s | 마감까지: %s분 | 마감후: %s"
             % (g("raceKey"), g("sport"), g("category"), g("minutesBefore"), g("afterClose")))
    P.append("출전 유효 마번: %s | 인기순(배당등장빈도): %s" % (g("validNos"), g("ranked")))
    P.append("")

    P.append("═══ 2. 현재 배당판 (전량) ═══")
    P.append("[단승] %s" % _fmt_odds_map(g("win")))
    _q = g("quinella") or {}
    P.append("[복승] %d개 조합" % len(_as_odds_items(_q)))
    P.append(_fmt_odds_map(_q))
    _e = g("exacta") or {}
    if _e:
        P.append("[쌍승] %d개" % len(_as_odds_items(_e)))
        P.append(_fmt_odds_map(_e))
    _t = g("trio") or {}
    if _t:
        P.append("[삼복승 수집분] %d개" % len(_as_odds_items(_t)))
        P.append(_fmt_odds_map(_t))
    P.append("")

    P.append("═══ 3. 배당 변동 이력 (전량) ═══")
    P.append(_fmt_timeline(g("timeline")))

    P.append("═══ 4. 이상감지 원본 ═══")
    P.append(_fmt_json_block(g("drops"), "급락 전체(조합·하락률·시점)"))
    P.append(_fmt_json_block(g("excess"), "초과급락 분석(말별 평균-전체평균)"))
    P.append(_fmt_json_block(g("massDrop"), "대규모 급락 판정"))
    P.append(_fmt_json_block(g("strongSignals"), "강신호 전문"))
    P.append(_fmt_json_block(g("signals"), "신호 목록 전문"))
    P.append(_fmt_json_block(g("inverse"), "역배열 분석"))
    P.append(_fmt_json_block(g("compression"), "배당 압축 패턴"))
    P.append(_fmt_json_block(g("darkHorses"), "복병(스마트머니 포함)"))
    P.append(_fmt_json_block(g("advanced"), "실시간 고도화(급락속도·연속하락·환급률)"))

    P.append("═══ 5. 전적·출마 정보 ═══")
    P.append(_fmt_json_block(g("form"), "말별 전적·점수"))
    P.append(_fmt_json_block(g("linePairs"), "라인 페어(경륜)"))

    P.append("═══ 6. 시스템의 판단 근거 ═══")
    P.append(_fmt_json_block(g("keyHorses"), "유력마"))
    P.append(_fmt_json_block(g("elimination"), "제거마(점수·사유)"))
    P.append(_fmt_json_block(g("integrated"), "통합 등급(이상감지+전적 가중)"))
    P.append(_fmt_json_block(g("signalQuality"), "신호 품질(상/중/하)"))
    P.append(_fmt_json_block(g("confidence"), "종합 신뢰도"))

    P.append("═══ 7. 최종 추천 (근거 원문·절단 없음) ═══")
    P.append(_fmt_json_block(g("corePicks"), "corePicks(finalQuinellas/finalTrifectas 등)"))
    P.append(_fmt_json_block(g("betRecommend"), "베팅 추천 전체(kind·combo·alloc·expOdds·label·reason)"))
    P.append(_fmt_json_block(g("strategy"), "BMED 전략·자금배분"))

    _lrn = g("learned")
    if _lrn:
        P.append("═══ 8. 누적 학습 통계 ═══")
        P.append(_fmt_json_block(_lrn, "학습 통계(조건별 적중률)"))

    P.append("═══ 분석 지시 ═══")
    P.append("1) 배당판과 시계열을 직접 읽고, 시스템이 놓친 자금 흐름·급락·역배열이 있는지 확인하라.")
    P.append("2) 유력마·제거마·최종추천이 위 데이터로 정당화되는지 검증하라. 근거 없이 선정된 말이 있는가?")
    P.append("3) 추천에 빠졌지만 데이터상 들어갔어야 할 조합이 있으면 마번과 근거를 들어 지적하라.")
    P.append("4) [코드 레벨] 0번의 실제 소스를 읽고 다음을 점검하라 —")
    P.append("   ⓐ 설계 의도(공식·임계값)와 실제 구현이 어긋난 곳")
    P.append("   ⓑ 이 경주 데이터에 대해 임계값이 부적절하게 동작한 곳(경계에서 뒤집힌 조건)")
    P.append("   ⓒ 조건 우선순위·단락(early return)·예외처리로 신호가 삼켜진 곳")
    P.append("   ⓓ 이 경주에서 실제로 타지 않은 분기인데 타야 했던 분기")
    P.append("   지적할 때는 반드시 '함수명 + app.py 줄번호 + 해당 조건식'을 인용하라.")
    P.append("5) 추측 금지. 모든 지적에는 위 데이터의 구체적 수치(마번·배당·%·시각)를 근거로 인용하라.")
    P.append("6) 코드에 없는 동작을 상상해 지적하지 마라. 소스에 근거가 없으면 지적하지 마라.")
    P.append("7) 문제가 없으면 status=SAFE, issues=[] 로 답하라. 억지로 문제를 만들지 마라.\n")
    P.append("출력(JSON만):")
    P.append('{"status":"SAFE|WARNING",'
             '"issues":["항목: 근거 수치"],'
             '"summary":"한줄 요약",'
             '"q_suggest":"복승 제안",'
             '"t_suggest":"삼복승 제안",'
             '"analysis":{'
             '"odds_read":"배당판에서 읽히는 자금 흐름",'
             '"signal_read":"이상감지 해석",'
             '"missed":[{"combo":"1+2","why":"근거"}],'
             '"logic_findings":[{"func":"함수명","line":"app.py 줄번호","code":"문제 조건식 원문",'
             '"problem":"무엇이 잘못됐나","evidence":"이 경주 수치 근거","fix":"어떻게 고칠지(코드 수준)",'
             '"severity":"high|mid|low"}]},'
             '"confidence":0}')

    text = "\n".join(P)
    if len(text) > _MAX_PROMPT_CHARS:      # 상한 초과 시 시계열만 오래된 순으로 축약(다른 자료는 보존)
        tl = g("timeline") or []
        keep = max(4, len(tl) // 2)
        ctx2 = dict(ctx)
        ctx2["timeline"] = tl[-keep:]
        ctx2["_truncated"] = True
        text = _build_full_prompt(ctx2) if keep < len(tl) else text[:_MAX_PROMPT_CHARS]
    return text


def _save_log(rk, result):
    try:
        safe = "".join(c if (c.isalnum() or "\uAC00" <= c <= "\uD7A3") else "_" for c in rk)
        ts = datetime.now().strftime("%H%M%S")
        fname = datetime.now().strftime("%Y%m%d") + "_" + safe + "_" + ts + ".json"
        path = os.path.join(_LOG_DIR, fname)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"raceKey": rk, "time": datetime.now().isoformat(), "result": result}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

# app.py 의 실제 발송 함수는 `_kakao_send_to_me(text, url=None)` (반환 {ok, error?}).
#   기존 후보명 3개는 삭제하지 않고 폴백으로 유지(다른 배포본 호환).
_KAKAO_FN_NAMES = ["_kakao_send_to_me", "_kakao_send_text", "_send_kakao_msg", "kakao_send"]


def _resolve_app_module():
    """실행 중인 app 모듈을 반환. `python app.py` 는 __main__, gunicorn 은 app 으로 로드된다.
    ⚠ sys.modules 를 먼저 조회한다 — importlib.import_module("app") 을 먼저 쓰면
      이미 __main__ 으로 실행 중인 app.py 가 별도 모듈 객체로 '한 번 더' 실행되어
      불필요한 재초기화가 발생한다(최후 폴백으로만 사용)."""
    import sys
    for name in ("__main__", "app"):
        mod = sys.modules.get(name)
        if mod is not None and any(hasattr(mod, fn) for fn in _KAKAO_FN_NAMES):
            return mod
    try:
        import importlib
        return importlib.import_module("app")
    except Exception:
        return None


def _send_kakao(rk, result):
    try:
        msg = "[" + rk + "] Gemini 경고\n" + result.get("summary", "") + "\n"
        issues = result.get("issues", [])
        if issues:
            msg += "⚠ " + " / ".join(issues[:2]) + "\n"
        if result.get("q_suggest"):
            msg += "권장복승: " + result["q_suggest"] + "\n"
        if result.get("t_suggest"):
            msg += "권장삼복승: " + result["t_suggest"]
        app_mod = _resolve_app_module()
        if app_mod is None:
            print("[Gemini] 카카오 발송 실패 — app 모듈을 찾지 못함")
            return
        for fn in _KAKAO_FN_NAMES:
            fn_obj = getattr(app_mod, fn, None)
            if fn_obj is None:
                continue
            res = fn_obj(msg)
            if isinstance(res, dict) and not res.get("ok"):
                print("[Gemini] 카카오 발송 실패(" + fn + ") —", res.get("error"))
            else:
                print("[Gemini] 카카오 경고 발송(" + fn + "):", rk)
            return
        print("[Gemini] 카카오 발송 실패 — 발송 함수 없음:", _KAKAO_FN_NAMES)
    except Exception as e:
        print("[Gemini] 카카오 발송 예외(무시) —", e)

def review_async(rk, final_q, final_t, special_q=None, line_pairs=None,
                 strong_signals=None, fav_axis=None, strong_axis=True,
                 drops=None, cur_mb=None, ctx=None):
    """ctx 를 주면 [전체자료 모드](배당판·시계열·전적·판단근거 전량)로 분석한다.
    ctx 가 없으면 기존 요약 프롬프트로 동작(하위호환 — 기존 호출부 무변경으로 계속 작동)."""
    def _run():
        try:
            with _GEMINI_LOCK:
                if time.time() - _GEMINI_CALLED.get(rk, 0) < _CALL_INTERVAL:
                    return
                _GEMINI_CALLED[rk] = time.time()
            key = _gemini_api_key()
            if not key:
                return
            # [전체자료 모드] ctx 가 있으면 배당판·시계열·전적·판단근거를 자르지 않고 전부 보낸다.
            _full = bool(ctx)
            if _full:
                prompt = _build_full_prompt(ctx)
                # 깊은 분석이 목적이므로 thinking 을 켜고 출력 예산도 크게 잡는다(비용보다 정확도 우선).
                # ⚠ maxOutputTokens 는 thinking 토큰과 출력 토큰의 '합'에 걸린다(실측: thinking 7860 +
                #   출력 318 = 8178 에서 MAX_TOKENS 로 잘림). thinkingBudget 보다 넉넉히 크게 잡을 것.
                _gen = {"temperature": 0.1, "maxOutputTokens": 32768,
                        "thinkingConfig": {"thinkingBudget": 8192},
                        "responseMimeType": "application/json"}
                _timeout = 300
            else:
                prompt = _build_prompt(rk, final_q, final_t, special_q or [],
                                       line_pairs or [], strong_signals or [],
                                       fav_axis, strong_axis, drops or [], cur_mb)
                _gen = {"temperature": 0.1, "maxOutputTokens": 800,
                        "thinkingConfig": {"thinkingBudget": 0},
                        "responseMimeType": "application/json"}
                _timeout = 15
            # ⚠ 키는 URL 쿼리(?key=)가 아닌 헤더로 전달 — 쿼리로 넣으면 requests 예외 메시지에
            #   전체 URL이 실려 API 키가 콘솔/로그로 그대로 유출된다(실제 발생 확인, 2026-07-28).
            headers = {"x-goog-api-key": key}
            payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": _gen}
            print("[Gemini] %s: %s 모드 · 프롬프트 %d자" % (rk, ("전체자료" if _full else "요약"), len(prompt)))
            result = None
            last_err = ""
            for _model in _GEMINI_MODELS:
                try:
                    resp = requests.post(_GEMINI_BASE % _model, headers=headers,
                                         json=payload, timeout=_timeout)
                    if resp.status_code != 200:
                        last_err = "%s %s" % (_model, _mask(resp.text, key)[:160])
                        continue
                    _cand = (resp.json().get("candidates") or [{}])[0]
                    _parts = (_cand.get("content") or {}).get("parts") or []
                    if not _parts:
                        last_err = "%s parts 없음(finishReason=%s)" % (_model, _cand.get("finishReason"))
                        continue
                    raw = _parts[0].get("text", "").strip()
                    raw_clean = raw.replace("```json", "").replace("```", "").strip()
                    result = json.loads(raw_clean)
                    break
                except Exception as _me:
                    last_err = "%s %s" % (_model, _mask(_me, key)[:160])
                    continue
            if result is None:
                print("[Gemini] " + str(rk) + ": 검수 실패(무시) — " + last_err)
                return
            print("[Gemini] " + str(rk) + ": " + result.get("status", "?") + " — " + result.get("summary", ""))
            _save_log(rk, result)   # 로그는 항상 남긴다(발송 여부와 무관)
            # [카카오 도배 방지] WARNING 이어도 아래 조건에서만 실제 발송한다. 판정 로그는 위에서 이미 보존.
            #   ⓐ issues 가 비면 근거 없는 경고 → 보류
            #   ⓑ 진단 항목 4개를 '전부' 나열하면 실제 판별이 아니라 프롬프트 항목 되읊기일 확률이 높다
            #      (2026-07-28 실측: 초기 5건 전부 4개 동일) → 신뢰 불가로 보류
            _issues = result.get("issues") or []
            if result.get("status") == "WARNING":
                if 1 <= len(_issues) <= 3:
                    _send_kakao(rk, result)
                else:
                    print("[Gemini] " + str(rk) + ": WARNING 이지만 발송 보류(issues %d개) — 로그만 저장"
                          % len(_issues))
        except Exception as e:
            print("[Gemini] " + str(rk) + ": 에러(무시) — " + _mask(e, _gemini_api_key()))
    threading.Thread(target=_run, daemon=True, name="gemini-" + str(rk)[:10]).start()
