# -*- coding: utf-8 -*-
"""마감 직전 **진성 급락** 포착 — 「⚡ 마감 직전 신호」 산출 (순수 계산 · 저장/발송 없음).

🔴 왜 이 축인가 (2026-08-28 측정 · 8월 88,977조합 · 3,069경주)
  대표: 「기수·전적은 남들이 다 하는 거야. **남들이 못하는 걸 알아야** 하는데」
  ⇒ 우리만 가진 **배당 시계열**(76,293틱)로만 볼 수 있는 것을 쟀다.

  급락 없음     엣지 0.951  CI [0.909,0.996]   🔴 상한<1 = 유의하게 나쁘다
  T-10분 이전   0.913 · T-10~5 0.963 · T-5~2 1.048
  🟢 T-2~0분    엣지 **1.249** CI [1.137,1.371] — **늦게 올수록 강하다**

  진성/페이크로 한 번 더 (우리 명단 밖 8,846건):
  🟢 반등 없음  엣지 **1.416** CI [1.206,1.661] · 적중 143 · 경주당 2.03 · 배당중앙 45.4배
  🔴 반등 있음  엣지 1.154 CI 하한 0.941(미달)
  가장 좁힌 것: 진성 + 급락 -25%+ + 배당 10~80배 → 엣지 **1.652** · 경주당 **0.70**
  🟢 원칙 2: 대박3뺀 **134.9%**(경마 142.1%) — 오늘 잰 안 중 유일하게 100% 초과

🔴 최종 배당만 보면 진성과 페이크가 **똑같이 생겼다.** 시계열이 있어야만 갈린다.

⚠ 판정 4단계: ①적중 143 🟢 ②대박3뺀 134.9% 🟢
  ③기간 3분할 1.368/1.673/1.220 🟡(마지막 CI 하한 0.910)
  ④경마 1.471(하한 1.227) 🟢 · 경륜 1.241(하한 0.874) 🟡 ⇒ **경마가 근거에 맞는다**

⚠ 이 파일은 **계산만** 한다. 판정 명단(displayedCombos)·회원 수신(finalQuinellas)을
  **한 줄도 건드리지 않는다** — 2026-08-28 아침 「판정만 바꾸고 회원은 그대로」 사고 재발 방지.
"""

DROP_MIN = 25.0        # 급락 문턱(%) — 실측 -25~40% 가 가장 강했다
REBOUND_MAX = 10.0     # 반등이 이보다 크면 **페이크**로 보고 버린다(엣지 1.154·하한 미달)
ODDS_LO, ODDS_HI = 10.0, 80.0   # 배당대 — 10배 미만은 표본이 얇고 80배+ 는 적중 25건뿐
MB_MAX = 2.0           # 마감 몇 분 이내인가
MAX_PICKS = 2          # 경주당 상한(실측 경주당 0.70개라 사실상 여유)


def _qmap(q):
    """{(a,b): odds} 정규화 — 리스트/딕트 두 형식."""
    out = {}
    if isinstance(q, dict):
        it = list(q.items())
    elif isinstance(q, list):
        it = []
        for e in q:
            if isinstance(e, dict) and e.get("combo"):
                c = e["combo"]
                if isinstance(c, (list, tuple)) and len(c) >= 2:
                    it.append(("%s+%s" % (c[0], c[1]), e.get("odds")))
    else:
        return out
    for k, v in it:
        try:
            o = float(v.get("odds") if isinstance(v, dict) else v)
        except (TypeError, ValueError):
            continue
        if o <= 1.0 or o >= 1000.0:
            continue
        pr = [p for p in str(k).replace("-", "+").replace(",", "+").split("+") if p.strip().isdigit()]
        if len(pr) != 2:
            continue
        a, b = int(pr[0]), int(pr[1])
        if a != b:
            out[(min(a, b), max(a, b))] = o
    return out


def picks(ticks, exclude=None, mb_max=MB_MAX):
    """마감 전 틱 목록 → 진성 급락 조합.

    ticks: [{minutes_before, quinella, ...}] (오염 틱은 호출부에서 이미 걸렀다고 본다)
    exclude: 이미 회원에게 나간 조합 집합(중복 발송 방지)
    반환 [(combo, odds, drop%, mb)] — 급락이 큰 순. 없으면 [].
    """
    ser = []
    for t in (ticks or []):
        if not isinstance(t, dict):
            continue
        mb = t.get("minutes_before")
        if mb is None or mb < 0 or not t.get("quinella"):
            continue
        ser.append((float(mb), _qmap(t["quinella"])))
    ser.sort(key=lambda x: -x[0])            # 시간순(mb 큰 것 = 이른 것)
    if len(ser) < 3:
        return []
    ex = set(exclude or ())
    out = []
    for i in range(1, len(ser)):
        pmb, pq = ser[i - 1]
        cmb, cq = ser[i]
        if cmb > mb_max:                      # 🔴 마감 mb_max 분 이내에 일어난 급락만
            continue
        for c, o in cq.items():
            if c in ex or not (ODDS_LO <= o <= ODDS_HI):
                continue
            po = pq.get(c)
            if not po or po <= 0:
                continue
            d = 100.0 * (o - po) / po
            if d > -DROP_MIN:
                continue
            # 🔴 **반등 판정** — 급락 이후 값이 다시 오르면 페이크다(실측 엣지 1.154·하한 0.941)
            after = [q.get(c) for m, q in ser if m <= cmb and q.get(c)]
            if len(after) >= 2:
                lo_ = min(after)
                if lo_ > 0 and 100.0 * (max(after) - lo_) / lo_ >= REBOUND_MAX:
                    continue
            out.append((c, o, d, cmb))
    # 같은 조합이 여러 번 잡히면 급락이 큰 것 하나만
    best = {}
    for c, o, d, mb in out:
        if c not in best or d < best[c][2]:
            best[c] = (c, o, d, mb)
    return sorted(best.values(), key=lambda x: x[2])[:MAX_PICKS]


def lines(ps):
    """카톡 문구 — 없으면 빈 리스트."""
    if not ps:
        return []
    out = ["⚡ 마감 직전 신호 — 방금 돈이 몰렸습니다"]
    for c, o, d, mb in ps:
        out.append(" 복승 %s (%.1f배) · %.0f%% 급락" % ("+".join(map(str, c)), o, abs(d)))
    out.append(" ※ 기존 추천은 그대로입니다. 이건 **추가**입니다")
    return out
