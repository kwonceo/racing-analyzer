#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""기수 DB 초기 데이터 생성 (한국 주요 기수 10명, 결정적 샘플).
확장 필드: recent30(최근30경주), byDistance(거리별), byTrack(주로상태별), byHorse(기수-마필 조합).
실제 수치는 경주 기록이 쌓이면 JockeyDB.recordRace 로 자동 갱신된다.
실행: python tools/gen_jockeys.py  → static/data/jockeys.json
"""
import os, json

# (이름, 소속, 승률%, 복승권율%) — 한국 주요 기수 10명
BASE = [
    ("문세영", "서울", 18.5, 42.1), ("임성실", "서울", 14.2, 38.4),
    ("김용근", "서울", 12.8, 35.0), ("정도윤", "서울", 11.0, 33.2),
    ("이찬호", "서울", 10.3, 31.5), ("서승운", "부산", 16.7, 40.0),
    ("유현명", "부산", 13.5, 36.8), ("조성곤", "부산", 12.1, 34.4),
    ("김혜선", "부산", 9.8, 30.0),  ("박정수", "서울", 8.9, 28.5),
]
# 거리는 고정 구간이 아니라 '실제 거리값'을 키로 사용(동적). 시드는 흔한 거리만,
# 새 거리는 결과 기록 시 JockeyDB.recordRace 가 자동 추가한다.
DISTANCES = ["1000", "1200", "1400", "1800"]
DIST_OFF = {"1000": 2.0, "1200": 5.0, "1400": -1.0, "1800": -4.0}  # 시드 거리별 적성 가감
TRACKS = ["양호", "다습", "불량", "건조"]
TRACK_OFF = {"양호": 2.0, "다습": -3.0, "불량": -6.0, "건조": 1.0}
SAMPLE_HORSES = ["천둥질주", "에레노아퀸", "번개의질주", "황금마차"]


def stat(rides, rate):
    rides = max(0, int(rides))
    return {"rides": rides, "places": round(rides * max(rate, 0) / 100)}


def build():
    jockeys = []
    for i, (name, track, wr, pr) in enumerate(BASE):
        rides = 760 + i * 55                      # 결정적 누적 기승수
        wins = round(rides * wr / 100)
        places = round(rides * pr / 100)
        recent_pr = min(60, pr + 6 - (i % 3) * 2)  # 최근 폼(약간 변동)
        j = {
            "name": name, "track": track, "winRate": wr, "placeRate": pr,
            "rides": rides, "recentForm": "",
            "recent30": {"rides": 30, "wins": round(30 * wr / 100), "places": round(30 * recent_pr / 100)},
            "byDistance": {d: stat(rides // 4, pr + DIST_OFF[d]) for d in DISTANCES},
            "byTrack": {t: stat(rides // 4, pr + TRACK_OFF[t]) for t in TRACKS},
            "byHorse": {},
        }
        # 상위 3명에 기수-마필 조합 샘플
        if i < 3:
            h = SAMPLE_HORSES[i]
            j["byHorse"][h] = {"rides": 6 + i, "wins": 2 + (i % 2), "places": 4 + i}
        jockeys.append(j)
    return {
        "updated": "2026-06-30",
        "description": ("기수 DB (KRA 주요 10명 초기 샘플). winRate=승률%, placeRate=복승권(3착내)율%. "
                        "recent30=최근30경주, byDistance=거리별, byTrack=주로상태별, byHorse=기수-마필 조합. "
                        "경주 결과 입력 시 자동 갱신됨."),
        "jockeys": jockeys,
    }


if __name__ == "__main__":
    out = os.path.join(os.path.dirname(__file__), "..", "static", "data", "jockeys.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(build(), f, ensure_ascii=False, indent=2)
    print("wrote", os.path.abspath(out), "—", len(BASE), "jockeys")
