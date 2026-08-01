# -*- coding: utf-8 -*-
"""[중앙경마 착순·확정배당 소급 백필 (2026-08-01 신설)] — `--dry` 기본 · **원본 삭제 없음**.

■ 왜
  중앙경마는 확정배당·착순이 **0건**이라 성적을 못 재고 있었다.
  `race/result.html` 에 둘 다 있고(2026-08-01 확인), 파서는 `tools/parse_jra_result.py` 에 있다.
  이 도구는 **이미 지나간 경주**를 채운다. 앞으로 들어올 것은 `app.py` 배선이 담당한다.

■ 🔴 스키마 매핑 — 손 대조로 검증됨(2026-08-01 삿포로 6R)
    우리 '복승' = 馬連(2두·순서무관) → payouts.quinella
      실측: 추천 [7,8] · 착순 1착8 2착7 · 우리 판정식 set(combo)==top2 → 적중
            馬連 払戻 조합 "7 8" · **570円** ⇒ 조합·판정 일치 확인
    ⚠ `枠連` 은 이번 경주에서 조합이 "7 8" 로 **같아 보이지만 540円** 이다(枠 번호는 마번이 아니다).
      절대 `quinella` 에 넣지 않는다.
    ⚠ `複勝`(1두·3착 이내)은 우리 '복승'과 **이름만 같고 다른 마권**이다.
    우리 '삼복승' = 3連複 → payouts.trio   (`3連単` 은 순서 있음 · 혼동 시 2.7배 과대)

■ 안전장치
  · `--dry` 기본. `--apply` 필요.
  · `--apply` 시 **먼저 백업**(`backups/jra_result_<타임스탬프>/`). 원본 삭제 없음.
  · 🔴 **미확정 경주에는 아무것도 쓰지 않는다**(`finished=False` 면 건너뜀).
  · `result` 가 **이미 있으면 덮지 않는다**(`--force` 없으면).
  · `payouts` 만 채우고 다른 필드는 무수정. `jra_result_backfilled` 에 이력을 남긴다.

사용: python tools/backfill_jra_result.py                (--dry)
      python tools/backfill_jra_result.py --apply
"""
import argparse
import json
import os
import re
import shutil
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from parse_jra_result import parse_result, _fetch, RESULT_URL   # noqa: E402  (파서 재사용·중복 금지)

LOG_DIR = os.path.join(BASE, "data", "analysis_log")
_JRA_TRACK = {"01": "삿포로", "02": "하코다테", "03": "후쿠시마", "04": "니가타", "05": "도쿄",
              "06": "나카야마", "07": "중경", "08": "교토", "09": "한신", "10": "고쿠라"}
_VENUE2CODE = {}
for _c, _v in _JRA_TRACK.items():
    _VENUE2CODE[_v] = _c
_VENUE2CODE["주쿄"] = "07"          # 중경=주쿄 이표기


def _race_id(venue, rno, ymd):
    """경기장+경주번호+개최일 → netkeiba race_id. 그날 개최 목록에서 場코드·경주번호로 찾는다."""
    import urllib.request, gzip
    code = _VENUE2CODE.get(venue)
    if not code:
        return None
    try:
        req = urllib.request.Request(
            "https://race.netkeiba.com/top/race_list_sub.html?kaisai_date=%s" % ymd,
            headers={"User-Agent": "Mozilla/5.0", "Accept-Encoding": "gzip"})
        with urllib.request.urlopen(req, timeout=12) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            html = raw.decode("utf-8", "replace")
    except Exception:
        return None
    for rid in sorted(set(re.findall(r"race_id=(\d{12})", html))):
        if rid[4:6] == code and int(rid[10:12]) == int(rno):
            return rid
    return None


def targets(day):
    """그날 중앙경마 analysis_log 중 **결과가 아직 없는** 것."""
    out = []
    pre = day.replace("-", "_")
    for f in sorted(os.listdir(LOG_DIR)):
        if not f.startswith(pre) or not f.endswith(".json"):
            continue
        try:
            d = json.load(open(os.path.join(LOG_DIR, f), encoding="utf-8"))
        except Exception:
            continue
        if d.get("category") != "japan_central" or d.get("sport") == "cycle":
            continue
        venue = f[11:-5].split("_")[0]
        m = re.search(r"(\d+)경주", f)
        if not m or venue not in _VENUE2CODE:
            continue
        has = bool((d.get("result") or {}).get("1st"))
        out.append((f, venue, int(m.group(1)), has, d))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default=time.strftime("%Y-%m-%d"))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--force", action="store_true", help="이미 결과가 있어도 덮어쓴다(기본 금지)")
    a = ap.parse_args()

    ymd = a.day.replace("-", "")
    rows = targets(a.day)
    print("=" * 80)
    print("중앙경마 착순·확정배당 백필  %s  (%s)" % ("[APPLY]" if a.apply else "[DRY-RUN]", a.day))
    print("=" * 80)
    print("대상 후보 %d경주\n" % len(rows))
    plans = []
    for f, venue, rno, has, doc in rows:
        if has and not a.force:
            print("  ⏭ %-18s 이미 결과 있음(건너뜀)" % f[11:-5])
            continue
        rid = _race_id(venue, rno, ymd)
        if not rid:
            print("  ❌ %-18s race_id 못 찾음" % f[11:-5])
            continue
        try:
            r = parse_result(_fetch(RESULT_URL % rid))
        except Exception as e:
            print("  ❌ %-18s 조회 실패 %s" % (f[11:-5], str(e)[:60]))
            continue
        if not r["finished"]:
            print("  ⏳ %-18s 결과 미확정 — **아무것도 안 쓴다**" % f[11:-5])
            continue
        print("  🟢 %-18s top3=%-12s quinella=%-7s trio=%-7s (race_id=%s)"
              % (f[11:-5], r["top3"], r["payouts"].get("quinella"), r["payouts"].get("trio"), rid))
        plans.append((f, r, rid))

    print("\n🔴 실제로 쓸 대상: **%d경주**" % len(plans))
    if not a.apply:
        print("⚠ DRY-RUN 이다. 승인 후 `--apply`.")
        return 0

    bdir = os.path.join(BASE, "backups", "jra_result_" + time.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(bdir, exist_ok=True)
    n = 0
    for f, r, rid in plans:
        p = os.path.join(LOG_DIR, f)
        shutil.copy2(p, os.path.join(bdir, f))            # ⚠ 백업 먼저
        try:
            d = json.load(open(p, encoding="utf-8"))
            res = d.get("result") or {}
            o = r["order"]
            res["1st"] = o[0]["no"] if len(o) > 0 else res.get("1st")
            res["2nd"] = o[1]["no"] if len(o) > 1 else res.get("2nd")
            res["3rd"] = o[2]["no"] if len(o) > 2 else res.get("3rd")
            res["payouts"] = dict(res.get("payouts") or {}, **{k: v for k, v in r["payouts"].items() if v})
            res["payouts_raw"] = r["payouts_raw"]
            res["order_full"] = r["order"]
            d["result"] = res
            d.setdefault("jra_result_backfilled", []).append(
                {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "race_id": rid,
                 "by": "backfill_jra_result.py", "source": "netkeiba result.html"})
            tmp = p + ".tmp%d" % os.getpid()
            json.dump(d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            os.replace(tmp, p)
            n += 1
        except Exception as e:
            print("   실패 %s: %s" % (f, str(e)[:70]))
    print("\n✅ 백필 %d경주 · 백업 %s" % (n, os.path.relpath(bdir, BASE)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
