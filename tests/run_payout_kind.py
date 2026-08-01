# -*- coding: utf-8 -*-
"""[마권 종류 회귀 테스트 (2026-08-01 신설)]

🔴 왜 만들었나 — `payouts.trifecta` 가 **경로마다 다른 마권**이었다. 조용히 틀리는 유형이다.
```
중앙(netkeiba `_JRA_PAY_MAP`) : 3連複 → trio      · 3連単 → **trifecta**
지방(`_keiba_result_payouts`) : 三連複 → **trifecta**  (3連単 안 받음)
경륜(`_keirin_result_parse`)  : 3連複 → **trifecta**  (3連単 안 받음)
```
⇒ 중앙만 반대다. 삼복승(순서 무관)으로 **판정**해놓고 3連単 배당으로 **회수**를 계산하면
   회수가 부풀려진다. 실측 배수 **중앙 4.7배 · 최대 18.7배**.

검사 3종
```
[1] 매핑 상수  — app.py `_JRA_PAY_MAP` 이 3連複→trio · 3連単→trifecta 인지
[2] 소비 지점  — 삼복승을 재는 도구가 **trio 우선**으로 읽는지(trifecta 단독 사용 금지)
[3] 실데이터   — trio·trifecta 를 **둘 다** 가진 경주에서 trifecta > trio 인지
                 (3連単은 3連複보다 항상 비싸다. 뒤집혀 있으면 매핑이 어긋난 것)
```
⚠ 円 단위 테스트(`run_payout_unit.py`)와 **다른 축**이다. 그쪽은 단위(円↔배),
  이쪽은 **마권 종류**다. 둘 다 "같은 필드에 다른 것이 들어간다"는 같은 계열이지만 검사가 다르다.

⚠ **한계**: `trifecta` 만 있고 `trio` 가 없는 경주는 **판별 근거가 없다**(검사에서 뺀다).
  실측상 그런 건 125건은 category 가 japan_central 로 오분류된 지방이라 안전하나,
  🔴 **통과가 "전부 안전"을 뜻하지는 않는다.**
"""
import glob
import io
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# 삼복승 배당을 **읽는 지점** — 새 도구·새 경로가 생기면 여기에 추가한다.
#   ⚠ 측정 도구만이 아니라 **학습·손익 경로**도 넣는다. 그쪽이 오염되면 시스템이 잘못 학습한다.
#   (name, 함수명, 검사할 본문 길이) — 함수가 길면 길이를 늘린다.
CONSUMERS = [("tools/measure_recovery.py", "measure_trio", 4000),
             ("app.py", "_apply_result_learning", 14000)]

fails = []
warns = []

# ── [1] 매핑 상수 ────────────────────────────────────────────────────────────
src = open(os.path.join(BASE, "app.py"), encoding="utf-8").read()
m = re.search(r"_JRA_PAY_MAP\s*=\s*\{(.*?)\}", src, re.S)
if not m:
    fails.append("[1] app.py 에서 _JRA_PAY_MAP 을 찾지 못했다(이름이 바뀌었나?)")
else:
    blk = m.group(1)
    for kanji, want in (("3連複", "trio"), ("3連単", "trifecta")):
        mm = re.search(re.escape(kanji) + r"\"\s*:\s*\"(\w+)\"", blk)
        if not mm:
            fails.append("[1] _JRA_PAY_MAP 에 %s 매핑이 없다" % kanji)
        elif mm.group(1) != want:
            fails.append("[1] _JRA_PAY_MAP: %s → %s (기대 %s)" % (kanji, mm.group(1), want))
    print("[1] 매핑 상수 — 3連複→trio · 3連単→trifecta  %s" % ("🟢 정상" if not fails else "🔴 이상"))

# ── [2] 소비 지점이 trio 우선인지 ────────────────────────────────────────────
for rel, fn, span in CONSUMERS:
    p = os.path.join(BASE, rel)
    if not os.path.exists(p):
        warns.append("[2] %s 없음 — 건너뜀" % rel)
        continue
    t = open(p, encoding="utf-8").read()
    i = t.find("def " + fn)
    if i < 0:
        fails.append("[2] %s 에 %s 가 없다" % (rel, fn))
        continue
    body = t[i:i + span]
    has_trio = 'get("trio")' in body or "get('trio')" in body
    if not has_trio:
        fails.append("[2] %s.%s 가 **trifecta 단독**으로 읽는다 — 중앙경마 3連単이 섞인다" % (rel, fn))
    else:
        print("[2] 소비 지점 %-32s trio 우선 🟢" % (rel + ":" + fn))

# ── [3] 실데이터 — trifecta > trio 인지 ──────────────────────────────────────
both = 0
bad = []
for f in sorted(glob.glob(os.path.join(BASE, "data", "analysis_log", "2026_*.json"))):
    try:
        d = json.load(open(f, encoding="utf-8"))
    except Exception:
        continue
    po = ((d.get("result") or {}).get("payouts") or {})
    a, b = po.get("trio"), po.get("trifecta")
    if a is None or b is None:
        continue
    both += 1
    try:
        if float(b) <= float(a):
            bad.append((os.path.basename(f), a, b))
    except Exception:
        pass
if both == 0:
    warns.append("[3] trio·trifecta 를 둘 다 가진 경주가 0건 — 판별 표본 없음")
    print("[3] 실데이터 — 대조 표본 0건 ⚠ (판정 보류)")
else:
    print("[3] 실데이터 — 대조 %d건 · 역전(3連単 ≤ 3連複) %d건 %s"
          % (both, len(bad), "🟢" if not bad else "🔴"))
    for x in bad[:5]:
        fails.append("[3] 역전: %s trio=%s trifecta=%s — 매핑이 어긋났을 수 있다" % x)

print()
for w in warns:
    print("  ⚠", w)
if fails:
    print("🔴 실패 %d건" % len(fails))
    for x in fails:
        print("   -", x)
    sys.exit(1)
print("🟢 통과 — 마권 종류 이상 0건")
print("⚠ 다만 `trifecta` 만 있는 경주는 판별 근거가 없어 검사에서 빠진다. **전부 안전을 뜻하지 않는다.**")
sys.exit(0)
