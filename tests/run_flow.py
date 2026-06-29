#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""오오이 2경주 픽스처로 전체 흐름 검증 (서버 실행 상태에서):
  1단계: 모든 API 엔드포인트 응답 확인
  2단계: 이상감지 → 등급분류 → 베팅추천 재현
실행: python app.py 로 서버 띄운 뒤 → python tests/run_flow.py
"""
import sys, os, json, urllib.request, urllib.error

sys.stdout.reconfigure(encoding="utf-8")
BASE = "http://127.0.0.1:8011"
FIX = json.load(open(os.path.join(os.path.dirname(__file__), "fixtures", "ooi_r2.json"), encoding="utf-8"))

P = {"pass": 0, "fail": 0}
def chk(label, ok, extra=""):
    print(f"  {'✅' if ok else '❌'} {label}{(' — ' + extra) if extra else ''}")
    P["pass" if ok else "fail"] += 1
    return ok

def post(path, body):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"content-type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status, json.loads(r.read().decode("utf-8"))

def get(path):
    with urllib.request.urlopen(BASE + path, timeout=10) as r:
        return r.status, json.loads(r.read().decode("utf-8"))

def options_allows_post(path):
    req = urllib.request.Request(BASE + path, method="OPTIONS")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return "POST" in (r.headers.get("Allow") or "")
    except urllib.error.HTTPError as e:
        return "POST" in (e.headers.get("Allow") or "")

# ─────────────────────────────────────────
print("=" * 60)
print("[1단계] 서버 / API 엔드포인트 응답 확인")
print("=" * 60)
try:
    st, h = get("/api/health")
    chk("GET /api/health", st == 200 and h.get("ok"), f"model={h.get('model')} has_key={h.get('has_key')}")
except Exception as e:
    print(f"  ❌ 서버 연결 실패: {e}\n  → 먼저 'python app.py' 로 서버를 띄우세요.")
    sys.exit(1)

# 데이터 엔드포인트(실호출 200 기대)
data_eps = [
    ("/api/score", {"race": {}, "horses": []}),
    ("/api/score/form", {"horses": []}),
    ("/api/odds/snapshot", {"raceKey": "__ping", "odds": {"1": 2.0}}),
    ("/api/odds/race", {"raceKey": "__ping"}),
    ("/api/odds/compute", {"raceKey": "__ping", "horses": [{"no": 1, "name": "x", "score": 50}]}),
    ("/api/odds/undo", {"raceKey": "__ping"}),
    ("/api/odds/clear", {"raceKey": "__ping"}),
    ("/api/analyze/combined", {"raceKey": "__ping", "race": {}, "horses": [], "oddsSnapshots": []}),
]
for path, body in data_eps:
    try:
        st, _ = post(path, body)
        chk(f"POST {path}", st == 200, f"HTTP {st}")
    except Exception as e:
        chk(f"POST {path}", False, str(e)[:60])

# AI/Vision 엔드포인트(비용 없이 OPTIONS로 등록만 확인 — 405 아님)
ai_eps = ["/api/extract/jockey", "/api/extract/race", "/api/extract/training",
          "/api/extract/results", "/api/detect", "/api/analyze",
          "/api/analyze/japan", "/api/analyze/odds"]
for path in ai_eps:
    chk(f"OPTIONS {path} (등록확인)", options_allows_post(path), "POST 허용")

# ─────────────────────────────────────────
print("\n" + "=" * 60)
print("[2단계] 오오이 2경주 재현: 이상감지 → 등급 → 베팅추천")
print("=" * 60)
RK, race, horses, budget = FIX["raceKey"], FIX["race"], FIX["horses"], FIX["budget"]

# (검증1) 전적 점수 → 4번 A등급
st, sc = post("/api/score", {"race": race, "horses": horses})
byno = {h["no"]: h for h in sc["horses"]}
print("\n[전적 점수/등급]")
for h in sorted(sc["horses"], key=lambda x: -x["totalScore"])[:6]:
    print(f"  {h['no']:>2}번 {h['name']:<10} 총{h['totalScore']:<6} 등급 {h['grade']}  최근착순 {h['recentPlacings']}")
chk("4번(에레노아퀸) 전적 A등급", byno[4]["grade"] == "A", f"grade={byno[4]['grade']}")

# (검증2) 배당 이상감지 → 4+6 급락/스마트머니
post("/api/odds/clear", {"raceKey": RK})
for snap in FIX["oddsSnapshots"]:
    post("/api/odds/snapshot", {"raceKey": RK, "odds": snap["odds"]})
edge_horses = [{"no": h["no"], "name": h["name"], "score": byno[h["no"]]["totalScore"]} for h in horses]
st, comp = post("/api/odds/compute", {"raceKey": RK, "horses": edge_horses})
ranked_sig = sorted(comp["horses"], key=lambda x: -x["signalScore"])
print("\n[배당 이상감지] 신호 상위")
for h in ranked_sig[:5]:
    print(f"  {h['no']:>2}번 신호 {h['signalScore']:<4} 드롭 {h['drop']*100:>5.0f}%  배당 {h['lastOdds']}  {h['tags']}")
flagged = {h["no"] for h in comp["horses"] if h["tags"]}
chk("4·6 모두 이상감지 플래그", {4, 6} <= flagged, f"플래그된 마번={sorted(flagged)}")
drop46 = all(h["drop"] >= 0.30 for h in comp["horses"] if h["no"] in (4, 6))
chk("4·6 마감직전 급락(≥30%)", drop46,
    "4번 " + f"{byno[4] and next(h['drop'] for h in comp['horses'] if h['no']==4)*100:.0f}%"
    + ", 6번 " + f"{next(h['drop'] for h in comp['horses'] if h['no']==6)*100:.0f}%")

# (검증3) 통합 분석 → 등급 + 베팅추천(4-6 포함)
st, cmb = post("/api/analyze/combined",
               {"raceKey": RK, "race": race, "horses": horses,
                "oddsSnapshots": FIX["oddsSnapshots"], "budget": budget})
picks = cmb["picks"]
print("\n[통합 분석 — 등급 카드]")
cbyno = {h["no"]: h for h in cmb["horses"]}
for g in ["A", "B", "C", "D"]:
    no = picks.get(g)
    if no:
        h = cbyno[no]; an = h.get("anomaly") or {}
        print(f"  {g}: {no}번 {h['name']} (전적 {h['totalScore']}, 배당신호 {an.get('signalScore')})")
print("[통합 분석 — 베팅추천]")
for b in cmb["bets"]:
    if b["available"]:
        print(f"  {b['type']} {'+'.join(b['slots'])} = {'-'.join(map(str, b['combo']))}  "
              f"{b['amount']:,}원({b['weightPct']}%)  손익분기 {b['breakevenOdds']}배")
chk("등급 A = 4번", picks.get("A") == 4, f"A={picks.get('A')}")
chk("등급 B = 6번", picks.get("B") == 6, f"B={picks.get('B')}")
qab = next((b for b in cmb["bets"] if b["key"] == "q_ab"), None)
chk("복승 A+B 추천 = 4-6", qab and qab["combo"] == [4, 6], f"combo={qab['combo'] if qab else None}")

post("/api/odds/clear", {"raceKey": RK})
print("\n" + "=" * 60)
print(f"결과: 통과 {P['pass']} / 실패 {P['fail']}")
print("=" * 60)
sys.exit(1 if P["fail"] else 0)
