"""페이크 의심 감점이 정당한가 — 소급 측정 (읽기 전용 · 배선 없음).

🔴 배경: `_flow_scores`(app.py 7949~) 가 급락 후 되오른 말에 **-15** 를 준다.
  대표 관찰(2026-08-12): 되오른 말이 두 번 다 정답에 들었다.
    소노다 2경주 8번 1착 117배 · 오이 6경주 12번 2착 62배
  ⚠ 그 2건은 **선택 편향**이다. 전수로 잰다.

측정: 페이크 감점 대상 말의 3착권 진입률 ↔ 같은 경주 다른 말의 3착권 진입률.
  분모를 반드시 병기한다(원칙 8-C). 적중 30건 미만이면 판정 불가(원칙 1).
"""
import json, io, glob, os, sys, collections

sys.path.insert(0, os.path.join(os.getcwd(), "tools"))
import measure_recovery as MR   # noqa: E402

MIN_HITS = getattr(MR, "MIN_HITS", 30)

fake_n = fake_hit = 0
oth_n = oth_hit = 0
by_sport = collections.defaultdict(lambda: [0, 0, 0, 0])
cases = []

for p in sorted(glob.glob("data/analysis_log/2026_08_*.json")):
    try:
        d = json.load(io.open(p, encoding="utf-8"))
    except Exception:
        continue
    r = d.get("result") or {}
    try:
        top3 = {int(r["1st"]), int(r["2nd"]), int(r["3rd"])}
    except (KeyError, TypeError, ValueError):
        continue
    # 🔴 flowScores 는 analysis_log 에 **저장되지 않는다**(an 전용).
    #   페이크 판정의 입력인 advanced.horseStreaks 는 저장되므로 그것으로 잰다.
    #   rebounded=true = 급락 후 되오름 = _flow_scores 의 fake 입력이다.
    fl = ((d.get("signal_quality_full") or {}).get("advanced") or {}).get("horseStreaks") or {}
    if not isinstance(fl, dict) or not fl:
        continue
    sp = "%s/%s" % (d.get("sport"), d.get("category"))
    for k, v in fl.items():
        try:
            no = int(k)
        except (TypeError, ValueError):
            continue
        if not isinstance(v, dict):
            continue
        is_fake = bool(v.get("rebounded"))
        hit = (no in top3)
        b = by_sport[sp]
        if is_fake:
            fake_n += 1
            b[0] += 1
            if hit:
                fake_hit += 1
                b[1] += 1
                if len(cases) < 10:
                    cases.append((d.get("raceKey"), no,
                                  (r.get("payouts") or {}).get("quinella")))
        else:
            oth_n += 1
            b[2] += 1
            if hit:
                oth_hit += 1
                b[3] += 1

print("== 페이크 의심 감점 대상의 3착권 진입률 (8월 전수) ==")
print("   ⚠ 분모 = horseStreaks 를 가진 경주(181건 · 12.3%)의 **말 단위**")
print()
print("   페이크 감점 대상 : %5d두 중 3착권 %4d두 (%.1f%%)"
      % (fake_n, fake_hit, fake_hit / max(1, fake_n) * 100))
print("   그 외           : %5d두 중 3착권 %4d두 (%.1f%%)"
      % (oth_n, oth_hit, oth_hit / max(1, oth_n) * 100))
d1 = fake_hit / max(1, fake_n) * 100
d2 = oth_hit / max(1, oth_n) * 100
print()
print("   차이: %+.1f%%p  ->  %s" % (
    d1 - d2,
    "🔴 감점 대상이 **더 잘 들어온다** — 감점이 손해다" if d1 > d2 + 1 else
    ("🟢 감점 대상이 덜 들어온다 — 감점이 정당하다" if d2 > d1 + 1 else
     "차이 없음 — 감점 근거가 약하다")))
if fake_hit < MIN_HITS:
    print("   ⚠ 판정 불가 — 페이크 적중 %d건 < %d건" % (fake_hit, MIN_HITS))

print()
print("== 종목별 ==")
print("   종목                  페이크N  3착권      그외N  3착권")
for sp, b in sorted(by_sport.items(), key=lambda kv: -kv[1][0]):
    if b[0] < 10:
        continue
    print("    %-20s %6d %5.1f%%   %6d %5.1f%%" % (
        sp, b[0], b[1] / max(1, b[0]) * 100, b[2], b[3] / max(1, b[2]) * 100))

print()
print("== 실물 표본(페이크 감점인데 3착권) ==")
for c in cases:
    print("    %-18s %2d번 · 복승 %s" % (c[0], c[1], c[2]))
