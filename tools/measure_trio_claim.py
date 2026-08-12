import json, io, glob, collections

# 🔴 대표 관찰 검증: "정답의 세 번째 말은 대부분 유력마 목록 밖에 있었다"
#   대상 = 복승 본선 1위 두 마리가 실제 1·2·3착 안에 든 경주(= 세 번째만 맞히면 됐던 경주)
tot = 0
inkey = outkey = 0
pos = collections.Counter()
cases = []

for p in sorted(glob.glob("data/analysis_log/2026_08_*.json")):
    try:
        d = json.load(io.open(p, encoding="utf-8"))
    except Exception:
        continue
    r = d.get("result") or {}
    try:
        top3 = [int(r["1st"]), int(r["2nd"]), int(r["3rd"])]
    except (KeyError, TypeError, ValueError):
        continue
    cp = d.get("corePicks") or {}
    fq = cp.get("finalQuinellas") or []
    if not fq:
        continue
    try:
        axis = set(int(x) for x in (fq[0].get("combo") or []))
    except (TypeError, ValueError):
        continue
    if len(axis) != 2 or not axis.issubset(set(top3)):
        continue                      # 축 두 마리가 3착 안에 없으면 대상 아님
    third = [n for n in top3 if n not in axis]
    if len(third) != 1:
        continue
    third = third[0]
    tot += 1
    key = [int(x) for x in (d.get("keyHorses") or cp.get("keyHorses") or [])]
    if third in key:
        inkey += 1
        pos[key.index(third) + 1] += 1
    else:
        outkey += 1
    trio = (r.get("payouts") or {}).get("trio")
    if trio and len(cases) < 8:
        cases.append((d.get("raceKey"), sorted(axis), third,
                      "유력마 %d위" % (key.index(third) + 1) if third in key else "유력마 밖",
                      trio))

print("== 🔴 대표 관찰 검증 ==")
print("   축 두 마리가 3착 안에 든 경주(= 세 번째만 맞히면 됐던 경주): %d" % tot)
print("   그중 정답 3착마가")
print("     유력마 목록 **안**: %d (%.1f%%)" % (inkey, inkey / max(1, tot) * 100))
print("     🔴 유력마 목록 **밖**: %d (%.1f%%)" % (outkey, outkey / max(1, tot) * 100))
print()
print("   유력마 안이었을 때 몇 위였나:")
for k in sorted(pos):
    print("     %d위 %d건 (%.1f%%)" % (k, pos[k], pos[k] / max(1, inkey) * 100))
print()
print("   실물 표본(삼복승 확정배당 보유):")
for c in cases:
    print("     %-16s 축%s + %d번(%s) · %.1f배" % (c[0], c[1], c[2], c[3], c[4]))

print()
print("== keyHorses 보유율 (분모 = 위 %d경주) ==" % tot)
nk = 0
for p in sorted(glob.glob("data/analysis_log/2026_08_*.json")):
    try:
        d = json.load(io.open(p, encoding="utf-8"))
    except Exception:
        continue
    if d.get("keyHorses") or (d.get("corePicks") or {}).get("keyHorses"):
        nk += 1
print("   keyHorses 를 가진 8월 로그: %d" % nk)
