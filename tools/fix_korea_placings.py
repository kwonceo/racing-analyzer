# -*- coding: utf-8 -*-
"""[소급 정정] 이미 저장된 prerace 의 recentPlacings 를 pastRaces 에서 다시 만든다.

2026-08-16. app.py 의 `_korea_fix_placings` 와 **같은 규칙**을 쓴다(런타임에서 import).

🔴 `--dry` 가 기본이다. `--apply` 를 줘야 파일을 고친다.
🔴 원본은 `recentPlacingsRaw` 에 남기고 백업도 뜬다. 지우지 않는다.
⚠ pastRaces 가 비어 있으면 손대지 않는다.
"""
import argparse
import glob
import importlib.util
import json
import os
import shutil
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_app():
    """app.py 의 규칙을 그대로 쓴다 — 규칙을 두 곳에 두지 않는다."""
    os.environ.pop("SERVER_SOFTWARE", None)
    sp = importlib.util.spec_from_file_location("_appmod", os.path.join(BASE, "app.py"))
    m = importlib.util.module_from_spec(sp)
    sys.modules["_appmod"] = m
    sp.loader.exec_module(m)
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--pattern", default="2026-*")
    a = ap.parse_args()
    try:
        app = _load_app()
        fix = app._korea_fix_placings
    except Exception as e:
        print("app.py 로드 실패:", str(e)[:200])
        return
    files = sorted(glob.glob(os.path.join(BASE, "data", "prerace", a.pattern + ".json")))
    bk = os.path.join(BASE, "backups", "korea_placings_%s" % time.strftime("%Y%m%d_%H%M%S"))
    tot_f, tot_h, changed = 0, 0, []
    for f in files:
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        hs = d.get("horses") or []
        if not hs:
            continue
        tot_f += 1
        n = fix(hs)
        if not n:
            continue
        tot_h += n
        changed.append((os.path.basename(f), n))
        if a.apply:
            os.makedirs(bk, exist_ok=True)
            shutil.copy2(f, os.path.join(bk, os.path.basename(f)))
            tmp = f + ".tmp%d" % os.getpid()
            with open(tmp, "w", encoding="utf-8") as fp:
                json.dump(d, fp, ensure_ascii=False, indent=1)
            os.replace(tmp, f)
    print("prerace 파일 %d개 · 정정 대상 %d개 · 고칠 말 %d마리" % (tot_f, len(changed), tot_h))
    for nm, n in changed[:20]:
        print("   %-28s %d마리" % (nm.replace(".json", ""), n))
    if a.apply:
        print("🟢 적용 완료. 백업 %s" % bk)
    else:
        print("⏸ --dry 였다. 실제로 고치려면 --apply 를 준다.")


if __name__ == "__main__":
    main()
