# -*- coding: utf-8 -*-
"""경륜 **결정수(決まり手)** 가 시장 위에 정보를 더하는지 잰다 (읽기 전용).

🔴 왜 이걸 재나
  경마에는 `_corner_move_bonus`(첫코너→4코너 순위 변화 · +8/-5)가 있어 「막판에 올라오는 말」을
  실적으로 가리는데, **경륜에는 그게 없다.** 경륜은 `_keirin_style_bonus` 가
  결정수 **최대 하나로 라벨만** 붙이고 +3~5점을 준다 —
    · 비율을 버린다(差 51% ↔ 差 100% 가 같은 점수)
    · 시행 수를 버린다(3전 100% ↔ 30전 83% 가 같은 점수)
    · 경주득점 70~95 범위에서 ±5점은 사실상 무의미하다
  ⇒ 저장된 `kimarite`(원본 시행 횟수)를 **비율 + 표본 가중**으로 써서 정보량을 잰다.

🔴 설계는 record_score 측정과 같다(원칙 8 — 비교 가능한 값인가)
  시장 내재확률은 「1착 확률」이라 「1·2착 진입」과 직접 비교가 안 된다.
  ⇒ **시장순위를 고정해 그 안에서** 갈라 본다. 시장이 이미 아는 것은 층 안에서 상쇄된다.

⚠ 원칙 1 n<30 판정 불가 · 원칙 27 마감 전 마지막 정상 틱 · 원칙 30 무리별 보유율 병기.
"""
import os, io, sys, json, glob, math

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import measure_score_edge as E

# kimarite = [逃(도주), 捲(젖히기), 差(차입), マ(마크)]
_IDX = {"도주": 0, "젖히기": 1, "차입": 2, "마크": 3}


def collect(pattern="2026_08_*", mb_min=5):
    rows, st = [], {"파일": 0, "결과없음": 0, "배당없음": 0, "결정수없음": 0, "채택": 0}
    for f in sorted(glob.glob(os.path.join(ROOT, "data", "analysis_log", pattern + ".json"))):
        st["파일"] += 1
        d = E._load(f)
        if not isinstance(d, dict) or (d.get("sport") or "") != "cycle":
            continue
        res = d.get("result") or {}
        try:
            top2 = {int(res.get("1st")), int(res.get("2nd"))}
        except Exception:
            st["결과없음"] += 1
            continue
        if len(top2) < 2:
            st["결과없음"] += 1
            continue
        ent = (d.get("raw_profile") or {}).get("entries") or []
        km = {}
        for e in ent:
            try:
                no = int(e.get("no"))
            except Exception:
                continue
            k = e.get("kimarite")
            if isinstance(k, list) and len(k) == 4 and sum(k) > 0:
                km[no] = {"km": k, "n": sum(k), "rentai": e.get("rentai"),
                          "decl": e.get("declaredStyle"), "gear": e.get("gear"),
                          "chaku": e.get("chaku")}
        if len(km) < 4:
            st["결정수없음"] += 1
            continue
        base = os.path.basename(f)[:-5]
        od = E._load(os.path.join(ROOT, "data", "odds_history", base + ".json"))
        tk = None
        for t in E._ticks(od or {}):
            if not isinstance(t, dict) or any(t.get(k) for k in E._BAD):
                continue
            mb = t.get("minutes_before")
            if mb is None or mb < mb_min or not t.get("quinella"):
                continue
            if tk is None or float(t.get("t") or 0) >= tk[0]:
                tk = (float(t.get("t") or 0), t)
        if not tk:
            st["배당없음"] += 1
            continue
        mr = E._market_rank(E._qmap(tk[1].get("quinella")))
        if not mr:
            st["배당없음"] += 1
            continue
        st["채택"] += 1
        for no, v in km.items():
            if no not in mr:
                continue
            k, n = v["km"], v["n"]
            rows.append({
                "rk": base, "no": no, "mkt": mr[no], "hit": 1 if no in top2 else 0,
                "n": n,
                "sashi": 100.0 * k[2] / n,                 # 차입(差) = 막판 역전
                "sashi_mark": 100.0 * (k[2] + k[3]) / n,   # 차입+마크 = 추입 계열
                "nige": 100.0 * k[0] / n,                  # 도주(逃) = 선행
                "makuri": 100.0 * k[1] / n,                # 젖히기(捲)
                "rentai": v["rentai"], "decl": v["decl"],
            })
    return rows, st


def _wil(k, n):
    if n == 0:
        return (0.0, 0.0)
    z, p = 1.96, k / float(n)
    dd = 1 + z * z / n
    c = (p + z * z / (2 * n)) / dd
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / dd
    return (max(0.0, c - h), min(1.0, c + h))


def table(rows, field, title, minn=0):
    g0 = [r for r in rows if r["n"] >= minn]
    print("\n  === %s ===  (⚠ 분모 = 선수 단위 · 시행 %d전 이상)" % (title, minn))
    print("  %-10s %-14s %6s %6s %8s %-18s %7s" %
          ("시장순위", field, "명", "입상", "입상률", "95%CI", "시행중앙"))
    for lo, hi, lab in ((1, 1, "1위"), (2, 2, "2위"), (3, 3, "3위"),
                        (4, 5, "4~5위"), (6, 99, "6위+")):
        grp = [r for r in g0 if lo <= r["mkt"] <= hi]
        if len(grp) < 60:
            continue
        vals = sorted(r[field] for r in grp)
        q1, q3 = vals[len(vals) // 3], vals[2 * len(vals) // 3]
        for sel, sl in ((lambda r: r[field] >= q3, "상위 1/3"),
                        (lambda r: r[field] <= q1, "하위 1/3")):
            sub = [r for r in grp if sel(r)]
            n = len(sub)
            if n == 0:
                continue
            k = sum(r["hit"] for r in sub)
            ci = _wil(k, n)
            ns = sorted(r["n"] for r in sub)
            mark = "" if n >= 30 else "  ⚠판정불가"
            print("  %-10s %-14s %6d %6d %7.1f%% [%5.1f%%,%5.1f%%] %7d%s" %
                  (lab, sl, n, k, 100.0 * k / n, 100 * ci[0], 100 * ci[1],
                   ns[len(ns) // 2], mark))


def main():
    rows, st = collect(sys.argv[1] if len(sys.argv) > 1 else "2026_08_*")
    print("  " + " · ".join("%s %d" % (k, v) for k, v in st.items()) + " · 선수 %d명" % len(rows))
    ns = sorted(r["n"] for r in rows)
    print("  ⚠ 결정수 시행 수 중앙 %d전 · 하위25%% %d전" % (ns[len(ns) // 2], ns[len(ns) // 4]))
    for fld, ti in (("sashi", "차입(差) 비율 — 막판 역전"),
                    ("sashi_mark", "차입+마크 = 추입 계열"),
                    ("nige", "도주(逃) 비율 — 선행")):
        table(rows, fld, ti, minn=10)


if __name__ == "__main__":
    main()
