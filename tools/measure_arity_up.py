"""두수가 늘어나는 틱 — 규모·오탐률 (읽기 전용 · 배선 없음).

🔴 카사마츠 6경주(2026-08-13): 8두 → 10두. 조합 28 → 45.
  지금 감지(_combo_count_dip)는 **줄어드는 것만** 본다. 증가는 어떤 게이트도 안 잡는다.
  취소마는 빠지기만 하고 늘지 않으므로 증가는 그 자체가 이상일 수 있다.

⚠ 다만 **초반 부분수집이 뒤늦게 채워지는 것**과 가려야 한다(원칙 20).
  그래서 마감까지 남은 시간(minutes_before)으로 나눠서 본다.
"""
import json, io, glob, os, re, collections

def nos_of(q):
    out = set()
    if isinstance(q, dict):
        for k in q:
            for x in re.split(r"[^0-9]+", str(k)):
                if x:
                    try:
                        out.add(int(x))
                    except ValueError:
                        pass
    elif isinstance(q, list):
        for e in q:
            for x in ((e or {}).get("combo") or []):
                try:
                    out.add(int(x))
                except (TypeError, ValueError):
                    pass
    return out


tot = 0
races_up = 0
ev = []
by_step = collections.Counter()
by_mb = collections.Counter()
by_mb_step = collections.defaultdict(collections.Counter)

for p in sorted(glob.glob("data/odds_history/2026_08_*.json")):
    try:
        d = json.load(io.open(p, encoding="utf-8"))
    except Exception:
        continue
    sn = [s for s in (d.get("snapshots") or []) if s.get("quinella")]
    if len(sn) < 2:
        continue
    tot += 1
    prev = None
    hit = False
    for s in sn:
        n = nos_of(s.get("quinella"))
        if not n:
            continue
        mx = max(n)
        if prev is not None and mx > prev:
            step = mx - prev
            mb = s.get("minutes_before")
            try:
                mb = float(mb)
            except (TypeError, ValueError):
                mb = None
            band = ("마감후" if mb is None else
                    "T-2 이내" if mb <= 2 else
                    "T-5 이내" if mb <= 5 else
                    "T-10 이내" if mb <= 10 else "T-10 이전")
            by_step["+%d두" % min(step, 3)] += 1
            by_mb[band] += 1
            by_mb_step[band]["+%d두" % min(step, 3)] += 1
            hit = True
            if len(ev) < 400:
                ev.append((os.path.basename(p)[:-5], s.get("time"), prev, mx, step,
                           band, str(s.get("src") or "")[:10]))
        prev = mx if prev is None else max(prev, mx)
    if hit:
        races_up += 1

print("== 작업1 · 두수(최대 마번)가 늘어난 경주 ==")
print("   대상 %d경주 (틱 2개 이상)" % tot)
print("   🔴 한 번이라도 늘어난 경주: %d (%.1f%%)" % (races_up, races_up / max(1, tot) * 100))
print("   증가 이벤트 총 %d회" % sum(by_step.values()))
print()
print("   증가 폭별:", dict(by_step))
print()
print("   🔴 마감까지 남은 시간별 (여기가 갈림):")
for k in ("T-10 이전", "T-10 이내", "T-5 이내", "T-2 이내", "마감후"):
    v = by_mb.get(k, 0)
    print("     %-10s %5d회 (%5.1f%%)  %s" % (
        k, v, v / max(1, sum(by_mb.values())) * 100,
        dict(by_mb_step.get(k, {}))))
print()
print("   ⚠ 초반(T-10 이전) 증가는 **부분수집이 채워지는 정상**일 가능성이 높다")
print("   🔴 마감 직전(T-2 이내) 증가는 취소마로 설명되지 않는다")
print()
print("   표본(마감 5분 이내 증가):")
n = 0
for e in ev:
    if e[5] in ("T-5 이내", "T-2 이내"):
        print("     %-26s %s  %d두 → %d두 (+%d) · %s · src=%s"
              % (e[0], e[1], e[2], e[3], e[4], e[5], e[6]))
        n += 1
        if n >= 14:
            break
if not n:
    print("     (없음)")
