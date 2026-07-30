# -*- coding: utf-8 -*-
"""[스냅샷 2중 기록 정리 2026-07-30] data/odds_history/*.json 의 중복 스냅샷 제거.

배경 — `_multi_collect_one` 이 oddspark 수집 1회에 `_history_append` 를 **2번** 불렀다.
  · 경로 A: `_do_triple_ingest(source="oddspark_bg")` → 내부에서 `_history_append`
  · 경로 B: `[마감전 신호 기록]` 블록이 `_history_append` 직접 호출
  두 경로의 게이트가 똑같이 `_fresh_private` 라 사설이 비활성이면 **둘 다** 실행됐다.
  실측(전체 823파일·10,997스냅샷): 중복 4,507건(41.0%) · oddspark 42.9% · private 7.4%.
  두 기록의 시각차는 **중앙 0.031초**(99.9%가 0.5초 미만) = 같은 사이클의 2연속 호출.
  → 스냅샷 수 통계가 **1.69배 과대**로 계상돼 왔다(체크리스트 완료선 판정 기준을 흔든다).

보존 규칙(권대표 지시 2026-07-30 · 정정 반영)
  · 같은 (초 단위 t, src, 복승 배당 내용) 을 **동일 스냅샷**으로 본다.
  · 그 묶음에서 **`anomalies` 를 보유한 쪽을 남긴다.**
    ⚠ "1번째를 남긴다"가 아니다 — 실측상 2번째에 이상감지가 있는 쌍이 2건 존재하므로
      기계적으로 1번째를 남기면 그 2건이 사라진다.
  · 양쪽 다 이상감지가 없으면 **1번째(먼저 기록된 쪽)** 를 남긴다.
  · 이상감지 수가 같으면 1번째를 남긴다(안정적·멱등).

안전장치
  · `--dry` 가 기본. 실제 수정은 `--apply` 필요.
  · **멱등** — 이미 정리된 파일은 중복이 0이라 대상에서 자동 제외된다.
  · `snapshots` 만 손댄다. `archive_snapshots`·`analysis`·`result` 등 다른 키는 **일절 무수정**.
    (archive_snapshots 는 설계상 영구 append-only 라 중복도 그대로 보존한다 — 원본 기록이다.)
  · 원자적 저장(tmp → os.replace).
  · ⚠ 실행 전 `backups/` 에 물리 백업을 반드시 먼저 뜰 것(이 스크립트는 백업하지 않는다).

사용:
  python tools/dedupe_odds_snapshots.py                 # 미리보기(기본)
  python tools/dedupe_odds_snapshots.py --apply         # 실제 적용
  python tools/dedupe_odds_snapshots.py --date 2026_07_30   # 특정 날짜만
  python tools/dedupe_odds_snapshots.py --report        # 경주별 distinct 재산출표만 출력
"""
import argparse
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HIST_DIR = os.path.join(BASE, "data", "odds_history")


def _qsig(snap):
    """복승 배당 내용의 서명 — dict/list 양쪽 형식을 정규화(app.py `_as_qmap` 과 같은 취지)."""
    q = snap.get("quinella")
    if isinstance(q, dict):
        return tuple(sorted((str(k), round(float(v), 3)) for k, v in q.items()
                            if isinstance(v, (int, float))))
    if isinstance(q, list):
        out = []
        for c in q:
            if not isinstance(c, dict):
                continue
            combo = c.get("combo") or c.get("pair")
            odds = c.get("odds")
            if combo is not None and isinstance(odds, (int, float)):
                out.append((str(combo), round(float(odds), 3)))
        return tuple(sorted(out))
    return ()


def _key(snap):
    try:
        t = round(float(snap.get("t") or 0), 0)
    except Exception:
        t = 0
    return (t, str(snap.get("src") or ""), _qsig(snap))


def _n_anom(snap):
    a = snap.get("anomalies")
    return len(a) if isinstance(a, (list, tuple)) else 0


def dedupe_snapshots(snaps):
    """반환 (남길 리스트, 제거 수). 원본 순서를 보존한다."""
    groups = {}
    for i, s in enumerate(snaps):
        groups.setdefault(_key(s), []).append(i)
    drop = set()
    for k, idxs in groups.items():
        if len(idxs) < 2:
            continue
        # 이상감지 보유 쪽 우선 → 동률이면 먼저 기록된 쪽
        best = max(idxs, key=lambda i: (_n_anom(snaps[i]), -i))
        for i in idxs:
            if i != best:
                drop.add(i)
    return [s for i, s in enumerate(snaps) if i not in drop], len(drop)


def _json_atomic(path, obj):
    tmp = "%s.tmp%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="실제 적용(미지정 시 미리보기)")
    ap.add_argument("--date", default="", help="파일명 접두 필터 (예: 2026_07_30)")
    ap.add_argument("--report", action="store_true", help="경주별 distinct 재산출표 출력")
    ap.add_argument("--top", type=int, default=15, help="표 출력 개수")
    # ⚠ 서버가 라이브로 같은 파일을 쓰고 있다. 최근 갱신 파일을 건드리면
    #   '읽고→쓰는 사이'에 들어온 스냅샷이 유실된다 → 기본 300초 이내 갱신분은 제외.
    ap.add_argument("--min-age-sec", type=int, default=300,
                    help="최근 N초 이내 갱신된 파일은 건너뜀(라이브 수집 중 유실 방지·기본 300)")
    a = ap.parse_args()

    if not os.path.isdir(HIST_DIR):
        print("odds_history 디렉터리 없음:", HIST_DIR)
        return 1

    files = sorted(f for f in os.listdir(HIST_DIR)
                   if f.endswith(".json") and ".corrupt." not in f and ".tmp" not in f
                   and f.startswith(a.date))
    tot_before = tot_after = tot_files = changed = bad = 0
    rows = []
    anom_kept_second = 0
    skipped_recent = []

    import time as _time
    _now = _time.time()
    for fn in files:
        p = os.path.join(HIST_DIR, fn)
        try:
            if a.min_age_sec > 0 and (_now - os.path.getmtime(p)) < a.min_age_sec:
                skipped_recent.append(fn[:-5])       # 라이브 수집 중 → 건드리지 않는다
                continue
        except Exception:
            pass
        try:
            doc = json.load(open(p, encoding="utf-8"))
        except Exception as e:
            bad += 1
            print("  ⚠ 파싱 실패(건너뜀): %s — %s" % (fn, str(e)[:60]))
            continue
        if not isinstance(doc, dict):
            continue
        snaps = doc.get("snapshots")
        if not isinstance(snaps, list) or not snaps:
            continue
        tot_files += 1
        kept, dropped = dedupe_snapshots(snaps)
        tot_before += len(snaps)
        tot_after += len(kept)
        rows.append((fn[:-5], len(snaps), len(kept), dropped))
        if not dropped:
            continue
        # 2번째가 남은(=1번째보다 이상감지가 많았던) 사례 집계 — 보존 규칙 정정의 실효 확인
        groups = {}
        for i, s in enumerate(snaps):
            groups.setdefault(_key(s), []).append(i)
        for k, idxs in groups.items():
            if len(idxs) > 1:
                best = max(idxs, key=lambda i: (_n_anom(snaps[i]), -i))
                if best != idxs[0]:
                    anom_kept_second += 1
        changed += 1
        if a.apply:
            doc["snapshots"] = kept
            _json_atomic(p, doc)

    print()
    print("=" * 72)
    print("모드            : %s" % ("🔴 APPLY(실제 수정)" if a.apply else "🟢 DRY-RUN(미리보기)"))
    print("대상 파일        : %d개 (스냅샷 보유) · 파싱 실패 %d개" % (tot_files, bad))
    print("중복 있는 파일   : %d개" % changed)
    print("스냅샷 %d → %d  (제거 %d건 · %.1f%%)"
          % (tot_before, tot_after, tot_before - tot_after,
             100.0 * (tot_before - tot_after) / tot_before if tot_before else 0))
    print("과대 배율        : %.2f배 → 1.00배" % (tot_before / tot_after if tot_after else 1))
    print("2번째를 남긴 묶음: %d건  ← '1번째 고정'이었다면 이 이상감지가 소실됐을 것" % anom_kept_second)
    if skipped_recent:
        print("라이브 제외      : %d개 (최근 %d초 내 갱신) — %s"
              % (len(skipped_recent), a.min_age_sec, ", ".join(skipped_recent[:6])))
        print("                   경주 종료 후 재실행하면 정리된다(멱등).")
    print("=" * 72)

    if a.report or not a.apply:
        rows.sort(key=lambda r: -r[3])
        print("\n제거량 상위 %d경주 (파일 / 총 / distinct / 제거):" % a.top)
        for r in rows[:a.top]:
            print("   %-34s %4d → %4d  (-%d)" % r)
        # 3틱+ 판정 기준 재산출(체크리스트 ④ 완료선 근거)
        b3 = sum(1 for r in rows if r[1] >= 3)
        a3 = sum(1 for r in rows if r[2] >= 3)
        n = len(rows)
        print("\n[체크리스트 ④ 근거] 스냅샷 3틱+ 경주 비율")
        print("   중복 포함 기준 : %d/%d = %.1f%%" % (b3, n, 100.0 * b3 / n if n else 0))
        print("   distinct 기준  : %d/%d = %.1f%%   ← 이쪽이 실제값" % (a3, n, 100.0 * a3 / n if n else 0))
    if not a.apply:
        print("\n※ 미리보기입니다. 실제 적용은 --apply 를 붙이세요(사전 물리 백업 필수).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
