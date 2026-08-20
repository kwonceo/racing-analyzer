# -*- coding: utf-8 -*-
"""[측정 · 읽기 전용] 삼복승 3착 후보 정렬을 바꿨을 때.

2026-08-20 대표 지시.
  app.py:10213  _cands.sort(reverse=True)   → **급락 폭이 큰 순**으로 1순위를 정한다.
  🔴 그런데 급락 크기별 적중률은 역U자다:
     ~10% 63.0 · 10~20% 66.6 · **20~30% 68.4(정점)** · 30~50% 62.6 · 50%+ 49.6
  ⇒ 정렬이 데이터와 반대 방향일 수 있다.

안
  A 현행    |drop| 내림차순          (급락이 클수록 1순위)
  B 교체    |drop - 25| 오름차순     (25%에 가까울수록 1순위)
  C 참고    |drop - 25| 오름차순인데 50% 초과는 맨 뒤로

🔴 이 도구의 첫 임무는 「재현되는가」다.
   저장된 reason 문자열에서 축과 받치기 순서를 읽고,
   drops_raw 로 추정한 급락값이 **그 순서를 되살리는지** 먼저 본다.
   되살리지 못하면 시뮬레이션 자체가 무의미하다 — 그 숫자를 먼저 적는다.

⚠ 한계: sig_meta 는 저장되지 않는다. rev·smart·dark 신호는 복원할 수 없고
   drops_raw 의 조합별 급락률로 **말별 최저값**을 추정할 뿐이다.
🔴 배선하지 않는다.
"""
import glob
import json
import os
import re
import statistics as st
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, 'tools'))

RE_MAIN = re.compile(r'확실 2두\(([0-9+]+)\)\s*\+\s*(?:신호 )?복병\s*(\d+)번')


def horse_drop(doc):
    """말별 급락 추정 — drops_raw 의 조합별 pct 중 그 말이 낀 것의 **최저값**."""
    best = {}
    for d in (doc.get('drops_raw') or []):
        p = d.get('pct')
        if not isinstance(p, (int, float)):
            continue
        for n in (d.get('combo') or []):
            try:
                n = int(n)
            except (TypeError, ValueError):
                continue
            if n not in best or p < best[n]:
                best[n] = float(p)
    return best


def load():
    out = []
    for f in sorted(glob.glob(os.path.join(BASE, 'data', 'analysis_log', '2026_08_*.json'))):
        b = os.path.basename(f)
        try:
            d = json.load(open(f, encoding='utf-8'))
        except Exception:
            continue
        if (d.get('sport') or '') != 'cycle':
            continue
        res = d.get('result') or {}
        tp = (res.get('payouts') or {}).get('trifecta')
        if tp is None:
            tp = (res.get('payouts') or {}).get('trio')
        if tp is None or res.get('3rd') is None:
            continue
        cp = d.get('corePicks') or {}
        # 🔴 [2026-08-20 정정] 파일 순서를 1순위로 읽으면 안 된다.
        #   실물 2026_08_18 코치 4경주는 **보험(4번)이 메인(7번)보다 앞에** 저장돼 있었다.
        #   ⇒ 순위는 reason 의 「메인/보험」 이름표로 정한다(원칙 8-D — 검증 코드도 검증한다).
        axis, tagged = None, []
        for t in (cp.get('finalTrifectas') or []):
            rs_ = str(t.get('reason') or '')
            m = RE_MAIN.search(rs_)
            if not m:
                continue
            ax = tuple(sorted(int(x) for x in m.group(1).split('+') if x.isdigit()))
            if axis is None:
                axis = ax
            if ax == axis:
                tagged.append((0 if '메인' in rs_ else 1, len(tagged), int(m.group(2))))
        backers = [n for _, _, n in sorted(tagged)]
        if not axis or len(axis) != 2 or not backers:
            continue
        m = re.match(r'(\d{4}_\d{2}_\d{2})', b)
        out.append({'race': b[:-5], 'date': m.group(1) if m else '',
                    'axis': list(axis), 'backers': backers, 'drop': horse_drop(d),
                    'tp': float(tp),
                    'ans': tuple(sorted([res['1st'], res['2nd'], res['3rd']]))})
    return out


def reorder(r, rule):
    bs = list(r['backers'])
    dr = r['drop']
    if rule == 'A':
        return bs
    known = [n for n in bs if n in dr]
    unknown = [n for n in bs if n not in dr]
    if rule == 'B':
        known.sort(key=lambda n: abs(abs(dr[n]) - 25.0))
    elif rule == 'C':
        known.sort(key=lambda n: (1 if abs(dr[n]) > 50 else 0, abs(abs(dr[n]) - 25.0)))
    return known + unknown


def score(rs, rule, topn, base=None, quiet=False, label=None):
    slots = hits = 0
    ret, hodds, odds = 0.0, [], []
    for r in rs:
        for b in reorder(r, rule)[:topn]:
            slots += 1
            if tuple(sorted(r['axis'] + [b])) == r['ans']:
                hits += 1
                ret += r['tp']
                hodds.append(r['tp'])
    if not slots:
        return None
    hodds.sort(reverse=True)
    d = {'n': len(rs), 'slots': slots, 'hits': hits,
         'hitRate': hits / len(rs) * 100,
         'roi': ret / slots * 100,
         'ex3': (ret - sum(hodds[:3])) / slots * 100,
         'medHit': st.median(hodds) if hodds else 0}
    if not quiet:
        g = "" if base is None else " 구좌%+6.1f%%" % ((slots / base - 1) * 100)
        print("  %-28s 경주%5d 구좌%5d 적중%4d(%4.1f%%) 회수%6.1f%% 대박뺀회수%6.1f%% 적중배당중앙%6.2f%s%s"
              % (label or rule, d['n'], slots, d['hits'], d['hitRate'], d['roi'],
                 d['ex3'], d['medHit'], g, "" if d['hits'] >= 30 else "  판정불가"))
    return d


def repro(rs):
    """🔴 재현 검사 — 추정 급락값으로 저장 순서를 되살리는가."""
    tot = same = partial = nodata = 0
    for r in rs:
        bs, dr = r['backers'], r['drop']
        if len(bs) < 2:
            continue
        tot += 1
        if not all(n in dr for n in bs):
            nodata += 1
            continue
        want = sorted(bs, key=lambda n: -abs(dr[n]))
        if want == bs:
            same += 1
        elif want[0] == bs[0]:
            partial += 1
    print("[재현 검사] 받치기 2명 이상 %d경주" % tot)
    print("    순서 완전 일치      %4d (%.1f%%)" % (same, 100 * same / tot if tot else 0))
    print("    1순위만 일치        %4d (%.1f%%)" % (partial, 100 * partial / tot if tot else 0))
    print("    급락값 없어 판정불가 %4d (%.1f%%)" % (nodata, 100 * nodata / tot if tot else 0))
    print("    🔴 재현률(1순위 기준) %.1f%%" %
          (100 * (same + partial) / (tot - nodata) if (tot - nodata) else 0))


def check(rs, want='코치_4경주'):
    print("=" * 140)
    print("[검산] %s" % want)
    hit = [r for r in rs if want in r['race']]
    if not hit:
        print("  대상에 없음(결과·확정배당·메인 삼복승 중 하나가 없다)")
        return
    for r in hit:
        print("  %s 축 %s 정답 %s (%.1f배)" % (r['race'], r['axis'], list(r['ans']), r['tp']))
        for rule, lab in (('A', '현행'), ('B', '25%에 가까운 순'), ('C', 'B + 50%초과 뒤로')):
            od = reorder(r, rule)
            top = tuple(sorted(r['axis'] + [od[0]])) if od else None
            print("    %-16s 받치기 %s → 1순위 %s %s"
                  % (lab, [(n, ("%.1f%%" % r['drop'][n]) if n in r['drop'] else "?") for n in od],
                     list(top) if top else '-', "🟢 적중" if top == r['ans'] else ""))


if __name__ == "__main__":
    rs = load()
    print("=" * 140)
    print("[매칭률] 8월 경륜 중 결과·삼복승 확정배당·메인 삼복승이 모두 있는 경주 %d" % len(rs))
    if len(rs) < 30:
        print("판정 불가")
        sys.exit(0)
    repro(rs)
    ds = sorted({r['date'] for r in rs if r['date']})
    mid = ds[len(ds) // 2] if ds else ''
    for topn in (1, 2, 3):
        print("=" * 140)
        print("[상위 %d조합만 산다]" % topn)
        b = score(rs, 'A', topn, label='A 현행(|급락| 큰 순)')
        for rule, lab in (('B', 'B 25%에 가까운 순'), ('C', 'C B + 50%초과 뒤로')):
            a = score(rs, rule, topn, b['slots'] if b else None, label=lab)
            if not (a and b):
                continue
            print("      회수 %+.1f%%p · 대박뺀회수 %+.1f%%p · 적중 %+.1f%%p"
                  % (a['roi'] - b['roi'], a['ex3'] - b['ex3'], a['hitRate'] - b['hitRate']))
            for pl, fn in (("전반", lambda r: r['date'] < mid), ("후반", lambda r: r['date'] >= mid)):
                sub = [r for r in rs if fn(r)]
                if len(sub) < 20:
                    continue
                x = score(sub, 'A', topn, quiet=True)
                y = score(sub, rule, topn, quiet=True)
                if x and y:
                    print("        %s 대박뺀회수 %.1f->%.1f %s (적중 %d->%d)"
                          % (pl, x['ex3'], y['ex3'],
                             "개선" if y['ex3'] > x['ex3'] else "악화", x['hits'], y['hits']))
    check(rs)
