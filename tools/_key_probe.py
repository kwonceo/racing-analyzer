# -*- coding: utf-8 -*-
"""[일회성 측정 · 읽기 전용] 조합 유력마를 전적 쪽으로 옮기면 성적이 어떻게 되나.

2026-08-15 대표 지시.

코드에서 읽은 사실(app.py):
  1단계 11274~11288  key_horses = 복승 배당 낮은 순 10개 조합의 등장빈도(+1/배당) 상위 3
                     → **전적이 한 글자도 안 들어간다.**
  2단계 11498~11522  전적이 있으면 combinedProb 순으로 재정렬
                     _prob_ev 4748~4755:
                        market   = (1/배당) * 0.75
                        form     = 최근 3착 이내 비율
                                   배당 10배+ → form * 0.7 / 배당 5배↓ → form * 1.1
                        combined = market * 0.7 + form * 0.3
  ⇒ 전적 비중은 30%이고, **그 30% 안에서도 배당이 다시 가감**한다.
  ⚠ 그리고 여기 쓰이는 '전적'은 record_score(150·80 같은 점수)가 **아니라**
     최근 3착 이내 비율이다. 화면에 보이는 점수와 다른 값이다.

⚠ 이 측정은 조합 생성 전체를 재현하지 않는다(원칙 3).
  상위 3두의 전조합(3구좌)을 산다는 **대리 정책**으로 순위 기준만 비교한다.
  절대값이 아니라 **안 사이의 상대 비교**로만 읽어야 한다.
"""
import sys, glob, json, os, itertools, statistics as st
sys.path.insert(0, 'tools')
import measure_recovery as M


def clean(r):
    try:
        return M.CLEAN_LO <= r["po"] / r["mo"] <= M.CLEAN_HI
    except Exception:
        return False


def build():
    meta = {}
    for f in glob.glob('data/analysis_log/2026_0*.json'):
        try:
            d = json.load(open(f, encoding='utf-8'))
        except Exception:
            continue
        res = d.get('result') or {}
        po = (res.get('payouts') or {}).get('quinella')
        if po is None or res.get('1st') is None or res.get('2nd') is None:
            continue
        hs = []
        for h in (d.get('horses') or []):
            try:
                n = int(h.get('no'))
            except (TypeError, ValueError):
                continue
            pl = h.get('recentPlacings') or []
            top3 = (sum(1 for p in pl if isinstance(p, int) and p <= 3) / len(pl)) if pl else 0.0
            hs.append({'no': n, 'rec': float(h.get('record_score') or 0), 'top3': top3})
        t = sorted({res['1st'], res['2nd']})
        b = os.path.basename(f)
        meta[(t[0], t[1], float(po))] = {
            'hs': hs, 'kh': [int(x) for x in (d.get('keyHorses') or []) if str(x).isdigit()],
            'kr': any(k in b for k in ('제주', '서울', '부산'))}
    return meta


def mkt_rank(r):
    """복승 배당에서 말별 최저배당 → 낮은 순."""
    best = {}
    for (a, b), o in (r["q"] or {}).items():
        for h in (a, b):
            if best.get(h) is None or o < best[h]:
                best[h] = o
    return [h for h, _ in sorted(best.items(), key=lambda kv: kv[1])], best


def line(nm, rows):
    if not rows:
        print("  %-26s 경주 0" % nm)
        return
    slots = sum(len(c) for _, c in rows)
    hit = [(r, c) for r, c in rows if sorted(r["top2"]) in [sorted(x) for x in c]]
    ret = sum(r["po"] for r, _ in hit)
    ho = sorted([r["po"] for r, _ in hit], reverse=True)
    o = [r["q"].get(tuple(sorted(x))) for r, c in rows for x in c]
    o = [x for x in o if x]
    print("  %-26s 경주%4d 구좌%5d 적중%3d(%4.1f%%) 3제외%6.1f%% 배당중앙%5.2f %s"
          % (nm, len(rows), slots, len(hit), len(hit) / len(rows) * 100,
             (ret - sum(ho[:3])) / slots * 100 if slots else 0,
             st.median(o) if o else 0, "" if len(hit) >= 30 else "⚠판정불가"))


def run(sport, only_kr=None, tag=""):
    meta = build()
    rs = []
    for r in M.load_races(sport=sport, pattern="2026_0*"):
        if not clean(r):
            continue
        m = meta.get((r["top2"][0], r["top2"][1], r["po"]))
        if not m or not m['hs']:
            continue
        if only_kr is not None and m['kr'] != only_kr:
            continue
        r.update(m)
        rs.append(r)
    if not rs:
        print("%s 데이터 없음" % tag)
        return
    print("=" * 112)
    print("%s %d경주 — 조합 유력마 기준을 바꾸면 (상위3 전조합 대리정책)" % (tag, len(rs)))

    def combos(order, r):
        v = [h for h in order if h in {x for k in r["q"] for x in k}][:3]
        return [list(c) for c in itertools.combinations(sorted(v), 2)] if len(v) >= 2 else []

    def mixed(r, w_form):
        mr, best = mkt_rank(r)
        sc = {}
        for h in r['hs']:
            n = h['no']
            o = best.get(n)
            mkt = (1.0 / o * 0.75) if o else 0.0
            sc[n] = mkt * (1 - w_form) + h['top3'] * w_form
        return [n for n, _ in sorted(sc.items(), key=lambda kv: -kv[1])]

    line("현행 keyHorses", [(r, combos(r['kh'], r)) for r in rs])
    line("배당만", [(r, combos(mkt_rank(r)[0], r)) for r in rs])
    line("전적 30%(지금 식)", [(r, combos(mixed(r, 0.3), r)) for r in rs])
    line("전적 50%", [(r, combos(mixed(r, 0.5), r)) for r in rs])
    line("전적 70%", [(r, combos(mixed(r, 0.7), r)) for r in rs])
    line("전적만(입상률)", [(r, combos(mixed(r, 1.0), r)) for r in rs])
    line("전적 점수순(record)", [(r, combos([h['no'] for h in sorted(r['hs'], key=lambda x: -x['rec'])], r)) for r in rs])


if __name__ == "__main__":
    run("cycle", None, "경륜")
    run("horse", True, "한국(경마 중)")
    run("horse", False, "일본 경마")


def run2(sport, only_kr=None, tag=""):
    """[작업1·3] 화면 전적 점수(record_score)로 재기 + 배당 깎기 유무."""
    meta = build()
    rs = []
    for r in M.load_races(sport=sport, pattern="2026_0*"):
        if not clean(r):
            continue
        m = meta.get((r["top2"][0], r["top2"][1], r["po"]))
        if not m or not m['hs']:
            continue
        if only_kr is not None and m['kr'] != only_kr:
            continue
        r.update(m)
        rs.append(r)
    if not rs:
        print("%s 데이터 없음" % tag)
        return
    print("=" * 112)
    print("%s %d경주 — 화면 전적 점수로 재기" % (tag, len(rs)))

    def combos(order, r):
        v = [h for h in order if h in {x for k in r["q"] for x in k}][:3]
        return [list(c) for c in itertools.combinations(sorted(v), 2)] if len(v) >= 2 else []

    def mix(r, w, use_rec, cut=True):
        _, best = mkt_rank(r)
        mx = max([h['rec'] for h in r['hs']] or [0]) or 1.0
        sc = {}
        for h in r['hs']:
            n = h['no']; o = best.get(n)
            mkt = (1.0 / o * 0.75) if o else 0.0
            f = (h['rec'] / mx) if use_rec else h['top3']
            if cut and o:                      # 지금 코드의 배당 가감
                if o >= 10:
                    f *= 0.7
                elif o <= 5:
                    f = min(1.0, f * 1.1)
            sc[n] = mkt * (1 - w) + f * w
        return [n for n, _ in sorted(sc.items(), key=lambda kv: -kv[1])]

    line("현행 keyHorses", [(r, combos(r['kh'], r)) for r in rs])
    for w in (0.3, 0.5, 0.7, 1.0):
        line("화면점수 %d%%" % int(w * 100), [(r, combos(mix(r, w, True), r)) for r in rs])
    print("  -- 배당 깎기(10배+ ×0.7)를 없애면 --")
    for w in (0.3, 0.5, 0.7):
        line("  화면점수 %d%% 깎기없음" % int(w * 100),
             [(r, combos(mix(r, w, True, cut=False), r)) for r in rs])
        line("  3착률   %d%% 깎기없음" % int(w * 100),
             [(r, combos(mix(r, w, False, cut=False), r)) for r in rs])


def compare_forms():
    """[작업2] 두 전적이 얼마나 다르고, 어느 쪽이 3착권을 잘 맞히나."""
    meta = build()
    same = tot = 0
    hit_rec = hit_top3 = n = 0
    for r in M.load_races(sport="cycle", pattern="2026_0*"):
        if not clean(r):
            continue
        m = meta.get((r["top2"][0], r["top2"][1], r["po"]))
        if not m or len(m['hs']) < 4:
            continue
        hs = m['hs']
        a = [h['no'] for h in sorted(hs, key=lambda x: -x['rec'])][:3]
        b = [h['no'] for h in sorted(hs, key=lambda x: -x['top3'])][:3]
        same += len(set(a) & set(b)); tot += 3
        t2 = set(r["top2"])
        hit_rec += len(t2 & set(a)); hit_top3 += len(t2 & set(b)); n += 1
    print("=" * 112)
    print("두 전적 비교 (경륜 %d경주)" % n)
    print("  상위3이 겹치는 정도  %.2f / 3" % (same / (tot / 3) if tot else 0))
    print("  1·2착을 상위3에 담은 수(2점 만점)")
    print("    화면 전적 점수  %.2f" % (hit_rec / n if n else 0))
    print("    최근 5경주 3착률 %.2f" % (hit_top3 / n if n else 0))
