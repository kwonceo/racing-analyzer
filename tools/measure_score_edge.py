# -*- coding: utf-8 -*-
"""시장순위를 고정하고 그 안에서 우리 점수순위가 정보를 더하는지 잰다 (읽기 전용).

🔴 왜 이 설계인가 (원칙 8 — 비교 가능한 값인가)
  시장 내재확률은 **1착 확률**이고 우리가 재려는 것은 **1·2착 진입**이라 직접 비교가 성립하지 않는다.
  ⇒ **시장순위를 고정해 놓고 그 안에서** 우리 점수순위로 갈라 입상률을 본다.
    「시장이 4위라고 한 말들 중에서 우리가 1위로 본 말이 더 오는가」
    시장이 이미 아는 것은 층 안에서 상쇄되고 **우리 점수가 더하는 정보만** 남는다.

⚠ 원칙 30 — 무리마다 record_score 보유율이 다르면 비교가 성립하지 않는다. 보유율을 함께 낸다.
⚠ 원칙 1  — 셀 n<30 은 판정 불가로 표기한다.
⚠ 원칙 27 — 배당은 **마감 전 마지막 정상 틱**만 쓴다(마감 후 값은 그 시점에 없었다).
⚠ 원칙 16 — 파일 매칭은 날짜까지 포함한다(analysis_log 파일명에서 파생).
"""
import os, io, sys, json, gzip, glob, math

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG  = os.path.join(ROOT, "data", "analysis_log")
ODDS = os.path.join(ROOT, "data", "odds_history")

# 오염 틱은 제외한다 — _review_observed 와 같은 목록
_BAD = ("odds_suspect", "baseline_reset", "next_race_blocked")


def _load(path):
    """⚠ .gz 를 먼저 본다 — odds_history 는 오래된 것이 압축돼 있다(2026-08-24 맹점)."""
    for p, op in ((path + ".gz", gzip.open), (path, io.open)):
        if os.path.exists(p):
            try:
                with op(p, "rt", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return None
    return None


def _ticks(doc):
    if not isinstance(doc, dict):
        return []
    out = list(doc.get("archive_snapshots") or [])
    out += list(doc.get("snapshots") or [])
    return out


def _last_pre_close(doc):
    """마감 전(minutes_before>=0) 마지막 **정상** 틱의 복승 배당."""
    best = None
    for t in _ticks(doc):
        if not isinstance(t, dict):
            continue
        if any(t.get(k) for k in _BAD):
            continue
        mb = t.get("minutes_before")
        if mb is None or mb < 0:
            continue
        q = t.get("quinella")
        if not q:
            continue
        if best is None or float(t.get("t") or 0) >= float(best[0]):
            best = (float(t.get("t") or 0), q)
    return best[1] if best else None


def _qmap(q):
    """복승 배당을 {(a,b): odds} 로 정규화한다(리스트/딕트 두 형식)."""
    out = {}
    if isinstance(q, dict):
        it = q.items()
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
        except Exception:
            continue
        if o <= 1.0 or o >= 1000.0:
            continue
        ks = str(k).replace("-", "+").replace(",", "+")
        pr = [p for p in ks.split("+") if p.strip().isdigit()]
        if len(pr) != 2:
            continue
        a, b = int(pr[0]), int(pr[1])
        if a == b:
            continue
        out[(min(a, b), max(a, b))] = o
    return out


def _market_rank(qm):
    """말별 시장 내재확률 = 그 말이 낀 조합의 1/배당 합. 순위만 쓴다."""
    w = {}
    for (a, b), o in qm.items():
        w[a] = w.get(a, 0.0) + 1.0 / o
        w[b] = w.get(b, 0.0) + 1.0 / o
    if len(w) < 4:
        return None
    order = sorted(w.items(), key=lambda kv: -kv[1])
    return {no: i + 1 for i, (no, _) in enumerate(order)}


def _score_rank(horses):
    """우리 점수순위 — record_score 내림차순. ⚠ 원칙 13: 필드 존재를 먼저 확인한다."""
    vals = []
    for h in horses or []:
        try:
            no = int(h.get("no"))
            sc = h.get("record_score")
            if sc is None:
                continue
            vals.append((no, float(sc)))
        except Exception:
            continue
    if len(vals) < 4:
        return None
    vals.sort(key=lambda kv: -kv[1])
    return {no: i + 1 for i, (no, _) in enumerate(vals)}


def _sport(d):
    s = (d.get("sport") or "").strip()
    return "경륜" if s == "cycle" else ("경마" if s == "horse" else s or "?")


def collect(pattern="2026_08_*"):
    rows, stat = [], {"파일": 0, "결과없음": 0, "배당없음": 0, "점수없음": 0, "채택": 0}
    for f in sorted(glob.glob(os.path.join(LOG, pattern + ".json"))):
        stat["파일"] += 1
        d = _load(f)
        if not isinstance(d, dict):
            continue
        res = d.get("result") or {}
        top2 = [res.get("1st"), res.get("2nd")]
        try:
            top2 = set(int(x) for x in top2 if x is not None)
        except Exception:
            top2 = set()
        if len(top2) < 2:
            stat["결과없음"] += 1
            continue
        base = os.path.basename(f)[:-5]
        qm = _qmap(_last_pre_close(_load(os.path.join(ODDS, base + ".json"))) or {})
        mr = _market_rank(qm)
        if not mr:
            stat["배당없음"] += 1
            continue
        sr = _score_rank(d.get("horses"))
        if not sr:
            stat["점수없음"] += 1
            continue
        stat["채택"] += 1
        sp = _sport(d)
        # 그 말이 낀 조합 중 최저 배당 — 「얼마짜리인가」를 함께 본다
        cheap = {}
        for (a, b), o in qm.items():
            cheap[a] = min(cheap.get(a, 9e9), o)
            cheap[b] = min(cheap.get(b, 9e9), o)
        for no in set(mr) & set(sr):
            rows.append({
                "rk": base, "sport": sp, "no": no,
                "mkt": mr[no], "sc": sr[no],
                "hit": 1 if no in top2 else 0,
                "odds": cheap.get(no),
                "nH": len(mr),
            })
    return rows, stat


def _wilson(k, n):
    if n == 0:
        return (0.0, 0.0)
    z, p = 1.96, k / float(n)
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def table(rows, title, mkt_buckets=((1, 1), (2, 2), (3, 3), (4, 5), (6, 99))):
    print("\n  === %s ===  (⚠ 분모 = 마 단위)" % title)
    print("  %-10s %-12s %6s %6s %8s %-18s %8s" %
          ("시장순위", "우리점수순위", "마", "입상", "입상률", "95%CI", "최저배당중앙"))
    for lo, hi in mkt_buckets:
        grp = [r for r in rows if lo <= r["mkt"] <= hi]
        if not grp:
            continue
        lab = "%d위" % lo if lo == hi else ("%d위+" % lo if hi > 50 else "%d~%d위" % (lo, hi))
        for slo, shi, slab in ((1, 2, "상위 1~2"), (3, 4, "중위 3~4"), (5, 99, "하위 5+")):
            sub = [r for r in grp if slo <= r["sc"] <= shi]
            n = len(sub)
            if n == 0:
                continue
            k = sum(r["hit"] for r in sub)
            ci = _wilson(k, n)
            od = sorted(r["odds"] for r in sub if r["odds"])
            med = od[len(od) // 2] if od else None
            mark = "" if n >= 30 else "  ⚠판정불가"
            print("  %-10s %-12s %6d %6d %7.1f%% [%5.1f%%,%5.1f%%] %10s%s" %
                  (lab, slab, n, k, 100.0 * k / n, 100 * ci[0], 100 * ci[1],
                   ("%.1f배" % med) if med else "-", mark))


def main():
    pat = sys.argv[1] if len(sys.argv) > 1 else "2026_08_*"
    rows, stat = collect(pat)
    print("  표본: %s" % pat)
    print("  파일 %d · 결과없음 %d · 배당없음 %d · 점수없음 %d · **채택 %d경주 · %d마**" %
          (stat["파일"], stat["결과없음"], stat["배당없음"], stat["점수없음"],
           stat["채택"], len(rows)))
    table(rows, "전체")
    for sp in ("경륜", "경마"):
        sub = [r for r in rows if r["sport"] == sp]
        if len(sub) >= 200:
            table(sub, sp)


if __name__ == "__main__":
    main()
