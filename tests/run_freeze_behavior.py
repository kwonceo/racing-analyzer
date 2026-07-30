# -*- coding: utf-8 -*-
"""[마감 시점 동결 — **행위** 테스트 (2026-07-31 신설)]

■ 왜 이 파일이 따로 필요한가 (🔴 오늘 발견)
  기존 `run_freeze_regression.py` 는 **고정 Fixture 만 읽는다.**
  Fixture 는 *이미 오염된 과거를 찍어둔 사진*이라, 코드를 아무리 고쳐도
  **영원히 초록이 되지 않는다.** 그것은 '역사 기록'이지 '동작 검증'이 아니다.
  ⇒ 그 파일은 **그대로 둔다**(외부 앵커·역사 기록으로서 가치가 있다).
     여기서는 **실제 동결 함수를 통과시켜** 복원이 되는지를 잰다.

■ 확인하는 것 (코드 용어 없이)
  마감 시점에 확정된 유력마가, 마감 후 지워진 파일에서 **되살아나는가.**
  쉽게 말하면: 답안지가 덧칠돼 있어도 **제출 직후 찍어둔 사진**으로 원본을 복원할 수 있는가.

■ ⚠ 이 테스트는 **통과해야 정답이다**(동결 구현 후).
  실패하면 복원 경로가 끊긴 것이므로 커밋을 막아야 한다.

사용: python tests/run_freeze_behavior.py [--json]
"""
import argparse
import glob
import json
import os
import sys
import threading
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 원본 로그(마감 후 오염됨) — 여기서 확정본을 되살릴 수 있어야 한다.
CASES = [
    ("① 카나자와 7R", "2026_07_2*_카나자와_7경주.json"),
    ("② 모리오카 3R", "2026_07_28_모리오카_3경주.json"),
    ("③ 나고야 3R", "2026_07_30_나고야_3경주.json"),
    ("④ 나고야 9R", "2026_07_30_나고야_9경주.json"),
]


def _load_freeze_funcs():
    """app.py 를 **기동하지 않고** 동결 함수만 떼어 실행한다(서버 부작용 방지)."""
    src = open(os.path.join(BASE, "app.py"), encoding="utf-8").read()
    a = src.index("_FREEZE_FIELDS = [")
    b = src.index("class _SkipOddsparkBridge")
    ns = {"os": os, "json": json, "time": time, "threading": threading,
          "__file__": os.path.join(BASE, "app.py"),
          # 테스트에서는 스냅샷·기록 부작용을 차단한다(읽기 전용 검증).
          "_timeline_snap_path": lambda rk: "",
          "_json_load_guard": lambda p, d, tag=None: (d, False),
          "_json_atomic": lambda *a, **k: None}
    exec(compile(src[a:b], "<freeze>", "exec"), ns)
    return ns


def _confirmed_key_horses(doc):
    """그 경주에서 **마감 시점에 확정돼 있던** 유력마(외부 대조용 정답)."""
    rows = [r for r in (doc.get("recommendation_history") or []) if isinstance(r, dict)]
    cl = [r for r in rows if r.get("closed") and r.get("keyHorses")]
    if cl:
        return cl[-1].get("keyHorses"), "closed_row"
    pre = [r for r in rows if not r.get("closed") and r.get("keyHorses")]
    if pre:
        return pre[-1].get("keyHorses"), "pre_close_row"
    return None, None


def run_case(ns, label, pattern):
    hits = sorted(glob.glob(os.path.join(BASE, "data", "analysis_log", pattern)))
    if not hits:
        return {"name": label, "ok": False, "detail": {"오류": "원본 로그 없음: %s" % pattern}}
    path = hits[-1]
    doc = json.load(open(path, encoding="utf-8"))
    want, wsrc = _confirmed_key_horses(doc)
    cur = doc.get("keyHorses")
    fz, src = ns["_frozen_capture"](doc.get("raceKey") or label, doc,
                                    doc.get("recommendation_history"))
    got = (fz or {}).get("keyHorses")
    det = {
        "원본 파일": os.path.basename(path),
        "현재 저장값(오염 가능)": cur,
        "마감 시점 확정값": want,
        "확정값 출처": wsrc,
        "복원 결과": got,
        "복원 출처": src,
    }
    if want is None:
        det["판정"] = "확정값 자체가 없음 — 복원 대상 아님"
        return {"name": label, "ok": (fz is None), "detail": det}
    ok = bool(got) and sorted(got) == sorted(want)
    det["복원 성공"] = ok
    if ok and (cur or []) != (want or []):
        det["🔴 되살린 것"] = "%s → %s" % (cur, got)
    return {"name": label, "ok": ok, "detail": det}


def sweep(ns):
    """전수 스윕 — 오염된 파일 전체에서 복원률을 잰다(⚠ 분모 명시)."""
    tot = drift = restored = failed = 0
    src_cnt = {}
    for f in glob.glob(os.path.join(BASE, "data", "analysis_log", "*.json")):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        if not d.get("readonly"):
            continue
        want, _w = _confirmed_key_horses(d)
        if want is None:
            continue
        tot += 1
        if sorted(d.get("keyHorses") or []) == sorted(want):
            continue
        drift += 1
        fz, src = ns["_frozen_capture"](d.get("raceKey") or "", d,
                                        d.get("recommendation_history"))
        got = (fz or {}).get("keyHorses")
        if got and sorted(got) == sorted(want):
            restored += 1
            src_cnt[src] = src_cnt.get(src, 0) + 1
        else:
            failed += 1
    return {"denominator_readonly_with_confirmed": tot, "drifted": drift,
            "restored": restored, "failed": failed, "by_source": src_cnt}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    ns = _load_freeze_funcs()
    res = [run_case(ns, lb, pt) for lb, pt in CASES]
    sw = sweep(ns)
    if args.json:
        print(json.dumps({"cases": res, "sweep": sw}, ensure_ascii=False, indent=1))
        return 0 if all(r["ok"] for r in res) else 1
    print("=" * 78)
    print("마감 시점 동결 — 행위 테스트  ⚠ 이건 '통과'가 정답이다")
    print("=" * 78)
    for r in res:
        print("\n%s  %s" % ("✅ 통과" if r["ok"] else "❌ 실패", r["name"]))
        for k, v in r["detail"].items():
            print("     · %-22s %s" % (k, v))
    print("\n" + "=" * 78)
    print("전수 스윕 (⚠ 분모 = readonly=True 이고 마감 확정값이 남아 있는 파일 %d개)"
          % sw["denominator_readonly_with_confirmed"])
    d = sw["denominator_readonly_with_confirmed"] or 1
    print("  유실(현재값 ≠ 확정값) : %d / %d (%.1f%%)" % (sw["drifted"], d, 100.0 * sw["drifted"] / d))
    dr = sw["drifted"] or 1
    print("  복원 성공             : %d / %d (%.1f%%)" % (sw["restored"], sw["drifted"], 100.0 * sw["restored"] / dr))
    print("  복원 실패             : %d / %d (%.1f%%)" % (sw["failed"], sw["drifted"], 100.0 * sw["failed"] / dr))
    print("  복원 출처별           : %s" % (sw["by_source"] or "없음"))
    ok = sum(1 for r in res if r["ok"])
    print("\n통과 %d / 실패 %d" % (ok, len(res) - ok))
    print("=" * 78)
    return 0 if ok == len(res) else 1


if __name__ == "__main__":
    sys.exit(main())
