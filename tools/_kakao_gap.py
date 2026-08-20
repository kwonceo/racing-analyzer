# -*- coding: utf-8 -*-
"""[측정 · 읽기 전용] 회원이 카톡으로 실제 받은 명단 ↔ 화면(displayedCombos) 차이.

2026-08-20 대표 지적 — 「나라 4경주 이후 20경주 이상을 하나도 못 맞혔다」.
  🔴 저장·화면 기준으로는 오늘 85경주 중 50적중(58.8%)이라 그 말과 맞지 않았다.
  ⇒ **대표가 보는 창구(카톡)와 우리가 재는 창구(저장)가 다른지**를 본다.

방법
  data/kakao_sent/<YYYYMMDD>.json 을 시간순으로 재생한다.
    T-5 / T-7      → 그 시점 명단으로 초기화
    즉시변경/T+1변경 → 본문의 「복승 추가/제외」를 적용
  그렇게 만들어진 **최종 수신 명단**을 정답·화면과 대조한다.

⚠ 본문 문구를 파싱하므로 문구가 바뀌면 이 도구도 바뀌어야 한다.
🔴 배선하지 않는다. 숫자만.
"""
import collections
import glob
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RE_COMBO = re.compile(r"'combo':\s*\[([0-9,\s]+)\]")
RE_DIFF = re.compile(r'복승 (추가|제외):\s*([^\n]+)')


def _combos_field(v):
    """quinellas 필드 — 문자열로 저장돼 있을 수도, 리스트일 수도 있다."""
    out = []
    if isinstance(v, list):
        for x in v:
            c = (x or {}).get('combo') if isinstance(x, dict) else x
            try:
                c = tuple(sorted(int(i) for i in (c or [])))
            except (TypeError, ValueError):
                continue
            if len(c) == 2:
                out.append(c)
        return out
    for m in RE_COMBO.finditer(str(v or '')):
        nn = [int(x) for x in re.findall(r'\d+', m.group(1))]
        if len(nn) == 2:
            out.append(tuple(sorted(nn)))
    return out


def run(ymd):
    p = os.path.join(BASE, 'data', 'kakao_sent', '%s.json' % ymd)
    if not os.path.exists(p):
        print('발송 이력 없음: %s' % p)
        return
    rows = json.load(open(p, encoding='utf-8'))
    if isinstance(rows, dict):
        rows = list(rows.values())
    rows = [r for r in rows if isinstance(r, dict) and r.get('raceKey')]
    rows.sort(key=lambda r: float(r.get('sentEpoch') or 0))

    disp, ans = {}, {}
    dstr = '%s_%s_%s' % (ymd[:4], ymd[4:6], ymd[6:])
    for f in glob.glob(os.path.join(BASE, 'data', 'analysis_log', dstr + '_*.json')):
        try:
            d = json.load(open(f, encoding='utf-8'))
        except Exception:
            continue
        rk = (d.get('raceKey') or '').strip() or os.path.basename(f)[len(dstr) + 1:-5].replace('_', ' ')
        dc = (d.get('corePicks') or {}).get('displayedCombos') or {}
        disp[rk] = set(tuple(sorted(int(v) for v in x))
                       for x in (dc.get('quinellas') or []) if len(x) == 2)
        res = d.get('result') or {}
        if res.get('1st') is not None and res.get('2nd') is not None:
            ans[rk] = (tuple(sorted([res['1st'], res['2nd']])),
                       (res.get('payouts') or {}).get('quinella'))

    cur = collections.defaultdict(set)
    phases = collections.defaultdict(list)
    for r in rows:
        rk, ph = r.get('raceKey'), r.get('phase')
        phases[rk].append(ph)
        if ph in ('T-5', 'T-7'):
            cur[rk] = set(_combos_field(r.get('quinellas')))
        else:
            for m in RE_DIFF.finditer(str(r.get('text') or '')):
                for c in m.group(2).split('·'):
                    nn = [int(x) for x in re.findall(r'\d+', c)]
                    if len(nn) != 2:
                        continue
                    k = tuple(sorted(nn))
                    if m.group(1) == '추가':
                        cur[rk].add(k)
                    else:
                        cur[rk].discard(k)

    tot = kh = dh = 0
    sub = 0
    gap = 0
    lost = []
    g = collections.defaultdict(lambda: [0, 0, 0])
    for rk, q in cur.items():
        if rk not in ans:
            continue
        a, qp = ans[rk]
        tot += 1
        kh += (a in q)
        D = disp.get(rk, set())
        dh += (a in D)
        if q <= D:
            sub += 1
        gap += len(D - q)
        key = 'T+1변경 받음' if 'T+1변경' in phases[rk] else 'T+1변경 못받음'
        g[key][0] += 1
        g[key][1] += (a in q)
        g[key][2] += (a in D)
        if (a in D) and (a not in q):
            lost.append((rk, sorted(q), a, qp, phases[rk][-1]))

    print('=' * 118)
    print('[%s] 결과확정 %d경주' % (ymd, tot))
    print('  🔴 카톡으로 받은 명단 기준 적중 %d (%.1f%%)' % (kh, 100 * kh / tot if tot else 0))
    print('  🟢 화면(displayedCombos) 기준 적중 %d (%.1f%%)' % (dh, 100 * dh / tot if tot else 0))
    print('  카톡이 화면의 부분집합인 경주 %d / %d · 화면에만 있는 조합 %d개'
          % (sub, tot, gap))
    print()
    print('  %-16s %6s %10s %10s' % ('', '경주', '카톡적중', '화면적중'))
    for k, v in g.items():
        print('  %-16s %6d %10d %10d' % (k, v[0], v[1], v[2]))
    print()
    print('  🔴 화면은 맞혔는데 카톡에는 그 조합이 없던 경주 %d건' % len(lost))
    for rk, q, a, qp, ph in lost[:14]:
        print('     %-16s 받은것 %-24s 정답 %-8s %s (마지막 %s)'
              % (rk, str(q)[:24], str(a), ('%.1f배' % qp) if qp else '', ph))


if __name__ == '__main__':
    run(sys.argv[1] if len(sys.argv) > 1 else '20260820')
