"""삼복승 세 번째 자리 후보 소급 측정 (읽기 전용 · 배선 없음).

🔴 규칙은 tools/measure_recovery.py 에서 import 한다(원칙 15).
   재현 못 한 것: 삼복승 정책 자체는 그 도구에 없어 새로 짰다(원칙 3 — 명시).
"""
import json, io, glob, os, collections, sys

sys.path.insert(0, os.path.join(os.getcwd(), "tools"))
import measure_recovery as MR   # noqa: E402

PAYBACK = MR.PAYBACK
MIN_HITS = getattr(MR, "MIN_HITS", 30)
print("판정선(환급률) = %.1f%% · 최소 적중 = %d건  [measure_recovery 에서 import]"
      % (PAYBACK, MIN_HITS))
print()


def drop_by_horse(dr):
    """조합 단위 급락 → 말별 최대 급락폭(음수 pct 의 절대값)."""
    out = {}
    for e in (dr or []):
        try:
            pct = float(e.get("pct"))
        except (TypeError, ValueError):
            continue
        if pct >= 0:
            continue
        for n in (e.get("combo") or []):
            try:
                n = int(n)
            except (TypeError, ValueError):
                continue
            out[n] = max(out.get(n, 0.0), -pct)
    return out


rows = []
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
    if len(top3) != 3:
        continue
    cp = d.get("corePicks") or {}
    fq = cp.get("finalQuinellas") or []
    if not fq:
        continue
    try:
        axis = sorted(int(x) for x in (fq[0].get("combo") or []))
    except (TypeError, ValueError):
        continue
    if len(axis) != 2:
        continue
    trio_odds = ((r.get("payouts") or {}).get("trio"))
    key = set(int(x) for x in (d.get("keyHorses") or cp.get("keyHorses") or []))
    dbh = drop_by_horse(d.get("drops_raw"))
    dark = cp.get("darkHorsePicks") or []
    rows.append({
        "rk": d.get("raceKey"), "sport": d.get("sport"), "top3": top3,
        "axis": axis, "trio": (float(trio_odds) if trio_odds else None),
        "key": key, "drop": dbh, "dark": dark,
        "bmed": (cp.get("bmedSpecial") or []),
        "hunt": (d.get("third_place_hunt") or {}),
        "ft": cp.get("finalTrifectas") or [],
        "horses": d.get("horses") or [],
        "nos": sorted(set(int(h.get("no")) for h in (d.get("horses") or [])
                          if h.get("no") is not None)),
    })

print("대상 경주(착순 3착 + 복승 본선 보유): %d" % len(rows))
print("  그중 삼복승 확정배당 보유: %d (%.1f%%)"
      % (sum(1 for x in rows if x["trio"]),
         sum(1 for x in rows if x["trio"]) / max(1, len(rows)) * 100))
print()


# ── 세 번째 자리 후보 (⚠ 데이터에 맞춰 재정의한 것은 표시) ──
def cand_A(x, n):          # 급락률 1위
    return [k for k, _ in sorted(x["drop"].items(), key=lambda kv: -kv[1])
            if k not in x["axis"]][:n]


def cand_B(x, n):          # 전적 1위 중 유력마 밖 (⚠ 확신도 필드가 없어 record_score 로 대체)
    hs = sorted((h for h in x["horses"] if h.get("no") is not None),
                key=lambda h: -(h.get("record_score") or 0))
    return [int(h["no"]) for h in hs
            if int(h["no"]) not in x["axis"] and int(h["no"]) not in x["key"]][:n]


def cand_C(x, n):          # 복병 (⚠ 별 등급 필드가 없어 anomCount 상위로 대체)
    return [int(e["no"]) for e in sorted(x["dark"], key=lambda e: -(e.get("anomCount") or 0))
            if e.get("no") is not None and int(e["no"]) not in x["axis"]][:n]


def cand_D(x, n):          # 💎 고배당 — ⚠ bmedSpecial 은 {combo:[a,b]} 다(no 아님)
    out = []
    for e in x["bmed"]:
        for v in (e.get("combo") or []):
            try:
                v = int(v)
            except (TypeError, ValueError):
                continue
            if v not in x["axis"] and v not in out:
                out.append(v)
    return out[:n]


def cand_E(x, n):          # 3착사냥 — candidates[].no (priority 순)
    h = x["hunt"] or {}
    if not h.get("active"):
        return []
    out = []
    for e in sorted((h.get("candidates") or []),
                    key=lambda z: (z.get("priority") or 99)):
        try:
            v = int(e.get("no"))
        except (TypeError, ValueError):
            continue
        if v not in x["axis"] and v not in out:
            out.append(v)
    return out[:n]


def cand_F(x, n):          # 현행 대조군 — finalTrifectas 상위
    out = []
    for t in x["ft"]:
        try:
            c = sorted(int(v) for v in (t.get("combo") or []))
        except (TypeError, ValueError):
            continue
        if len(c) == 3 and c not in out:
            out.append(c)
    return out[:n]


CANDS = [("A 급락1위", cand_A), ("B 전적1위(유력마밖)", cand_B),
         ("C 복병(급락횟수)", cand_C), ("D 다이아", cand_D),
         ("E 3착사냥", cand_E), ("F 현행", cand_F)]


def run(name, fn, n):
    """🔴 적중률은 전 경주로, 회수율은 **삼복승 확정배당 보유 경주만**으로 잰다.
    배당 없는 경주를 회수 분모에 넣으면 전패로 세어 0%대가 나온다(첫 측정의 오류)."""
    seats = hits = fired = 0
    p_seats = p_hits = 0
    ret = 0.0
    pays = []
    for x in rows:
        combos = fn(x, n) if name.startswith("F") else [
            sorted(x["axis"] + [t]) for t in fn(x, n)]
        if not combos:
            continue
        fired += 1
        for c in combos:
            seats += 1
            ok = (set(c) == x["top3"])
            if ok:
                hits += 1
            if x["trio"]:                      # 회수 분모 = 배당 보유분만
                p_seats += 1
                if ok:
                    p_hits += 1
                    ret += x["trio"]
                    pays.append(x["trio"])
    hr = (hits / fired * 100) if fired else 0.0
    rr = (ret / p_seats * 100) if p_seats else 0.0
    med = sorted(pays)[len(pays) // 2] if pays else None
    return fired, seats, hits, hr, p_seats, p_hits, rr, med


for n in (1, 2, 3):
    print("=" * 82)
    print("== 삼복승 %d조합 ==" % n)
    print("  후보                 발동  구좌  적중  적중률 | 배당구좌 적중 회수율 배당중앙 판정")
    for name, fn in CANDS:
        fired, seats, hits, hr, ps, ph, rr, med = run(name, fn, n)
        verdict = ("판정불가(배당적중%d<%d)" % (ph, MIN_HITS)) if ph < MIN_HITS else (
            "🟢 통과" if rr >= PAYBACK else "미달")
        print("   %-18s %5d %5d %5d %6.1f%% | %6d %4d %6.1f%% %7s  %s" % (
            name, fired, seats, hits, hr, ps, ph, rr,
            ("%.1f" % med) if med else "-", verdict))
    print()

# ── 작업3: 축을 바꾼 안 ──
print("=" * 78)
print("== 작업3 · 축 안 비교 (삼복승 1조합) ==")


def axis_alt(x):
    """축 1마리 + 급락 상위 2마리 (몬베츠 7R 형태)."""
    a0 = x["axis"][0]
    ds = [k for k, _ in sorted(x["drop"].items(), key=lambda kv: -kv[1]) if k != a0][:2]
    return [sorted([a0] + ds)] if len(ds) == 2 else []


def axis_cur(x):
    t = cand_A(x, 1)
    return [sorted(x["axis"] + t)] if t else []


for nm, f in (("현행 축(복승본선2)+급락1위", axis_cur),
              ("대안 축1 + 급락상위2", axis_alt)):
    seats = hits = 0
    ret = 0.0
    fired = 0
    pays = []
    for x in rows:
        cs = f(x)
        if not cs:
            continue
        fired += 1
        for c in cs:
            seats += 1
            if set(c) == x["top3"]:
                hits += 1
                if x["trio"]:
                    ret += x["trio"]
                    pays.append(x["trio"])
    rr = (ret / seats * 100) if seats else 0.0
    print("   %-26s 발동 %4d · 구좌 %4d · 적중 %3d · 회수율 %6.1f%% · 배당보유 %d  %s"
          % (nm, fired, seats, hits, rr, len(pays),
             "판정불가" if hits < MIN_HITS else ""))
