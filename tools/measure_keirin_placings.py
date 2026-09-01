# -*- coding: utf-8 -*-
"""[읽기 전용] 경륜 착순(`keirinPlacings`)이 **시장 위에 정보를 더하는가**.

🔴 무엇을 재나
  `_elim_score` 는 `avg_place >= 5` 면 **제거 -30점**을 준다. 그런데 경륜은 `recentPlacings` 가
  0% 라 `avg_place` 가 늘 `None` 이고 **그 줄이 한 번도 안 걸린다**(2026-08-31 실측).
  2026-08-31 에 출마표 원문에서 착순을 뽑아 `keirinPlacings` 로 쌓기 시작했다.
  ⇒ 그 값을 `recentPlacings` 자리에 넣으면 **제거마 판정이 바뀐다**(실측 21.5% · 57경주).
  이 도구는 **켜기 전에** 그 판정이 옳은지 잰다.

🔴 어떻게 재나 (원칙 준수)
  · 원칙 16 — 파일 매칭에 **날짜를 포함**한다(analysis_log 파일명에서 파생).
  · 원칙 14 — 입상률만 보면 안 된다. **엣지 = 실측 ÷ 시장암시** 로 본다.
              시장이 이미 아는 것이면 엣지가 1.0 근처이고 **새 정보가 아니다.**
  · 원칙 1  — n<30 은 판정 불가. 명시한다.
  · 원칙 2  — 극단값에 흔들리는 지표가 아니므로(입상률) 1·3건 제외는 쓰지 않는다.
              대신 **Wilson 신뢰구간**을 병기한다.
  · 원칙 26 — 표본·정제·분모를 함께 적는다.

  시장암시(마별 1·2착 진입 확률):
    복승 배당에서 `p(i,j) = (1/odds) / Σ(1/odds)` 를 만들고 `P(i) = Σ_j p(i,j)`.
    Σ_i P(i) = 2 가 되므로 **1·2착에 드는 두 자리**를 정확히 배분한다.
  ⚠ 마감 직전(마감 전 마지막) 스냅샷을 쓴다 — 오염 틱은 건너뛴다.

⚠ 재현 못 한 것(정직하게)
  · `_final_picks` 전체를 재현하지 않는다. **제거 -30점의 옳고 그름**만 잰다.
    회수율 판정은 그 뒤 별도(`measure_recovery`)다.

실행: python tools/measure_keirin_placings.py
"""
import io
import os
import sys
import json
import gzip
import glob
import math

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 🔴 판정선·정제 기준은 measure_recovery 것을 그대로 쓴다(규칙을 두 곳에 두지 않는다).
try:
    import importlib.util
    _sp = importlib.util.spec_from_file_location(
        "mr", os.path.join(BASE, "tools", "measure_recovery.py"))
    _mr = importlib.util.module_from_spec(_sp)
    _sp.loader.exec_module(_mr)
    CLEAN_LO, CLEAN_HI = _mr.CLEAN_LO, _mr.CLEAN_HI
except Exception as _e:                                    # 원칙 19 — 조용히 다른 값을 쓰지 않는다
    print("🔴 measure_recovery 를 못 읽었다 — 기준이 갈릴 수 있어 중단한다: %s" % str(_e)[:80])
    raise SystemExit(1)

ELIM_AVG = 5.0          # `_elim_score` 의 문턱 — 🔴 여기서 임의로 바꾸지 않는다
BAD_TICK = ("odds_suspect", "baseline_reset", "next_race_blocked")


def _load(p):
    for path, gz in ((p, False), (p + ".gz", True)):
        try:
            if gz:
                with gzip.open(path, "rt", encoding="utf-8") as f:
                    return json.load(f)
            with io.open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            continue
    return None


def _pair(k):
    for sep in ("+", "-", "_"):
        if sep in str(k):
            a, b = str(k).split(sep)[:2]
            try:
                return tuple(sorted((int(a), int(b))))
            except ValueError:
                return None
    return None


def _qmap(sn):
    q = (sn or {}).get("quinella")
    if isinstance(q, list):
        q = {"+".join(str(z) for z in (it.get("combo") or [])): it.get("odds")
             for it in q if isinstance(it, dict)}
    out = {}
    for k, v in (q or {}).items():
        t = _pair(k)
        if not t:
            continue
        try:
            o = float(v.get("odds") if isinstance(v, dict) else v)
        except (TypeError, ValueError):
            continue
        if o > 0:
            out[t] = o
    return out


def _market_top2(qm):
    """복승 배당 → 마별 **1·2착 진입 시장암시확률**. Σ = 2 가 되도록 정규화."""
    inv = {c: 1.0 / o for c, o in qm.items() if o > 0}
    s = sum(inv.values())
    if s <= 0:
        return {}
    out = {}
    for (a, b), v in inv.items():
        p = v / s
        out[a] = out.get(a, 0.0) + p
        out[b] = out.get(b, 0.0) + p
    return out


def _wilson(k, n, z=1.96):
    if not n:
        return (0.0, 0.0)
    p = k / float(n)
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - r) / d, (c + r) / d)


def rows(pattern="2026_0*"):
    out = []
    for f in sorted(glob.glob(os.path.join(BASE, "data", "analysis_log", pattern + ".json"))):
        name = os.path.basename(f)[:-5]
        d = _load(f)
        if not isinstance(d, dict) or d.get("sport") != "cycle":
            continue
        hs = [h for h in (d.get("horses") or []) if h.get("keirinPlacings")]
        if not hs:
            continue
        rr = _load(os.path.join(BASE, "data", "race_results", name + ".json"))   # 🔴 날짜 포함
        if not isinstance(rr, dict):
            continue
        r = rr.get("result") or {}
        try:
            top2 = {int(r["1st"]), int(r["2nd"])}
        except (TypeError, ValueError, KeyError):
            continue
        oh = _load(os.path.join(BASE, "data", "odds_history", name + ".json")) or {}
        sn = [s for s in (oh.get("snapshots") or [])
              if s.get("quinella") and not any(s.get(b) for b in BAD_TICK)
              and not s.get("after_close")]
        qm = _qmap(sn[-1] if sn else {})
        mk = _market_top2(qm)
        if not mk:
            continue
        out.append({"rk": name, "horses": hs, "top2": top2, "mk": mk})
    return out


def main():
    rs = rows()
    nh = sum(len(r["horses"]) for r in rs)
    print("▣ 경륜 착순(keirinPlacings) — 제거 -30점이 옳은가")
    print("   ⚠ 표본: 경륜 · keirinPlacings 보유 · 결과 확정 · 마감전 배당 보유 **%d경주 / %d명**"
          % (len(rs), nh))
    print("   🔴 문턱은 `_elim_score` 의 avg_place >= %.0f 를 **그대로** 쓴다\n" % ELIM_AVG)
    if len(rs) < 10:
        print("   ⚠ 경주 %d개 — 아직 볼 것이 없다." % len(rs))
        return

    grp = {"pen": [], "ok": []}
    for r in rs:
        for h in r["horses"]:
            ps = [int(x) for x in (h.get("keirinPlacings") or []) if isinstance(x, (int, float))]
            if not ps:
                continue
            no = h.get("no")
            try:
                no = int(no)
            except (TypeError, ValueError):
                continue
            m = r["mk"].get(no)
            if m is None:
                continue
            avg = sum(ps) / float(len(ps))
            grp["pen" if avg >= ELIM_AVG else "ok"].append(
                (1 if no in r["top2"] else 0, m, avg, len(ps)))

    print("   %-22s %5s %5s %8s %8s %8s   %s" %
          ("구분", "n", "입상", "입상률", "시장암시", "엣지", "입상률 95%CI"))
    for k, lbl in (("pen", "🔴 평균 %.0f착↑(-30 대상)" % ELIM_AVG), ("ok", "🟢 평균 %.0f착 미만" % ELIM_AVG)):
        v = grp[k]
        n = len(v)
        if not n:
            print("   %-22s      — 표본 없음" % lbl)
            continue
        hit = sum(x[0] for x in v)
        rate = hit / float(n)
        mkt = sum(x[1] for x in v) / float(n)
        edge = (rate / mkt) if mkt else 0.0
        lo, hi = _wilson(hit, n)
        flag = "" if n >= 30 else "  ⚠ n<30 판정불가"
        print("   %-22s %5d %5d %7.1f%% %7.1f%% %8.3f   [%.1f%%, %.1f%%]%s"
              % (lbl, n, hit, 100 * rate, 100 * mkt, edge, 100 * lo, 100 * hi, flag))

    pen, ok = grp["pen"], grp["ok"]
    print()
    if pen and ok:
        print("   ⇒ 입상률 차이 %.1f%%p (%.1f%% ↔ %.1f%%)"
              % (100 * (sum(x[0] for x in ok) / len(ok) - sum(x[0] for x in pen) / len(pen)),
                 100 * sum(x[0] for x in ok) / len(ok), 100 * sum(x[0] for x in pen) / len(pen)))
        print("   🔴 **엣지가 1.0 근처면 시장이 이미 아는 것**이라 새 정보가 아니다(원칙 14).")
        print("      제거 -30점의 근거가 되려면 대상군 엣지가 **1.0보다 뚜렷이 낮아야** 한다.")
    print()
    print("   ⚠ 이 도구는 **제거 -30점의 옳고 그름**만 잰다. `_final_picks` 를 재현하지 않는다.")
    print("      회수율 판정은 표본이 쌓인 뒤 `measure_recovery` 로 따로 한다.")


if __name__ == "__main__":
    main()
