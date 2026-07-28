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
                 drops=None, cur_mb=None):
    def _run():
        try:
            with _GEMINI_LOCK:
                if time.time() - _GEMINI_CALLED.get(rk, 0) < _CALL_INTERVAL:
                    return
                _GEMINI_CALLED[rk] = time.time()
            key = _gemini_api_key()
            if not key:
                return
            prompt = _build_prompt(rk, final_q, final_t, special_q or [],
                                   line_pairs or [], strong_signals or [],
                                   fav_axis, strong_axis, drops or [], cur_mb)
            # ⚠ 키는 URL 쿼리(?key=)가 아닌 헤더로 전달 — 쿼리로 넣으면 requests 예외 메시지에
            #   전체 URL이 실려 API 키가 콘솔/로그로 그대로 유출된다(실제 발생 확인, 2026-07-28).
            headers = {"x-goog-api-key": key}
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.1,
                    "maxOutputTokens": 800,          # 300 은 2.5 계열에서 MAX_TOKENS 로 잘려 JSON 파손
                    "thinkingConfig": {"thinkingBudget": 0},   # thinking 토큰이 출력예산을 잠식하는 것 방지
                    "responseMimeType": "application/json",    # JSON 이외 출력 차단
                },
            }
            result = None
            last_err = ""
            for _model in _GEMINI_MODELS:
                try:
                    resp = requests.post(_GEMINI_BASE % _model, headers=headers,
                                         json=payload, timeout=15)
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
