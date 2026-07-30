# -*- coding: utf-8 -*-
"""[마감 확정본 소급 복구 (2026-07-31 · 권대표 승인 권고안)]

마감 후 덮어쓰기로 지워진 `keyHorses` 를 **확정본에서 되살린다.**
🔴 실측: readonly=True 이고 확정값이 남아 있는 529개 중 **492개(93.0%)** 가 유실.
   복원 시뮬레이션은 **492/492(100%)** 성공했다.

■ 🔴 출처에 따라 처리를 **다르게** 한다 (권대표 지시)
  · `closed_row`    (235건) = '🔒 마감 확정' 행 = **확정본** → `keyHorses` 를 덮어쓴다
  · `pre_close_row` (257건) = 마지막 마감 전 행 = **확정 아님**
      → `frozen` 블록에만 넣고 **top-level `keyHorses` 는 건드리지 않는다**(원본 훼손 0)
  · T-10분 초과분은 `srcTrust` 에 **별도 표기**한다 — 마감 시점 값이 아니다.

■ ⚠ 절대 손대지 않는 것
  `result` · `hit` · `profit` · `review` — 적중 판정·성적표가 흔들리면 안 된다.

■ 안전장치
  · `--dry` 가 **기본**. 실제 기록은 `--apply` 필수.
  · `--apply` 시 **물리 백업 선행 필수**. 백업 실패 시 즉시 중단한다.
  · **멱등** — `frozen.restoredBy == "backfill"` 이면 건너뛴다. 재실행해도 결과가 같다.

사용:
  python tools/restore_frozen_keyhorses.py                 # 미리보기(기본)
  python tools/restore_frozen_keyhorses.py --limit 20      # 앞 20건만 상세 출력
  python tools/restore_frozen_keyhorses.py --apply         # 실제 기록(백업 후)
"""
import argparse
import datetime
import glob
import json
import os
import shutil
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGDIR = os.path.join(BASE, "data", "analysis_log")
HISTDIR = os.path.join(BASE, "data", "odds_history")
BACKUP_ROOT = os.path.join(BASE, "backup")

# 이 시각(분)을 넘는 pre_close_row 는 '마감 시점 값이 아님'으로 별도 표기한다.
FAR_MIN = 10.0
# ⚠ 절대 수정 금지 — 적중 판정·성적 관련
PROTECTED = ("result", "hit", "profit", "review")


def _minutes_before(doc_path, row):
    """추천 행의 시각이 마감 몇 분 전인가. 마감시각을 모르면 None."""
    hp = os.path.join(HISTDIR, os.path.basename(doc_path))
    if not os.path.exists(hp):
        return None
    try:
        dl = (json.load(open(hp, encoding="utf-8")) or {}).get("deadline_epoch")
        if not dl:
            return None
        hh, mm, ss = [int(x) for x in str(row.get("time") or "")[:8].split(":")]
        dt = datetime.datetime.fromtimestamp(dl).replace(hour=hh, minute=mm, second=ss)
        return round((dl - dt.timestamp()) / 60.0, 1)
    except Exception:
        return None


def plan_one(path):
    """1건 분석 → 복구 계획 dict 또는 None(대상 아님)."""
    try:
        doc = json.load(open(path, encoding="utf-8"))
    except Exception:
        return None
    if not doc.get("readonly"):
        return None
    if ((doc.get("frozen") or {}).get("restoredBy")) == "backfill":
        return {"path": path, "skip": "이미 복구됨(멱등)"}
    rows = [r for r in (doc.get("recommendation_history") or []) if isinstance(r, dict)]
    cl = [r for r in rows if r.get("closed") and r.get("keyHorses")]
    pre = [r for r in rows if not r.get("closed") and r.get("keyHorses")]
    if cl:
        row, src, trust = cl[-1], "closed_row", "확정"
    elif pre:
        row, src, trust = pre[-1], "pre_close_row", "마감 직전(확정 아님)"
    else:
        return None
    want = row.get("keyHorses")
    cur = doc.get("keyHorses")
    if sorted(cur or []) == sorted(want or []):
        return None                                  # 유실 아님 → 대상 아님
    mb = _minutes_before(path, row)
    if src == "pre_close_row" and mb is not None and mb > FAR_MIN:
        trust = "🔴 T-%.1f분 — 마감 시점 값 아님" % mb
    return {"path": path, "src": src, "trust": trust, "mb": mb,
            "cur": cur, "want": want,
            # 🔴 확정본만 top-level 을 덮어쓴다. pre_close_row 는 frozen 에만 남긴다.
            "overwrite": (src == "closed_row"),
            "at": row.get("time")}


def apply_one(p, backup_dir):
    path = p["path"]
    shutil.copy2(path, os.path.join(backup_dir, os.path.basename(path)))   # 백업 선행
    doc = json.load(open(path, encoding="utf-8"))
    fz = dict(doc.get("frozen") or {})
    fz.update({"keyHorses": p["want"], "src": p["src"], "srcTrust": p["trust"],
               "srcMinutesBefore": p["mb"], "at": p["at"],
               "restoredBy": "backfill",
               "restoredAt": time.strftime("%Y-%m-%d %H:%M:%S"),
               "previousKeyHorses": p["cur"]})
    doc["frozen"] = fz
    if p["overwrite"]:
        doc["keyHorses"] = p["want"]
    for k in PROTECTED:                              # ⚠ 방어 — 어떤 경우에도 손대지 않는다
        if k in doc and k in fz:
            fz.pop(k, None)
    tmp = path + ".tmp%d" % os.getpid()
    json.dump(doc, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="실제 기록(기본은 미리보기)")
    ap.add_argument("--limit", type=int, default=12, help="상세 출력 건수")
    args = ap.parse_args()

    plans, skipped = [], 0
    for f in sorted(glob.glob(os.path.join(LOGDIR, "*.json"))):
        p = plan_one(f)
        if not p:
            continue
        if p.get("skip"):
            skipped += 1
            continue
        plans.append(p)

    ow = [p for p in plans if p["overwrite"]]
    fo = [p for p in plans if not p["overwrite"]]
    far = [p for p in plans if str(p["trust"]).startswith("🔴")]

    print("=" * 78)
    print("마감 확정본 소급 복구  %s" % ("🔴 --apply (실제 기록)" if args.apply else "🟢 --dry (미리보기·기본)"))
    print("=" * 78)
    print("⚠ 분모 = data/analysis_log 전체 %d개"
          % len(glob.glob(os.path.join(LOGDIR, "*.json"))))
    print("  복구 대상            : %d건" % len(plans))
    print("   ├ ① closed_row     : %d건  → top-level keyHorses **덮어씀**(확정본)" % len(ow))
    print("   └ ③ pre_close_row  : %d건  → frozen 블록에만 기록(**원본 무수정**)" % len(fo))
    print("  🔴 T-%.0f분 초과      : %d건  → srcTrust 에 별도 표기(마감 시점 값 아님)" % (FAR_MIN, len(far)))
    print("  이미 복구됨(건너뜀)   : %d건" % skipped)
    print("  ⚠ result·hit·profit·review 는 손대지 않는다")

    if far:
        print("\n--- 🔴 T-%.0f분 초과 (전건) ---" % FAR_MIN)
        for p in far:
            print("   %-34s T-%.1f분  %s → %s"
                  % (os.path.basename(p["path"])[:34], p["mb"], p["cur"], p["want"]))

    print("\n--- 복구 미리보기 (앞 %d건) ---" % args.limit)
    for p in plans[:args.limit]:
        print("   %-34s %-14s T%s  %s → %s%s"
              % (os.path.basename(p["path"])[:34], p["src"],
                 ("-%.1f분" % p["mb"]) if p["mb"] is not None else "?",
                 p["cur"], p["want"], "" if p["overwrite"] else "  (frozen 에만)"))

    if not args.apply:
        print("\n🟢 --dry 이므로 **파일을 전혀 건드리지 않았다.**")
        print("   실제 기록하려면: python tools/restore_frozen_keyhorses.py --apply")
        return 0

    if not plans:
        print("\n대상 없음 — 종료")
        return 0
    backup_dir = os.path.join(BACKUP_ROOT, "restore_" + time.strftime("%Y%m%d_%H%M%S"))
    try:
        os.makedirs(backup_dir, exist_ok=False)
    except Exception as e:
        print("🔴 백업 디렉터리 생성 실패 — **중단**:", e)
        return 1
    done = fail = 0
    for p in plans:
        try:
            apply_one(p, backup_dir)
            done += 1
        except Exception as e:
            fail += 1
            print("   실패 %s: %s" % (os.path.basename(p["path"]), str(e)[:90]))
    print("\n복구 완료 %d건 · 실패 %d건" % (done, fail))
    print("물리 백업: %s" % backup_dir)
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
