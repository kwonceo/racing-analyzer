# -*- coding: utf-8 -*-
"""[중앙경마 category 오분류 소급 정정 (2026-08-01 신설)] — `fix_keirin_sport_tag.py` 선례 그대로.

■ 무엇을 고치는가
  지방경마·경륜이 `category="japan_central"` 로 저장돼 **중앙경마 탭에 지방이 뜬다.**
  원인은 확장 `content.js:1023` 의 `isCentral = (japanType==='central') || _isJRACentral(rk)` —
  **팝업 수동 토글이 경기장명 검사를 `||` 로 이겨버린다.** 서버는 그 값을 검증 없이 저장해 왔다.
  (신규 유입은 `app.py` 의 [승인 B] 가드가 막는다. 이 도구는 **이미 쌓인 것**을 고친다.)

■ 판정 기준 — **경기장명 화이트리스트**(`app.py._JRA_TRACKS` 를 **런타임 파싱해 재사용**)
  ⚠ 목록을 여기 복사하지 않는다. 두 곳에 두면 반드시 갈라진다(`fix_keirin_sport_tag.py` 와 같은 원칙).
  `category=="japan_central"` 인데 경기장이 화이트리스트에 없고 한국 경마장도 아니면 → `japan_local`.
  ⚠ `sport` 가 `cycle`·`boat`·`bike` 인 것은 **건드리지 않는다**(이미 확정된 종목).

■ 🔴 회원 화면이 바뀐다
  정정하면 **중앙경마 탭에서 보이던 지방 경주가 사라진다.** 규모를 먼저 보고하고 승인받은 뒤 `--apply`.

■ 안전장치
  · `--dry` 가 **기본**. `--apply` 를 붙여야 실제로 바꾼다.
  · `--apply` 시 **먼저 백업**(`backups/category_fix_<타임스탬프>/`)한다. **원본 삭제 없음.**
  · `category` **한 필드만** 바꾸고 `category_fixed` 에 정정 이력을 남긴다. 다른 필드 무수정.
  · 원자적 저장(tmp → replace).

사용: python tools/fix_central_category.py            (--dry 기본)
      python tools/fix_central_category.py --apply
"""
import argparse
import ast
import collections
import json
import os
import re
import shutil
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET_DIRS = ["analysis_log", "odds_history", "race_results"]


def _load_from_app(name):
    """app.py 에서 상수를 **런타임 파싱**해 가져온다(목록을 두 곳에 두지 않는다)."""
    src = open(os.path.join(BASE, "app.py"), encoding="utf-8").read()
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == name:
            if isinstance(n.value, ast.Call):                 # re.compile("...")
                return re.compile(ast.literal_eval(n.value.args[0]))
            return ast.literal_eval(n.value)
    raise SystemExit("app.py 에서 %s 를 찾지 못했다 — 이름이 바뀌었는지 확인할 것" % name)


JRA_TRACKS = _load_from_app("_JRA_TRACKS")
KRA_RE = _load_from_app("_KRA_TRACK_RE")


def _venue_of(fn, doc):
    """경기장명 — 파일명 접두(YYYY_MM_DD_) 다음 토큰. 없으면 doc 의 race/raceKey."""
    base = fn[:-5]
    if re.match(r"^\d{4}_\d{2}_\d{2}_", base):
        base = base[11:]
    v = base.split("_")[0]
    if not v:
        v = str((doc or {}).get("race") or (doc or {}).get("raceKey") or "").split()[0]
    return v


def scan():
    """정정 대상 수집. 반환 [(dir, 파일, 경기장, sport)]"""
    out = []
    for d in TARGET_DIRS:
        p = os.path.join(BASE, "data", d)
        if not os.path.isdir(p):
            continue
        for fn in sorted(os.listdir(p)):
            if not fn.endswith(".json") or ".corrupt." in fn or ".tmp" in fn:
                continue
            try:
                doc = json.load(open(os.path.join(p, fn), encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(doc, dict) or doc.get("category") != "japan_central":
                continue
            if doc.get("sport") in ("cycle", "boat", "bike"):
                continue                                   # 이미 확정된 종목은 손대지 않는다
            v = _venue_of(fn, doc)
            if not v or any(t in v for t in JRA_TRACKS):
                continue                                   # 🟢 진짜 중앙 — 그대로 둔다
            if KRA_RE.search(v) or KRA_RE.search(str(doc.get("race") or "")):
                continue                                   # 한국경마는 별도 카테고리
            out.append((d, fn, v, doc.get("sport")))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="실제로 정정한다(기본은 미실행)")
    a = ap.parse_args()

    rows = scan()
    print("=" * 82)
    print("중앙경마 category 오분류 소급 정정  %s" % ("[APPLY]" if a.apply else "[DRY-RUN · 아무것도 안 바꾼다]"))
    print("=" * 82)
    print("화이트리스트(app.py._JRA_TRACKS) %d개 재사용 · 대상 디렉터리 %s" % (len(JRA_TRACKS), TARGET_DIRS))
    print("\n🔴 정정 대상 **%d건**  (japan_central → japan_local)" % len(rows))
    byd = collections.Counter(r[0] for r in rows)
    byv = collections.Counter(r[2] for r in rows)
    byday = collections.Counter(r[1][:10] for r in rows)
    print("   디렉터리별: %s" % dict(byd))
    print("   경기장별  : %s" % dict(byv.most_common()))
    print("   날짜별(최근 10): %s" % dict(sorted(byday.items(), reverse=True)[:10]))
    print("\n⚠ 🔴 **회원 화면이 바뀐다** — 중앙경마 탭에서 위 %d건이 사라지고 지방 탭으로 간다." % len(rows))

    if not a.apply:
        print("\n⚠ DRY-RUN 이다. 승인 후 `--apply` 를 붙일 것.")
        return 0

    stamp = time.strftime("%Y%m%d_%H%M%S")
    bdir = os.path.join(BASE, "backups", "category_fix_" + stamp)
    n = 0
    for d, fn, v, sp in rows:
        src = os.path.join(BASE, "data", d, fn)
        dst = os.path.join(bdir, d)
        os.makedirs(dst, exist_ok=True)
        shutil.copy2(src, os.path.join(dst, fn))            # ⚠ 백업 먼저(원본 삭제 없음)
        try:
            doc = json.load(open(src, encoding="utf-8"))
            doc["category"] = "japan_local"
            doc.setdefault("category_fixed", []).append({
                "at": time.strftime("%Y-%m-%d %H:%M:%S"), "from": "japan_central",
                "to": "japan_local", "venue": v, "by": "fix_central_category.py"})
            tmp = src + ".tmp%d" % os.getpid()
            json.dump(doc, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            os.replace(tmp, src)
            n += 1
        except Exception as e:
            print("   실패 %s/%s: %s" % (d, fn, str(e)[:70]))
    print("\n✅ 정정 %d건 · 백업 %s" % (n, os.path.relpath(bdir, BASE)))
    print("⚠ 되돌리려면 백업 디렉터리에서 파일을 그대로 복사하면 된다(원본 삭제 없음).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
