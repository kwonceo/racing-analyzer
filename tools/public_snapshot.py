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
import json
import time
import urllib.parse
import urllib.request

LOCAL = "http://127.0.0.1:8011"
TIMEOUT = 12
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


def build(with_analyze=True):
    """공개 스냅샷 1개를 만든다. 실패한 조각은 `_err` 를 담고 **나머지는 계속**한다."""
    t0 = time.time()
    dash = _get("/api/multi/dashboard")
    detail = {}
    keys = _active_keys(dash) if not dash.get("_err") else []
    for k in keys:
        q = urllib.parse.quote(str(k), safe="")
        d = {"latest": _get("/api/odds/triple/latest?raceKey=" + q),
             "timeline": _post("/api/odds/signal-timeline", {"raceKey": k})}
        if with_analyze:
            d["analyze"] = _post("/api/odds/triple/analyze", {"raceKey": k})
        detail[k] = d
    snap = {"ver": SNAPSHOT_VER,
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "atEpoch": int(time.time()),
            "buildSec": round(time.time() - t0, 2),
            "dashboard": dash,
            "detail": detail,
            "detailKeys": keys}
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
