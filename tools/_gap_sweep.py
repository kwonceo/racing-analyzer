# -*- coding: utf-8 -*-
"""[측정 · 읽기 전용] 원칙 30 훑기 — 무리를 나눠 비교한 것 중 **보유율이 달랐던 것**을 찾는다.

2026-08-21 대표 지시(원칙 28 — 원칙을 만들면 그 자리에서 기존 것을 훑는다).
  🔴 원칙 30: 값이 없으면 그 행은 0·False·낮은 범주로 떨어진다.
    그 무리의 성적이 다르면 「신호를 찾았다」로 착각한다.
  오늘 셋이 걸렸다 — 세 신호 우위 · 급락 편향 · 판형 U자.

훑는 방법
  이 프로젝트가 무리를 나눌 때 쓴 축을 하나씩 잡고,
  **각 무리의 원자료 보유율**을 낸다. 보유율이 크게 다르면 그 비교는 성립하지 않는다.

훑는 축 (오늘까지 결론을 낸 것)
  ① drop_source            live / backfill / 없음        ← 급락 편향(이미 확인됨)
  ② raceShape              느린 판 / 정석 / 난전          ← 판형 U자(이미 확인됨)
  ③ sport                  경륜 / 경마                    ← 종목별 결론 전반
  ④ 판정 명단 유무          displayedCombos 있음/없음      ← 회수율 전반
  ⑤ 확정배당 유무           payouts.quinella 있음/없음     ← 모든 회수율의 분모
  ⑥ 삼복승 확정배당 유무     trifecta/trio 있음/없음        ← 삼복승 1순위 해제
  ⑦ sigMeta 유무           있음/없음                      ← 근거 한 줄·정렬 교체
  ⑧ 마감 전 이력 유무        recommendation_history mb>=5   ← T-5 가용률 판정

🔴 배선하지 않는다. 숫자만.
"""
import collections
import glob
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FIELDS = [
    ("각질(gait)", lambda d, hs: sum(1 for h in hs if h.get("gait") or h.get("styleType")), True),
    ("전적(record_score)", lambda d, hs: sum(1 for h in hs if h.get("record_score") is not None), True),
    ("최근착순(recentPlacings)", lambda d, hs: sum(1 for h in hs if h.get("recentPlacings")), True),
    ("급락(drops_raw)", lambda d, hs: 1 if (d.get("drops_raw") or []) else 0, False),
    ("sigMeta", lambda d, hs: 1 if ((d.get("corePicks") or {}).get("sigMeta")) else 0, False),
    ("확정배당(복승)", lambda d, hs: 1 if ((d.get("result") or {}).get("payouts") or {}).get("quinella") is not None else 0, False),
    ("확정배당(삼복승)", lambda d, hs: 1 if (((d.get("result") or {}).get("payouts") or {}).get("trifecta") is not None
                                       or ((d.get("result") or {}).get("payouts") or {}).get("trio") is not None) else 0, False),
    ("판정 명단", lambda d, hs: 1 if (((d.get("corePicks") or {}).get("displayedCombos") or {}).get("quinellas")) else 0, False),
    ("T-5 이력", lambda d, hs: 1 if any((_mb(x) or -1) >= 5 for x in (d.get("recommendation_history") or [])) else 0, False),
]


def _mb(x):
    try:
        return float(x.get("minutes_before"))
    except (TypeError, ValueError, AttributeError):
        return None


def shape_of(hs):
    gs = [str(h.get("gait") or h.get("styleType") or "") for h in hs]
    if not any(gs):
        return "각질없음"
    lead = sum(1 for g in gs if "선행" in g)
    return "느린 판" if lead <= 1 else ("정석" if lead <= 3 else "난전")


def load(pattern="2026_0*"):
    rows = []
    for f in sorted(glob.glob(os.path.join(BASE, "data", "analysis_log", pattern + ".json"))):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        hs = d.get("horses") or []
        m = re.match(r"(\d{4}_\d{2})", os.path.basename(f))
        rows.append({"doc": d, "hs": hs, "month": m.group(1) if m else "?",
                     "sport": d.get("sport") or "?",
                     "shape": shape_of(hs),
                     "res": 1 if (d.get("result") or {}).get("1st") is not None else 0})
    return rows


def block(rows, axis, keyfn, tag):
    g = collections.defaultdict(list)
    for r in rows:
        g[keyfn(r)].append(r)
    ks = [k for k in g if len(g[k]) >= 30]
    if len(ks) < 2:
        print("  [%s] 무리가 하나뿐이거나 표본 부족 — 훑기 불가" % tag)
        return
    print("  [%s]" % tag)
    hdr = "     %-14s %6s" % ("무리", "경주")
    for name, _, _ in FIELDS:
        hdr += " %13s" % name[:13]
    print(hdr)
    stat = {}
    for k in sorted(ks, key=lambda x: -len(g[x])):
        v = g[k]
        line = "     %-14s %6d" % (str(k)[:14], len(v))
        vals = []
        for name, fn, per_horse in FIELDS:
            if per_horse:
                tot = sum(len(r["hs"]) for r in v)
                got = sum(fn(r["doc"], r["hs"]) for r in v)
                p = 100 * got / tot if tot else 0.0
            else:
                p = 100 * sum(fn(r["doc"], r["hs"]) for r in v) / len(v)
            vals.append(p)
            line += " %12.1f%%" % p
        stat[k] = vals
        print(line)
    # 🔴 무리 사이 보유율 차이가 큰 항목을 짚는다
    warn = []
    for i, (name, _, _) in enumerate(FIELDS):
        vs = [stat[k][i] for k in stat]
        if max(vs) - min(vs) >= 20.0:
            warn.append("%s %.0f~%.0f%%" % (name, min(vs), max(vs)))
    if warn:
        print("     🔴 보유율이 20%p 넘게 갈리는 항목: " + " · ".join(warn))
    else:
        print("     🟢 보유율 차이 20%p 미만 — 이 축의 비교는 성립한다")


if __name__ == "__main__":
    rows = load()
    print("=" * 150)
    print("원칙 30 훑기 · 분석로그 %d경주 (2026-07~08)" % len(rows))
    print("⚠ 각 무리의 **원자료 보유율**을 낸다. 20%p 넘게 갈리면 그 축의 성적 비교는 성립하지 않는다.")
    print("=" * 150)
    block(rows, "month", lambda r: r["month"], "① 달")
    block(rows, "sport", lambda r: r["sport"], "② 종목")
    block([r for r in rows if r["sport"] == "cycle"], "shape", lambda r: r["shape"], "③ 판형(경륜)")
    block([r for r in rows if r["month"] >= "2026_08" and r["sport"] == "cycle"],
          "shape", lambda r: r["shape"], "④ 판형(경륜 · 8월만)")
    block(rows, "res", lambda r: "결과있음" if r["res"] else "결과없음", "⑤ 결과 유무")
