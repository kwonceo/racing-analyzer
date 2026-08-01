# -*- coding: utf-8 -*-
"""[중앙경마 확정배당 단위 소급 정정 (2026-08-01 신설)] — `--dry` 기본 · **삭제 없음**.

■ 사고
  netkeiba `result.html` 경로(`_jra_result_save`)가 **円을 그대로** `payouts` 에 넣었다.
  지방(`_keiba_result_payouts`)·`backfill_payouts.py` 는 원래 **`/100.0`** 을 한다 — **경로별 단위 불일치**.
  ⇒ 니가타 11R `1,040円` 을 **"1,040배 적중"** 으로 보고했다. **실제는 10.4배.**

■ 🔴 판별 기준 (코드로 명시 · 지방을 건드리지 않기 위한 핵심)
  ① `result.payouts_raw` 가 **dict** 이고
  ② 그 안에 **円 표기 키**(`単勝`·`複勝`·`馬連`·`馬単`·`枠連`·`ワイド`·`3連複`·`3連単`)가 하나라도 있고
  ③ 아직 `payout_unit_fixed` 이력이 **없다**(멱등)
  ⚠ `payouts_raw` 의 円 키는 **netkeiba result.html 파서만** 만든다.
    지방(NAR) 경로는 `payouts_raw` 를 이 형태로 만들지 않으므로 **구조적으로 안 걸린다.**
  🔴 그래도 `--dry` 에서 **지방이 0건인지 반드시 눈으로 확인**한다. 1건이라도 나오면 **중단**한다.
    (지방에는 ≥100배인 **진짜 고배당 33건**이 있다 — 그것을 나누면 데이터가 망가진다.)

■ 무엇을 하는가
  대상 경주의 `result.payouts` **전 필드**를 `/100.0` 한다.
  ⚠ `quinella` 만 고치면 안 된다 — `win`·`exacta`·`trio`·`trifecta` 가 **함께 円**이다.
  ⚠ `payouts_raw`·착순·다른 필드는 **일절 수정하지 않는다.**

■ 안전장치
  · `--dry` 기본 · `--apply` 필요 · `--apply` 시 **백업 먼저**(`backups/payoutunit_<ts>/`)
  · `payout_unit_fixed` 이력으로 **두 번 적용 방지**
  · 정정 전후 값을 **전부 출력**한다(1040 → 10.4 식)

사용: python tools/fix_jra_payout_unit.py
      python tools/fix_jra_payout_unit.py --apply
"""
import argparse
import collections
import json
import os
import re
import shutil
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(BASE, "data", "analysis_log")
# 🔴 netkeiba result.html 파서(`_jra_res_cells`)가 만드는 円 표기 키. 지방 경로엔 없다.
YEN_KEYS = ("単勝", "複勝", "馬連", "馬単", "枠連", "ワイド", "3連複", "3連単", "三連複")
KRA = re.compile(r"(서울|부산|부경|제주|과천)")


def is_yen_scale(res):
    raw = res.get("payouts_raw")
    if not isinstance(raw, dict):
        return False
    return any(k in raw for k in YEN_KEYS)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    tgt, already, cat_cnt = [], 0, collections.Counter()
    for f in sorted(os.listdir(LOG_DIR)):
        if not f.endswith(".json"):
            continue
        try:
            d = json.load(open(os.path.join(LOG_DIR, f), encoding="utf-8"))
        except Exception:
            continue
        res = d.get("result") or {}
        if not (res.get("payouts") or {}):
            continue
        if not is_yen_scale(res):
            continue
        if d.get("payout_unit_fixed"):
            already += 1
            continue
        cat = d.get("category") or "?"
        cat_cnt[cat] += 1
        tgt.append((f, cat, dict(res["payouts"])))

    print("=" * 92)
    print("중앙경마 확정배당 단위 소급 정정  %s" % ("[APPLY]" if a.apply else "[DRY-RUN]"))
    print("=" * 92)
    print("판별: payouts_raw 에 円 표기 키(%s …) 존재" % ", ".join(YEN_KEYS[:4]))
    print("대상 %d건 (이미 정정됨 %d)" % (len(tgt), already))
    print("🔴 category 분포: %s" % dict(cat_cnt))
    local = sum(v for k, v in cat_cnt.items() if k != "japan_central")
    if local:
        print("🔴🔴 **중단** — 중앙(japan_central) 이외가 %d건 잡혔다. 지방 진짜 고배당을 건드릴 위험." % local)
        print("   판별 기준을 다시 확인할 것. 이 상태로 --apply 하지 않는다.")
        return 2
    print("🟢 지방 0건 — 판별 기준이 의도대로 중앙만 잡는다.\n")
    print("  %-30s %s" % ("경주", "정정 전 → 후"))
    for f, _c, po in tgt:
        s = " · ".join("%s %s→%.1f" % (k, v, float(v) / 100.0)
                       for k, v in sorted(po.items()) if isinstance(v, (int, float)))
        print("  %-30s %s" % (f[11:-5], s))

    if not a.apply:
        print("\n⚠ DRY-RUN 이다. 승인 후 `--apply`.")
        return 0

    bdir = os.path.join(BASE, "backups", "payoutunit_" + time.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(bdir, exist_ok=True)
    n = 0
    for f, _c, _po in tgt:
        src = os.path.join(LOG_DIR, f)
        shutil.copy2(src, os.path.join(bdir, f))
        try:
            d = json.load(open(src, encoding="utf-8"))
            res = d.get("result") or {}
            before = dict(res.get("payouts") or {})
            after = {}
            for k, v in before.items():
                try:
                    after[k] = round(float(v) / 100.0, 1)
                except (TypeError, ValueError):
                    after[k] = v
            res["payouts"] = after
            d["result"] = res
            d["payout_unit_fixed"] = {
                "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "by": "fix_jra_payout_unit.py",
                "reason": "netkeiba result.html 경로가 円을 그대로 저장했다 — 배 단위로 정정(÷100)",
                "before": before, "after": after,
            }
            tmp = src + ".tmp%d" % os.getpid()
            json.dump(d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            os.replace(tmp, src)
            n += 1
        except Exception as e:
            print("   실패 %s: %s" % (f, str(e)[:70]))
    print("\n✅ 정정 %d건 · 백업 %s" % (n, os.path.relpath(bdir, BASE)))
    print("⚠ `payouts_raw`·착순·다른 필드는 수정하지 않았다. 이력은 `payout_unit_fixed` 에 남는다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
