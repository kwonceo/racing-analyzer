import json, io, glob, collections

n = collections.Counter()
ex = {}
tot = 0

for p in sorted(glob.glob("data/analysis_log/2026_08_1*.json")):
    try:
        d = json.load(io.open(p, encoding="utf-8"))
    except Exception:
        continue
    tot += 1
    cp = d.get("corePicks") or {}
    rk = d.get("raceKey")

    # ① T-5 복원
    fq = cp.get("finalQuinellas") or []
    late = [q for q in fq if q.get("pickTier") == "late"]
    rest = [q for q in fq if q.get("t5Restored")]
    if late:
        n["① 마감신호 추가(pickTier=late)"] += 1
        ex.setdefault("late", (rk, late[0].get("combo"), late[0].get("tierLabel")))
    if rest:
        n["① T-5 복원(t5Restored)"] += 1
        ex.setdefault("rest", (rk, rest[0].get("combo")))

    # ② 배당 오염 감지
    dc = cp.get("displayedCombos") or {}
    if dc.get("blocked"):
        n["② 확장전용 차단"] += 1
    sq = d.get("signal_quality_full") or {}
    if (d.get("odds_suspect") or sq.get("oddsSuspect")):
        n["② odds_suspect"] += 1

    # ③ 갱신 횟수
    rh = d.get("recommendation_history") or []
    if rh:
        n["③ 추천 이력 보유"] += 1
        if len(rh) >= 3:
            n["③ 추천이 3번 이상 바뀜"] += 1
        ex.setdefault("rh", (rk, len(rh)))
    ct = cp.get("collectTicks")
    if ct:
        n["③ 수집 틱수(collectTicks)"] += 1
        ex.setdefault("ct", (rk, ct))

    # ④ 급락 근거
    dr = d.get("drops_raw") or []
    if dr:
        n["④ 급락 원자료(drops_raw)"] += 1
        big = [e for e in dr if (e.get("pct") or 0) <= -30]
        if big:
            n["④ 30%+ 급락 조합 있음"] += 1
            ex.setdefault("drop", (rk, big[0].get("combo"), big[0].get("pct"),
                                   big[0].get("prev"), big[0].get("cur")))
    sd = d.get("signals_detected") or []
    if sd:
        n["④ 시각별 신호(signals_detected)"] += 1
        ex.setdefault("sd", (rk, len(sd), sd[0].get("time"), str(sd[0].get("detail"))[:50]))

    # ⑤ 기타 이미 있는 것
    if cp.get("trioObserve"):
        n["⑤ 삼복승 관찰(신규)"] += 1
    if dc.get("extra"):
        n["⑤ 판정 편입(💎·라인짝)"] += 1
    if cp.get("raceGradeJudge", {}).get("locked"):
        n["⑤ 마감 시점 등급 동결"] += 1

print("== 8월 11~13일 로그 %d건 기준 보유율 ==" % tot)
for k, v in sorted(n.items()):
    print("   %-32s %4d (%5.1f%%)" % (k, v, v / max(1, tot) * 100))

print()
print("== 실물 표본 ==")
for k, v in ex.items():
    print("   %-6s %s" % (k, v))
