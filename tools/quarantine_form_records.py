# -*- coding: utf-8 -*-
"""[오염 전적 레코드 정리 (2026-08-01 신설)] — `--dry` 기본 · **삭제 없음 · 재표기만**.

■ 대상
  `starters_store.json` 중 `source == "korea"` 인데 **한국 경마장이 아닌** raceKey 레코드.
  (실측 11개: 삿포로 6·7·8·9·10 · 주쿄 6·8 · 중경 6·7 · 니가타 6·8)

■ 🔴 왜 "격리"가 아니라 "재표기"인가 — 안 두 개를 비교했다
  | 안 | 하는 일 | 문제 |
  |---|---|---|
  | 격리(다른 파일로 이동) | 레코드를 `starters_store` 밖으로 뺀다 | 그 경주가 **전적 없음**이 되는데, 그건 **가드 B 가 이미 만드는 상태**와 같다. 즉 얻는 게 없고 **원본이 그 자리에서 사라져** 나중에 "무엇이 붙어 있었나"를 못 본다 |
  | 🟢 **재표기(채택)** | `source` 를 `korea_contaminated` 로 바꾸고 이력을 남긴다 | 원본 `horses` 를 **그 자리에 보존**하면서 "이것은 오염이다"를 데이터에 남긴다 |
  ⇒ **재표기를 택했다.** 무삭제 원칙에도 맞고, 사후 재조사가 가능하다.

■ 🔴 함께 확인해야 하는 것 (이것 없이 재표기하면 **가드가 뚫린다**)
  가드 B(`app.py` `_form_from_starters`)는 `source == "korea"` **정확 일치**로 막는다.
  `korea_contaminated` 로 바꾸면 그 조건에 **안 걸린다** → `prescored` 분기로 다시 들어간다.
  ⇒ **가드 B 를 `source.startswith("korea")` 로 넓힌 뒤에** 이 도구를 돌려야 한다.
     이 스크립트는 그 조건이 반영됐는지 **app.py 를 읽어 자동 확인**하고, 아니면 **거부한다.**

■ 안전장치
  · `--dry` 기본 · `--apply` 필요 · `--apply` 시 **백업 먼저**(`backups/starters_<ts>/`)
  · `horses` 등 다른 필드 **무수정** · 이미 재표기된 것은 건너뜀(멱등)
  · 🔴 가드 B 확장 미확인 시 **실행 거부**(조용히 뚫리는 것을 막는다)

사용: python tools/quarantine_form_records.py
      python tools/quarantine_form_records.py --apply
"""
import argparse
import json
import os
import re
import shutil
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE = os.path.join(BASE, "starters_store.json")
NEW_SOURCE = "korea_contaminated"
_KRA_FALLBACK = r"(서울|부산경남|부경|부산|제주|과천|렛츠런|한국마사회|경마공원|KRA)"


def _kra_re():
    try:
        src = open(os.path.join(BASE, "app.py"), encoding="utf-8").read()
        m = re.search(r'_KRA_TRACK_RE\s*=\s*re\.compile\(r"([^"]+)"\)', src)
        if m:
            return re.compile(m.group(1)), src
    except Exception:
        pass
    return re.compile(_KRA_FALLBACK), ""


def _guard_widened(appsrc):
    """가드 B 가 `korea` **접두**를 막도록 넓혀졌는가(정확일치면 재표기 시 뚫린다)."""
    return bool(re.search(r'\.get\("source"\)\s*or\s*""\)\.startswith\("korea"\)', appsrc)
                or re.search(r'str\([^)]*source[^)]*\)\.startswith\("korea"\)', appsrc))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    KRA, appsrc = _kra_re()
    db = json.load(open(STORE, encoding="utf-8"))
    tgt, already = [], 0
    for k, v in db.items():
        s = (v or {}).get("source") or ""
        if s == NEW_SOURCE:
            already += 1
            continue
        if s != "korea":
            continue
        if KRA.search(k):
            continue                                  # 🟢 한국경마 = 정상
        tgt.append((k, len((v or {}).get("horses") or [])))

    widened = _guard_widened(appsrc)
    print("=" * 80)
    print("오염 전적 레코드 재표기  %s" % ("[APPLY]" if a.apply else "[DRY-RUN]"))
    print("=" * 80)
    print("가드 B 가 `korea` 접두를 막는가: %s" % ("🟢 예" if widened else "🔴 아니오(정확일치)"))
    print("대상 %d개 (이미 재표기 %d)" % (len(tgt), already))
    for k, n in tgt:
        print("   %-16s horses=%d" % (k, n))

    if not a.apply:
        print("\n⚠ DRY-RUN 이다. 승인 후 `--apply`.")
        if not widened:
            print("🔴 가드 B 가 아직 정확일치다 — **먼저 `startswith(\"korea\")` 로 넓혀야 한다.**")
        return 0

    if not widened:
        print("\n🔴 실행 거부 — 가드 B 가 `source == \"korea\"` 정확일치다.")
        print("   지금 재표기하면 `korea_contaminated` 가 가드에 안 걸려 **오염 전적이 다시 점수에 들어간다.**")
        print("   app.py `_form_from_starters` 를 `startswith(\"korea\")` 로 넓힌 뒤 다시 실행할 것.")
        return 2

    bdir = os.path.join(BASE, "backups", "starters_" + time.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(bdir, exist_ok=True)
    shutil.copy2(STORE, os.path.join(bdir, "starters_store.json"))
    n = 0
    for k, _c in tgt:
        rec = db[k]
        rec["source"] = NEW_SOURCE
        rec["source_prev"] = "korea"
        rec["contaminated"] = {
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "by": "quarantine_form_records.py",
            "reason": "한국 PDF 전적이 한국 경마장이 아닌 경주에 부착됨 — 점수 계산에 쓰이면 안 된다",
            "note": "horses 원본은 그대로 보존. 삭제하지 않았다.",
        }
        n += 1
    tmp = STORE + ".tmp%d" % os.getpid()
    json.dump(db, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, STORE)
    print("\n✅ 재표기 %d개 · 백업 %s" % (n, os.path.relpath(bdir, BASE)))
    print("⚠ `horses` 는 하나도 수정하지 않았다(원본 보존).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
