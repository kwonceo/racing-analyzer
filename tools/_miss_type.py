# -*- coding: utf-8 -*-
"""[복기 · 읽기 전용] 놓친 경주를 여섯 유형으로 자동 분류.

2026-08-15 대표 정의
  가 두 말 다 후보에도 없었다
  나 두 말 다 우리 조합에 있었는데 그 짝만 없었다
  다 조합에 있었는데 상한이나 컷에 잘렸다
  라 다이아나 참고로 밀렸다
  마 신호가 붙어 있었는데 조합에 안 넣었다
  바 아무 근거도 없었다. 못 맞히는 경주였다

판정 순서(위가 우선)
  다 → 라 → 나 → 마 → 가 → 바
  🔴 순서를 바꾸면 분포가 달라진다. 「만들었다가 버린 것」을 먼저 세는 것이
     우리가 손댈 수 있는 것이기 때문이다.
"""
import sys, glob, json, os, collections


def classify(d):
    res = d.get('result') or {}
    if res.get('1st') is None or res.get('2nd') is None:
        return None
    ans = tuple(sorted([res['1st'], res['2nd']]))
    cp = d.get('corePicks') or {}
    dc = [tuple(sorted(c)) for c in ((cp.get('displayedCombos') or {}).get('quinellas') or [])]
    if not dc:
        return None
    if ans in dc:
        return ('적중', ans, None)
    ref = [tuple(sorted(x.get('combo') or [])) for x in (cp.get('quinellaRef') or []) if x.get('combo')]
    dia = [tuple(sorted(x.get('combo') or [])) for x in (cp.get('bmedSpecial') or []) if x.get('combo')]
    cand = set(int(x) for x in ((d.get('elimination') or {}).get('candidates') or [])
               if str(x).isdigit())
    mine = {h for c in dc for h in c}
    # 신호가 붙은 마번(급락 조합에서 추출)
    sig = set()
    for a in (d.get('anomaly_history') or []):
        try:
            if float(a.get('drop') or 0) > -20:
                continue
        except (TypeError, ValueError):
            continue
        for tok in str(a.get('combo') or '').replace('-', '+').split('+'):
            if tok.strip().isdigit():
                sig.add(int(tok))
    if ans in ref:
        return ('다 잘림', ans, None)
    if ans in dia:
        return ('라 다이아', ans, None)
    if ans[0] in mine and ans[1] in mine:
        return ('나 짝만없음', ans, None)
    if ans[0] in sig and ans[1] in sig:
        return ('마1 둘다신호', ans, None)
    if ans[0] in sig or ans[1] in sig:
        return ('마2 한쪽신호', ans, None)
    if ans[0] not in cand and ans[1] not in cand:
        return ('가 후보에도없음', ans, None)
    return ('바 근거없음', ans, None)


def run(pattern="2026_08_*", only=None, hi_only=False):
    cnt = collections.Counter(); n = 0; ex = collections.defaultdict(list)
    for f in sorted(glob.glob('data/analysis_log/%s.json' % pattern)):
        b = os.path.basename(f)
        if only == 'kr' and not any(k in b for k in ('제주', '서울', '부산')):
            continue
        if only == 'jp' and any(k in b for k in ('제주', '서울', '부산')):
            continue
        try:
            d = json.load(open(f, encoding='utf-8'))
        except Exception:
            continue
        r = classify(d)
        if not r:
            continue
        ty, ans, _ = r
        if hi_only:
            po = ((d.get('result') or {}).get('payouts') or {}).get('quinella')
            if po is None or float(po) < 20:
                continue
        cnt[ty] += 1; n += 1
        if len(ex[ty]) < 3:
            ex[ty].append(b.replace('.json', '') + ' %d+%d' % ans)
    if not n:
        print("  대상 0경주")
        return
    print("  대상 %d경주" % n)
    for k in ('적중', '다 잘림', '라 다이아', '나 짝만없음', '마1 둘다신호', '마2 한쪽신호',
              '가 후보에도없음', '바 근거없음'):
        v = cnt.get(k, 0)
        if not v:
            continue
        print("    %-14s %4d경주 (%4.1f%%)   예: %s"
              % (k, v, v / n * 100, ' · '.join(ex[k][:2])))
    miss = n - cnt.get('적중', 0)
    if miss:
        ba = cnt.get('바 근거없음', 0) + cnt.get('가 후보에도없음', 0)
        print("    🔴 손댈 수 없는 것(가+바) %d / 미적중 %d = %.1f%%" % (ba, miss, ba / miss * 100))


if __name__ == "__main__":
    print("=" * 96)
    print("8월 전체")
    run("2026_08_*")
    print("=" * 96)
    print("8월 · 정답 복승 20배 이상만")
    run("2026_08_*", hi_only=True)
    print("=" * 96)
    print("8월 · 한국만")
    run("2026_08_*", only='kr')
