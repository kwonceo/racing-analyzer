# -*- coding: utf-8 -*-
"""시계열 축 엣지 측정 — 아직 안 판 4축 (완전 읽기 전용).

판정: 엣지 = 실측 적중률 ÷ 평균 시장암시확률.
      🔴 **부트스트랩 95% CI 하한 > 1.0** 이라야 생존(점추정 1.0 초과는 통과가 아니다).
⚠ 원칙 1 — n(적중) < 30 이면 판정 불가로 표시한다.
⚠ 원칙 2 — 상위 1·3건 제외 회수율을 병기한다(극단값 의존 확인).
"""
import io, json, sys
import numpy as np

P = r'C:\Users\Administrator\Desktop\경마분석서버\data\ts_features.jsonl'
RNG = np.random.default_rng(20260905)
BOOT = 2000


def load():
    rows = [json.loads(l) for l in io.open(P, encoding='utf-8') if l.strip()]
    return (np.array([r['hit'] for r in rows], dtype=bool),
            np.array([r['implied'] for r in rows], dtype=float),
            np.array([r['odds'] for r in rows], dtype=float),
            {k: np.array([(np.nan if r.get(k) is None else r[k]) for r in rows], dtype=float)
             for k in ('d2', 'd10', 'cb', 'sig_chg')})


def edge(hit, imp, odds):
    n = hit.size
    if n == 0: return None
    h = int(hit.sum()); p = h / n; m = float(imp.mean())
    e = p / m if m > 0 else float('nan')
    idx = RNG.integers(0, n, size=(BOOT, n))
    bh = hit[idx].mean(axis=1); bm = imp[idx].mean(axis=1)
    be = np.divide(bh, bm, out=np.zeros(BOOT), where=bm > 0)
    lo, hi = np.percentile(be, [2.5, 97.5])
    won = odds[hit]
    ret = float(won.sum()) / n * 100 if n else 0.0
    w = np.sort(won)[::-1]
    r1 = float(w[1:].sum()) / n * 100 if w.size > 1 else 0.0
    r3 = float(w[3:].sum()) / n * 100 if w.size > 3 else 0.0
    return dict(n=n, hits=h, rate=p * 100, imp=m * 100, edge=e, lo=lo, hi=hi,
                ret=ret, r1=r1, r3=r3, med=float(np.median(won)) if won.size else 0.0)


def show(title, bins, hit, imp, odds, feat):
    print('\n' + '=' * 96)
    print(title)
    print('=' * 96)
    print('{:<26} {:>7} {:>6} {:>7} {:>7} {:>7} {:>15} {:>8} {:>8}'.format(
        '구간', 'n', '적중', '실측%', '시장%', '엣지', '엣지 95%CI', '3제외%', '판정'))
    for label, mask in bins:
        m = mask & ~np.isnan(feat) if feat is not None else mask
        r = edge(hit[m], imp[m], odds[m])
        if not r or r['n'] == 0:
            print('{:<26} {:>7}'.format(label, 0)); continue
        if r['hits'] < 30:      v = '⚠판정불가'
        elif r['lo'] > 1.0:     v = '🟢 생존'
        elif r['hi'] < 1.0:     v = '🔴 유의열위'
        else:                   v = '— 미달'
        print('{:<26} {:>7,} {:>6,} {:>6.2f}% {:>6.2f}% {:>7.3f} [{:>5.3f},{:>5.3f}] {:>7.1f}% {:>8}'.format(
            label, r['n'], r['hits'], r['rate'], r['imp'], r['edge'], r['lo'], r['hi'], r['r3'], v))


def main():
    hit, imp, odds, F = load()
    print('전체 {:,}행 · 적중 {:,} · 엣지 {:.4f}  (시장 전판이므로 1.0 근처라야 정상)'.format(
        hit.size, int(hit.sum()), (hit.mean() / imp.mean())))

    d2, d10, cb, sg = F['d2'], F['d10'], F['cb'], F['sig_chg']

    show('【축 A】 마감 2분 배당 변화 — 급락은 이미 팠다. 🔴 급등(양수)은 안 팠다',
         [('급락 -40%↓',      d2 <= -0.40), ('급락 -25~40%', (d2 > -0.40) & (d2 <= -0.25)),
          ('급락 -10~25%',    (d2 > -0.25) & (d2 <= -0.10)), ('횡보 ±10%', (d2 > -0.10) & (d2 < 0.10)),
          ('🔴 급등 +10~25%', (d2 >= 0.10) & (d2 < 0.25)), ('🔴 급등 +25~50%', (d2 >= 0.25) & (d2 < 0.50)),
          ('🔴 급등 +50%↑',   d2 >= 0.50)], hit, imp, odds, d2)

    show('【축 B】 Σ(1/배당) 시간 변화 — 시장 전체 자금 유입/유출 (한 번도 안 봤다)',
         [('Σ 감소 -3%↓',   sg <= -0.03), ('Σ -1~3% 감소', (sg > -0.03) & (sg <= -0.01)),
          ('Σ 거의 불변',    (sg > -0.01) & (sg < 0.01)), ('Σ +1~3% 증가', (sg >= 0.01) & (sg < 0.03)),
          ('Σ 증가 +3%↑',   sg >= 0.03)], hit, imp, odds, sg)

    show('【축 C】 조합 간 상관 붕괴 — 그 말 평균 대비 이 조합만 따로 움직임 🔴 가장 새롭다',
         [('이 조합만 크게 급락 -20%↓', cb <= -0.20), ('-10~20% 따로 급락', (cb > -0.20) & (cb <= -0.10)),
          ('-3~10% 따로 급락',        (cb > -0.10) & (cb <= -0.03)), ('말과 함께 움직임 ±3%', (cb > -0.03) & (cb < 0.03)),
          ('+3~10% 따로 급등',        (cb >= 0.03) & (cb < 0.10)), ('이 조합만 크게 급등 +10%↑', cb >= 0.10)],
         hit, imp, odds, cb)

    show('【축 D】 T-10 → 마감 전체 변화 (긴 창)',
         [('-50%↓', d10 <= -0.50), ('-25~50%', (d10 > -0.50) & (d10 <= -0.25)),
          ('-10~25%', (d10 > -0.25) & (d10 <= -0.10)), ('±10%', (d10 > -0.10) & (d10 < 0.10)),
          ('+10~50%', (d10 >= 0.10) & (d10 < 0.50)), ('+50%↑', d10 >= 0.50)], hit, imp, odds, d10)


if __name__ == '__main__':
    main()
