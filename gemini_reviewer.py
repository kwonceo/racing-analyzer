import os, json, threading, time, requests
from datetime import datetime

_GEMINI_CALLED = {}
_GEMINI_LOCK = threading.Lock()
_CALL_INTERVAL = 300
_LOG_DIR = os.path.join(os.path.dirname(__file__), "logs", "gemini_review")
os.makedirs(_LOG_DIR, exist_ok=True)
_GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"

def _gemini_api_key():
    return (os.environ.get("GEMINI_API_KEY") or "").strip()

def _build_prompt(rk, final_q, final_t, special_q, line_pairs, strong_signals, fav_axis, strong_axis, drops, cur_mb):
    def fmt_combo(lst):
        if not lst: return "없음"
        return ", ".join("+".join(str(x) for x in (item.get("combo") or [])) +
            (f"({item.get(\'odds\')}배)" if item.get("odds") else "") for item in lst[:4])
    drop_txt = ", ".join(f"{d.get(\'combo\', d.get(\'no\', \'?\'))} {d.get(\'pct\', 0):.1f}%"
        for d in sorted(drops, key=lambda x: x.get("pct", 0))[:3]) if drops else "없음"
    return f"""너는 BMED 경마/경륜 시스템의 수석 로직 검수관이다. 반드시 JSON만 출력해라.
[경주] {rk} [마감까지] {cur_mb}분 [왕축] {fav_axis} strongAxis={strong_axis}
[강신호] {len(strong_signals or [])}건 [급락] {drop_txt}
[라인] {json.dumps(line_pairs or [], ensure_ascii=False)}
[복승] {fmt_combo(final_q)} [삼복승] {fmt_combo(final_t)} [보조] {fmt_combo(special_q)}
진단: 1)맹목적왕축 2)B라인누락 3)라인교차 4)급락미반영
출력형식: {{"status":"SAFE"또는"WARNING","issues":[],"summary":"한줄","q_suggest":"","t_suggest":""}}"""

def _save_log(rk, prompt, result):
    try:
        safe = "".join(c if c.isalnum() or "가" <= c <= "힣" else "_" for c in rk)
        ts = datetime.now().strftime("%H%M%S")
        path = os.path.join(_LOG_DIR, f"{datetime.now().strftime('%Y%m%d')}_{safe}_{ts}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"raceKey": rk, "time": datetime.now().isoformat(), "result": result}, f, ensure_ascii=False, indent=2)
    except Exception: pass

def _send_kakao(rk, result):
    try:
        msg = f"\U0001f6a8 [{rk}] Gemini 경고\n{result.get('summary','')}\n"
        issues = result.get("issues", [])
        if issues: msg += "\u26a0 " + " / ".join(issues[:2]) + "\n"
        if result.get("q_suggest"): msg += f"\U0001f4a1 권장복승: {result['q_suggest']}\n"
        if result.get("t_suggest"): msg += f"\U0001f4a1 권장삼복승: {result['t_suggest']}"
        import importlib
        app_mod = importlib.import_module("app")
        for fn in ["_kakao_send_text", "_send_kakao_msg", "kakao_send"]:
            fn_obj = getattr(app_mod, fn, None)
            if fn_obj: fn_obj(msg); break
    except Exception: pass

def review_async(rk, final_q, final_t, special_q=None, line_pairs=None,
                 strong_signals=None, fav_axis=None, strong_axis=True, drops=None, cur_mb=None):
    def _run():
        try:
            with _GEMINI_LOCK:
                if time.time() - _GEMINI_CALLED.get(rk, 0) < _CALL_INTERVAL: return
                _GEMINI_CALLED[rk] = time.time()
            key = _gemini_api_key()
            if not key: return
            prompt = _build_prompt(rk, final_q, final_t, special_q or [], line_pairs or [],
                                   strong_signals or [], fav_axis, strong_axis, drops or [], cur_mb)
            resp = requests.post(f"{_GEMINI_URL}?key={key}",
                json={"contents": [{"parts": [{"text": prompt}]}],
                      "generationConfig": {"temperature": 0.1, "maxOutputTokens": 300}}, timeout=5)
            resp.raise_for_status()
            raw = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            result = json.loads(raw.replace("```json","").replace("```","").strip())
            print(f"[Gemini] {rk}: {result.get('status')} — {result.get('summary','')}")
            _save_log(rk, prompt, result)
            if result.get("status") == "WARNING": _send_kakao(rk, result)
        except Exception as e:
            print(f"[Gemini] {rk}: 에러(무시) — {e}")
    threading.Thread(target=_run, daemon=True, name=f"gemini-{rk[:10]}").start()
