# -*- coding: utf-8 -*-
"""[snapshot_compare 날짜 오염 격리·재생성 (2026-08-01 신설)]

■ 무엇을 고치는가
  `_snapshot_metas_for_race` 가 **날짜 없이 raceKey 로만** 스냅샷을 모으고,
  저장 파일명 `snapshot_compare/<raceKey>.json` **에도 날짜가 없어** 다른 날이 서로를 덮었다.
  실측: 114개 중 **8개(7.0%)** 가 서로 다른 날짜를 짝지었다.
    예) `나고야_11경주.json` — T-10·T-2 는 07-29 인데 **마감후가 07-15**,
        그 짝으로 계산된 `"최저배당 변화 -56.7%"` 가 저장되고 타이밍 학습에 들어갔다.

■ 무엇을 하는가 (⚠ **삭제하지 않는다**)
  ① 오염분 **격리** — `snapshot_compare/_quarantine/<원본명>` 으로 **이동**(원본 보존).
  ② 정상분 **날짜 파일명으로 이관** — `<YYYY-MM-DD>_<raceKey>.json` 으로 복사(구 파일은 남긴다).
  ③ **재생성** — 원본 스냅샷 PNG+메타(파일명에 날짜 100%)로 **날짜별로 다시 만든다.**
     ⚠ 재생성은 `app.py` 의 계산식을 **재구현하지 않고 그대로 임포트**해 쓴다
       (식을 베끼면 두 곳이 갈라진다 — 원칙 12 계열).

■ 왜 재생성이 가능한가
  스냅샷 PNG 434장이 **전부 남아 있고 파일명에 `YYYY_MM_DD_` 접두가 100%** 있다.
  compare 는 그 메타에서 파생된 값이라 **원본이 있으면 언제든 다시 만들 수 있다.**
  ⚠ 단 스냅샷이 없는 날(7/31 이후 0장)은 재생성 대상 자체가 없다.

사용: python tools/fix_snapshot_compare.py            (--dry 기본: 아무것도 안 바꾼다)
      python tools/fix_snapshot_compare.py --apply
"""
import argparse
import json
import os
import re
import shutil
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CD = os.path.join(BASE, "data", "snapshot_compare")
SD = os.path.join(BASE, "data", "snapshots")
QD = os.path.join(CD, "_quarantine")
_PFX = re.compile(r"^(\d{4})[_-](\d{2})[_-](\d{2})[_-]")
_DATED = re.compile(r"^\d{4}-\d{2}-\d{2}_")


def _fdate(name):
    g = _PFX.match(name or "")
    return "%s-%s-%s" % g.groups() if g else ""


def stage_dates(doc):
    """compare 문서의 단계별 날짜(파일명 접두 기준). 원본 파일명이 없으면 '시각' 앞 10자."""
    out = {}
    for k in ("T-10", "T-2", "마감후"):
        v = doc.get(k)
        if isinstance(v, dict):
            d = _fdate(v.get("파일") or "") or str(v.get("시각") or "")[:10]
            if d:
                out[k] = d
    return out


def scan():
    """(오염, 정상) 분리. 오염 = 단계별 날짜가 2종 이상."""
    bad, ok = [], []
    for f in sorted(os.listdir(CD)):
        if not f.endswith(".json") or _DATED.match(f):
            continue                                  # 이미 날짜 파일명이면 대상 아님
        p = os.path.join(CD, f)
        if not os.path.isfile(p):
            continue
        try:
            doc = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        ds = stage_dates(doc)
        (bad if len(set(ds.values())) > 1 else ok).append((f, doc, ds))
    return bad, ok


def regen_targets():
    """재생성 대상 = 스냅샷에 `close` 트리거가 있는 (raceKey, 날짜) 쌍 전부."""
    pairs = set()
    if not os.path.isdir(SD):
        return []
    for fn in os.listdir(SD):
        if not fn.lower().endswith(".png"):
            continue
        try:
            meta = json.load(open(os.path.join(SD, fn.rsplit(".", 1)[0] + ".json"), encoding="utf-8"))
        except Exception:
            continue
        if (meta.get("trigger") or "") != "close":
            continue
        rk = (meta.get("raceKey") or "").strip()
        d = _fdate(fn) or str(meta.get("at") or "")[:10]
        if rk and d:
            pairs.add((rk, d))
    return sorted(pairs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="실제로 이동·재생성한다(기본은 미실행)")
    # 🔴 [2026-08-01 · 권대표 결정] 격리만 하고 **이관·재생성은 취소**됐다.
    #   스냅샷 판정 경로를 끊었으므로 compare 를 다시 만드는 것이 무의미하다.
    #   ⚠ ②③ 코드는 **지우지 않는다**(무삭제) — 판정을 되살리면 그때 필요하다.
    ap.add_argument("--quarantine-only", action="store_true",
                    help="🔴 오염분 격리만 한다(정상분 이관·재생성 생략) — 2026-08-01 지시 기본 운용")
    a = ap.parse_args()

    bad, ok = scan()
    pairs = regen_targets()
    print("=" * 78)
    print("snapshot_compare 날짜 오염 격리·재생성  %s" % ("[APPLY]" if a.apply else "[DRY-RUN · 아무것도 안 바꾼다]"))
    print("=" * 78)
    print("구 파일명(날짜 없음) %d개 = 🔴 오염 %d + 🟢 정상 %d" % (len(bad) + len(ok), len(bad), len(ok)))
    print("재생성 가능한 (경주, 날짜) 쌍: %d개  ← 스냅샷 원본이 남아 있어 다시 만들 수 있다" % len(pairs))

    print("\n🔴 격리 대상(삭제 아님 · _quarantine/ 으로 이동)")
    for f, doc, ds in bad:
        print("   %-28s %s   변화량=%s" % (f, ds, (doc.get("변화량") or {}).get("최저배당 변화")))
    if not bad:
        print("   (없음)")

    if not a.apply:
        print("\n⚠ DRY-RUN 이다. 실제 반영은 --apply 를 붙인다.")
        print("   ① 오염 %d개 격리 ② 정상 %d개를 날짜 파일명으로 이관 ③ %d쌍 재생성"
              % (len(bad), len(ok), len(pairs)))
        return 0

    os.makedirs(QD, exist_ok=True)
    moved = 0
    for f, doc, ds in bad:
        try:
            shutil.move(os.path.join(CD, f), os.path.join(QD, f))    # ⚠ 이동(보존) — 삭제 아님
            moved += 1
        except Exception as e:
            print("   격리 실패 %s: %s" % (f, e))
    print("\n✅ 격리 %d개 → %s" % (moved, os.path.relpath(QD, BASE)))

    if a.quarantine_only:
        print("\n⏭ `--quarantine-only` — 정상분 이관(%d개)·재생성(%d쌍)은 **실행하지 않는다.**"
              % (len(ok), len(pairs)))
        print("   사유: 스냅샷 판정 경로 중단(app.py SNAPSHOT_JUDGE_ENABLED=False) → 재생성이 무의미하다.")
        print("   ⚠ 해당 코드는 지우지 않았다 — 판정을 되살리면 `--apply` 만으로 다시 쓸 수 있다.")
        return 0

    # 정상분을 날짜 파일명으로 이관(복사 — 구 파일은 하위호환용으로 남긴다)
    migrated = 0
    for f, doc, ds in ok:
        d = (doc.get("raceDate") or (list(ds.values()) or [""])[0])
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", str(d or "")):
            continue
        dst = os.path.join(CD, "%s_%s" % (d, f))
        if os.path.exists(dst):
            continue
        try:
            doc.setdefault("raceDate", d)
            json.dump(doc, open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            migrated += 1
        except Exception as e:
            print("   이관 실패 %s: %s" % (f, e))
    print("✅ 정상분 날짜 파일명 이관 %d개 (구 파일은 보존)" % migrated)

    # 재생성 — app.py 의 계산식을 **그대로 임포트**해 쓴다(식을 베끼지 않는다)
    made = 0
    try:
        sys.path.insert(0, BASE)
        os.environ.setdefault("RACING_NO_SERVE", "1")
        import app as _app                                  # noqa: E402
        for rk, d in pairs:
            try:
                if _app._snapshot_build_compare(rk, d):
                    made += 1
            except Exception as e:
                print("   재생성 실패 %s %s: %s" % (d, rk, str(e)[:70]))
    except Exception as e:
        print("⚠ 재생성 건너뜀(app import 실패): %s" % str(e)[:120])
        print("   → 서버가 떠 있는 상태라면 재생성은 서버 쪽에서 자연히 다시 만들어진다.")
    print("✅ 재생성 %d개" % made)
    print("\n⚠ `snapshot_timing.json` 재산출은 **별도**다 — 오염 제거를 먼저 확인한 뒤 진행할 것.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
