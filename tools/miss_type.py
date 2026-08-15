# -*- coding: utf-8 -*-
"""[복기 자동 기록] 놓친 경주를 여섯 유형으로 분류해 하루 한 장으로 남긴다.

2026-08-15 대표 지시 · 2026-08-16 스케줄러 배선.

유형(대표 정의)
  가  두 말 다 후보에도 없었다
  나  두 말 다 우리 조합에 있었는데 그 짝만 없었다
  다  조합에 있었는데 상한이나 컷에 잘렸다
  라  다이아나 참고로 밀렸다
  마1 정답 두 말 **모두**에 신호가 있었는데 조합에 안 넣었다   ← 진짜 크기
  마2 한 말에만 신호가 있었다
  바  아무 근거도 없었다. 못 맞히는 경주였다

판정 순서  다 → 라 → 나 → 마1 → 마2 → 가 → 바
  🔴 순서를 바꾸면 분포가 달라진다. 「만들었다가 버린 것」을 먼저 세는 이유는
     그것이 우리가 손댈 수 있는 것이기 때문이다.

🔴 완전 읽기 전용이다. analysis_log 를 읽기만 하고 logs/miss_type/ 에만 쓴다.
   추천·판정·수집 어디에도 개입하지 않는다.
"""
import glob
import json
import os
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(BASE, "logs", "miss_type")

ORDER = ['적중', '다 잘림', '라 다이아', '나 짝만없음', '마1 둘다신호', '마2 한쪽신호',
         '가 후보에도없음', '바 근거없음']


def _sig_horses(d, thr=-20.0):
    """급락 신호가 붙은 마번. anomaly_history 의 조합 급락에서 뽑는다."""
    out = set()
    for a in (d.get('anomaly_history') or []):
        try:
            if float(a.get('drop') or 0) > thr:
                continue
        except (TypeError, ValueError):
            continue
        for tok in str(a.get('combo') or '').replace('-', '+').split('+'):
            if tok.strip().isdigit():
                out.add(int(tok))
    return out


def classify(d):
    """(유형, 정답조합) 또는 None(결과·추천이 없어 판정 불가)."""
    res = d.get('result') or {}
    if res.get('1st') is None or res.get('2nd') is None:
        return None
    ans = tuple(sorted([res['1st'], res['2nd']]))
    cp = d.get('corePicks') or {}
    dc = [tuple(sorted(c)) for c in ((cp.get('displayedCombos') or {}).get('quinellas') or [])]
    if not dc:
        return None
    if ans in dc:
        return ('적중', ans)
    ref = [tuple(sorted(x.get('combo') or [])) for x in (cp.get('quinellaRef') or []) if x.get('combo')]
    dia = [tuple(sorted(x.get('combo') or [])) for x in (cp.get('bmedSpecial') or []) if x.get('combo')]
    cand = set(int(x) for x in ((d.get('elimination') or {}).get('candidates') or [])
               if str(x).isdigit())
    mine = {h for c in dc for h in c}
    sig = _sig_horses(d)
    if ans in ref:
        return ('다 잘림', ans)
    if ans in dia:
        return ('라 다이아', ans)
    if ans[0] in mine and ans[1] in mine:
        return ('나 짝만없음', ans)
    if ans[0] in sig and ans[1] in sig:
        return ('마1 둘다신호', ans)
    if ans[0] in sig or ans[1] in sig:
        return ('마2 한쪽신호', ans)
    if ans[0] not in cand and ans[1] not in cand:
        return ('가 후보에도없음', ans)
    return ('바 근거없음', ans)


def daily(date_ymd):
    """하루치를 한 장으로. date_ymd 는 'YYYY-MM-DD'.
    반환 dict · logs/miss_type/<날짜>.json 에 저장한다."""
    pat = date_ymd.replace('-', '_')
    rows, cnt = [], {}
    for f in sorted(glob.glob(os.path.join(BASE, 'data', 'analysis_log', '%s_*.json' % pat))):
        try:
            d = json.load(open(f, encoding='utf-8'))
        except Exception:
            continue
        r = classify(d)
        if not r:
            continue
        ty, ans = r
        po = ((d.get('result') or {}).get('payouts') or {}).get('quinella')
        nm = os.path.basename(f).replace('.json', '').replace(pat + '_', '')
        rows.append({'race': nm, 'type': ty, 'answer': list(ans),
                     'payout': (float(po) if po is not None else None)})
        cnt[ty] = cnt.get(ty, 0) + 1
    n = len(rows)
    hit = cnt.get('적중', 0)
    miss = n - hit
    unfix = cnt.get('바 근거없음', 0) + cnt.get('가 후보에도없음', 0)
    hi = [x for x in rows if (x['payout'] or 0) >= 20]
    hicnt = {}
    for x in hi:
        hicnt[x['type']] = hicnt.get(x['type'], 0) + 1
    doc = {
        'date': date_ymd, 'at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'races': n, 'hit': hit, 'miss': miss,
        'hitRate': round(hit / n * 100, 1) if n else 0,
        'byType': {k: cnt.get(k, 0) for k in ORDER if cnt.get(k)},
        'unfixable': unfix,
        'unfixablePct': round(unfix / miss * 100, 1) if miss else 0,
        'highOdds': {'races': len(hi), 'byType': hicnt},
        'rows': rows,
    }
    try:
        os.makedirs(OUT_DIR, exist_ok=True)
        with open(os.path.join(OUT_DIR, '%s.json' % date_ymd), 'w', encoding='utf-8') as fp:
            json.dump(doc, fp, ensure_ascii=False, indent=1)
    except Exception as e:
        print('[유형분류] 저장 실패(무시):', str(e)[:120])
    return doc


def text(doc):
    """하루 한 장 — 사람이 읽는 형태."""
    if not doc or not doc.get('races'):
        return '%s 대상 경주 없음' % (doc or {}).get('date', '?')
    L = ['%s 놓친 이유 한 장 — %d경주 · 적중 %d(%.1f%%)'
         % (doc['date'], doc['races'], doc['hit'], doc['hitRate'])]
    for k in ORDER:
        v = (doc.get('byType') or {}).get(k)
        if v:
            L.append('  %-14s %3d경주 (%.1f%%)' % (k, v, v / doc['races'] * 100))
    if doc.get('miss'):
        L.append('  손댈 수 없는 것(가+바) %d / 미적중 %d = %.1f%%'
                 % (doc['unfixable'], doc['miss'], doc['unfixablePct']))
    hi = doc.get('highOdds') or {}
    if hi.get('races'):
        top = sorted((hi.get('byType') or {}).items(), key=lambda kv: -kv[1])[:3]
        L.append('  정답 20배 이상 %d경주 — %s'
                 % (hi['races'], ' · '.join('%s %d' % (k, v) for k, v in top)))
    return '\n'.join(L)


if __name__ == "__main__":
    import sys
    d = sys.argv[1] if len(sys.argv) > 1 else time.strftime('%Y-%m-%d')
    print(text(daily(d)))
