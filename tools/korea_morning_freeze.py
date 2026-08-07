# -*- coding: utf-8 -*-
"""🔴 [2026-08-07 대표 지시] 한국경마 **전적 후보를 아침에 동결**한다.

  "전적은 배당과 무관하니 아침에 확정해도 된다.
   그러면 우리가 먼저 봤고 시장이 나중에 따라왔나를 잴 수 있다."

이 도구가 하는 일은 **기록뿐**이다.
  · 🔴 판정 로직·추천·학습에 일절 개입하지 않는다. 완전 읽기 전용.
  · 저장은 별도 파일(`data/korea_freeze/<날짜>.json`)이고 기존 경로를 건드리지 않는다.
  · 같은 날 여러 번 찍는다(아침 기준점 → 마감). `snaps` 에 append 된다.

왜 아침이어야 하나
  전적은 배당과 무관하므로 **발주 전에 확정할 수 있다.** 그 시점 배당을 함께 남기면
  나중에 "우리가 먼저 봤는데 시장이 따라왔나"를 **시간 순서로** 말할 수 있다.
  마감 뒤에 짚으면 사후 이야기가 된다.

축은 합치지 않는다(record_score 0.0 이 566회 포화된 전례)
  ① 최근 착순 평균(recentPlacings) ② 레이팅(rating). **각각 순위를 낸다.**

사용
  python tools/korea_morning_freeze.py --tag morning
  python tools/korea_morning_freeze.py --tag close
"""
import os
import io
import sys
import json
import time
import argparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SESS = os.path.join(ROOT, "data", "korea_session.json")
STORE = os.path.join(ROOT, "triple_store.json")
OUT_DIR = os.path.join(ROOT, "data", "korea_freeze")


def _dense(vals):
    """동점은 공동 순위(2026-08-06 확정 규칙과 같게 맞춘다)."""
    out, prev, rank = {}, None, 0
    for i, (k, v) in enumerate(vals):
        if prev is None or v != prev:
            rank, prev = i + 1, v
        out[k] = rank
    return out


def axis_ranks(horses):
    """전적 축별 순위. 🔴 배당을 입력에 넣지 않는다."""
    rp, rt = [], []
    for h in horses:
        no = h.get("horseNum")
        if no is None:
            continue
        pl = [x for x in (h.get("recentPlacings") or []) if isinstance(x, (int, float))]
        if pl:
            rp.append((int(no), sum(pl) / float(len(pl))))       # 낮을수록 좋다
        try:
            v = float(str(h.get("rating") or "").strip())
            rt.append((int(no), -v))                             # 높을수록 좋다 → 부호 반전
        except Exception:
            pass
    rp.sort(key=lambda x: x[1])
    rt.sort(key=lambda x: x[1])
    return {"최근착순": _dense(rp), "레이팅": _dense(rt)}


def odds_of(rk_candidates):
    """그 경주의 현재 복승 배당에서 말별 대용값(그 말이 낀 최저 복승)을 만든다.
    ⚠ 배당이 없으면 빈 dict — 없는 것을 지어내지 않는다."""
    try:
        d = json.load(io.open(STORE, encoding="utf-8"))
    except Exception:
        return {}, None
    for k, v in d.items():
        if k not in rk_candidates:
            continue
        q = v.get("quinella") or {}
        best = {}
        for combo, od in (q.items() if isinstance(q, dict) else []):
            try:
                a, b = [int(x) for x in str(combo).split("+")]
                o = float(od)
            except Exception:
                continue
            if o <= 0:
                continue
            for n in (a, b):
                if n not in best or o < best[n]:
                    best[n] = o
        if best:
            return best, k
    return {}, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="morning", help="morning / close / 자유 문자열")
    a = ap.parse_args()

    sess = json.load(io.open(SESS, encoding="utf-8"))
    day = sess.get("date") or time.strftime("%Y-%m-%d")
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, "%s.json" % day)
    doc = {"date": day, "snaps": []}
    if os.path.exists(path):
        try:
            doc = json.load(io.open(path, encoding="utf-8"))
        except Exception:
            pass

    snap = {"tag": a.tag, "at": time.strftime("%Y-%m-%d %H:%M:%S"), "races": []}
    n_odds = 0
    for r in (sess.get("races") or []):
        ven, no = r.get("venue"), r.get("raceNo")
        hs = r.get("horses") or []
        if not ven or no is None:
            continue
        ranks = axis_ranks(hs)
        cands = {"%s %s경주" % (ven, no), "%s %d경주" % (ven, no)}
        od, used = odds_of(cands)
        if od:
            n_odds += 1
        snap["races"].append({
            "venue": ven, "raceNo": no, "postTime": r.get("postTime"),
            "nHorses": len(hs), "axisRanks": ranks, "odds": od, "oddsKey": used,
            # 🔴 전적 후보 = 축별 상위 3두(합치지 않는다 · 축마다 따로 남긴다)
            "top3": {k: [n for n, rk in sorted(v.items(), key=lambda x: x[1])[:3]]
                     for k, v in ranks.items()},
        })
    doc["snaps"].append(snap)
    tmp = path + ".tmp"
    io.open(tmp, "w", encoding="utf-8").write(json.dumps(doc, ensure_ascii=False))
    os.replace(tmp, path)
    print("[동결] %s · tag=%s · 경주 %d · 배당 보유 %d경주 → %s"
          % (day, a.tag, len(snap["races"]), n_odds, os.path.basename(path)))
    print("⚠ 배당이 0경주면 아직 확장이 안 보내는 것이다. 배당이 들어온 뒤 다시 찍어야 기준점이 선다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
