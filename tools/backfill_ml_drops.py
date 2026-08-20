# -*- coding: utf-8 -*-
"""[소급] 학습셋의 급락 칸을 동결본(drops_raw)으로 채우고 **출처 표식**을 남긴다.

2026-08-20 대표 지시.
  `_ml_row_build` 가 마감 후 재계산값(an["drops"])을 읽어 92%가 비어 있었다.
  코드는 고쳤으나 **새 행부터** 적용된다 → 과거분을 소급한다.

🔴 출처 표식이 이 작업의 핵심이다.
   소급하면 「원래 채워져 있던 것」과 「소급으로 채운 것」이 섞여 **구분이 영영 사라진다.**
   그러면 「분리해서 재라」를 지킬 수 없다.
     features["drop_source"] = "live"     원래 있던 값(마감 후에도 살아남은 것)
                             = "backfill" drops_raw 동결본에서 소급한 것
                             = None       급락 기록 자체가 없어 못 채우는 것
   ⚠ 기존 live 행에도 표식을 넣는다. 안 넣으면 그것도 구분이 안 된다.

⚠ 깨진 줄은 **지우지 않고 원문 그대로 통과**시킨다(관대 파싱 원칙).
⚠ `--dry` 가 기본. `--apply` 를 붙여야 실제로 쓴다. 쓰기 전 백업을 강제한다.
"""
import argparse
import io
import json
import os
import shutil
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
ML = os.path.join(BASE, 'data', 'ml_training_data.jsonl')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='실제로 쓴다(기본은 미실행)')
    a = ap.parse_args()

    import app as m   # _analysis_log_path 재사용 — 경로 규칙을 두 곳에 두지 않는다

    lines = io.open(ML, encoding='utf-8', errors='replace').read().splitlines()
    out, stat = [], {'live': 0, 'backfill': 0, 'none': 0, 'broken': 0, 'blank': 0}
    miss_file = 0
    for ln in lines:
        s = ln.strip()
        if not s:
            out.append(ln)
            stat['blank'] += 1
            continue
        try:
            d = json.loads(s)
        except Exception:
            out.append(ln)              # 🔴 깨진 줄은 원문 그대로 통과
            stat['broken'] += 1
            continue
        f = d.get('features') or {}
        if f.get('max_drop_pct') is not None:
            f['drop_source'] = 'live'
            stat['live'] += 1
        else:
            rk = '%s %s' % (d.get('date') or '', d.get('raceKey') or '')
            dps = []
            try:
                p, _, _ = m._analysis_log_path(rk.strip())
                doc = json.load(io.open(p, encoding='utf-8'))
                dps = [x.get('pct') for x in (doc.get('drops_raw') or [])
                       if isinstance(x.get('pct'), (int, float))]
            except Exception:
                miss_file += 1
            if dps:
                f['max_drop_pct'] = min(dps)
                f['drop_source'] = 'backfill'
                stat['backfill'] += 1
            else:
                f['drop_source'] = None
                stat['none'] += 1
        d['features'] = f
        out.append(json.dumps(d, ensure_ascii=False))

    tot = stat['live'] + stat['backfill'] + stat['none']
    print("행 %d (깨진 줄 %d · 빈 줄 %d · 분석로그 못 찾음 %d)"
          % (tot, stat['broken'], stat['blank'], miss_file))
    print("  live     %5d  원래 채워져 있던 값" % stat['live'])
    print("  backfill %5d  drops_raw 로 소급" % stat['backfill'])
    print("  없음      %5d  drops_raw 자체가 없어 영영 못 채움" % stat['none'])
    filled = stat['live'] + stat['backfill']
    print("  채움률 %.1f%% -> %.1f%%"
          % (stat['live'] / tot * 100 if tot else 0, filled / tot * 100 if tot else 0))

    if not a.apply:
        print("\n⚠ DRY-RUN 이다. 실제로 쓰려면 `--apply`.")
        return

    bdir = os.path.join(BASE, 'backups')
    os.makedirs(bdir, exist_ok=True)
    bak = os.path.join(bdir, 'ml_training_%s.jsonl' % time.strftime('%Y%m%d_%H%M%S'))
    shutil.copy2(ML, bak)                     # 🔴 쓰기 전 백업. 한 파일이라 잃으면 통째다
    print("\n백업 %s" % bak)
    tmp = ML + '.tmp_backfill'
    with io.open(tmp, 'w', encoding='utf-8', newline='') as fh:
        fh.write('\n'.join(out) + '\n')
    os.replace(tmp, ML)
    print("✅ 적용 완료")


if __name__ == '__main__':
    main()
