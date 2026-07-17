# -*- coding: utf-8 -*-
"""
[API189 경주로/함수율 op명 확정 프로브]  — 홈서버(KRA API 접근 가능)에서 실행.

사용법:
    cd C:\\Users\\USER\\Desktop\\경마분석서버
    py tools\\probe_api189.py                 # 기본: 어제 날짜·서울(meet=1)·1경주
    py tools\\probe_api189.py 20260718 1 1     # rc_date meet rc_no 직접 지정

동작:
  - 공공데이터포털 15063953 '한국마사회_경주로정보'(=API189) 후보 op 경로 × 파라미터셋을
    apis.data.go.kr/B551015 에 순차 호출.
  - 각 후보의 resultCode / item 개수 / 첫 item 필드명(함수율·경주로상태 포함 여부)을 출력.
  - ✅ 함수율/경주로상태 필드가 담긴 경로를 찾으면 그 경로와 '.env 설정법'을 안내.

키: 환경변수 KRA_API_KEY 우선, 없으면 아래 기본 키(프로젝트 발급분) 사용.
"""
import json
import os
import sys
import urllib.parse
import urllib.request
import urllib.error

DEFAULT_KEY = "ddca677dd8a964c69babb6de1d24555bd63924e1f720a9383758ac1db516835f"
BASE = "http://apis.data.go.kr/B551015"

# 후보 op 경로(B551015/ 뒤) — 형제 API 명명 + 경주로정보 데이터셋 추정명
OP_CANDIDATES = [
    "API189_1/raceCourseInfo_1", "API189/raceCourseInfo_1",
    "API189_1/API189_1", "API189/API189_1",
    "API189_1/raceCourseInfo", "API189/raceCourseInfo",
    "API189_1/getRaceCourseInfo", "API189/getRaceCourseInfo",
    "API189_1/raceCourse_1", "API189/raceCourse_1",
    "API189_1/trackStateInfo_1", "API189/trackStateInfo_1",
    "API189_1/raceCourseState_1", "API189/raceCourseState_1",
    "raceCourseInfo/getRaceCourseInfo", "raceCourseInfo/getraceCourseInfo",
]

# 파라미터셋 변형(경마장·경주일자·경주번호 명명 변형 방어)
def param_sets(rc_date, meet, rc_no):
    return [
        {"meet": meet, "rc_date": rc_date, "rc_no": rc_no},
        {"meet": meet, "rc_date": rc_date},
        {"rc_crs": meet, "race_dt": rc_date, "rc_no": rc_no},
        {"meet": meet, "rcDate": rc_date, "rcNo": rc_no},
    ]

WATER_KEYS = ("hamsuYul", "hamsuyul", "waterRatio", "waterContent", "hamsu",
              "moistRate", "moisture", "함수율", "hamSuYul")
STATE_KEYS = ("trackState", "trackStat", "trStat", "jujoState", "경주로상태",
              "주로상태", "trackCondition")


def call(path, params, key):
    p = dict(params)
    p.setdefault("_type", "json")
    p.setdefault("numOfRows", "10")
    p.setdefault("pageNo", "1")
    p["serviceKey"] = key
    url = BASE + "/" + path + "?" + urllib.parse.urlencode(p)
    try:
        raw = urllib.request.urlopen(
            urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=20
        ).read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return None, "HTTP %d" % e.code, None
    except Exception as e:
        return None, "요청실패 %s" % e, None
    try:
        d = json.loads(raw)
    except Exception:
        return None, "JSON아님(XML에러 가능): " + raw[:120].replace("\n", " "), None
    resp = (d or {}).get("response", {}) or {}
    hdr = resp.get("header", {}) or {}
    code = hdr.get("resultCode")
    msg = hdr.get("resultMsg")
    body = resp.get("body", {}) or {}
    items = (body.get("items") or {})
    item = items.get("item") if isinstance(items, dict) else items
    if isinstance(item, dict):
        item = [item]
    return item, ("%s %s" % (code, msg) if code not in (None, "00", "0") else None), (item[0] if item else None)


def has_target(it):
    if not isinstance(it, dict):
        return False, False
    w = any(it.get(k) not in (None, "", "-") for k in WATER_KEYS)
    s = any(it.get(k) not in (None, "", "-") for k in STATE_KEYS)
    return w, s


def main():
    args = sys.argv[1:]
    rc_date = args[0] if len(args) >= 1 else "20260716"
    meet = args[1] if len(args) >= 2 else "1"
    rc_no = args[2] if len(args) >= 3 else "1"
    key = os.environ.get("KRA_API_KEY", "").strip() or DEFAULT_KEY
    print("=" * 68)
    print("API189 경주로/함수율 op명 프로브  (rc_date=%s meet=%s rc_no=%s)" % (rc_date, meet, rc_no))
    print("키:", key[:10] + "…", "· base:", BASE)
    print("=" * 68)
    winners = []
    for path in OP_CANDIDATES:
        for params in param_sets(rc_date, meet, rc_no):
            item, err, first = call(path, params, key)
            pk = ",".join(k for k in params if k != "serviceKey")
            if err:
                # HTTP 404/500 은 경로 자체가 무효 → 조용히 다음 파라미터셋(같은 경로) 스킵 힌트만
                if params is param_sets(rc_date, meet, rc_no)[0]:
                    print("  ✗ %-34s [%s]  %s" % (path, pk, err))
                continue
            n = len(item or [])
            if not n:
                print("  · %-34s [%s]  resultCode=00 이나 item 0개" % (path, pk))
                continue
            w, s = has_target(first)
            keys = sorted(first.keys()) if isinstance(first, dict) else []
            tag = ("💧함수율" if w else "") + ("🏁경주로상태" if s else "")
            print("  %s %-34s [%s]  item=%d  %s" % ("✅" if (w or s) else "▶", path, pk, n, tag))
            print("      필드:", keys)
            if w or s:
                winners.append((path, pk, keys))
            break  # 이 경로는 이 파라미터셋으로 성공 → 다음 경로
    print("=" * 68)
    if winners:
        path, pk, keys = winners[0]
        op = path.split("/", 1)[1] if "/" in path else path
        print("✅ 확정 경로:", path)
        print("   파라미터:", pk)
        print("   함수율/상태 필드:", [k for k in keys if any(t in k.lower() for t in ("hamsu", "water", "moist", "track", "state", "함수", "주로", "날씨", "weather"))])
        print("")
        print("👉 서버 자동연동 방법 (둘 중 하나):")
        print("   A) 자동탐색에 맡기기: 그냥 서버 재시작하면 app.py 가 이 경로를 자동 발견·캐시합니다.")
        print("   B) 명시 고정: 프로젝트 폴더 .env 에 아래 한 줄 추가 후 서버 재시작")
        print("      KRA_TRACK_OP=%s" % op)
    else:
        print("⚠ 함수율/경주로상태 필드를 가진 경로를 못 찾았습니다.")
        print("  → 이 날짜에 개최가 없거나(다른 rc_date 로 재시도), 구독 승인이 아직 반영 안 됐을 수 있습니다.")
        print("  → 공공데이터포털 15063953 '경주로정보' 마이페이지에서 '엔드포인트/오퍼레이션'을 확인해")
        print("     정확 경로를 알려주시면 후보 목록에 바로 반영하겠습니다.")
    print("=" * 68)


if __name__ == "__main__":
    main()
