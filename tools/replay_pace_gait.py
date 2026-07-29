# -*- coding: utf-8 -*-
"""[읽기전용 리플레이] 전개(페이스) × 각질 가치 검증.

가설: "페이스가 '빠른' 경주에서, 복승 조합에 '추입/선입' 각질 말이 포함되면
      실제 적중률·기대값이 시장 배당이 암시하는 확률보다 유의미하게 높은가?"

방법: 시장 전체 조합을 모집단으로 삼아(추천 선택 편향 제거) 각 조합을
  ⓐ실제 적중 여부 ⓑ배당 ⓒ각질 구성 으로 분류.
  · 실측적중률 = hit/n
  · 시장암시확률 = 환급률 0.75 / 배당  (일본 복승 공제율 약 25%)
  · **엣지 = 실측적중률 / 시장암시확률** — 1.0 초과면 시장이 그 유형을 과소평가한 것.
  · 실현기대값 = Σ(적중 조합 배당) / n  — 그 유형 전 조합에 1원씩 걸었을 때 회수.
⚠ 운영 서버·데이터에 일절 쓰지 않는다(읽기 전용).
"""
import json, glob, gzip, os, collections, itertools, math

BASE = r"C:\Users\Administrator\Desktop\경마분석서버"
LOG = os.path.join(BASE, "data", "analysis_log")
RES = os.path.join(BASE, "data", "race_results")
HIST = os.path.join(BASE, "data", "odds_history")
TAKEOUT = 0.75            # 환급률(복승) — 시장암시확률 = TAKEOUT / 배당


def load(p):
    try:
        op = gzip.open if p.endswith(".gz") else open
        with op(p, "rt", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def hist_doc(fn):
    for c in (os.path.join(HIST, fn), os.path.join(HIST, fn + ".gz")):
        d = load(c)
        if d:
            return d
    return None


def board_from(doc):
    """마감에 가장 가까운 '건전한' 스냅샷의 복승 전판 배당."""
    for s in reversed(doc.get("archive_snapshots") or doc.get("snapshots") or []):
        if s.get("after_close") or s.get("next_race_blocked") or s.get("odds_suspect"):
            continue
        q = s.get("quinella") or {}
        out = {}
        for k, v in (q.items() if isinstance(q, dict) else []):
            try:
                kk = tuple(sorted(int(x) for x in str(k).split("+")))
                o = float(v)
            except (TypeError, ValueError):
                continue
            if len(kk) == 2 and o > 0:
                out[kk] = o
        if len(out) >= 6:
            return out
    return {}


rows = []          # (pace, cls, odds, hit)
races = skipped = 0
for p in sorted(glob.glob(os.path.join(LOG, "*.json"))):
    fn = os.path.basename(p)
    d = load(p)
    if not d:
        continue
    pa = (d.get("corePicks") or {}).get("paceAnalysis") or {}
    gl = pa.get("gaitLists") or {}
    pace = pa.get("pace")
    if not pace or not gl:
        continue
    rd = load(os.path.join(RES, fn)) or {}
    r = rd.get("result") or d.get("result") or {}
    try:
        top2 = frozenset(int(r[k]) for k in ("1st", "2nd"))
    except (TypeError, ValueError, KeyError):
        continue
    if len(top2) != 2:
        continue
    hd = hist_doc(fn)
    board = board_from(hd) if hd else {}
    if len(board) < 6:
        skipped += 1
        continue
    races += 1
    late = set()          # 추입·선입 = 후미에서 오는 각질
    front = set()
    for g in ("추입", "선입"):
        late |= {int(x) for x in (gl.get(g) or [])}
    for g in ("선행",):
        front |= {int(x) for x in (gl.get(g) or [])}
    for k, o in board.items():
        a, b = k
        nl = len({a, b} & late)
        nf = len({a, b} & front)
        if nl == 2:
            cls = "추입/선입 2두"
        elif nl == 1:
            cls = "추입/선입 1두"
        else:
            cls = "추입/선입 0두"
        rows.append((pace, cls, nf, o, frozenset(k) == top2))

print("판정 경주 %d (배당판 확보) · 배당판 미확보 스킵 %d · 조합 표본 %d"
      % (races, skipped, len(rows)))


def report(title, sel, keyf):
    if not sel:
        return
    print("\n=== %s ===" % title)
    print("%-16s %7s %6s %8s %10s %8s %9s" % ("구분", "n", "hit", "실측률", "시장암시", "엣지", "실현기대"))
    g = collections.defaultdict(list)
    for r in sel:
        g[keyf(r)].append(r)
    for k in sorted(g, key=lambda x: str(x)):
        v = g[k]
        n = len(v)
        h = sum(1 for x in v if x[4])
        rate = h / n
        imp = sum(TAKEOUT / x[3] for x in v) / n
        ev = sum(x[3] for x in v if x[4]) / n
        edge = (rate / imp) if imp else 0
        star = "  ***" if (edge >= 1.15 and h >= 12) else ("  *" if edge >= 1.05 and h >= 12 else "")
        print("%-16s %7d %6d %7.2f%% %9.2f%% %8.2f %9.3f%s"
              % (str(k), n, h, rate * 100, imp * 100, edge, ev, star))


report("① 전체 — 각질 구성별", rows, lambda r: r[1])
report("② 페이스별 × 각질 구성", rows, lambda r: "%s/%s" % (r[0], r[1]))
report("③ 빠른 페이스만 — 선행 포함 수별",
       [r for r in rows if r[0] == "빠른"], lambda r: "선행 %d두" % r[2])

# ④ 배당대 교차 — 고배당에서 각질 엣지가 살아있는가
print("\n=== ④ 빠른 페이스 · 배당대 × 추입포함 ===")
BK = [(0, 5), (5, 12), (12, 30), (30, 9e9)]
print("%-12s %-14s %6s %5s %8s %8s %9s" % ("배당대", "각질", "n", "hit", "실측률", "엣지", "실현기대"))
for lo, hi in BK:
    for cls in ("추입/선입 0두", "추입/선입 1두", "추입/선입 2두"):
        v = [r for r in rows if r[0] == "빠른" and r[1] == cls and lo <= r[3] < hi]
        if len(v) < 25:
            continue
        n = len(v)
        h = sum(1 for x in v if x[4])
        rate = h / n
        imp = sum(TAKEOUT / x[3] for x in v) / n
        ev = sum(x[3] for x in v if x[4]) / n
        print("%-12s %-14s %6d %5d %7.2f%% %8.2f %9.3f"
              % ("%g-%g" % (lo, hi if hi < 9e8 else 999), cls, n, h, rate * 100,
                 (rate / imp) if imp else 0, ev))

# ⑤ 통계적 유의성 — 빠른 페이스에서 '추입 포함' vs '미포함' 2비율 검정
fa = [r for r in rows if r[0] == "빠른" and r[1] != "추입/선입 0두"]
fb = [r for r in rows if r[0] == "빠른" and r[1] == "추입/선입 0두"]
if fa and fb:
    n1, x1 = len(fa), sum(1 for x in fa if x[4])
    n2, x2 = len(fb), sum(1 for x in fb if x[4])
    p1, p2 = x1 / n1, x2 / n2
    pp = (x1 + x2) / (n1 + n2)
    se = math.sqrt(pp * (1 - pp) * (1 / n1 + 1 / n2))
    z = (p1 - p2) / se if se else 0
    print("\n=== ⑤ 유의성(빠른 페이스 · 추입/선입 포함 vs 미포함) ===")
    print("  포함  n=%d hit=%d (%.2f%%)" % (n1, x1, p1 * 100))
    print("  미포함 n=%d hit=%d (%.2f%%)" % (n2, x2, p2 * 100))
    print("  z=%.2f → %s" % (z, "유의(95%)" if abs(z) >= 1.96 else "유의하지 않음"))
