# -*- coding: utf-8 -*-
"""[경주 흐름 소급 수집 (2026-07-31)]

🔴 **시간에 민감하다.** 決まり手·착차·上り·S/B 는 결과 페이지에만 있고,
   개최가 끝나면 페이지가 내려가 **영원히 못 받는다.**

왜 소급이 필요한가:
  결과 백필은 `result` 가 이미 있는 경주를 **다시 조회하지 않는다.**
  흐름 파싱을 배선한 시점(2026-07-31 09:28) 이전에 결과가 들어온 경주는
  1·2·3착만 있고 흐름이 비어 있다 — 그 경주들만 따로 채운다.

⚠ `result` 의 기존 키(1st/2nd/3rd/payouts)는 **건드리지 않는다.** flow·winKimarite 만 추가.
⚠ 이미 flow 가 있으면 건너뛴다(멱등).
⚠ `--dry` 가 기본. 실제 기록은 `--apply`.

사용:
  python tools/backfill_flow.py                    # 오늘·미리보기
  python tools/backfill_flow.py --apply
  python tools/backfill_flow.py --date 2026-07-31 --apply
"""
import argparse
import glob
import json
import os
import re
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

_SRC = open(os.path.join(BASE, "app.py"), encoding="utf-8").read()


def _load_from_app():
    """app.py 를 **기동하지 않고** 필요한 파서·상수만 떼어 온다(서버 부작용 방지)."""
    import ast as _ast
    import html as _htmllib
    import urllib.request
    ns = {"re": re, "_htmllib": _htmllib, "json": json, "os": os, "time": time,
          "urllib": urllib, "print": print}
    for fn in ("_kstrip", "_keirin_table_rows", "_keirin_result_parse", "_track_norm"):
        a = _SRC.index("def %s(" % fn)
        b = _SRC.index("\ndef ", a + 5)
        try:
            exec(compile(_SRC[a:b], "<%s>" % fn, "exec"), ns)
        except Exception:
            pass
    for name in ("KEIRIN_JO", "_TRACK_GROUPS"):
        for n in _ast.walk(_ast.parse(_SRC)):
            if isinstance(n, _ast.Assign) and any(getattr(x, "id", None) == name for x in n.targets):
                ns[name] = _ast.literal_eval(n.value)
                break
    return ns


NS = _load_from_app()
JO_REV = {v: k for k, v in (NS.get("KEIRIN_JO") or {}).items()}
HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Accept": "*/*"}


def fetch_flow(jo, ymd, rno):
    import urllib.request
    url = ("https://www.oddspark.com/keirin/RaceKekka.do?joCode=%s&kaisaiBi=%s&raceNo=%s"
           % (jo, ymd, rno))
    raw = urllib.request.urlopen(urllib.request.Request(url, headers=HDR), timeout=20).read()
    try:
        htm = raw.decode("utf-8")
    except Exception:
        htm = raw.decode("shift_jis", "replace")
    if "開催情報がありません" in htm:
        return None, "개최정보 없음"
    p = NS["_keirin_result_parse"](htm)
    if not p.get("flow"):
        return None, "flow 파싱 0행(미게시 가능)"
    return p, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=time.strftime("%Y-%m-%d"))
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    ymd = a.date.replace("-", "")
    pat = os.path.join(BASE, "data", "analysis_log", a.date.replace("-", "_") + "_*.json")
    files = sorted(glob.glob(pat))
    todo, skipped = [], 0
    for f in files:
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        r = d.get("result") or {}
        if not r.get("1st"):
            continue                       # 결과 없음 → 대상 아님
        if r.get("flow"):
            skipped += 1                   # 이미 있음 → 멱등
            continue
        m = re.match(r"\d{4}_\d{2}_\d{2}_(.+?)_(\d+)경주", os.path.basename(f))
        if not m:
            continue
        venue, rno = m.group(1), m.group(2)
        jo = JO_REV.get(venue)
        if not jo:
            # 한자·별칭이면 표준키로 정규화해 다시 찾는다
            try:
                jo = JO_REV.get(NS["_track_norm"](venue))
            except Exception:
                jo = None
        todo.append({"path": f, "venue": venue, "rno": rno, "jo": jo})

    print("=" * 78)
    print("경주 흐름 소급 수집  %s" % ("🔴 --apply" if a.apply else "🟢 --dry(기본)"))
    print("=" * 78)
    print("⚠ 분모 = %s 분석 로그 %d건 · 결과보유·flow없음 %d건 · 이미보유 %d건"
          % (a.date, len(files), len(todo), skipped))
    done = fail = 0
    for t in todo:
        if not t["jo"]:
            print("  %-16s %s경주  joCode 미등록(경륜 아님 또는 미등록)" % (t["venue"], t["rno"]))
            fail += 1
            continue
        try:
            p, err = fetch_flow(t["jo"], ymd, t["rno"])
        except Exception as e:
            p, err = None, str(e)[:70]
        if not p:
            print("  %-16s %s경주  ❌ %s" % (t["venue"], t["rno"], err))
            fail += 1
            continue
        fl = p["flow"]
        print("  %-16s %s경주  ✅ 승부수=%-6s flow %d행  (1착 上り %s · S/B %s)"
              % (t["venue"], t["rno"], p.get("winKimarite") or "-", len(fl),
                 fl[0].get("lastLap"), fl[0].get("sb") or "-"))
        if a.apply:
            d = json.load(open(t["path"], encoding="utf-8"))
            d.setdefault("result", {})
            d["result"]["flow"] = fl                       # ⚠ 기존 키 무변경·추가만
            if p.get("winKimarite"):
                d["result"]["winKimarite"] = p["winKimarite"]
            d["result"]["flowBackfilledAt"] = time.strftime("%Y-%m-%d %H:%M:%S")
            tmp = t["path"] + ".tmp%d" % os.getpid()
            json.dump(d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            os.replace(tmp, t["path"])
            done += 1
    print()
    if a.apply:
        print("기록 %d건 · 실패 %d건" % (done, fail))
    else:
        print("🟢 --dry 이므로 파일을 건드리지 않았다. 기록하려면 --apply")
    return 0


if __name__ == "__main__":
    sys.exit(main())
