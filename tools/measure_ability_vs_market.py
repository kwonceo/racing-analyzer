# -*- coding: utf-8 -*-
"""[읽기 전용] 순수 능력 분석 vs 시장(배당) — 어느 쪽이 1·2착을 담는가.

🔴 왜 이 측정인가 (2026-09-01 대표 화두)
  "배당 신호를 모르고 **순수하게 말의 능력과 여러 데이터로** 분석하던 슈퍼컴퓨터가
   한국 경마에서 크게 혼났다."
  ⇒ 우리 시스템도 같은 갈림길에 서 있다. 오늘 하루 축을 여섯 개 쟀고 **생존 0개**였다.
    그 결과가 "우리가 못해서"인지 "구조가 그런지"를 이 측정이 가른다.

측정: 경주마다 **상위 3두**를 세 방식으로 뽑아 1·2착 **두 마리를 다 담았는가**를 센다.
  ① 순수 능력   record_score(전적 점수) 상위 3두      ← 배당을 전혀 안 본다
  ② 순수 시장   복승 배당 기반 내재확률 상위 3두        ← 능력을 전혀 안 본다
  ③ 현행        corePicks.keyHorses 상위 3두          ← 우리가 실제로 쓰는 것

⚠ 원칙 8-C — 세 방식 **모두 산출 가능한 경주**만 분모로 쓴다(같은 경주에서 비교).
⚠ 원칙 30 — 능력 점수가 없는 말을 하위로 밀지 않는다. 그런 경주는 분모에서 뺀다.
⚠ 이것은 **포함률**이다. 회수율이 아니다 — 포함률이 높다고 돈을 버는 것이 아니다(원칙 14).

실행: python tools/measure_ability_vs_market.py [패턴]
"""
import io
import os
import sys
import json
import gzip
import glob
import collections

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BAD = ("odds_suspect", "baseline_reset", "next_race_blocked")


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


def _n(v):
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def main():
    pat = sys.argv[1] if len(sys.argv) > 1 else "2026_0*"
    # 분류: 일본경마 / 한국경마 / 경륜
    KR = ("서울", "부산", "제주", "부경", "과천")
    st = collections.defaultdict(lambda: collections.Counter())
    skip = collections.Counter()
    for f in sorted(glob.glob(os.path.join(BASE, "data", "analysis_log", pat + ".json"))):
        d = load(f)
        if not isinstance(d, dict):
            continue
        sp = d.get("sport")
        if sp not in ("horse", "cycle"):
            continue
        nm = os.path.basename(f)[:-5]
        rr = load(os.path.join(BASE, "data", "race_results", nm + ".json"))
        if not isinstance(rr, dict):
            skip["결과없음"] += 1
            continue
        res = rr.get("result") or {}
        try:
            top2 = set([int(res["1st"]), int(res["2nd"])])
        except (TypeError, ValueError, KeyError):
            skip["착순없음"] += 1
            continue
        oh = load(os.path.join(BASE, "data", "odds_history", nm + ".json")) or {}
        sn = [s for s in (oh.get("snapshots") or [])
              if isinstance(s, dict) and s.get("quinella") and not s.get("after_close")
              and not any(s.get(b) for b in BAD)]
        if not sn:
            skip["배당없음"] += 1
            continue
        qm = qmap(sn[-1])
        if len(qm) < 6:
            skip["배당부족"] += 1
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
        hs = d.get("horses") or []
        # ① 순수 능력 — record_score 가 **전 두수에 있어야** 쓴다(원칙 30)
        abil = [(int(h["no"]), _n(h.get("record_score"))) for h in hs if _n(h.get("no")) is not None]
        abil = [(no, v) for no, v in abil if v is not None and no in P]
        if len(abil) < 5 or len(abil) < len([h for h in hs if _n(h.get("no")) is not None]) * 0.8:
            skip["능력점수 부족"] += 1
            continue
        # ③ 현행 keyHorses
        kh = [int(x) for x in ((d.get("corePicks") or {}).get("keyHorses") or [])
              if _n(x) is not None][:3]
        if len(kh) < 3:
            skip["keyHorses 부족"] += 1
            continue
        a3 = set(no for no, _v in sorted(abil, key=lambda x: -x[1])[:3])
        m3 = set(no for no, _v in sorted(P.items(), key=lambda x: -x[1])[:3])
        k3 = set(kh)
        grp = "경륜" if sp == "cycle" else ("한국경마" if any(k in nm for k in KR) else "일본경마")
        for g in (grp, "전체"):
            st[g]["n"] += 1
            st[g]["능력"] += 1 if top2 <= a3 else 0
            st[g]["시장"] += 1 if top2 <= m3 else 0
            st[g]["현행"] += 1 if top2 <= k3 else 0
            st[g]["능력∩시장"] += len(a3 & m3)
            st[g]["현행∩시장"] += len(k3 & m3)
            st[g]["현행∩능력"] += len(k3 & a3)

    print("[순수 능력 vs 시장] 상위 3두가 1·2착을 **둘 다** 담았는가")
    print("   표본 %s · 세 방식 모두 산출 가능한 경주만" % pat)
    print("   제외: %s" % dict(skip))
    print("")
    print("   %-10s %6s %10s %10s %10s   %s" % ("구분", "경주", "① 능력만", "② 시장만", "③ 현행", "겹침(3두 중)"))
    for g in ("전체", "일본경마", "한국경마", "경륜"):
        c = st.get(g)
        if not c or c["n"] < 30:
            if c:
                print("   %-10s %6d  ⚠ n<30 판정 불가" % (g, c["n"]))
            continue
        n = c["n"]
        print("   %-10s %6d %9.1f%% %9.1f%% %9.1f%%   능력↔시장 %.2f · 현행↔시장 %.2f · 현행↔능력 %.2f"
              % (g, n, 100.0 * c["능력"] / n, 100.0 * c["시장"] / n, 100.0 * c["현행"] / n,
                 c["능력∩시장"] / float(n), c["현행∩시장"] / float(n), c["현행∩능력"] / float(n)))
    print("")
    print("   🔴 ①이 ②보다 낮으면 「순수 능력 분석」은 시장을 못 이긴다는 뜻이다.")
    print("   ⚠ 포함률이지 회수율이 아니다 — 시장을 그대로 베끼면 공제율만큼 진다(원칙 14).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
