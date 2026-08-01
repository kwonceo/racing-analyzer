# -*- coding: utf-8 -*-
"""[확정배당 과거분 소급 백필 (2026-08-01 신설)] — `--dry` 기본 · **원본 삭제 없음**.

■ 왜 (선행 조건)
  경마 확정배당 보유가 **765건 중 24건(3.1%)** 뿐이라 `measure_recovery.py` 분모가 **12건**이다.
  정렬 변경·폭 확대 등 **어떤 안도 검증할 수 없다.** 분모를 채우는 것이 모든 것의 선행 조건이다.

■ 🔴 진단 (17차)
  `_jp_result_backfill_once` 는 **이미 있고 20분 주기로 돈다**(app.py:25851).
  문제는 두 가지:
    ① **당일만 본다** — `_missing_results(date)` 기본값이 오늘이다(app.py:15365)
    ② 07-31 이전에는 **착순만 저장하고 `payouts` 를 안 만들었다**
       (07-31 주석: *"파서는 이미 있는데 결과 백필 경로에서만 빠져 있었다"*)
  ⇒ **착순은 있는데 확정배당만 없는 경주가 650건** 남았다. 이 도구가 그것을 채운다.

■ 무엇을 하는가
  `analysis_log` 에서 **착순은 있고 `payouts.quinella` 가 없는** 경주를 찾아
  공식 소스(`keiba.go.jp RaceMarkTable`)에서 확정환급을 받아 채운다.
  ⚠ 파서는 `app.py` 의 `_keiba_result_payouts` 와 **같은 정규식**을 쓴다(두 곳에 두지 않으려 재구현이 아니라 복사가 아닌
    **런타임 파싱으로 상수만 가져오고 정규식은 여기 1벌**). 값 검증은 아래 assert 로 한다.
  🔴 **과거분이 실제로 남아 있음을 실측 확인**(2026-08-01): 07-15·07-31 소노다 1R 모두 `馬連複 ... 円` 반환.
     `len=7724` 는 **그날 그 경기장이 미개최**라는 뜻이지 소스가 없다는 뜻이 아니다.

■ 안전장치
  · `--dry` 기본 · `--apply` 필요 · `--apply` 시 **백업 먼저**(`backups/payouts_<ts>/`)
  · 🔴 **착순이 없는 경주는 건드리지 않는다**(이 도구는 확정배당 전용)
  · 🔴 이미 `payouts.quinella` 가 있으면 **덮지 않는다**
  · `payouts` 만 채우고 다른 필드 무수정 · `payouts_backfilled` 에 이력

사용: python tools/backfill_payouts.py --days 30
      python tools/backfill_payouts.py --days 30 --apply
"""
import argparse
import ast
import collections
import html as _htmllib
import json
import os
import re
import shutil
import sys
import time
import urllib.parse
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(BASE, "data", "analysis_log")
UA = "Mozilla/5.0"


def _from_app(name):
    """app.py 에서 상수를 런타임 파싱(목록을 두 곳에 두지 않는다)."""
    src = open(os.path.join(BASE, "app.py"), encoding="utf-8").read()
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == name:
            return ast.literal_eval(n.value)
    raise SystemExit("app.py 에서 %s 를 찾지 못했다" % name)


BABA = _from_app("_JP_BABA_CODE")
RESULT_BASE = _from_app("KEIBA_RESULT_BASE")
_PAGE_CACHE = {}


def fetch_page(baba, ymd, rno):
    key = (baba, ymd, str(rno))
    if key in _PAGE_CACHE:
        return _PAGE_CACHE[key]
    d = "%s/%s/%s" % (ymd[:4], ymd[4:6], ymd[6:8])
    u = "%s?k_raceDate=%s&k_raceNo=%s&k_babaCode=%s" % (
        RESULT_BASE, urllib.parse.quote(d), rno, baba)
    try:
        with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA}),
                                    timeout=15) as r:
            h = r.read().decode("utf-8", "replace")
    except Exception:
        h = None
    if len(_PAGE_CACHE) > 400:
        _PAGE_CACHE.clear()
    _PAGE_CACHE[key] = h
    return h


def parse_payouts(html):
    """→ {'quinella': 배(倍), 'trifecta': 배} · ⚠ 円 → 배 변환은 /100.0 (app.py 와 동일)."""
    if not html:
        return {}
    body = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", _htmllib.unescape(html)))
    out = {}
    mq = re.search(r"馬連複\s*(\d{1,2})\s*-\s*(\d{1,2})\s*([\d,]+)円", body)
    if mq:
        out["quinella"] = round(int(mq.group(3).replace(",", "")) / 100.0, 1)
        out["_q_combo"] = [int(mq.group(1)), int(mq.group(2))]
    mt = re.search(r"三連複\s*(\d{1,2})\s*-\s*(\d{1,2})\s*-\s*(\d{1,2})\s*([\d,]+)円", body)
    if mt:
        out["trifecta"] = round(int(mt.group(4).replace(",", "")) / 100.0, 1)
        out["_t_combo"] = [int(mt.group(i)) for i in (1, 2, 3)]
    return out


def baba_of(name):
    for k, c in BABA.items():
        if k and k in name:
            return c
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=40)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="0=전부")
    a = ap.parse_args()

    cutoff = time.time() - a.days * 86400
    rows = []
    skip_no_result = skip_have = skip_nomap = 0
    for f in sorted(os.listdir(LOG_DIR)):
        if not f.endswith(".json"):
            continue
        try:
            d = json.load(open(os.path.join(LOG_DIR, f), encoding="utf-8"))
        except Exception:
            continue
        if (d.get("sport") or "") != "horse":
            continue
        res = d.get("result") or {}
        if not res.get("1st") or not res.get("2nd"):
            skip_no_result += 1
            continue                                    # 🔴 착순 없는 것은 대상 아님
        if (res.get("payouts") or {}).get("quinella"):
            skip_have += 1
            continue                                    # 🔴 이미 있으면 안 덮는다
        ymd = f[:10].replace("_", "")
        try:
            if time.mktime(time.strptime(ymd, "%Y%m%d")) < cutoff:
                continue
        except Exception:
            continue
        venue = f[11:-5].split("_")[0]
        m = re.search(r"(\d+)경주", f)
        b = baba_of(venue)
        if not b or not m:
            skip_nomap += 1
            continue
        rows.append((f, venue, int(m.group(1)), ymd, b, res))
    if a.limit:
        rows = rows[:a.limit]

    print("=" * 84)
    print("확정배당 과거분 백필  %s  (최근 %d일)" % ("[APPLY]" if a.apply else "[DRY-RUN]", a.days))
    print("=" * 84)
    print("대상 후보 %d경주  (착순없음 %d 제외 · 이미보유 %d 제외 · 경마장 매핑실패 %d 제외)"
          % (len(rows), skip_no_result, skip_have, skip_nomap))

    ok, miss, mismatch = [], [], []
    for i, (f, venue, rno, ymd, b, res) in enumerate(rows):
        p = parse_payouts(fetch_page(b, ymd, rno))
        if not p.get("quinella"):
            miss.append((f, venue))
            continue
        # 🔴 검증: 확정배당 조합이 우리가 저장한 착순 1·2착과 일치하는가
        top2 = sorted([int(res.get("1st") or 0), int(res.get("2nd") or 0)])
        if sorted(p.get("_q_combo") or []) != top2:
            mismatch.append((f, venue, p.get("_q_combo"), top2))
            continue                                    # 🔴 안 맞으면 **쓰지 않는다**
        ok.append((f, p))
        if (i + 1) % 50 == 0:
            print("   … %d/%d 진행" % (i + 1, len(rows)))

    print("\n🟢 채울 수 있는 것      : **%d경주**" % len(ok))
    print("🔴 소스에 배당 없음     : %d경주" % len(miss))
    print("🔴 착순↔배당 조합 불일치: %d경주  ← **쓰지 않는다**" % len(mismatch))
    for f, v, c, t in mismatch[:5]:
        print("     %s  소스조합 %s vs 우리착순 %s" % (f[:-5], c, t))
    byday = collections.Counter(f[:10] for f, _ in ok)
    print("\n날짜별(상위 8): %s" % dict(sorted(byday.items(), reverse=True)[:8]))

    if not a.apply:
        print("\n⚠ DRY-RUN 이다. 승인 후 `--apply`.")
        return 0

    bdir = os.path.join(BASE, "backups", "payouts_" + time.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(bdir, exist_ok=True)
    n = 0
    for f, p in ok:
        src = os.path.join(LOG_DIR, f)
        shutil.copy2(src, os.path.join(bdir, f))
        try:
            d = json.load(open(src, encoding="utf-8"))
            res = d.get("result") or {}
            po = dict(res.get("payouts") or {})
            po["quinella"] = p["quinella"]
            if p.get("trifecta"):
                po["trifecta"] = p["trifecta"]
            res["payouts"] = po
            d["result"] = res
            d.setdefault("payouts_backfilled", []).append(
                {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "by": "backfill_payouts.py",
                 "source": "keiba.go.jp RaceMarkTable"})
            tmp = src + ".tmp%d" % os.getpid()
            json.dump(d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            os.replace(tmp, src)
            n += 1
        except Exception as e:
            print("   실패 %s: %s" % (f, str(e)[:70]))
    print("\n✅ 백필 %d경주 · 백업 %s" % (n, os.path.relpath(bdir, BASE)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
