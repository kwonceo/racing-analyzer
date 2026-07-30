# -*- coding: utf-8 -*-
"""[아메다스 매핑 생성기 (2026-07-31)]

경기장 **표준키** → JMA 아메다스 **최근접 관측지점** 매핑표를 1회 생성한다.

⚠ 표준키 기준이다. `_TRACK_GROUPS`/`_track_norm` 이 만드는 표준키를 그대로 쓴다 —
  한자(`平塚`)·한글(`히라츠카`)·영문(`hiratsuka`)이 전부 같은 키로 모이므로
  매핑표는 표준키 하나만 담으면 된다(별칭 중복 불필요).

⚠ 개최지가 늘면 이 스크립트를 다시 돌린다. 거리는 **다시 재야 한다**.

산출: data/amedas_map.json
  { "<표준키>": {"station": "44132", "name": "東京", "km": 3.2, "lat":…, "lon":…}, … }

사용:
  python tools/build_amedas_map.py            # 생성 + 거리표 출력
  python tools/build_amedas_map.py --dry      # 출력만(파일 미기록)
"""
import os
import sys
import json
import math
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "amedas_map.json")
TABLE_URL = "https://www.jma.go.jp/bosai/amedas/const/amedastable.json"

# 🔴 임계 20km — 초과 시 매핑하지 않는다(값 없이 "제공되지 않는 정보"로 남긴다).
#   근거: 지상 바람의 국지 변동 규모(해륙풍·산곡풍)가 대략 10~20km.
#   ⚠ 틀린 바람보다 없는 게 낫다.
MAX_KM = 20.0

# ══════════════ 경기장 좌표 (표준키 기준) ══════════════
#  ⚠ 한국 경마장(서울·부산·제주)은 **아메다스가 일본 전용**이라 넣지 않는다 →
#    조회 시 자동으로 "제공되지 않는 정보"가 된다(지어내는 것보다 부재가 안전).
VENUE_LATLON = {
    # ── 경륜장(競輪場) ──
    "다치카와": (35.7086, 139.4178), "마쓰도": (35.7906, 139.9264),
    "케이오카쿠": (35.6497, 139.5231), "세이부엔": (35.7719, 139.4489),
    "도리데": (35.9089, 140.0722), "우쓰노미야": (36.5658, 139.9033),
    "마에바시": (36.3814, 139.0664), "히라츠카": (35.3242, 139.3450),
    "오다와라": (35.2606, 139.1622), "이토": (34.9711, 139.0958),
    "시즈오카": (34.9808, 138.4064), "이와키타이라": (37.0553, 140.8892),
    "아오모리": (40.8272, 140.7539), "야히코": (37.7061, 138.8300),
    "도야마": (36.7286, 137.1592), "카나자와": (36.5900, 136.6428),
    "기후": (35.4231, 136.7597), "도요하시": (34.7500, 137.3814),
    "나라": (34.6839, 135.8331), "와카야마": (34.2311, 135.1739),
    "기시와다": (34.4694, 135.3722), "다마노": (34.4919, 133.9422),
    "히로시마": (34.3892, 132.5019), "다카마쓰": (34.3436, 134.0553),
    "마쓰야마": (33.8397, 132.7778), "구루메": (33.3236, 130.5306),
    "다케오": (33.1928, 130.0086), "사세보": (33.1750, 129.7256),
    "구마모토": (32.8058, 130.7314), "호후": (34.0511, 131.5786),
    "이즈": (35.0206, 138.9269), "에도가와": (35.7069, 139.8767),
    # ── 경마장(競馬場) ──
    "삿포로": (43.0919, 141.3283), "하코다테": (41.8058, 140.7539),
    "몬베츠": (42.4881, 142.0189), "오비히로": (42.9200, 143.2000),
    "모리오카": (39.7208, 141.1919), "미즈사와": (39.1481, 141.1522),
    "후쿠시마": (37.7519, 140.4497), "니가타": (38.0231, 139.2214),
    "우라와": (35.8531, 139.6606), "후나바시": (35.7017, 139.9950),
    "오이": (35.5928, 139.7461), "카와사키": (35.5361, 139.7031),
    "나카야마": (35.7328, 140.0022), "도쿄": (35.6644, 139.4844),
    "카사마츠": (35.3706, 136.7683), "추쿄": (35.0731, 137.0022),
    "마쓰사카": (34.5758, 136.5347), "쿄토": (34.9022, 135.7178),
    "한신": (34.7794, 135.3617), "소노다": (34.7581, 135.3878),
    "히메지": (34.8281, 134.6683), "사가": (33.3106, 130.2464),
    "코쿠라": (33.8214, 130.8353), "코치": (33.5486, 133.6117),
    "나고야": (35.0836, 136.7522),
}


def _dms(v):
    """아메다스 좌표는 [도, 분] 배열이다."""
    try:
        return float(v[0]) + float(v[1]) / 60.0
    except Exception:
        return None


def haversine(a1, o1, a2, o2):
    R = 6371.0
    p1, p2 = math.radians(a1), math.radians(a2)
    dp = math.radians(a2 - a1)
    dl = math.radians(o2 - o1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def load_table():
    req = urllib.request.Request(TABLE_URL, headers={"User-Agent": "Mozilla/5.0", "Accept": "*/*"})
    return json.loads(urllib.request.urlopen(req, timeout=20).read().decode("utf-8"))


def has_wind(st):
    """`elems` 3번째 자리가 '1' 이면 풍향·풍속 관측 지점이다."""
    e = str(st.get("elems") or "")
    return len(e) >= 3 and e[2] == "1"


def build(table):
    out, miss = {}, []
    for key, (la, lo) in sorted(VENUE_LATLON.items()):
        best, bkm = None, 1e9
        for code, st in table.items():
            if not has_wind(st):
                continue
            sla, slo = _dms(st.get("lat")), _dms(st.get("lon"))
            if sla is None or slo is None:
                continue
            km = haversine(la, lo, sla, slo)
            if km < bkm:
                best, bkm = (code, st, sla, slo), km
        if best is None or bkm > MAX_KM:
            miss.append((key, round(bkm, 1) if best else None))
            continue
        code, st, sla, slo = best
        out[key] = {"station": code, "name": st.get("kjName"), "km": round(bkm, 2),
                    "lat": round(sla, 4), "lon": round(slo, 4)}
    return out, miss


def main():
    dry = "--dry" in sys.argv
    table = load_table()
    print("아메다스 지점 %d개 (풍속 관측 %d개)"
          % (len(table), sum(1 for s in table.values() if has_wind(s))))
    mp, miss = build(table)
    print("\n=== 경기장 → 최근접 풍속 관측지점 (⚠ 분모 = 좌표 등록 %d곳) ===" % len(VENUE_LATLON))
    for k, v in sorted(mp.items(), key=lambda x: x[1]["km"]):
        print("  %-10s → %-8s %-6s %5.1f km" % (k, v["station"], v["name"], v["km"]))
    if miss:
        print("\n🔴 임계 %.0fkm 초과 — 매핑 제외(\"제공되지 않는 정보\"):" % MAX_KM)
        for k, km in miss:
            print("  %-10s %s" % (k, ("%.1f km" % km) if km else "지점 없음"))
    kms = [v["km"] for v in mp.values()]
    if kms:
        kms.sort()
        print("\n매핑 %d/%d곳 · 중앙 %.1fkm · 평균 %.1fkm · 최대 %.1fkm"
              % (len(mp), len(VENUE_LATLON), kms[len(kms) // 2],
                 sum(kms) / len(kms), kms[-1]))
    if dry:
        print("\n--dry : 파일 미기록")
        return
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + ".tmp%d" % os.getpid()
    json.dump(mp, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, OUT)
    print("\n기록: %s" % OUT)


if __name__ == "__main__":
    main()
