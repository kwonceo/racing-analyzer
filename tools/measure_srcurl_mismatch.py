"""소스 URL 의 경주 식별자와 raceKey 가 어긋나는가 (읽기 전용 · 배선 없음).

🔴 실물(2026-08-13 카사마츠 6경주): oddsSource 가 keiba.go.jp DebaTable 인데
  URL 의 k_raceNo 가 2 다. 그 경주는 6경주다.
  ⇒ 소스 URL 에 경주 식별자가 다 들어 있는데 대조를 안 하고 있다.

⚠ 배선 전에 **오탐률을 먼저 잰다**(원칙 20). 20% 넘으면 배선하지 않는다.
"""
import json, io, glob, os, re, collections

BABA = {
    "30": "몬베츠", "35": "모리오카", "36": "미즈사와", "42": "우라와", "43": "후나바시",
    "44": "오이", "45": "카와사키", "46": "카나자와", "47": "카사마츠", "48": "나고야",
    "50": "소노다", "51": "히메지", "54": "코치", "55": "사가", "31": "오비히로",
}
MISS_URL = 0
tot = 0
bad = []
by_venue = collections.Counter()
no_url = collections.Counter()


def rno_of(rk):
    m = re.search(r"(\d+)\s*(경주|R)", str(rk or ""))
    return int(m.group(1)) if m else None


for p in sorted(glob.glob("data/odds_history/2026_08_*.json")):
    try:
        d = json.load(io.open(p, encoding="utf-8"))
    except Exception:
        continue
    b = os.path.basename(p)[:-5]
    venue, rno_s = b[11:].rsplit("_", 1)
    rno = rno_of(rno_s)
    if rno is None:
        continue
    urls = set()
    for s in ((d.get("snapshots") or []) + (d.get("archive_snapshots") or [])):
        v = str(s.get("src") or "")
        if "http" in v:
            urls.add(v)
    tot += 1
    if not urls:
        MISS_URL += 1
        no_url[str((d.get("snapshots") or [{}])[-1].get("src") or "?")] += 1
        continue
    for u in urls:
        m = re.search(r"k_raceNo=(\d+)", u)
        mb = re.search(r"k_babaCode=(\d+)", u)
        mdt = re.search(r"k_raceDate=([\d%A-Za-z]+)", u)
        u_rno = int(m.group(1)) if m else None
        u_ven = BABA.get(mb.group(1)) if mb else None
        errs = []
        if u_rno is not None and u_rno != rno:
            errs.append("경주번호 %s ≠ %s" % (u_rno, rno))
        if u_ven and u_ven != venue:
            errs.append("경기장 %s ≠ %s" % (u_ven, venue))
        if errs:
            by_venue[venue] += 1
            bad.append((b, u[:70], " · ".join(errs)))
        break

print("== 작업2 · 소스 URL ↔ raceKey 대조 (8월 odds_history) ==")
print("   대상 %d경주" % tot)
print("   🔴 URL 이 아예 없는 경주: %d (%.1f%%)" % (MISS_URL, MISS_URL / max(1, tot) * 100))
print("      그 경주들의 src 값 상위:", dict(no_url.most_common(5)))
print()
print("   🔴 URL 식별자와 raceKey 가 다른 경주: %d (%.2f%%)"
      % (len(bad), len(bad) / max(1, tot) * 100))
if by_venue:
    print("   경기장별:", dict(by_venue.most_common(10)))
print()
for b in bad[:12]:
    print("     %-28s %s" % (b[0], b[2]))
    print("        %s" % b[1])
print()
print("🔴 오탐률 판정: URL 이 있는 경주만 대상이므로")
print("   실제 가드 적용 대상 = %d경주 (전체의 %.1f%%)"
      % (tot - MISS_URL, (tot - MISS_URL) / max(1, tot) * 100))
print("   그중 차단될 비율 = %.1f%%"
      % (len(bad) / max(1, tot - MISS_URL) * 100))
