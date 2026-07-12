# -*- coding: utf-8 -*-
"""[다중 경주 동시 배당판] 단위 테스트 — 저장소 격리·손상복구·스케줄 파싱·카드·triple_store 무영향."""
import importlib.util
import json
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("appmod", os.path.join(ROOT, "app.py"))
m = importlib.util.module_from_spec(spec)
sys.modules["appmod"] = m
spec.loader.exec_module(m)

P = F = 0


def ok(name, cond):
    global P, F
    if cond:
        P += 1
        print("  ✅", name)
    else:
        F += 1
        print("  ❌", name)


td = tempfile.mkdtemp()
m.MULTI_STORE_FILE = os.path.join(td, "multi.json")
m.SCHEDULE_FILE = os.path.join(td, "sched.json")
m.TRIPLE_STORE = os.path.join(td, "triple.json")   # 격리(오염 방지)
m.STARTERS_STORE = os.path.join(td, "starters.json")

print("=== [다중경주] 저장소 격리·손상복구 ===")
# 빈 로드
ok("빈 저장소 로드 = {}", m._multi_store_load() == {})
# 저장·재로드
m._multi_store_save({"사가 9경주": {"quinella": [], "t": 1}})
ok("저장 후 재로드", m._multi_store_load().get("사가 9경주") is not None)
# 손상 파일 → 자동 초기화
open(m.MULTI_STORE_FILE, "w", encoding="utf-8").write("{not json!!!")
ok("손상 시 자동 초기화 = {}", m._multi_store_load() == {})
ok("손상 파일 자동 삭제됨", not os.path.exists(m.MULTI_STORE_FILE))

print("=== [1번] 발주시각 파싱 ===")
ep = m._post_time_epoch("15:30", "20260711")
ok("HH:MM+ymd → epoch", isinstance(ep, float) and ep > 0)
ok("잘못된 형식 → None", m._post_time_epoch("bad", "20260711") is None)
ok("빈 ymd → None", m._post_time_epoch("15:30", "") is None)

print("=== [1번] RaceList 경주번호+発走時間 파싱(모의 HTML·경주별 fetch) ===")
_orig_fetch = m._keirin_fetch
m._MULTI_POST_CACHE = {}   # 캐시 격리
_PT = {1: "14:05", 2: "14:35", 9: "17:20"}
_LIST_HTML = ('<a href="RaceList.do?raceNb=1">1R</a>'
              '<a href="RaceList.do?raceNb=2">2R</a>'
              '<a href="RaceList.do?raceNb=9">9R</a>')


def _mock_fetch(url):
    mm = m.re.search(r"raceNb=(\d+)", url)
    if mm:   # 경주별 페이지 → 그 경주의 発走時間(実 oddspark 라벨)
        return "발주정보 発走時間 %s 그외" % _PT.get(int(mm.group(1)), "00:00")
    return _LIST_HTML   # 목록 페이지 → raceNb 링크만


m._keirin_fetch = _mock_fetch
try:
    races = m._multi_race_list("30", "26", "20260711")
    ok("경주 3개 파싱", len(races) == 3)
    ok("1R 発走時間 14:05", races[0]["raceNo"] == 1 and races[0]["postTime"] == "14:05")
    ok("9R 発走時間 17:20", races[-1]["raceNo"] == 9 and races[-1]["postTime"] == "17:20")
    ok("postEpoch 산출", races[0]["postEpoch"] is not None)
    ok("발주시각 캐시 동작", (("20260711", "30", "26", 1) in m._MULTI_POST_CACHE))
finally:
    m._keirin_fetch = _orig_fetch
    m._MULTI_POST_CACHE = {}

print("=== [3번] 카드 요약(_triple_analyze 재사용·읽기전용) ===")
# 최소 rec 구성(복승 배당) → 카드 생성
rec = {
    "quinella": [{"combo": [1, 2], "odds": 3.0}, {"combo": [1, 3], "odds": 5.0}, {"combo": [2, 3], "odds": 8.0}],
    "exacta": [], "trio": [], "win": {},
    "history": [{"t": 1, "quinella": [{"combo": [1, 2], "odds": 3.0}], "exacta": [], "trio": [], "win": {}}],
    "sport": "horse", "category": "japan_local", "venue": "사가", "raceNo": 9,
    "postTime": "17:20", "postEpoch": m._post_time_epoch("17:20", "20260711"), "t": 1,
}
card = m._multi_card("사가 9경주", rec)
ok("카드 생성됨", card is not None)
if card:
    ok("venue/raceNo", card.get("venue") == "사가" and card.get("raceNo") == 9)
    ok("keyHorses TOP3(최대 3두)", isinstance(card.get("keyHorses"), list) and len(card["keyHorses"]) <= 3)
    ok("urgency 필드 존재", card.get("urgency") in ("normal", "warn", "urgent"))

print("=== [2번] 수집이 triple_store를 건드리지 않음(핵심 원칙) ===")
# triple_store 초기 상태 스냅샷
m._triple_save({"기존단일 5경주": {"quinella": [], "t": 100}})
before = json.load(open(m.TRIPLE_STORE, encoding="utf-8"))
# 모의 수집(fetch/parse/live 모킹)
_of, _oq, _ox, _ol = m._keirin_fetch, m._keiba_parse_quinella, m._keiba_parse_exacta, m._keiba_odds_live
m._keirin_fetch = lambda url: ""
m._keiba_parse_quinella = lambda html: [{"combo": [1, 2], "odds": 3.4}, {"combo": [1, 3], "odds": 5.1}, {"combo": [2, 3], "odds": 7.7}]
m._keiba_parse_exacta = lambda html: []
m._keiba_odds_live = lambda q, x: True
try:
    key = m._multi_collect_one({"venue": "코치", "opTrackCd": "31", "sponsorCd": "26"},
                               {"raceNo": 5, "postTime": "18:00", "postEpoch": 1}, "20260711")
    ok("수집 → multi_race_store 저장", key == "코치 5경주" and m._multi_store_load().get("코치 5경주") is not None)
    after = json.load(open(m.TRIPLE_STORE, encoding="utf-8"))
    ok("triple_store 불변(무영향)", before == after)
finally:
    m._keirin_fetch, m._keiba_parse_quinella, m._keiba_parse_exacta, m._keiba_odds_live = _of, _oq, _ox, _ol

print("=== [6번] 스케줄 실패해도 예외 전파 안 함(단일 모드 유지) ===")
_os = m._keiba_schedule
m._keiba_schedule = lambda ymd, force=False: (_ for _ in ()).throw(RuntimeError("network down"))
try:
    sched = m._multi_schedule_fetch()
    ok("스케줄 실패 → 빈 tracks 반환(예외 없음)", isinstance(sched, dict) and sched.get("tracks") == [])
finally:
    m._keiba_schedule = _os

print("=" * 56)
print("결과: 통과 %d / 실패 %d" % (P, F))
print("=" * 56)
sys.exit(1 if F else 0)
