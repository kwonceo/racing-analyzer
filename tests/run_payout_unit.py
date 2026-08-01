# -*- coding: utf-8 -*-
"""[확정배당 단위 회귀 테스트 (2026-08-01 신설)] — **완전 읽기 전용**.

🔴 왜: netkeiba `result.html` 경로가 **円을 그대로** `payouts` 에 저장해 **100배 부풀려졌다**.
  니가타 11R `1,040円` 을 **"1,040배 적중"** 으로 보고했다(실제 10.4배). **경로별 단위 불일치**가 원인이다.
  ⚠ **아무 예외도 안 났다.** 값이 계속 들어와서 아무도 몰랐다 — `枠連` 오매핑과 같은 "조용히 틀리는" 유형.

■ 검사 2종
  ① 🔴 **円 스케일 잔존** — `payouts_raw` 에 円 표기 키가 있는데 `payouts` 값이 **100 이상**이면 실패.
     (円 표기 키는 netkeiba 파서만 만든다. 그 경로는 저장 시 `/100` 하므로 100 이상이 남을 수 없다.)
  ② 🟡 **경로별 변환 대조** — `/100.0` 을 해야 하는 저장 지점이 실제로 하는지 소스에서 확인.

■ ⚠ 이 테스트가 잡지 못하는 것
  円 값이 **100 미만**인 경우(예: `複勝 90円`)는 배 스케일과 구분되지 않는다.
  🔴 그래서 ①은 **하한 탐지**다. "통과 = 전부 안전"이 아니다.
"""
import glob
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(BASE, "data", "analysis_log")
YEN_KEYS = ("単勝", "複勝", "馬連", "馬単", "枠連", "ワイド", "3連複", "3連単", "三連複")

# 🔴 `payouts` 에 값을 넣는 지점 — 円 소스면 `/100` 이 **반드시** 있어야 한다.
#    (파일, 앵커 문자열, 그 근처에서 찾아야 하는 변환 표식)
CONV_SITES = [
    ("app.py", 'res["payouts"] = dict(res.get("payouts") or {}, **_pay_conv)', "/ 100.0"),
    ("tools/backfill_jra_result.py", 'res["payouts"] = dict(res.get("payouts") or {},', "/ 100.0"),
    ("tools/backfill_payouts.py", 'out["quinella"] = round(', "/ 100.0"),
]


# 円 표기 키 → payouts 필드. 🔴 **정확 대조**용(임계값 추측 금지).
PAY_MAP = {"単勝": "win", "馬連": "quinella", "馬単": "exacta",
           "3連複": "trio", "三連複": "trio", "3連単": "trifecta"}


def _yen_of(cell):
    """`"1,040円"` · `"180円 140円 230円"` → 첫 값 1040."""
    m = re.search(r"([\d,]+)\s*円", str((cell or {}).get("yen") or ""))
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def check_yen_left():
    """🔴 **정확 대조**: 저장값이 `payouts_raw` 의 円과 같으면 미변환이다.

    ⚠ 처음엔 `payouts ≥ 100` 을 기준으로 짰다가 **삼복승 255.9배·3連単 630.6배를 오탐**했다
      (삼복승은 100배 이상이 정상이다). 임계값으로 단위를 추정하면 안 된다 —
      **원본 円과 직접 비교**해야 한다. (원칙 8-D: 검증 코드도 검증한다)
    """
    bad = []
    for f in sorted(glob.glob(os.path.join(LOG_DIR, "*.json"))):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        res = d.get("result") or {}
        raw = res.get("payouts_raw")
        po = res.get("payouts") or {}
        if not isinstance(raw, dict) or not any(k in raw for k in YEN_KEYS):
            continue
        for jk, fk in PAY_MAP.items():
            if jk not in raw or fk not in po:
                continue
            yen = _yen_of(raw[jk])
            try:
                cur = float(po[fk])
            except (TypeError, ValueError):
                continue
            if yen is None or yen < 100:
                continue                      # 円 100 미만은 배와 구분 불가 — 판정하지 않는다
            if abs(cur - yen) < 0.5:          # 저장값 == 円 → **미변환**
                bad.append((os.path.basename(f)[:-5], fk, cur, yen))
    return bad


def check_conv_sites():
    miss = []
    for rel, anchor, need in CONV_SITES:
        p = os.path.join(BASE, rel)
        if not os.path.exists(p):
            miss.append((rel, "파일 없음"))
            continue
        s = open(p, encoding="utf-8").read()
        i = s.find(anchor)
        if i < 0:
            miss.append((rel, "앵커를 찾지 못했다(리팩터링됐을 수 있다) — 수동 확인 필요"))
            continue
        if need not in s[max(0, i - 1200):i + 400]:
            miss.append((rel, "저장 지점 근처에 `%s` 변환이 없다" % need))
    return miss


def main():
    print("=" * 88)
    print("확정배당 **단위** 회귀 테스트   🔴 円을 배로 저장하면 100배가 된다")
    print("=" * 88)
    bad = check_yen_left()
    print("\n[1] 円 스케일 잔존(payouts_raw 에 円 키 + payouts ≥ 100) : %d건" % len(bad))
    for b, k, v, y in bad[:15]:
        print("    🔴 %-34s %-10s 저장 %-10s ↔ 원본 %s円 (미변환)" % (b, k, v, y))
    miss = check_conv_sites()
    print("\n[2] 저장 지점 변환 대조 : 이상 %d건" % len(miss))
    for rel, why in miss:
        print("    🔴 %-34s %s" % (rel, why))
    print("\n" + "=" * 88)
    if bad or miss:
        print("🔴 실패 — 위 항목을 고칠 것.")
        print("   소급 정정: python tools/fix_jra_payout_unit.py --apply")
        return 1
    print("✅ 통과 — 円 스케일 잔존 0건 · 저장 지점 변환 정상")
    print("⚠ 円 값이 100 **미만**인 항목은 배와 구분이 안 돼 판정에서 뺐다. 통과가 '전부 안전'은 아니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
