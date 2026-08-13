"""착순 마번이 그 경주 두수를 넘는가 — 소급 측정 (읽기 전용 · 배선 없음).

🔴 실물(2026-08-13 카사마츠 4경주): 8두 경주인데 착순이 10-9-5 로 들어왔다.
  10·9 는 그 경주에 없는 마번이다. 실제 착순은 8-6-1 이고 정답 복승 6+8(3.8배)은
  본선이었다 — **적중인데 미적중으로 판정됐다.**

⚠ 두수 기준은 여러 소스를 합집합으로 쓴다(원칙 22 — 출마표 단독 금지):
  rosterNos ∪ horses[].no ∪ 배당에 등장한 마번
"""
import json, io, glob, os, sys, collections, re

sys.path.insert(0, os.path.join(os.getcwd(), "tools"))
import measure_recovery as MR   # noqa: E402

MIN_HITS = getattr(MR, "MIN_HITS", 30)


def odds_nos(p):
    """odds_history 스냅샷에 등장한 마번(가장 넓은 근거)."""
    out = set()
    try:
        d = json.load(io.open(p, encoding="utf-8"))
    except Exception:
        return out
    for s in ((d.get("snapshots") or []) + (d.get("archive_snapshots") or [])):
        q = s.get("quinella")
        if isinstance(q, dict):
            for k in q:
                for x in re.split(r"[^0-9]+", str(k)):
                    if x:
                        out.add(int(x))
        elif isinstance(q, list):
            for e in q:
                for x in (e.get("combo") or []):
                    try:
                        out.add(int(x))
                    except (TypeError, ValueError):
                        pass
    return out


bad = []
skipped = []
tot = 0
by_sport = collections.Counter()
by_venue = collections.Counter()
flip = 0

for p in sorted(glob.glob("data/analysis_log/2026_08_*.json")):
    try:
        d = json.load(io.open(p, encoding="utf-8"))
    except Exception:
        continue
    r = d.get("result") or {}
    fin = []
    for k in ("1st", "2nd", "3rd"):
        try:
            fin.append(int(r[k]))
        except (KeyError, TypeError, ValueError):
            pass
    if len(fin) < 2:
        continue
    cp = d.get("corePicks") or {}
    ref = set()
    for x in (cp.get("rosterNos") or []):
        try:
            ref.add(int(x))
        except (TypeError, ValueError):
            pass
    for h in (d.get("horses") or []):
        try:
            ref.add(int(h["no"]))
        except (KeyError, TypeError, ValueError):
            pass
    ref |= odds_nos(os.path.join("data/odds_history", os.path.basename(p)))
    if len(ref) < 3:
        continue                      # 근거가 얇으면 판정하지 않는다(오탐 방지)
    tot += 1
    ghost = sorted(set(fin) - ref)
    if not ghost:
        continue
    # 🔴 [오탐 구분] 명단이 **부분수집**이면 유령이 아니다(원칙 20·22).
    #   판정 조건: 명단이 1..N 으로 **빈틈없이 연속**이고, 그때만 N 초과를 오염으로 본다.
    #   구멍이 있으면(예: [2,3,4,5,6] — 1번 없음) 수집이 덜 된 것이므로 **판정하지 않는다.**
    #   ⚠ raceHorseCount 가 있으면 그것과도 대조한다.
    rmax = max(ref)
    contiguous = (ref == set(range(1, rmax + 1)))
    rhc = cp.get("raceHorseCount")
    try:
        rhc = int(rhc)
    except (TypeError, ValueError):
        rhc = None
    if not contiguous:
        skipped.append((os.path.basename(p)[:-5], sorted(ref), fin, ghost, "명단 불연속(부분수집)"))
        continue
    if rhc and max(fin) <= rhc:
        skipped.append((os.path.basename(p)[:-5], sorted(ref), fin, ghost,
                        "raceHorseCount=%d 안" % rhc))
        continue
    v = os.path.basename(p)[11:-5].rsplit("_", 1)[0]
    sp = "%s/%s" % (d.get("sport"), d.get("category"))
    by_sport[sp] += 1
    by_venue[v] += 1
    hit = bool(d.get("hit"))
    dc = (cp.get("displayedCombos") or {}).get("quinellas") or []
    bad.append((os.path.basename(p)[:-5], sorted(ref), fin, ghost, hit, len(dc)))
    if not hit and dc:
        flip += 1

print("== 작업1 · 착순에 그 경주에 없는 마번이 있는가 ==")
print("   ⚠ 두수 근거 = rosterNos ∪ horses ∪ 배당 등장 마번(합집합 · 원칙 22)")
print("   대상 %d경주" % tot)
print("   🔴 유령 마번이 섞인 경주: %d (%.2f%%)" % (len(bad), len(bad) / max(1, tot) * 100))
print("   그중 미적중으로 판정된 것: %d  ← 적중이 뒤집혔을 수 있다" % flip)
print()
if by_sport:
    print("   종목별:", dict(by_sport))
    print("   경기장별 상위:", dict(by_venue.most_common(10)))
print()
print("   ⚠ 판정 보류(부분수집 등): %d건" % len(skipped))
for k in skipped[:8]:
    print("     %-28s 명단 %s · 착순 %s · 사유 %s" % (k[0], k[1][:10], k[2], k[4]))
print()
print("   🔴 확정 표본:")
for b in bad[:15]:
    print("     %-28s 명단 %s · 착순 %s · 🔴유령 %s · hit=%s · 판정 %d조합"
          % (b[0], b[1][:10], b[2], b[3], b[4], b[5]))
