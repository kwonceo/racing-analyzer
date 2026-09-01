# -*- coding: utf-8 -*-
"""[읽기 전용] 경마 고유 축 — 시장 위에 정보가 있나(엣지).

🔴 왜 지금인가 (2026-09-01)
  2026-08-11 에 「다음 세션 1순위」로 적어 둔 것이 있다:
    "totalScore 는 마체중 **변화량**만 본다. **부담 대비 마체중**과 **착차**는 계산식에 아예 없다.
     필드는 이미 있다(저장 배선 · 실데이터 3경주 확인). 🔴 섀도우로 먼저 잰다."
  그때 3경주였다. 지금 최근 경마 horses 가 32종 키를 100% 보유하고 누적 20,101행이다.
  ⇒ 처음으로 잴 수 있다.

🔴 판정 방식 — 경륜 build_flow_table 과 같다(새 규칙을 만들지 않는다).
  엣지 = 실측 입상률 ÷ 시장암시확률. 시장암시는 복승 배당에서:
    p(i,j) = (1/odds) / SUM(1/odds) · P(i) = SUM_j p(i,j)  ⇒ SUM_i P(i) = 2 (1·2착 두 자리)
  🔴 CI 하한 > 1.0 이라야 시장 위의 정보다. 점추정 1.0 초과는 근거가 아니다.

⚠ 원칙 1(n<30 판정 불가) · 2(극단값) · 15(확정배당) · 16(날짜 매칭) · 30(결손이 신호처럼 보인다)
⚠ 결손 편향 방어: 각 축은 그 필드를 **가진 말끼리만** 상·하위로 가른다.
   없는 말을 하위로 밀면 "없어서 모인 무리"를 신호로 착각한다(원칙 30).

실행: python tools/measure_horse_axes.py [패턴]
"""
import io
import os
import sys
import json
import gzip
import glob
import random
import collections

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BAD = ("odds_suspect", "baseline_reset", "next_race_blocked")
BOOT_N, SEED, MIN_N = 2000, 20260901, 30


def load(p):
    for path, gz in ((p, False), (p + ".gz", True)):
        try:
            if gz:
                with gzip.open(path, "rt", encoding="utf-8") as f:
                    return json.load(f)
            with io.open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            continue
    return None


def qmap(sn):
    q = (sn or {}).get("quinella")
    if isinstance(q, list):
        q = {"+".join(str(z) for z in (it.get("combo") or [])): it.get("odds")
             for it in q if isinstance(it, dict)}
    out = {}
    for k, v in (q or {}).items():
        pr = [x for x in str(k).replace("-", "+").split("+") if x.strip().isdigit()]
        if len(pr) != 2 or pr[0] == pr[1]:
            continue
        try:
            o = float(v.get("odds") if isinstance(v, dict) else v)
        except Exception:
            continue
        if o > 0:
            out[(min(int(pr[0]), int(pr[1])), max(int(pr[0]), int(pr[1])))] = o
    return out


def _num(v):
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def _avg(xs):
    xs = [_num(x) for x in (xs or [])]
    xs = [x for x in xs if x is not None]
    return (sum(xs) / len(xs)) if xs else None


# 축 정의 — 값이 클수록 "좋다"고 보는 방향으로 통일한다.
def ax_bw_ratio(h, race):
    """부담 대비 마체중 = 마체중 / 부담중량. 🔴 2026-08-11 1순위로 남겨둔 축."""
    bw, bd = _num(h.get("bodyWeight")), _num(h.get("burdenWeight"))
    return (bw / bd) if (bw and bd and bd > 0) else None


def ax_margin(h, race):
    """착차 평균(작을수록 좋다) → 부호를 뒤집어 크면 좋게 맞춘다."""
    m = _avg(h.get("marginList"))
    return (-m) if m is not None else None


def ax_last3f(h, race):
    """상3F 평균(작을수록 빠르다) → 부호 반전."""
    m = _avg(h.get("last3fList"))
    return (-m) if m is not None else None


def ax_corner_move(h, race):
    """코너 통과 상대위치 변화 — 첫 코너 대비 마지막 코너가 앞이면 +(막판 추입).
    ⚠ _corner_move_bonus 와 같은 취지지만 재현이 아니다(그 함수는 app.py 안에 있다)."""
    cs = h.get("corners")
    fs = h.get("fieldSizes")
    if not isinstance(cs, list) or not cs:
        return None
    vals = []
    for i, c in enumerate(cs):
        # 🔴 실측: 저장 형식이 "2-2"(첫코너-마지막코너) **문자열**이다.
        #   처음에 list/tuple 로 기대해 표본 0 이 나왔다(원칙 8-E — 원자료를 열어 확인).
        if isinstance(c, str):
            parts = [p for p in c.replace(",", "-").split("-") if p.strip()]
        elif isinstance(c, (list, tuple)):
            parts = list(c)
        else:
            continue
        if len(parts) < 2:
            continue
        n = _num(fs[i]) if isinstance(fs, list) and i < len(fs) else None
        a, b = _num(parts[0]), _num(parts[-1])
        if a is None or b is None or not n or n <= 1:
            continue
        vals.append((a - b) / n)
    return (sum(vals) / len(vals)) if vals else None


def ax_jockey(h, race):
    """기수 복승률."""
    return _num(h.get("jockeyRate"))


def ax_dist_exp(h, race):
    """이번 거리 경험 횟수(과거 거리 목록에 현재 거리가 몇 번)."""
    d = _num(race.get("distance"))
    pd = h.get("pastDistances")
    if not d or not isinstance(pd, list):
        return None
    return float(sum(1 for x in pd if _num(x) == d))


AXES = (("부담대비 마체중", ax_bw_ratio), ("착차(작을수록up)", ax_margin),
        ("상3F(빠를수록up)", ax_last3f), ("코너 전진", ax_corner_move),
        ("기수 복승률", ax_jockey), ("거리 경험 횟수", ax_dist_exp))


def boot_edge(v):
    random.seed(SEED)
    n, es = len(v), []
    for _ in range(BOOT_N):
        s = [v[random.randrange(n)] for _ in range(n)]
        h = sum(x[0] for x in s)
        m = sum(x[1] for x in s)
        es.append((h / m) if m else 0.0)
    es.sort()
    return es[int(BOOT_N * 0.025)], es[int(BOOT_N * 0.975)]


def show(lbl, v):
    n = len(v)
    if n < MIN_N:
        print("      %-16s n=%-5d 판정 불가(n<%d)" % (lbl, n, MIN_N))
        return
    h = sum(x[0] for x in v)
    m = sum(x[1] for x in v)
    e = (h / m) if m else 0.0
    lo, hi = boot_edge(v)
    print("      %-16s n=%-5d 입상 %4d (%5.1f%%) · 시장 %5.1f%% · 엣지 %.3f ci[%.3f~%.3f] %s"
          % (lbl, n, h, 100.0 * h / n, 100.0 * m / n, e, lo, hi,
             "🟢 생존" if lo > 1.0 else ""))


def main():
    pat = sys.argv[1] if len(sys.argv) > 1 else "2026_0*"
    races = 0
    buckets = collections.defaultdict(lambda: {"hi": [], "lo": []})
    for f in sorted(glob.glob(os.path.join(BASE, "data", "analysis_log", pat + ".json"))):
        d = load(f)
        if not isinstance(d, dict) or d.get("sport") != "horse":
            continue
        nm = os.path.basename(f)[:-5]
        rr = load(os.path.join(BASE, "data", "race_results", nm + ".json"))
        if not isinstance(rr, dict):
            continue
        res = rr.get("result") or {}
        try:
            top2 = set([int(res["1st"]), int(res["2nd"])])
        except (TypeError, ValueError, KeyError):
            continue
        oh = load(os.path.join(BASE, "data", "odds_history", nm + ".json")) or {}
        sn = [s for s in (oh.get("snapshots") or [])
              if isinstance(s, dict) and s.get("quinella") and not s.get("after_close")
              and not any(s.get(b) for b in BAD)]
        if not sn:
            continue
        qm = qmap(sn[-1])
        if len(qm) < 6:
            continue
        inv = {}
        for c, o in qm.items():
            inv[c] = 1.0 / o
        tot = sum(inv.values())
        if tot <= 0:
            continue
        P = collections.defaultdict(float)
        for pair, val in inv.items():
            P[pair[0]] += val / tot
            P[pair[1]] += val / tot
        hs = [h for h in (d.get("horses") or []) if _num(h.get("no")) is not None]
        if len(hs) < 6:
            continue
        prof = d.get("raw_profile") or {}
        race = {"distance": prof.get("distance") or d.get("distance")}
        races += 1
        for lbl, fn in AXES:
            got = []
            for h in hs:
                v = fn(h, race)
                if v is not None and int(h["no"]) in P:
                    got.append((h, v))
            if len(got) < 4:
                continue
            got.sort(key=lambda x: -x[1])
            k = max(1, len(got) // 3)
            for tag, grp in (("hi", got[:k]), ("lo", got[-k:])):
                for h, _v in grp:
                    no = int(h["no"])
                    buckets[lbl][tag].append((1 if no in top2 else 0, P[no]))

    print("[경마 고유 축] 시장 위에 정보가 있나")
    print("   표본 %s · 결과+마감전배당 보유 %d경주" % (pat, races))
    print("   🔴 판정: 엣지 CI 하한 > 1.0 이라야 생존(점추정 1.0 초과는 근거가 아니다)")
    print("   각 축은 그 필드를 가진 말끼리만 상·하위 1/3 로 가른다(원칙 30)")
    print("")
    for lbl, _fn in AXES:
        b = buckets[lbl]
        if not b["hi"] and not b["lo"]:
            print("   * %s — 표본 없음" % lbl)
            continue
        print("   * %s" % lbl)
        show("상위 1/3", b["hi"])
        show("하위 1/3", b["lo"])
    print("")
    print("   생존 축이 없으면 그 정보는 이미 배당에 들어 있다는 뜻이다(원칙 14).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
