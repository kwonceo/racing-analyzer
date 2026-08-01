# -*- coding: utf-8 -*-
"""[수집 커버리지 측정 (2026-08-01 신설)] — **완전 읽기 전용**.

■ 🔴 왜 이 파일이 필요한가 — 분모 오류가 **일곱 번** 났다
  2026-08-01 에 "지방경마 커버리지 29%" 라고 보고했는데, **하루 전체 스케줄(24경주)** 을
  분모로 쓴 값이었다. **발주완료 기준으로는 5/5 = 100%** 였다.
  원칙 8-C("모든 비율에 분모를 명시한다")를 만든 **뒤에도** 같은 실수를 반복했다.
  ⇒ **사람 기억으로는 안 된다. 분모를 코드가 강제한다.**
  (`measure_recovery.py` 가 회수율에 대해 하는 일을 커버리지에 대해 한다.)

■ 🔴 분모 규칙 (코드에 박아 둔다 · 바꾸려면 이 주석부터 고칠 것)
  분모 = **`postEpoch <= now` 인 경주**(= 이미 발주된 경주)
  ⛔ **하루 전체 스케줄을 분모로 쓰지 않는다.** 오후 개최 경마장은 오전에 재면
     "커버리지 20%"처럼 보이는데, 그건 **아직 시작도 안 한 경주**다.
  ⛔ 과거 날짜를 잴 때는 그날 스케줄이 남아 있지 않으므로 **분모 없음**으로 표기한다
     (추정치를 만들지 않는다 — 없는 분모를 지어내면 그게 여덟 번째 오류가 된다).
  ⚠ 중앙경마(JRA)는 `today_schedule.json`(oddspark)에 **애초에 없다** → **분모 없음**.
     "커버리지 0%"가 아니라 **"잴 수 없음"** 이다. 둘을 구분해서 낸다.

■ 무엇을 세는가
  분자 = `data/odds_history/<날짜>_<경기장>_<N>경주.json` 중 **스냅샷 1개 이상**인 파일 수
  ⚠ 파일 존재가 아니라 **스냅샷 보유**로 센다(빈 파일을 수집으로 세지 않는다).

사용: python tools/measure_coverage.py            # 오늘
      python tools/measure_coverage.py --days 7   # 최근 7일(분모 없이 건수만)
"""
import argparse
import collections
import json
import os
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OH = os.path.join(BASE, "data", "odds_history")
SCHED = os.path.join(BASE, "data", "today_schedule.json")

# 경기장 → 종목. ⚠ 임시 목록이다 — `tools/track_key.py`(표준키 모듈) 도입 시 그쪽으로 옮긴다.
NAR = ("코치", "오비히로", "카와사키", "나고야", "소노다", "몬베츠", "카사마츠", "오오이",
       "모리오카", "카나자와", "후나바시", "우라와", "사가", "미즈사와", "히메지")
JRA = ("삿포로", "중경", "주쿄", "추쿄", "니가타", "고쿠라", "도쿄", "나카야마",
       "한신", "쿄토", "교토", "하코다테", "후쿠시마")
KRA = ("서울", "부산", "부경", "제주", "과천")
# 스케줄 표기 ↔ 저장 표기가 다른 것(표준키 모듈 도입 전 임시)
ALIAS = {"いわき平": "이와키타이라", "武雄": "다케오", "防府": "호후"}


def cat_of(v):
    if v in NAR:
        return "지방경마"
    if v in JRA:
        return "중앙경마"
    if v in KRA:
        return "한국경마"
    return "경륜"


def collected(day):
    """분자: 그날 스냅샷 1개 이상인 경주 수(경기장별)."""
    got = collections.Counter()
    pre = day + "_"
    if not os.path.isdir(OH):
        return got
    for f in os.listdir(OH):
        if not f.startswith(pre) or not f.endswith(".json"):
            continue
        try:
            d = json.load(open(os.path.join(OH, f), encoding="utf-8"))
        except Exception:
            continue
        if not (d.get("snapshots") or []):
            continue
        got[f[len(pre):-5].split("_")[0]] += 1
    return got


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=0, help="0=오늘(분모 있음) · N=최근 N일(분모 없음·건수만)")
    a = ap.parse_args()

    if a.days:
        print("=" * 78)
        print("수집 건수 — 최근 %d일   🔴 **분모 없음**(과거 스케줄 미보존) · 커버리지 아님" % a.days)
        print("=" * 78)
        now = time.time()
        for i in range(a.days - 1, -1, -1):
            day = time.strftime("%Y_%m_%d", time.localtime(now - i * 86400))
            g = collected(day)
            c = collections.Counter()
            for v, n in g.items():
                c[cat_of(v)] += n
            print("%s  지방%3d 중앙%3d 한국%3d 경륜%3d" %
                  (day, c["지방경마"], c["중앙경마"], c["한국경마"], c["경륜"]))
        print("\n⚠ 위 숫자는 **분모가 없다**. 커버리지로 인용하지 말 것.")
        return 0

    day = time.strftime("%Y_%m_%d")
    now = time.time()
    try:
        raw = json.load(open(SCHED, encoding="utf-8"))
        tracks = raw.get("tracks") if isinstance(raw, dict) else raw
    except Exception as e:
        print("🔴 스케줄을 읽지 못했다 → **분모 없음**. 커버리지를 내지 않는다. (%s)" % str(e)[:60])
        return 1

    sched, passed = collections.Counter(), collections.Counter()
    for t in tracks:
        nm = t.get("name") or t.get("track") or t.get("venue") or "?"
        nm = ALIAS.get(nm, nm)
        for r in (t.get("races") or []):
            sched[nm] += 1
            if (r.get("postEpoch") or 0) <= now:          # 🔴 분모 = 발주완료
                passed[nm] += 1
    got = collected(day)

    print("=" * 84)
    print("수집 커버리지  %s   🔴 **분모 = 발주완료 경주(postEpoch ≤ now)**" % time.strftime("%Y-%m-%d %H:%M"))
    print("=" * 84)
    print("%-16s %-8s %6s %8s %6s %s" % ("트랙", "종목", "스케줄", "발주완료", "수집", "커버"))
    agg = collections.defaultdict(lambda: [0, 0, 0])
    for nm in sorted(sched, key=lambda x: -passed[x]):
        c = cat_of(nm)
        p, g = passed[nm], got.get(nm, 0)
        agg[c][0] += sched[nm]; agg[c][1] += p; agg[c][2] += g
        print("%-16s %-8s %6d %8d %6d %s" %
              (nm, c, sched[nm], p, g, ("%.0f%%" % (100.0 * g / p)) if p else "⏳미발주"))

    print("\n── 종목별 ──")
    for c, (s, p, g) in sorted(agg.items()):
        print("%-8s 스케줄 %3d · 발주완료 %3d · 수집 %3d → %s"
              % (c, s, p, g, ("커버 %.0f%%" % (100.0 * g / p)) if p else "⏳ 미발주(분모 0)"))

    # 스케줄에 아예 없는 경기장(= 분모를 만들 수 없다)
    orphan = {v: n for v, n in got.items() if v not in sched}
    if orphan:
        oc = collections.Counter()
        for v, n in orphan.items():
            oc[cat_of(v)] += n
        print("\n🔴 스케줄에 없는 경기장 = **분모 없음 · 커버리지 산출 불가**(0% 아님)")
        for c, n in oc.most_common():
            print("   %-8s 수집 %d경주   %s" % (c, n, [v for v in orphan if cat_of(v) == c]))
        print("   ⚠ 중앙경마(JRA)는 oddspark 스케줄에 애초에 없다 — 구조적이다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
