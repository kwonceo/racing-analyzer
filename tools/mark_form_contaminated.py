# -*- coding: utf-8 -*-
"""[전적 오염 표기 (2026-08-01 신설)] — `--dry` 기본 · **삭제·수정 없음 · 필드 1개 추가만**.

■ 왜
  한국 PDF 전적(`source="korea"`)이 JRA·일부 지방 경주에 붙어 **점수 계산에 실제로 쓰였다**
  (`_form_from_starters` app.py:1590 `prescored` 분기 → form → `_integrated_grades` → keyHorses).
  ⇒ 그 경주의 유력마·추천은 **다른 나라 다른 말의 전적**으로 만들어졌다. **판정 오염**이다.

■ 무엇을 하는가
  `data/analysis_log/*.json` 중 아래를 **전부** 만족하는 파일에 `form_contaminated` 를 **추가만** 한다.
    ① `raw_profile.source == "korea"`
    ② 경기장이 **한국 경마장이 아님**(서울·부산·부경·제주·과천·렛츠런…)
  ⚠ 기존 필드는 **하나도 건드리지 않는다.** `result`·`corePicks`·`hit` 전부 그대로다.

■ 🔴 측정 한계 (반드시 함께 읽을 것)
  `raw_profile` 배선이 **2026-07-30** 이라 그 이전 로그에는 이 필드가 **없다.**
  ⇒ 여기서 세는 건수는 **하한**이고, **실제 오염 규모는 이보다 크다.**
  7/29 이전은 판별 근거가 남아 있지 않아 **측정 불가**다.

■ 분모에서 뺄지는 **별도 판단**이다
  이 도구는 **표기만** 한다. `measure_recovery.py` 등 측정 도구는 이 필드를 **아직 읽지 않는다**
  → 표기 전후로 회수율 값이 바뀌지 않는다(의도된 동작).

■ 안전장치
  · `--dry` 기본 · `--apply` 필요 · `--apply` 시 **백업 먼저**(`backups/formcontam_<ts>/`)
  · 이미 표기된 파일은 **다시 쓰지 않는다**(멱등)
  · 원자적 저장(tmp → os.replace)

사용: python tools/mark_form_contaminated.py
      python tools/mark_form_contaminated.py --apply
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

# ⚠ 경기장 판정은 app.py `_KRA_TRACK_RE` 와 **같은 목록**을 쓴다(두 곳에 두지 않으려 런타임 파싱).
_KRA_FALLBACK = r"(서울|부산경남|부경|부산|제주|과천|렛츠런|한국마사회|경마공원|KRA)"


def _kra_re():
    try:
        src = open(os.path.join(BASE, "app.py"), encoding="utf-8").read()
        m = re.search(r'_KRA_TRACK_RE\s*=\s*re\.compile\(r"([^"]+)"\)', src)
        if m:
            return re.compile(m.group(1)), "app.py"
    except Exception:
        pass
    return re.compile(_KRA_FALLBACK), "폴백(내장)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    KRA, kra_src = _kra_re()
    rows, already = [], 0
    no_raw = 0
    src_cnt = collections.Counter()
    for f in sorted(os.listdir(LOG_DIR)):
        if not f.endswith(".json"):
            continue
        try:
            d = json.load(open(os.path.join(LOG_DIR, f), encoding="utf-8"))
        except Exception:
            continue
        rp = d.get("raw_profile") or {}
        src = rp.get("source")
        if not src:
            no_raw += 1
            continue
        src_cnt[src] += 1
        if src != "korea":
            continue
        venue = f[11:-5].split("_")[0]
        if KRA.search(venue):
            continue                                   # 🟢 한국경마 = 정상 입력
        if d.get("form_contaminated"):
            already += 1
            continue                                   # 멱등
        rows.append((f, venue, f[:10], len(rp.get("entries") or []), rp.get("fieldSize")))

    print("=" * 82)
    print("전적 오염 표기  %s   (한국경마장 판정 목록 출처: %s)"
          % ("[APPLY]" if a.apply else "[DRY-RUN]", kra_src))
    print("=" * 82)
    print("🔴 raw_profile 없음(=판별 불가) : %d파일  ← **7/29 이전. 실제 오염은 여기에도 있다**" % no_raw)
    print("   raw_profile source 분포      : %s" % dict(src_cnt.most_common()))
    print("🔴 표기 대상(korea 전적 · 한국 경마장 아님): **%d건**  (이미 표기됨 %d)" % (len(rows), already))
    byv = collections.Counter(r[1] for r in rows)
    byd = collections.Counter(r[2] for r in rows)
    print("   경기장별: %s" % dict(byv.most_common()))
    print("   날짜별  : %s" % dict(sorted(byd.items())))
    print("\n   (샘플 8건 — 전적두수 ↔ 출마표두수)")
    for f, v, d0, ne, fs in rows[:8]:
        print("     %-42s entries=%-3s fieldSize=%s" % (f[:-5], ne, fs))

    if not a.apply:
        print("\n⚠ DRY-RUN 이다. 승인 후 `--apply`.")
        print("⚠ 🔴 이 건수는 **하한**이다 — raw_profile 배선(07-30) 이전은 측정 불가.")
        return 0

    bdir = os.path.join(BASE, "backups", "formcontam_" + time.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(bdir, exist_ok=True)
    n = 0
    for f, venue, _d0, _ne, _fs in rows:
        src_p = os.path.join(LOG_DIR, f)
        shutil.copy2(src_p, os.path.join(bdir, f))
        try:
            d = json.load(open(src_p, encoding="utf-8"))
            d["form_contaminated"] = {
                "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "by": "mark_form_contaminated.py",
                "reason": "한국 PDF 전적(source=korea)이 한국 경마장이 아닌 경주에 부착됨 — 전적이 점수 계산에 반영되어 유력마·추천이 오염됨",
                "venue": venue,
                "note": "표기 전용. 이 파일의 다른 필드는 수정하지 않았다. 측정 분모 제외 여부는 별도 판단.",
            }
            tmp = src_p + ".tmp%d" % os.getpid()
            json.dump(d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            os.replace(tmp, src_p)
            n += 1
        except Exception as e:
            print("   실패 %s: %s" % (f, str(e)[:70]))
    print("\n✅ 표기 %d건 · 백업 %s" % (n, os.path.relpath(bdir, BASE)))
    print("⚠ 🔴 이 건수는 **하한**이다 — raw_profile 배선(07-30) 이전은 측정 불가.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
