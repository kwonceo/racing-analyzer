# -*- coding: utf-8 -*-
"""[복기 · 읽기 전용] 오늘 한국 경마 전수 복기.

2026-08-15 대표 지시. 경주마다 한 장으로 적는다.
  우리가 낸 조합 / 실제 착순 / 정답 복승 배당 / 우리 말이 몇 등 했나 /
  정답 말이 화면 어디에 있었나 / 신호가 붙었나 / 유형 여섯 중 무엇인가

유형(대표 정의)
  가 두 말 다 후보에도 없었다
  나 두 말 다 우리 조합에 있었는데 그 짝만 없었다
  다 조합에 있었는데 상한이나 컷에 잘렸다
  라 다이아나 참고로 밀렸다
  마 신호가 붙어 있었는데 조합에 안 넣었다
  바 그 밖(한쪽만 있었다 등)
"""
import sys, glob, json, os


def load(date="2026_08_15"):
    out = []
    for f in sorted(glob.glob('data/analysis_log/%s_*.json' % date)):
        b = os.path.basename(f)
        if not any(k in b for k in ('서울', '부산', '제주')):
            continue
        try:
            d = json.load(open(f, encoding='utf-8'))
        except Exception:
            continue
        out.append((b.replace('.json', '').replace(date + '_', ''), d))
    return out


def qmap(d):
    """마지막 스냅샷의 복승 배당맵."""
    p = 'data/odds_history/' + os.path.basename(d.get('_path', '')) if d.get('_path') else None
    return {}


def review(date="2026_08_15"):
    rows = load(date)
    print("=" * 100)
    print("오늘 한국 경마 복기 — %d경주" % len(rows))
    stat = {}
    for nm, d in rows:
        res = d.get('result') or {}
        t1, t2, t3 = res.get('1st'), res.get('2nd'), res.get('3rd')
        cp = d.get('corePicks') or {}
        fq = cp.get('finalQuinellas') or []
        picks = [tuple(sorted(q.get('combo') or [])) for q in fq if q.get('combo')]
        pod = {tuple(sorted(q.get('combo') or [])): q.get('odds') for q in fq if q.get('combo')}
        kh = [int(x) for x in (d.get('keyHorses') or []) if str(x).isdigit()]
        star = [int(x) for x in (cp.get('starHorses') or []) if str(x).isdigit()]
        dia = [tuple(sorted(x.get('combo') or [])) for x in (cp.get('bmedSpecial') or []) if x.get('combo')]
        ref = [tuple(sorted(x.get('combo') or [])) for x in (cp.get('quinellaRef') or []) if x.get('combo')]
        cand = (d.get('elimination') or {}).get('candidates') or []
        sigs = d.get('signals_detected') or []
        signos = set()
        for s in sigs:
            for tok in str(s.get('detail') or ''):
                pass
        print("-" * 100)
        if t1 is None or t2 is None:
            print("%-10s 결과 없음 · 추천 %s" % (nm, [list(p) for p in picks]))
            continue
        ans = tuple(sorted([t1, t2]))
        hit = ans in picks
        # 우리 말들이 몇 등 했나
        order = {t1: 1, t2: 2}
        if t3 is not None:
            order[t3] = 3
        mine = sorted({h for p in picks for h in p})
        place = ' · '.join('%d번 %s' % (h, ('%d착' % order[h]) if h in order else '4착밖') for h in mine)
        # 정답 두 말이 어디 있었나
        def where(h):
            if h in kh[:3]:
                return '유력마'
            if h in star:
                return '화면별표'
            if any(h in p for p in dia):
                return '다이아'
            if any(h in p for p in ref):
                return '참고(컷밖)'
            if h in cand:
                return '후보'
            return '없음'
        w1, w2 = where(t1), where(t2)
        # 유형
        both_in = all(any(h in p for p in picks) for h in ans)
        if hit:
            ty = '적중'
        elif ans in ref:
            ty = '다 (컷·상한에 잘림)'
        elif ans in dia:
            ty = '라 (다이아로 밀림)'
        elif both_in:
            ty = '나 (둘 다 있었는데 짝만 없음)'
        elif w1 == '없음' and w2 == '없음':
            ty = '가 (둘 다 후보에도 없음)'
        else:
            ty = '바 (한쪽만 있었다)'
        stat[ty] = stat.get(ty, 0) + 1
        print("%-10s 착순 %s-%s-%s   %s" % (nm, t1, t2, t3, '🟢 적중' if hit else '🔴 미적중'))
        print("   우리 조합   %s" % ' · '.join('%d+%d %s배' % (p[0], p[1], pod.get(p) or '?') for p in picks))
        print("   정답 복승   %d+%d" % ans)
        print("   우리 말 착순 %s" % (place or '없음'))
        print("   정답 위치   %d번=%s · %d번=%s" % (t1, w1, t2, w2))
        print("   신호 %d건 · 유형 %s" % (len(sigs), ty))
    print("=" * 100)
    print("유형 집계")
    for k, v in sorted(stat.items(), key=lambda kv: -kv[1]):
        print("  %-24s %d경주" % (k, v))


if __name__ == "__main__":
    review(sys.argv[1] if len(sys.argv) > 1 else "2026_08_15")
