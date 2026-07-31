# -*- coding: utf-8 -*-
"""[경기장 단일 진실 · 1단계 (2026-07-31)] — 생성 + 대조만. **코드는 안 바꾼다.**

🔴 왜: 경기장 이름을 관리하는 테이블이 **8개**이고 각각 독립이다.
   하나를 고쳐도 나머지는 그대로다 — 오늘 이 유형으로 **네 번** 걸렸다.
   `平塚` 는 `_TRACK_GROUPS`(아침 수정)와 `KEIRIN_JO`(오후 수정) **두 곳에서** 빠져 있었다.

이 스크립트가 하는 일:
  ① 8개 소스에서 경기장 정보를 모아 `data/venues.json` 을 만든다
  ② **역생성**해서 기존 테이블과 대조한다 — 차이가 나오면 그 자체가 발견이다

⚠ **1단계는 읽기 전용이다.** 파일 하나를 만들 뿐 app.py 동작에 영향이 없다.
⚠ `verified` 에 확인 근거와 날짜를 남긴다. **추정값은 그렇게 표시**한다.

사용:
  python tools/build_venues.py            # 대조만(파일 미기록)
  python tools/build_venues.py --write
"""
import argparse
import ast
import glob
import json
import os
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "data", "venues.json")
SRC = open(os.path.join(BASE, "app.py"), encoding="utf-8").read()


def _const(name):
    for n in ast.walk(ast.parse(SRC)):
        if isinstance(n, ast.Assign) and any(getattr(x, "id", None) == name for x in n.targets):
            try:
                return ast.literal_eval(n.value)
            except Exception:
                return None
    return None


def _tool_const(path, name):
    try:
        s = open(os.path.join(BASE, path), encoding="utf-8").read()
        for n in ast.walk(ast.parse(s)):
            if isinstance(n, ast.Assign) and any(getattr(x, "id", None) == name for x in n.targets):
                return ast.literal_eval(n.value)
    except Exception:
        pass
    return None


def collect():
    """8개 소스 → {표준키: 레코드}. ⚠ 어디서 왔는지(`_sources`)를 반드시 남긴다."""
    groups = _const("_TRACK_GROUPS") or {}
    jo = _const("KEIRIN_JO") or {}
    nar = _const("NAR_NANKAN_BABA") or {}
    latlon = _tool_const("tools/build_amedas_map.py", "VENUE_LATLON") or {}
    wv = _const("WEATHER_VENUES") or {}
    try:
        amedas = json.load(open(os.path.join(BASE, "data", "amedas_map.json"), encoding="utf-8"))
    except Exception:
        amedas = {}
    # 스케줄(당일) · starters_store 는 **관측**이다 — 등록 소스가 아니라 커버리지 확인용.
    sched = {}
    try:
        import urllib.request
        d = json.loads(urllib.request.urlopen(
            "http://127.0.0.1:8011/api/multi/schedule", timeout=8).read().decode())
        for t in d.get("tracks") or []:
            if t.get("venue"):
                sched[t["venue"]] = {k: v for k, v in t.items() if k != "races"}
    except Exception:
        pass
    st_keys = set()
    try:
        db = json.load(open(os.path.join(BASE, "starters_store.json"), encoding="utf-8"))
        for k in db:
            st_keys.add(str(k).rsplit(" ", 1)[0])
    except Exception:
        pass

    out = {}
    keys = set(groups) | set(jo.values()) | set(nar.values()) | set(latlon) | set(wv) | set(amedas)
    for k in sorted(keys):
        rec = {"key": k, "names": {}, "codes": {}, "geo": None, "amedas": None,
               "sport": None, "_sources": [], "verified": {}}
        if k in groups:
            rec["names"]["aliases"] = list(groups[k])
            rec["_sources"].append("_TRACK_GROUPS")
        for code, name in jo.items():
            if name == k:
                rec["codes"]["joCode"] = code
                rec["sport"] = "cycle"
                rec["_sources"].append("KEIRIN_JO")
                rec["verified"]["joCode"] = "2026-07-31 RaceKekka <title> 확정"
        for code, name in nar.items():
            if name == k:
                rec["codes"]["narBaba"] = code
                rec["sport"] = "horse"
                rec["_sources"].append("NAR_NANKAN_BABA")
        if k in latlon:
            rec["geo"] = {"lat": latlon[k][0], "lon": latlon[k][1]}
            rec["_sources"].append("VENUE_LATLON")
            rec["verified"]["geo"] = "2026-07-31 아메다스 매핑 검증(전 경기장 20km 이내)"
        if k in amedas:
            rec["amedas"] = amedas[k]
            rec["_sources"].append("amedas_map")
        if k in wv:
            rec["_sources"].append("WEATHER_VENUES")
        if k in sched:
            rec["_sources"].append("schedule(today)")
            if sched[k].get("sport"):
                rec["sport"] = rec["sport"] or sched[k]["sport"]
        if k in st_keys:
            rec["_sources"].append("starters_store")
        out[k] = rec
    return out, {"groups": groups, "jo": jo, "nar": nar, "latlon": latlon,
                 "amedas": amedas, "sched": sched, "st_keys": st_keys}


def crosscheck(v, raw):
    """🔴 역생성 대조 — 차이가 나오면 그것이 발견이다."""
    issues = []
    for k, r in v.items():
        srcs = set(r["_sources"])
        if "KEIRIN_JO" in srcs and "_TRACK_GROUPS" not in srcs:
            issues.append(("표기 별칭 없음", k, "joCode 는 있는데 _TRACK_GROUPS 에 없다"))
        if r.get("sport") == "cycle" and "VENUE_LATLON" not in srcs:
            issues.append(("좌표 없음", k, "경륜장인데 좌표 미등록 → 바람 정보 불가"))
        if "VENUE_LATLON" in srcs and "amedas_map" not in srcs:
            issues.append(("아메다스 없음", k, "좌표는 있는데 관측지점 매핑이 없다"))
        if "schedule(today)" in srcs and not r["codes"]:
            issues.append(("코드 없음", k, "오늘 개최인데 joCode·narBaba 둘 다 없다"))
        if "starters_store" in srcs and "_TRACK_GROUPS" not in srcs:
            issues.append(("표기 별칭 없음", k, "전적은 쌓이는데 표준키 미등록"))
    # 스케줄에 있는데 venues 에 아예 없는 경우
    for nm in raw["sched"]:
        if nm not in v:
            issues.append(("venues 누락", nm, "오늘 개최인데 어느 테이블에도 없다"))
    return issues


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    v, raw = collect()
    print("=" * 78)
    print("경기장 단일 진실 · 1단계 (생성 + 대조)  %s"
          % ("🔴 --write" if a.write else "🟢 대조만(기본)"))
    print("=" * 78)
    print("⚠ 분모 = 8개 소스 합집합 %d곳" % len(v))
    cnt = {}
    for r in v.values():
        for s in r["_sources"]:
            cnt[s] = cnt.get(s, 0) + 1
    for s in sorted(cnt, key=lambda x: -cnt[x]):
        print("  %-22s %3d곳" % (s, cnt[s]))
    iss = crosscheck(v, raw)
    print("\n🔴 대조 결과 — 불일치 %d건 (차이가 곧 발견이다)" % len(iss))
    by = {}
    for t, k, why in iss:
        by.setdefault(t, []).append((k, why))
    for t in sorted(by, key=lambda x: -len(by[x])):
        print("\n  [%s] %d건" % (t, len(by[t])))
        for k, why in by[t][:12]:
            print("     %-12s %s" % (k, why))
        if len(by[t]) > 12:
            print("     … 외 %d곳" % (len(by[t]) - 12))
    if not a.write:
        print("\n🟢 대조만 했다. 파일을 만들려면 --write")
        return 0
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    doc = {"_생성": time.strftime("%Y-%m-%d %H:%M:%S"),
           "_설명": "경기장 단일 진실 1단계. 기존 8개 테이블에서 역생성했다. "
                    "⚠ 아직 app.py 는 이 파일을 읽지 않는다(1단계는 대조 전용).",
           "venues": v}
    tmp = OUT + ".tmp%d" % os.getpid()
    json.dump(doc, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, OUT)
    print("\n기록: %s (%d곳)" % (OUT, len(v)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
