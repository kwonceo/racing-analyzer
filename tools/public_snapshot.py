# -*- coding: utf-8 -*-
"""[공개 스냅샷] 회원 공개 사이트(bmed-public)로 **밀어낼** 데이터를 만든다 (2026-08-31 대표 승인 C안).

🔴 왜 푸시인가
  종전 구조: Railway(공개 사이트) → **ngrok 터널** → 우리 서버 127.0.0.1:8011
  그 터널이 끊겨 카톡 「📊 배당판 →」 링크가 회원에게 **빈 페이지**로 나가고 있었다.
  🔴 터널을 다시 열면 **8011 전체가 인터넷에 노출**된다(`/admin` 인증 없음 · `.env` 키 3종).
  ⇒ 열지 않는다. **우리 서버가 공개용만 밖으로 밀어낸다.** 인바운드 노출이 0 이다.
     (2026-08-30 읽기 전용 미러와 같은 원칙이다.)

🔴 무엇을 담나 — 공개 프론트가 **실제로 쓰는 것만**
  dashboard          /api/multi/dashboard        (순수 · 저장 호출 0 확인)
  detail[key].latest   /api/odds/triple/latest     (순수 · 저장 호출 0 확인)
  detail[key].timeline /api/odds/signal-timeline   (순수 · 저장 호출 0 확인)
  detail[key].analyze  /api/odds/triple/analyze    ⚠ 아래 참조
  ⚠ `/api/cycle/results` 는 공개 사이트가 **자기 파일**에서 읽는다 — 담지 않는다.
  ⚠ 무료/프리미엄 구분은 **공개 사이트가 이미 갖고 있다**(app_public 내부 게이트).
    여기서는 **전부 담고** 정책은 그쪽에 맡긴다(2026-08-31 대표 지시).

⚠ analyze 만 예외인 이유(숨기지 않는다)
  `/api/odds/triple/analyze` 는 **재분석·저장 부작용**이 있다(CLAUDE.md 2026-07-30).
  미러는 그래서 안 불렀다 — **조회자가 부르면 무한정 늘어나기** 때문이다.
  🔴 여기는 다르다: **60초 고정 루프 · 활성 경주만(기본 12개 상한)** 이라
    로컬 화면 한 대가 더 붙은 것과 같다(로컬 UI 는 이미 30초마다 부른다).
  ⚠ 그래도 **마감 후 경주는 대상에서 뺀다**(ACTIVE_AFTER_SEC) — 마감 후 재분석을 늘리지 않기 위해서다.

실행(점검용): python tools/public_snapshot.py
"""
import io
import os
import json
import time
import urllib.parse
import urllib.request

LOCAL = "http://127.0.0.1:8011"
TIMEOUT = 12
DASH_TIMEOUT = 30           # 🔴 dashboard 는 카드 141개라 무겁다(실측 0.7~12초+) — 넉넉히 준다
MAX_DETAIL = 12             # 상세를 담을 최대 경주 수(활성 순)
ACTIVE_BEFORE_SEC = 3600    # 발주 1시간 전부터
ACTIVE_AFTER_SEC = 300      # 🔴 마감 후 5분까지만 — 그 뒤는 안 부른다(재분석 억제)
SNAPSHOT_VER = 1


def _get(path, timeout=TIMEOUT):
    try:
        req = urllib.request.Request(LOCAL + path, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        return {"_err": str(e)[:120]}


def _post(path, body, timeout=TIMEOUT):
    try:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(LOCAL + path, data=data,
                                     headers={"Content-Type": "application/json",
                                              "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        return {"_err": str(e)[:120]}


def _cards(dash):
    for k in ("cards", "races", "items"):
        v = (dash or {}).get(k)
        if isinstance(v, list):
            return v
    return []


def _active_keys(dash):
    """상세를 담을 경주 — 발주 1시간 전 ~ 마감 5분 후. 임박한 것부터."""
    out = []
    for c in _cards(dash):
        if not isinstance(c, dict):
            continue
        k = c.get("raceKey") or c.get("key")
        if not k:
            continue
        s = c.get("secondsLeft")
        if not isinstance(s, (int, float)):
            continue
        if -ACTIVE_AFTER_SEC <= s <= ACTIVE_BEFORE_SEC:
            out.append((abs(s), k))
    out.sort()
    return [k for _, k in out[:MAX_DETAIL]]


# 🔴 [2026-08-31] 활성 밖 경주용 **슬림 analyze** — 저장분에서 이 키만 담는다.
#   왜: 대시보드 카드는 141개인데 상세는 12개뿐이라 나머지를 누르면 404 였다.
#   전체 analyze 는 경주당 53.6KB 지만 슬림은 10.9KB(80% 절감) — 오늘 전체를 담아도 gzip 약 20KB.
SLIM_KEYS = ("raceKey", "sport", "category", "summary", "corePicks",
             "strong_signals", "result", "raceShape")
LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "data", "analysis_log")


def _slim_rest(active):
    """오늘 분석된 경주 중 **활성 밖**의 것 — 저장분에서 슬림 analyze 만.

    🔴 파일만 읽는다(analyze 엔드포인트를 부르지 않는다 — 재분석·저장 부작용).
    """
    out = {}
    pre = time.strftime("%Y_%m_%d") + "_"
    try:
        names = [x for x in os.listdir(LOG_DIR) if x.startswith(pre) and x.endswith(".json")]
    except OSError:
        return out
    for nm in names:
        try:
            with io.open(os.path.join(LOG_DIR, nm), encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            continue
        rk = d.get("raceKey")
        if not rk or rk in active:
            continue
        out[rk] = {"analyze": {k: d[k] for k in SLIM_KEYS if d.get(k) is not None},
                   "slim": True}
    return out


# ══════════ [경륜 실적 · 2026-08-31] **지어낸 숫자를 실측으로 바꾼다** ══════════
#   🔴 공개 사이트가 `_DUMMY_CYCLE_RESULTS`(적중률 **100%** · 47건 · 하드코딩 5건)를 내보내고 있었다.
#     화면에는 「경륜 6개 추천 적중률 100%」가 초록 KPI 로 떴다. **전부 가짜다.**
#   ⇒ 우리 저장분에서 **실측**을 만들어 스냅샷에 담는다.
#   ⚠ 평균이 아니라 **중앙값**을 대표값으로 쓴다 — 평균은 소수 고배당에 끌린다(원칙 2).
#   ⚠ 판정선·회수율은 담지 않는다 — 「사면 번다」로 읽힐 수 있다(CLAUDE.md 광고 문구 금지).
CYCLE_TTL = 1800            # 전월 전수 스캔이 약 7초 — 30분에 한 번만 다시 센다
CYCLE_DAYS = 30
CYCLE_RECORDS = 8
_CYCLE_CACHE = {"at": 0.0, "data": None}
RESULT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "data", "race_results")


def cycle_results():
    """경륜 실측 성적. 🔴 저장분만 읽는다 · 30분 캐시."""
    if _CYCLE_CACHE["data"] and (time.time() - _CYCLE_CACHE["at"]) < CYCLE_TTL:
        return _CYCLE_CACHE["data"]
    import datetime
    cut = (datetime.date.today() - datetime.timedelta(days=CYCLE_DAYS)).strftime("%Y_%m_%d")
    n = 0
    od = []
    rec = []
    try:
        names = sorted(x for x in os.listdir(LOG_DIR) if x.endswith(".json") and x[:10] >= cut)
    except OSError:
        names = []
    for nm in names:
        try:
            with io.open(os.path.join(LOG_DIR, nm), encoding="utf-8") as f:
                d = json.load(f)
            if d.get("sport") != "cycle":
                continue
            dc = [tuple(sorted(int(x) for x in c))
                  for c in (((d.get("corePicks") or {}).get("displayedCombos") or {})
                            .get("quinellas") or []) if c and len(c) == 2]
            if not dc:
                continue
            rp = os.path.join(RESULT_DIR, nm)
            if not os.path.exists(rp):
                continue
            with io.open(rp, encoding="utf-8") as f:
                rr = json.load(f)
            if rr.get("payouts_approx") or rr.get("payouts_suspect"):
                continue
            r = rr.get("result") or {}
            po = float((rr.get("payouts") or {}).get("quinella"))      # 🔴 payouts 는 최상위(원칙 8-E)
            top2 = tuple(sorted((int(r["1st"]), int(r["2nd"]))))
        except Exception:
            continue
        n += 1
        if top2 in dc:
            od.append(po)
            rec.append({"date": nm[5:10].replace("_", "."),
                        "race": nm[11:-5].replace("_", " "),
                        "combo": "%d+%d" % top2, "odds": round(po, 1), "hit": True})
    od_sorted = sorted(od)
    med = od_sorted[len(od_sorted) // 2] if od_sorted else 0
    out = {"source": "measured",
           "kpi": {"winRate": round(100.0 * len(od) / n, 1) if n else 0,
                   "total": n, "hits": len(od),
                   "medianOdds": round(med, 1),
                   "maxOdds": round(max(od), 1) if od else 0,
                   "over10": sum(1 for x in od if x >= 10),
                   "days": CYCLE_DAYS},
           "records": rec[-CYCLE_RECORDS:][::-1],
           # 🔴 화면이 이 값을 어떻게 불러야 하는지 **여기서 정한다**(문구를 두 곳에 두지 않는다)
           "labels": {"winRate": "경륜 복승 경주 적중률",
                      "medianOdds": "적중 배당 중앙값",
                      "note": "최근 %d일 · 확정배당 보유 %d경주 실측" % (CYCLE_DAYS, n)}}
    _CYCLE_CACHE["at"] = time.time()
    _CYCLE_CACHE["data"] = out
    return out


def build(with_analyze=True):
    """공개 스냅샷 1개를 만든다. 실패한 조각은 `_err` 를 담고 **나머지는 계속**한다."""
    t0 = time.time()
    dash = _get("/api/multi/dashboard", DASH_TIMEOUT)
    detail = {}
    keys = _active_keys(dash) if not dash.get("_err") else []
    for k in keys:
        q = urllib.parse.quote(str(k), safe="")
        d = {"latest": _get("/api/odds/triple/latest?raceKey=" + q),
             "timeline": _post("/api/odds/signal-timeline", {"raceKey": k})}
        if with_analyze:
            d["analyze"] = _post("/api/odds/triple/analyze", {"raceKey": k})
        detail[k] = d
    # 🔴 활성 밖 경주도 **슬림**으로 담는다(누르면 404 나던 것을 막는다)
    try:
        for k, v in _slim_rest(set(keys)).items():
            detail.setdefault(k, v)
    except Exception as e:                                   # 실패해도 활성분은 그대로 나간다
        detail.setdefault("_slimErr", {"analyze": {"_err": str(e)[:80]}})
    snap = {"ver": SNAPSHOT_VER,
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "atEpoch": int(time.time()),
            "buildSec": round(time.time() - t0, 2),
            "dashboard": dash,
            "detail": detail,
            "detailKeys": keys,
            "cycleResults": cycle_results(),      # 🔴 지어낸 100% 를 실측으로 바꾼다
            # 🔴 dashboard 를 못 받았으면 **보낼 수 없는 스냅샷**이다.
            #   그대로 올리면 Railway 의 **멀쩡한 직전 스냅샷을 빈 것으로 덮는다.**
            #   ⚠ 실측: 서버가 바쁠 때 dashboard 가 12초를 넘겨 timed out 이 났다.
            "usable": not bool((dash or {}).get("_err")) and len(_cards(dash)) > 0}
    return snap


def size_kb(snap):
    return round(len(json.dumps(snap, ensure_ascii=False).encode("utf-8")) / 1024.0, 1)


if __name__ == "__main__":
    s = build()
    print("[공개 스냅샷] %s · %.1f초 · %.1f KB" % (s["at"], s["buildSec"], size_kb(s)))
    print("  카드 %d개 · 상세 %d경주" % (len(_cards(s["dashboard"])), len(s["detail"])))
    print("  상세 대상:", ", ".join(s["detailKeys"]) or "(없음 — 활성 경주 없음)")
    if (s["dashboard"] or {}).get("_err"):
        print("  🔴 dashboard 실패:", s["dashboard"]["_err"])
    with io.open("data/_public_snapshot_preview.json", "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=1)
    print("  미리보기 저장: data/_public_snapshot_preview.json")
