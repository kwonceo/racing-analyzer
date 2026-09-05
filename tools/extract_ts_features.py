# -*- coding: utf-8 -*-
"""배당 시계열 특징 추출 — 아직 안 판 축을 재기 위한 재료 (완전 읽기 전용).

🔴 왜: CLAUDE.md 2026-09-01 결론 — 「말 능력은 시장이 이미 반영했고, 시계열은 우리만 갖고 있다」.
   그런데 그 시계열 201MB 에서 실제로 판 것은 **급락 하나**뿐이다.
   여기서 뽑는 것: ①급등 ②Σ(1/배당) 시간변화 ③조합 간 상관 붕괴 ④틱 정지

⚠ .gz 를 반드시 함께 읽는다 — 2026-08-24 에 「.gz 를 못 읽어 표본이 0」이 있었고 또 반복했다.
⚠ 오염 틱 제외: after_close · odds_suspect · baseline_reset · next_race_blocked
출력: 조합 1행 jsonl
"""
import io, gzip, json, glob, os, sys, time, statistics

BASE = r'C:\Users\Administrator\Desktop\경마분석서버\data\odds_history'
OUT  = r'C:\Users\Administrator\Desktop\경마분석서버\data\ts_features.jsonl'


def load(p):
    if p.endswith('.gz'):
        with gzip.open(p, 'rt', encoding='utf-8') as f:
            return json.load(f)
    return json.load(io.open(p, encoding='utf-8'))


def pick(ticks, target):
    """minutes_before 가 target 에 가장 가까운 틱(target 이상 쪽 우선)."""
    ge = [s for s in ticks if s['minutes_before'] >= target]
    if ge:
        return min(ge, key=lambda s: s['minutes_before'] - target)
    return max(ticks, key=lambda s: s['minutes_before'])


def sigma(q):
    tot = 0.0
    for v in q.values():
        try:
            f = float(v)
            if f > 0: tot += 1.0 / f
        except (TypeError, ValueError):
            pass
    return tot


def main():
    limit = 0
    ref = 2.0        # 🔴 변화량 기준 틱(마감 N분 전). cut 을 쓰면 cut 보다 커야 한다
    cut = 0.0          # 🔴 원칙 27 — 이 시각(마감 N분 전)까지의 틱만 쓴다(look-ahead 차단)
    for a in sys.argv[1:]:
        if a.startswith("--cut="): cut = float(a.split("=")[1])
        elif a.startswith("--ref="): ref = float(a.split("=")[1])
        elif a.isdigit(): limit = int(a)
    fs = sorted(glob.glob(BASE + r'\*.json') + glob.glob(BASE + r'\*.json.gz'))
    if limit: fs = fs[-limit:]
    t0 = time.time(); nrace = nrow = 0
    outp = OUT if not (cut or ref != 2.0) else OUT.replace(".jsonl", "_cut%g_ref%g.jsonl" % (cut, ref))
    out = io.open(outp, "w", encoding="utf-8")
    for f in fs:
        try:
            d = load(f)
        except Exception:
            continue
        r = d.get('result') or {}
        if not (r.get('1st') and r.get('2nd')) or not d.get('deadline_epoch'):
            continue
        sn = (d.get('archive_snapshots') or []) + (d.get('snapshots') or [])
        cl = [s for s in sn
              if isinstance(s.get('quinella'), dict) and s['quinella']
              and not s.get('after_close') and not s.get('odds_suspect')
              and not s.get('baseline_reset') and not s.get('next_race_blocked')
              and isinstance(s.get('minutes_before'), (int, float))]
        if cut:
            cl = [s for s in cl if s["minutes_before"] >= cut]   # 발동 시점 이후 틱 제거
        if len(cl) < 4: continue
        cl.sort(key=lambda s: -s['minutes_before'])          # 먼 시점 → 마감
        last, t2, t10 = cl[-1], pick(cl, ref), pick(cl, 10)
        ql, q2, q10 = last['quinella'], t2['quinella'], t10['quinella']
        sl, s2, s10 = sigma(ql), sigma(q2), sigma(q10)
        if not (1.05 <= sl <= 2.5):  continue                # 배당판 건전성
        try:
            top2 = {int(r['1st']), int(r['2nd'])}
        except (TypeError, ValueError):
            continue
        rk = os.path.basename(f).replace('.json.gz', '').replace('.json', '')
        # 말별 변화 중앙값(상관 붕괴용)
        chg = {}
        for k, v in ql.items():
            try:
                a, b = float(v), float(q2.get(k))
                if a > 0 and b > 0: chg[k] = (a - b) / b
            except (TypeError, ValueError, AttributeError):
                pass
        horse_ch = {}
        for k, c in chg.items():
            for h in k.split('+'):
                horse_ch.setdefault(h, []).append(c)
        hmed = {h: statistics.median(v) for h, v in horse_ch.items() if len(v) >= 3}
        nrace += 1
        for k, v in ql.items():
            try:
                o = float(v)
            except (TypeError, ValueError):
                continue
            if not (o > 0): continue
            hs = k.split('+')
            if len(hs) != 2: continue
            try:
                nos = {int(hs[0]), int(hs[1])}
            except ValueError:
                continue
            c2 = chg.get(k)
            cb = None
            if c2 is not None:
                ms = [hmed[h] for h in hs if h in hmed]
                if ms: cb = c2 - statistics.mean(ms)          # 그 말 평균 대비 이 조합의 초과 변화
            def rel(qq, ss):
                try:
                    b = float(qq.get(k))
                    return (o - b) / b if b > 0 else None
                except (TypeError, ValueError):
                    return None
            out.write(json.dumps({
                'rk': rk, 'combo': k, 'hit': nos == top2,
                'odds': o, 'implied': (1.0 / o) / sl,
                'd2': c2, 'd10': rel(q10, s10), 'cb': cb,
                'sig_l': round(sl, 4), 'sig_chg': (sl - s10) / s10 if s10 > 0 else None,
                'nt': len(cl), 'mb_last': last['minutes_before'],
                'sport': (d.get('analysis') or {}).get('sport') or '?',
            }, ensure_ascii=False) + '\n')
            nrow += 1
    out.close()
    print('경주 {:,} · 조합행 {:,} · {:.1f}초 → {}'.format(nrace, nrow, time.time() - t0, outp))


if __name__ == '__main__':
    main()
