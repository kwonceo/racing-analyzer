# -*- coding: utf-8 -*-
"""[netkeiba 요청 제한 (2026-08-02 신설)] — **차단을 두 번 당하지 않기 위한 유일한 관문**.

■ 왜 만들었나 (실측 근거)
  2026-08-02 01:06:39~01:08:52 에 **588건을 132.5초**에 저장했다 = **4.4 req/s · 분당 265**.
  그 직후 netkeiba 가 **전 서브도메인 HTTP 400** 으로 우리 IP 를 막았다.
  🔴 **정상 수집은 문제가 아니었다** — 하루 36경주 × 15틱 × 2요청 ≈ **1,080요청/일 · 피크 분당 6**.
  문제는 **소급 수집을 몰아친 것**이다. 그래서 제한은 **소급을 훨씬 더 느리게** 건다.

■ 무엇을 하는가
  ① 요청 간 **최소 간격**(실시간 1.0초 · 소급 4.0초)
  ② **분당·시간당·일일 상한** — 일일을 넘으면 **그날은 중단**
  ③ **400/403 이 연속 3회면 즉시 중단 + 기록** — 🔴 **자동 재시도하지 않는다. 사람이 켠다.**
  ④ 카운터를 **파일에 남긴다** — 재기동해도 이어진다. "오늘 몇 번 보냈나"를 항상 알 수 있다.

■ ⚠ 이 모듈은 **세는 것과 막는 것만** 한다. 요청은 부르는 쪽이 한다(의존을 만들지 않는다).
⚠ 실패해도 예외를 위로 올리지 않는다 — 단, `allow()` 가 False 면 **부르는 쪽이 반드시 멈춰야 한다.**
"""
import json
import os
import threading
import time

BASE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(BASE, "data", "_netkeiba_quota.json")

# ── 판정선 (⚠ 사후에 느슨하게 바꾸지 않는다 — 바꾸려면 근거를 함께 적는다) ──────────
MIN_INTERVAL = {"live": 1.0, "backfill": 4.0}   # 요청 간 최소 간격(초)
LIMIT_MIN = 40          # 분당   (정상 피크 6 의 6.7배 여유)
LIMIT_HOUR = 400        # 시간당
LIMIT_DAY = 2000        # 일일   (정상 1,080 의 1.9배 여유)
FAIL_STREAK_STOP = 3    # 400/403 연속 N회면 그날 중단
BLOCK_CODES = (400, 403, 429)

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
    """날짜·시간·분이 바뀌면 해당 카운터만 0으로. **일일 중단 사유는 날짜가 바뀔 때만 푼다.**"""
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


def record(ok=True, code=None):
    """요청 **직후** 부른다. 400/403/429 가 `FAIL_STREAK_STOP` 회 연속이면 그날 중단한다."""
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
                print("🔴🔴 [netkeiba] %s → **그날 중단**. 자동 재개하지 않는다(사람이 켠다)."
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
