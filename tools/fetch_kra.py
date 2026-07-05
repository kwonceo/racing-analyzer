#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KRA 공공데이터(data.go.kr) 수집기 — 과거 경주성적 + 현직기수 일괄 수집.

연동 API (제공: 한국마사회 B551015)
  1) 경주별상세성적표  getracedetailresult   → data/kra_history.json 누적
  2) 현직기수정보      getcurrentjockeyinfo   → static/data/jockeys.json 갱신(실제 복승률)
  3) AI학습용 경주결과 (선택, 엔드포인트 확인 후 사용)

사용 예:
  python tools/fetch_kra.py --from 20260101 --to 20260131          # 기간 성적 수집
  python tools/fetch_kra.py --jockeys                               # 기수 DB 갱신만
  python tools/fetch_kra.py --from 20260601 --to 20260630 --jockeys # 둘 다
  python tools/fetch_kra.py --from 20260101 --to 20260131 --meet 1  # 서울만

API 키(서비스키) 지정 우선순위:
  --key 인자  >  환경변수 KRA_API_KEY  >  data/kra_key.txt (웹 UI에서 저장)
키는 data.go.kr '마이페이지 > 인증키' 의 (디코딩된 or 인코딩된) 일반 인증키.
"""

import os
import sys
import json
import time
import argparse
from datetime import datetime, timedelta

# Windows 콘솔(cp949)에서도 한글/이모지 출력이 깨지거나 죽지 않도록 UTF-8 강제
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

try:
    import requests
except ImportError:
    sys.exit("requests 모듈이 필요합니다:  pip install requests")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
HISTORY_FILE = os.path.join(DATA_DIR, "kra_history.json")
JOCKEYS_FILE = os.path.join(ROOT, "static", "data", "jockeys.json")
KEY_FILE = os.path.join(DATA_DIR, "kra_key.txt")

# ── 엔드포인트 (실제 data.go.kr 명세) ─────────────────────────────────
BASE = "http://apis.data.go.kr/B551015"
EP_RACE = f"{BASE}/racedetailresult/getracedetailresult"      # 경주별상세성적표
EP_JOCKEY = f"{BASE}/currentjockeyInfo/getcurrentjockeyinfo"    # 현직기수정보
# 한국마사회_기수통산성적비교 — 활용신청 상세페이지의 '요청주소(End Point)'로 교체 가능.
#   기본값은 명명규칙 추정치. 환경변수 KRA_JOCKEY_COMP_URL 또는 --comp-url 로 정확한 주소 지정.
EP_JOCKEY_COMP = os.environ.get("KRA_JOCKEY_COMP_URL") or f"{BASE}/jockeyResult/getJockeyResult"
# AI학습용 경주결과: 신청 페이지의 요청주소로 교체 후 --ai 사용 (파라미터 rccrs_cd, race_dt)
EP_AI = f"{BASE}/AI_RaceResult/getAiRaceResult"                 # ⚠ 확인 필요(플레이스홀더)

MEETS = {1: "서울", 2: "제주", 3: "부경"}
NUM_ROWS = 100
SLEEP = 0.25            # 호출 간 간격(초) — 트래픽 제한 배려


# ── 유틸 ─────────────────────────────────────────────────────────────
def _env_from_dotenv(name):
    """의존성 없이 .env 에서 name 값 읽기 (app.py 와 동일 방식)."""
    path = os.path.join(ROOT, ".env")
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == name:
                return v.strip().strip('"').strip("'")
    return ""


def load_key(cli_key):
    if cli_key:
        return cli_key.strip()
    env = os.environ.get("KRA_API_KEY")
    if env:
        return env.strip()
    dot = _env_from_dotenv("KRA_API_KEY")     # .env 파일 직접 지원
    if dot:
        return dot.strip()
    if os.path.exists(KEY_FILE):
        with open(KEY_FILE, encoding="utf-8") as f:
            return f.read().strip()
    return ""


def _num(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def api_get(url, params, key):
    """data.go.kr GET → items(list). serviceKey는 인코딩키(%포함)면 그대로 전달."""
    p = dict(params)
    p.setdefault("_type", "json")
    p.setdefault("numOfRows", NUM_ROWS)
    p.setdefault("pageNo", 1)
    # requests가 serviceKey를 재인코딩하지 않도록 이미 인코딩된 키는 URL에 직접 부착
    sep = "&" if "?" in url else "?"
    if "%" in key:
        full = f"{url}{sep}serviceKey={key}"
        r = requests.get(full, params=p, timeout=20)
    else:
        p2 = dict(p); p2["serviceKey"] = key
        r = requests.get(url, params=p2, timeout=20)
    # HTTP 오류 시 data.go.kr 응답 본문을 함께 노출(401 원인 진단: 미승인/미활성/키형식)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code} — {r.text[:200].strip() or '(빈 응답)'} "
                           f"[키길이 {len(key)}, %{'O' if '%' in key else 'X'}]")
    # JSON 우선, 실패 시 원문 반환(에러 메시지 노출용)
    try:
        data = r.json()
    except ValueError:
        raise RuntimeError(f"JSON 파싱 실패(키/엔드포인트 확인). 응답: {r.text[:300]}")
    resp = (data or {}).get("response", {})
    header = resp.get("header", {})
    code = header.get("resultCode")
    if code not in (None, "00", "0"):
        raise RuntimeError(f"API 오류 {code}: {header.get('resultMsg')}")
    body = resp.get("body", {}) or {}
    items = (body.get("items") or {})
    item = items.get("item") if isinstance(items, dict) else items
    if item is None:
        return [], int(body.get("totalCount") or 0)
    if isinstance(item, dict):
        item = [item]
    return item, int(body.get("totalCount") or len(item))


def paged(url, params, key, label=""):
    """페이지네이션으로 전체 item 수집."""
    out, page = [], 1
    while True:
        params = dict(params); params["pageNo"] = page
        try:
            items, total = api_get(url, params, key)
        except Exception as e:
            print(f"  ! {label} p{page} 실패: {e}")
            break
        out.extend(items)
        if len(out) >= total or not items or len(items) < NUM_ROWS:
            break
        page += 1
        time.sleep(SLEEP)
    return out


def daterange(d_from, d_to):
    a = datetime.strptime(d_from, "%Y%m%d")
    b = datetime.strptime(d_to, "%Y%m%d")
    while a <= b:
        yield a.strftime("%Y%m%d")
        a += timedelta(days=1)


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


# ── 1) 경주성적 수집 → kra_history.json ──────────────────────────────
def fetch_races(d_from, d_to, meets, key):
    hist = load_json(HISTORY_FILE, {})
    races = hist.get("races", {})
    by_horse = hist.get("byHorse", {})
    added = 0
    for date in daterange(d_from, d_to):
        for meet in meets:
            items = paged(EP_RACE, {"meet": meet, "rc_date": date}, key,
                          label=f"{MEETS[meet]} {date}")
            if not items:
                continue
            # 경주번호별로 묶기
            for it in items:
                rc_no = str(it.get("rcNo") or it.get("rc_no") or "")
                rkey = f"{meet}_{date}_{rc_no}"
                # [보완#3] 경주 거리(m) — 엔드포인트별 필드명 방어. 없으면 None(거리경험 훅 비활성 유지).
                rc_dist = _num(it.get("rcDist") or it.get("rc_dist") or it.get("distance") or it.get("rcLength"))
                horse = {
                    "stOrd": _num(it.get("stOrd") or it.get("ord")),      # 착순
                    "no": _num(it.get("chulNo")),                          # 마번(출전번호)
                    "hrId": (str(it.get("hrNo") or "")).strip(),           # 마 등록번호
                    "hrName": (it.get("hrName") or "").strip(),
                    "jkName": (it.get("jkName") or "").strip(),
                    "trName": (it.get("trName") or "").strip(),
                    "win": _num(it.get("win")),                            # 단승 확정배당
                    "plc": _num(it.get("plc")),                            # 복승 확정배당
                    "rcTime": it.get("rcTime"),
                    "wgHr": it.get("wgHr"),                                # 마체중
                    "wgBudam": it.get("wgBudam"),                          # 부담중량
                    "hrRating": _num(it.get("hrRating")),                  # 레이팅
                }
                rec = races.setdefault(rkey, {"meet": meet, "date": date, "rcNo": rc_no,
                                              "rcDist": rc_dist, "horses": []})
                if rec.get("rcDist") is None and rc_dist is not None:
                    rec["rcDist"] = rc_dist   # 기존 레코드 백필
                # 같은 마번 중복 방지
                if not any(h.get("no") == horse["no"] for h in rec["horses"]):
                    rec["horses"].append(horse)
                    added += 1
                # 마명 인덱스(과거기록 자동매칭용)
                nm = horse["hrName"]
                if nm:
                    arr = by_horse.setdefault(nm, [])
                    tag = f"{date}_{meet}_{rc_no}"
                    if not any(x.get("_tag") == tag for x in arr):
                        arr.append({"_tag": tag, "date": date, "meet": meet, "rcNo": rc_no,
                                    "stOrd": horse["stOrd"], "win": horse["win"], "rcDist": rc_dist,
                                    "jkName": horse["jkName"], "hrRating": horse["hrRating"]})
            print(f"  · {MEETS[meet]} {date}: {len(items)}두")
            time.sleep(SLEEP)
    hist["races"] = races
    hist["byHorse"] = by_horse
    hist["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    save_json(HISTORY_FILE, hist)
    print(f"✓ 경주성적: {added}두 신규 · 총 {len(races)}경주 → {HISTORY_FILE}")
    return added


# ── 2) 현직기수 → jockeys.json 갱신(실제 복승률) ─────────────────────
def fetch_jockeys(meets, key):
    db = load_json(JOCKEYS_FILE, {"jockeys": []})
    existing = {j.get("name"): j for j in db.get("jockeys", [])}
    count = 0
    for meet in meets:
        items = paged(EP_JOCKEY, {"meet": meet}, key, label=f"기수 {MEETS[meet]}")
        for it in items:
            name = (it.get("jkName") or "").strip()
            if not name:
                continue
            rc = _num(it.get("rcCntT")) or 0                       # 통산 출전
            o1 = _num(it.get("ord1CntT")) or 0
            o2 = _num(it.get("ord2CntT")) or 0
            o3 = _num(it.get("ord3CntT")) or 0
            win_rate = _num(it.get("winRateT"))
            plc_rate = _num(it.get("plcRateT"))
            if win_rate is None:
                win_rate = round(o1 / rc * 100, 1) if rc else 0.0
            if plc_rate is None:                                    # 복승권(3착내)율
                plc_rate = round((o1 + o2 + o3) / rc * 100, 1) if rc else 0.0
            j = existing.get(name, {})
            j.update({
                "name": name, "track": MEETS.get(meet, j.get("track", "")),
                "winRate": win_rate, "placeRate": plc_rate, "rides": int(rc),
                "jkNo": it.get("jkNo"), "kraSynced": True,
            })
            # 확장 필드 기본값 보존(앱이 결과 기록으로 채움)
            j.setdefault("recentForm", "")
            j.setdefault("byDistance", {}); j.setdefault("byTrack", {}); j.setdefault("byHorse", {})
            existing[name] = j
            count += 1
        time.sleep(SLEEP)
    db["jockeys"] = list(existing.values())
    db["updated"] = datetime.now().strftime("%Y-%m-%d")
    db["kraUpdated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    save_json(JOCKEYS_FILE, db)
    print(f"✓ 현직기수: {count}명 갱신(실제 복승률 반영) → {JOCKEYS_FILE}")
    return count


# ── 2b) 기수통산성적비교 → jockeys.json 통산 상세(career) 보강 ────────────
def fetch_jockey_comp(meets, key, url=None):
    """한국마사회_기수통산성적비교: 기수별 통산 상세성적(연도/경마장 비교)을 병합.
    현직기수정보에 없는 세부 필드를 career 로 채운다. 엔드포인트가 다르면 --comp-url 로 지정."""
    ep = url or EP_JOCKEY_COMP
    db = load_json(JOCKEYS_FILE, {"jockeys": []})
    existing = {j.get("name"): j for j in db.get("jockeys", [])}
    count = 0
    for meet in meets:
        items = paged(ep, {"meet": meet}, key, label=f"기수통산 {MEETS.get(meet, meet)}")
        for it in items:
            name = (it.get("jkName") or it.get("jockeyName") or "").strip()
            if not name:
                continue
            # 통산(T)·올해(Y) 착별 카운트를 방어적으로 수집(제공 필드명 편차 대응)
            def g(*keys):
                for k in keys:
                    v = it.get(k)
                    if v not in (None, ""):
                        return _num(v)
                return None
            career = {
                "totalRides": g("rcCntT", "totRcCnt", "rcCnt"),
                "win": g("ord1CntT", "totOrd1Cnt", "ord1Cnt"),
                "second": g("ord2CntT", "totOrd2Cnt", "ord2Cnt"),
                "third": g("ord3CntT", "totOrd3Cnt", "ord3Cnt"),
                "winRate": g("winRateT", "totWinRate", "winRate"),
                "placeRate": g("plcRateT", "totPlcRate", "plcRate"),
                "thisYearRides": g("rcCntY", "yrRcCnt"),
                "thisYearWin": g("ord1CntY", "yrOrd1Cnt"),
            }
            j = existing.get(name, {"name": name})
            j["career"] = {k: v for k, v in career.items() if v is not None}
            # 현직기수정보가 비어있으면 통산비교 값으로 승률/복승률 보강
            if j.get("placeRate") in (None, 0) and career.get("placeRate"):
                j["placeRate"] = career["placeRate"]
            if j.get("winRate") in (None, 0) and career.get("winRate"):
                j["winRate"] = career["winRate"]
            j["kraCompSynced"] = True
            existing[name] = j
            count += 1
        time.sleep(SLEEP)
    db["jockeys"] = list(existing.values())
    db["kraCompUpdated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    save_json(JOCKEYS_FILE, db)
    print(f"✓ 기수통산성적비교: {count}명 career 보강 → {JOCKEYS_FILE}")
    return count


def main():
    ap = argparse.ArgumentParser(description="KRA 공공데이터 수집기")
    ap.add_argument("--from", dest="d_from", help="시작일 YYYYMMDD")
    ap.add_argument("--to", dest="d_to", help="종료일 YYYYMMDD (미지정 시 시작일과 동일)")
    ap.add_argument("--meet", type=int, choices=[1, 2, 3], help="경마장 1서울/2제주/3부경 (미지정=전체)")
    ap.add_argument("--jockeys", action="store_true", help="현직기수 DB 갱신(+기수통산성적비교 병합)")
    ap.add_argument("--comp-url", help="기수통산성적비교 요청주소(EndPoint) — data.go.kr 활용신청 상세의 정확한 주소")
    ap.add_argument("--no-comp", action="store_true", help="기수통산성적비교 병합 생략")
    ap.add_argument("--key", help="data.go.kr 서비스키 (미지정 시 env/파일)")
    args = ap.parse_args()

    key = load_key(args.key)
    if not key:
        print("✗ API 키가 없습니다. --key, 환경변수 KRA_API_KEY, 또는 웹 UI에서 저장(data/kra_key.txt) 하세요.")
        print("  data.go.kr 에서 '한국마사회_경주별상세성적표'·'현직기수정보' 활용신청 후 인증키 발급.")
        sys.exit(1)

    meets = [args.meet] if args.meet else [1, 2, 3]
    if not args.d_from and not args.jockeys:
        ap.error("--from(기간) 또는 --jockeys 중 하나는 필요합니다.")

    if args.d_from:
        d_to = args.d_to or args.d_from
        print(f"■ 경주성적 수집 {args.d_from}~{d_to} · {[MEETS[m] for m in meets]}")
        fetch_races(args.d_from, d_to, meets, key)
    if args.jockeys:
        print(f"■ 현직기수 갱신 · {[MEETS[m] for m in meets]}")
        fetch_jockeys(meets, key)
        if not args.no_comp:
            print(f"■ 기수통산성적비교 병합 · EndPoint={args.comp_url or EP_JOCKEY_COMP}")
            try:
                fetch_jockey_comp(meets, key, args.comp_url)
            except Exception as e:
                print(f"  ! 기수통산성적비교 실패(엔드포인트 확인 필요, --comp-url 로 지정): {e}")
    print("완료.")


if __name__ == "__main__":
    main()
