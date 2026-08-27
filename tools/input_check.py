# -*- coding: utf-8 -*-
"""[입력 검증] 우리 숫자가 맞는지 매일 자동으로 본다.

2026-08-16 대표 지시.

🔴 왜 필요한가 — 오늘 찾은 결함을 **전부 대표가 눈으로 찾았다.**
   착순 30.8% 뒤집힘 · 목록 셋이 서로 다름 · 전적 0점인 말이 축 · 전적 반영 사실상 0.
   시스템은 하나도 스스로 못 찾았다.
⚠ 복기(miss_type)로는 이것을 못 잡는다. 복기는 **정답을 놓쳤나**를 보고
  이 도구는 **우리 숫자가 맞나**를 본다. 둘은 다른 것을 잰다.

검사 넷
  A 착순이 두 곳에서 어긋나나   prerace 의 recentPlacings ↔ pastRaces[].placing
  B 전적 없는 말이 추천에 있나   record_score 가 없는 말이 표시 조합에 들어갔나
  C 유력마 목록 셋이 얼마나 다른가  점수표 상위3 ↔ keyHorses ↔ starHorses
  D 배당이 경주에 제대로 붙었나   표시 조합인데 배당판에 없거나 0인 것

🔴 **막지 않는다. 보이게만 한다.** 어떤 값도 고치지 않고 어떤 추천도 차단하지 않는다.
🔴 완전 읽기 전용이다. logs/input_check/ 에만 쓴다.
"""
import glob
import json
import os
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(BASE, 'logs', 'input_check')
KR = ('서울', '부산', '제주')


def _num(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def check_a(date_ymd):
    """A 착순이 두 곳에서 어긋나나(한국 PDF 전용)."""
    d10 = date_ymd                                  # 'YYYY-MM-DD'
    tot, bad, races, badraces, rows = 0, 0, 0, 0, []
    for p in sorted(glob.glob(os.path.join(BASE, 'data', 'prerace', '%s_*.json' % d10))):
        try:
            d = json.load(open(p, encoding='utf-8'))
        except Exception:
            continue
        hs = d.get('horses') or []
        if not hs:
            continue
        races += 1
        rb = []
        for h in hs:
            rp = h.get('recentPlacings') or []
            pr = [x.get('placing') for x in (h.get('pastRaces') or []) if x.get('placing')]
            if not rp or not pr:
                continue
            tot += 1
            if _num(rp[0]) != _num(pr[0]):
                bad += 1
                rb.append('%s번 %s↔%s' % (h.get('horseNum'), rp[0], pr[0]))
        if rb:
            badraces += 1
            rows.append({'race': os.path.basename(p).replace('.json', ''), 'bad': rb})
    return {'name': '착순 두 곳 어긋남', 'checked': tot, 'bad': bad, 'races': races,
            'badRaces': badraces,
            'pct': round(bad / tot * 100, 1) if tot else 0, 'rows': rows[:10],
            'ok': (bad == 0) if tot else None}


def _logs(date_ymd):
    pat = date_ymd.replace('-', '_')
    for f in sorted(glob.glob(os.path.join(BASE, 'data', 'analysis_log', '%s_*.json' % pat))):
        try:
            yield f, json.load(open(f, encoding='utf-8'))
        except Exception:
            continue


def check_bcd(date_ymd):
    """B 전적 없는 말이 추천에 · C 목록 셋 차이 · D 배당 미부착."""
    b = {'name': '전적 없는 말이 추천에', 'races': 0, 'bad': 0, 'rows': []}
    # 🔴 [2026-08-28] **두 개의 다른 문제를 합쳐 세고 있었다** — 93%가 나와 변별력을 잃었다(원칙 18).
    #   ⓐ 전적 상위3 ↔ keyHorses : keyHorses 는 **배당 기반**이라 다른 것이 **설계대로**다
    #      (2026-08-24 실측 — 전적을 섞으면 성적이 나빠져 기각했다). 이건 경보가 아니라 정보다.
    #   🔴 ⓑ keyHorses ↔ starHorses : starHorses 는 **실제 추천 조합에 나온 말**이다.
    #      다르다는 것은 「유력마로 뽑았는데 조합에 안 들어갔다」는 뜻이고 **회원 화면이 갈린다.**
    #      8/27 실측 — ⓐ 19% 일치(정상) · 🔴 ⓑ **42% 일치(58% 불일치)**.
    c = {'name': '유력마 목록 셋 차이', 'races': 0, 'same': 0, 'diff': 0, 'rows': [],
         'axisDiff': 0, 'starDiff': 0, 'starRaces': 0}
    d = {'name': '배당 안 붙은 조합', 'races': 0, 'combos': 0, 'bad': 0, 'rows': []}
    for f, doc in _logs(date_ymd):
        nm = os.path.basename(f).replace('.json', '')
        cp = doc.get('corePicks') or {}
        dc = []
        for x in ((cp.get('displayedCombos') or {}).get('quinellas') or []):
            try:
                dc.append(tuple(sorted(int(v) for v in x)))
            except (TypeError, ValueError):
                pass
        if not dc:
            continue
        hs = doc.get('horses') or []
        # B — 표시 조합에 든 말 중 전적 점수가 없는 것
        b['races'] += 1
        sc = {}
        for h in hs:
            n = _num(h.get('no'))
            if n is not None:
                sc[n] = h.get('record_score') or h.get('totalScore') or 0
        used = {h for cb in dc for h in cb}
        no_form = sorted(h for h in used if not sc.get(h))
        if no_form:
            b['bad'] += 1
            b['rows'].append({'race': nm, 'horses': no_form,
                              'of': len(used), 'combos': [list(x) for x in dc]})
        # C — 목록 셋
        top3 = [n for n, _ in sorted(sc.items(), key=lambda kv: -kv[1])[:3] if sc.get(n)]
        kh = [_num(x) for x in (doc.get('keyHorses') or [])][:3]
        sh = [_num(x) for x in (cp.get('starHorses') or [])][:3]
        kh = [x for x in kh if x is not None]
        sh = [x for x in sh if x is not None]
        if top3 and kh:
            c['races'] += 1
            if set(top3) != set(kh):
                c['axisDiff'] += 1          # 설계상 축 차이 — 정보
            if sh:
                c['starRaces'] += 1
                if set(kh) != set(sh):
                    c['starDiff'] += 1      # 🔴 진짜 문제 — 유력마가 조합에 안 들어갔다
            if set(top3) == set(kh) == (set(sh) if sh else set(kh)):
                c['same'] += 1
            else:
                c['diff'] += 1
                if len(c['rows']) < 10:
                    c['rows'].append({'race': nm, '점수표': top3, '조합유력마': kh, '화면별표': sh})
        # D — 표시 조합인데 배당판에 없거나 0
        q = {}
        for x in (cp.get('quinella') or []):
            if isinstance(x, dict):
                cb = x.get('combo') or []
                if len(cb) == 2:
                    try:
                        q[tuple(sorted(int(v) for v in cb))] = float(x.get('odds') or 0)
                    except (TypeError, ValueError):
                        pass
        if q:
            d['races'] += 1
            miss = [list(x) for x in dc if not q.get(x)]
            d['combos'] += len(dc)
            if miss:
                d['bad'] += len(miss)
                if len(d['rows']) < 10:
                    d['rows'].append({'race': nm, 'combos': miss})
    b['pct'] = round(b['bad'] / b['races'] * 100, 1) if b['races'] else 0
    b['ok'] = (b['bad'] == 0) if b['races'] else None
    c['pct'] = round(c['diff'] / c['races'] * 100, 1) if c['races'] else 0
    c['ok'] = (c['diff'] == 0) if c['races'] else None
    d['pct'] = round(d['bad'] / d['combos'] * 100, 1) if d['combos'] else 0
    d['ok'] = (d['bad'] == 0) if d['combos'] else None
    return b, c, d


def daily(date_ymd=None, save=True):
    date_ymd = date_ymd or time.strftime('%Y-%m-%d')
    a = check_a(date_ymd)
    b, c, d = check_bcd(date_ymd)
    items = [a, b, c, d]
    doc = {'date': date_ymd, 'at': time.strftime('%Y-%m-%d %H:%M:%S'),
           'items': items,
           'alerts': [x['name'] for x in items if x.get('ok') is False]}
    if save:
        try:
            os.makedirs(OUT_DIR, exist_ok=True)
            with open(os.path.join(OUT_DIR, '%s.json' % date_ymd), 'w', encoding='utf-8') as fp:
                json.dump(doc, fp, ensure_ascii=False, indent=1)
        except Exception as e:
            print('[입력검증] 저장 실패(무시):', str(e)[:120])
    return doc


def text(doc):
    if not doc:
        return '결과 없음'
    L = ['%s 입력 검증' % doc['date']]
    for x in doc['items']:
        if x.get('ok') is None:
            L.append('  ⏳ %-16s 볼 것이 없다' % x['name'])
        elif x['ok']:
            L.append('  🟢 %-16s 이상 없음' % x['name'])
        else:
            if x['name'].startswith('착순'):
                L.append('  🔴 %-16s %d마리 중 %d마리 어긋남(%.1f%%) · 경주 %d/%d'
                         % (x['name'], x['checked'], x['bad'], x['pct'], x['badRaces'], x['races']))
            elif x['name'].startswith('전적'):
                L.append('  🔴 %-16s %d경주 중 %d경주(%.1f%%)에 전적 없는 말이 추천에 있다'
                         % (x['name'], x['races'], x['bad'], x['pct']))
            elif x['name'].startswith('유력마'):
                L.append('  🔴 %-16s %d경주 중 %d경주(%.1f%%)에서 셋이 서로 다르다'
                         % (x['name'], x['races'], x['diff'], x['pct']))
            else:
                L.append('  🔴 %-16s 표시 조합 %d개 중 %d개(%.1f%%)에 배당이 안 붙었다'
                         % (x['name'], x['combos'], x['bad'], x['pct']))
            for r in (x.get('rows') or [])[:3]:
                L.append('       %s' % json.dumps(r, ensure_ascii=False)[:110])
    if not doc['alerts']:
        L.append('  🟢 넷 다 이상 없음')
    L.append('  ⚠ 막지 않는다. 보이게만 한다.')
    return '\n'.join(L)


def kakao_line(doc):
    """카톡 한 줄 — 걸린 것만."""
    if not doc or not doc.get('alerts'):
        return None
    out = []
    for x in doc['items']:
        if x.get('ok') is not False:
            continue
        if x['name'].startswith('착순'):
            out.append('착순 어긋남 %d마리(%.0f%%)' % (x['bad'], x['pct']))
        elif x['name'].startswith('전적'):
            out.append('전적 없는 말 추천 %d경주' % x['bad'])
        elif x['name'].startswith('유력마'):
            # 🔴 진짜 문제만 경보로 낸다 — 축 차이는 설계대로라 넣지 않는다(원칙 18).
            _sr = x.get('starRaces') or 0
            if _sr:
                out.append('유력마↔추천조합 불일치 %d경주(%.0f%%)'
                           % (x.get('starDiff') or 0, 100.0 * (x.get('starDiff') or 0) / _sr))
        else:
            out.append('배당 미부착 %d조합' % x['bad'])
    return '🔴 입력 검증 — ' + ' · '.join(out)


if __name__ == "__main__":
    import sys
    dt = sys.argv[1] if len(sys.argv) > 1 else time.strftime('%Y-%m-%d')
    print(text(daily(dt)))
