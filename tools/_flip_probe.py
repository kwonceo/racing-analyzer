# -*- coding: utf-8 -*-
"""[측정 · 읽기 전용] 조합 만드는 순서를 뒤집으면.

2026-08-15 대표 지시.
  지금  배당 싼 조합 10개 → 거기 자주 나오는 말 → 서로 조합
  바꿀  전적으로 후보 → 배당 신호로 재배치 → 신호 온 말 중심으로 조합

작업1 지금 구조에서 신호가 순위를 바꾸는가
  코드(app.py 11507~11510) 정렬 키
    combinedProb*1000 + total + formScoreAdj/100 + 50*dualConverge
  신호는 total 에만 들어간다(_elim_score 4812~4816: 급락30%+ +30 · 쌍승상위 +20).
  🔴 combinedProb 는 0~100 인데 1000 을 곱한다. total 최대 +50 은 묻힌다.
     즉 경로는 있으나 힘이 없다.

작업2 새 구조 소급
  신호는 anomaly_history 의 조합 급락에서 마번을 뽑는다(구조화돼 있는 유일한 소스).

⚠ 조합 생성 전체를 재현하지 않는다. 상위3 전조합이라는 대리 정책으로 순위 기준만 비교한다.
"""
import sys, glob, json, os, itertools, collections, statistics as st
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
        # 신호: 급락 조합에서 마번을 뽑고 급락폭 합을 말별로 누적
        sig = collections.Counter()
        for a in (d.get('anomaly_history') or []):
            try:
                dp = float(a.get('drop') or 0)
            except (TypeError, ValueError):
                continue
            if dp >= -20:              # 20% 미만 하락은 신호로 안 본다
                continue
            for tok in str(a.get('combo') or '').replace('-', '+').split('+'):
                if tok.strip().isdigit():
                    sig[int(tok)] += abs(dp)
        t = sorted({res['1st'], res['2nd']})
        b = os.path.basename(f)
        meta[(t[0], t[1], float(po))] = {
            'hs': hs, 'kh': [int(x) for x in (d.get('keyHorses') or []) if str(x).isdigit()],
            'sig': dict(sig), 'kr': any(k in b for k in ('제주', '서울', '부산'))}
    return meta


def best_odds(r):
    best = {}
    for (a, b), o in (r["q"] or {}).items():
        for h in (a, b):
            if best.get(h) is None or o < best[h]:
                best[h] = o
    return best


def line(nm, rows):
    if not rows:
        print("  %-30s 경주 0" % nm)
        return
    slots = sum(len(c) for _, c in rows)
    hit = [(r, c) for r, c in rows if sorted(r["top2"]) in [sorted(x) for x in c]]
    ret = sum(r["po"] for r, _ in hit)
    ho = sorted([r["po"] for r, _ in hit], reverse=True)
    o = [r["q"].get(tuple(sorted(x))) for r, c in rows for x in c]
    o = [x for x in o if x]
    print("  %-30s 경주%4d 구좌%5d 적중%3d(%4.1f%%) 3제외%6.1f%% 배당중앙%5.2f %s"
          % (nm, len(rows), slots, len(hit), len(hit) / len(rows) * 100,
             (ret - sum(ho[:3])) / slots * 100 if slots else 0,
             st.median(o) if o else 0, "" if len(hit) >= 30 else "⚠판정불가"))


def rank_report(rs):
    """작업1 — keyHorses 1위가 배당 몇 순위였나."""
    c = collections.Counter(); n = 0
    for r in rs:
        if not r['kh']:
            continue
        bo = best_odds(r)
        order = [h for h, _ in sorted(bo.items(), key=lambda kv: kv[1])]
        try:
            k = order.index(r['kh'][0]) + 1
        except ValueError:
            continue
        c[k if k <= 5 else 6] += 1; n += 1
    print("  유력마 1위가 배당 몇 순위였나 (대상 %d경주)" % n)
    for k in sorted(c):
        lab = '배당 %d순위' % k if k <= 5 else '배당 6순위 이하'
        print("    %-16s %4d경주 (%4.1f%%)" % (lab, c[k], c[k] / n * 100 if n else 0))
    far = sum(v for k, v in c.items() if k >= 4)
    print("    🔴 4순위 이하가 1위로 올라온 경우 %d경주 (%.1f%%)" % (far, far / n * 100 if n else 0))


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
        r.update(m); rs.append(r)
    if not rs:
        print("%s 데이터 없음" % tag)
        return
    print("=" * 116)
    print("%s %d경주" % (tag, len(rs)))
    rank_report(rs)

    def combos(order, r):
        v = [h for h in order if h in {x for k in r["q"] for x in k}][:3]
        return [list(c) for c in itertools.combinations(sorted(v), 2)] if len(v) >= 2 else []

    def form_only(r):
        return [h['no'] for h in sorted(r['hs'], key=lambda x: (-x['top3'], -x['rec']))]

    def form_mix(r, w_odds):
        bo = best_odds(r)
        mx = max([h['rec'] for h in r['hs']] or [0]) or 1.0
        sc = {}
        for h in r['hs']:
            o = bo.get(h['no'])
            mkt = (1.0 / o * 0.75) if o else 0.0
            sc[h['no']] = (h['top3'] * 0.5 + h['rec'] / mx * 0.5) * (1 - w_odds) + mkt * w_odds
        return [n for n, _ in sorted(sc.items(), key=lambda kv: -kv[1])]

    def by_signal(r, base):
        """신호 온 말을 위로 올린다(신호 크기 순), 나머지는 기존 순서."""
        sig = r['sig'] or {}
        on = [h for h in base if sig.get(h)]
        on.sort(key=lambda h: -sig[h])
        return on + [h for h in base if h not in on]

    print("  -- 안 비교 (상위3 전조합 대리정책) --")
    line("안A 지금 방식(keyHorses)", [(r, combos(r['kh'], r)) for r in rs])
    line("안B 전적만 → 신호 재배치", [(r, combos(by_signal(r, form_only(r)), r)) for r in rs])
    line("   (참고) 전적만 · 재배치 없음", [(r, combos(form_only(r), r)) for r in rs])
    line("안C 전적+배당30 → 신호 재배치", [(r, combos(by_signal(r, form_mix(r, 0.3)), r)) for r in rs])
    line("안C2 전적+배당50 → 신호 재배치", [(r, combos(by_signal(r, form_mix(r, 0.5)), r)) for r in rs])
    n_sig = sum(1 for r in rs if r['sig'])
    print("  ※ 신호가 하나라도 있는 경주 %d / %d (%.1f%%)" % (n_sig, len(rs), n_sig / len(rs) * 100))


if __name__ == "__main__":
    run("cycle", None, "경륜")
    run("horse", False, "일본 경마")
