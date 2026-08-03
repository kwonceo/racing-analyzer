# -*- coding: utf-8 -*-
"""[keiba.go.jp(NAR 공식) 요청 제한 (2026-08-03 신설)] — netkeiba 차단을 두 번 당하지 않기 위한 관문.

■ 왜 **별도 모듈**인가 (netkeiba_guard 를 재사용하지 않은 이유)
  `netkeiba_guard` 는 **전역 단일 상태**(`data/_netkeiba_quota.json`)를 쓴다.
  거기에 keiba.go.jp 요청을 섞으면 **netkeiba 쿼터가 오염**돼 "오늘 netkeiba 에 몇 번 보냈나"를
  알 수 없게 된다. 그 카운터는 **차단 재발을 막는 유일한 근거**이므로 오염시키면 안 된다.
  🔴 그래서 `netkeiba_guard.py` 는 **한 줄도 건드리지 않고** 같은 규약의 모듈을 따로 둔다.
  ⚠ 코드가 일부 중복되지만 **격리가 우선**이다(한쪽 사고가 다른 쪽을 끌고 가지 않는다).

■ 판정선 근거 (⚠ 사후에 느슨하게 바꾸지 않는다)
  keiba.go.jp 실측: 1요청 **0.5초 · 265KB**. 정상 사용은
    ⓐ 실시간 폴백 — oddspark 실패분만이라 하루 수십 건
    ⓑ 개최일 오전 선수집 — NAR 개최 3~5장 × 12경주 ≈ **40~70요청/일**
  ⇒ 상한은 그 **여유 배수**로 잡는다. 넘으면 그날 중단한다.

■ ⚠ 이 모듈은 **세는 것과 막는 것만** 한다. 요청은 부르는 쪽이 한다.
⚠ `allow()` 가 False 면 **부르는 쪽이 반드시 멈춰야 한다.**
"""
import json
import os
import threading
import time

BASE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(BASE, "data", "_nar_quota.json")

# ── 판정선 ────────────────────────────────────────────────────────────────
#   live     = 배당 수집 창 안의 폴백(경주당 최대 1회)
#   preload  = 개최일 오전 선수집(대량) — 🔴 훨씬 느리게 건다
MIN_INTERVAL = {"live": 1.0, "preload": 1.2}
LIMIT_MIN = 30          # 분당   (선수집이 1.2초 간격이면 분당 50 이 상한이라 그보다 낮게)
LIMIT_HOUR = 300        # 시간당
LIMIT_DAY = 800         # 일일   (정상 40~70 의 10배 이상 여유 · 넘으면 그날 중단)
FAIL_STREAK_STOP = 3    # 차단코드 연속 N회면 그날 중단
BLOCK_CODES = (400, 403, 429, 503)

_LOCK = threading.RLock()
_MEM = None


def _now():
    return time.time()


def _keys(t=None):
    t = t or _now()
    lt = time.localtime(t)
    return (time.strftime("%Y-%m-%d", lt), time.strftime("%Y-%m-%d %H", lt),
            time.strftime("%Y-%m-%d %H:%M", lt))


def _load():
    global _MEM
    if _MEM is not None:
        return _MEM
    try:
        _MEM = json.load(open(STATE, encoding="utf-8"))
    except Exception:
        _MEM = {}
    return _MEM


def _save(d):
    try:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        tmp = STATE + ".%d.tmp" % threading.get_ident()   # ⚠ 스레드별 tmp(충돌 사고 재발 방지)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, STATE)
    except Exception:
        pass                                             # 저장 실패가 수집을 막으면 안 된다


def _roll(d):
    """날짜·시간·분이 바뀌면 해당 카운터만 0으로. **중단 사유는 날짜가 바뀔 때만 푼다.**"""
    dk, hk, mk = _keys()
    if d.get("day") != dk:
        d.update({"day": dk, "day_n": 0, "stopped": None, "stopped_at": None})
    if d.get("hour") != hk:
        d.update({"hour": hk, "hour_n": 0})
    if d.get("minute") != mk:
        d.update({"minute": mk, "min_n": 0})
    return d


def allow(mode="live"):
    """→ (ok, 사유). 🔴 False 면 **요청을 보내지 않는다.**"""
    with _LOCK:
        d = _roll(_load())
        if d.get("stopped"):
            return False, "중단됨(%s) — 🔴 사람이 켤 때까지 재개하지 않는다" % d["stopped"]
        gap = _now() - float(d.get("last_ts") or 0)
        need = MIN_INTERVAL.get(mode, MIN_INTERVAL["live"])
        if gap < need:
            return False, "최소 간격 미달(%.2f초 < %.1f초 · mode=%s)" % (gap, need, mode)
        if d.get("min_n", 0) >= LIMIT_MIN:
            return False, "분당 상한 %d 도달" % LIMIT_MIN
        if d.get("hour_n", 0) >= LIMIT_HOUR:
            return False, "시간당 상한 %d 도달" % LIMIT_HOUR
        if d.get("day_n", 0) >= LIMIT_DAY:
            d["stopped"] = "일일 상한 %d 도달" % LIMIT_DAY
            d["stopped_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            _save(d)
            return False, d["stopped"]
        return True, "ok"


def wait_allow(mode="live", max_wait=20.0):
    """간격 미달이면 **버리지 않고 기다린다**(상한·중단은 즉시 False).

    🔴 2026-08-02 실사고 대응: netkeiba 에서 '간격 미달 → 거부'로 만들었다가
      JRA 발주시각 조회 36건이 **전부 버려져 중앙경마 0경주 수집**이 됐다.
      ⚠ 요청 수가 늘지 않는다 — 같은 요청을 느리게 보낼 뿐이다.
    """
    t0 = _now()
    while True:
        ok, why = allow(mode)
        if ok:
            return True, why
        if "간격 미달" not in why:
            return False, why                 # 상한·중단은 기다려도 안 풀린다
        if _now() - t0 >= max_wait:
            return False, "간격 대기 초과(%.1f초) · %s" % (max_wait, why)
        time.sleep(0.2)


def record(ok=True, code=None):
    """요청 **직후** 부른다. 차단코드가 연속 `FAIL_STREAK_STOP` 회면 그날 중단한다."""
    with _LOCK:
        d = _roll(_load())
        d["last_ts"] = _now()
        d["min_n"] = d.get("min_n", 0) + 1
        d["hour_n"] = d.get("hour_n", 0) + 1
        d["day_n"] = d.get("day_n", 0) + 1
        d["total"] = d.get("total", 0) + 1
        if ok:
            d["fail_streak"] = 0
        else:
            d["fail_streak"] = d.get("fail_streak", 0) + 1
            d["last_fail_code"] = code
            d["last_fail_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            if code in BLOCK_CODES and d["fail_streak"] >= FAIL_STREAK_STOP:
                d["stopped"] = "차단코드 %s %d회 연속" % (code, d["fail_streak"])
                d["stopped_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                print("🔴🔴 [keiba.go.jp] %s → **그날 중단**. 자동 재개하지 않는다(사람이 켠다)."
                      % d["stopped"])
        _save(d)
        return d


def stats():
    with _LOCK:
        return dict(_roll(_load()))


def reset_stop(who="manual"):
    """🔴 **사람이 판단해서만** 부른다. 자동 호출 금지."""
    with _LOCK:
        d = _roll(_load())
        d["stopped"] = None
        d["fail_streak"] = 0
        d["reset_by"] = "%s @ %s" % (who, time.strftime("%Y-%m-%d %H:%M:%S"))
        _save(d)
        return d
