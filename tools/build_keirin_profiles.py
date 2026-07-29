# -*- coding: utf-8 -*-
"""[적중왕전개 Phase 0] 경륜 시뮬레이션 프로파일 누적 — data/simulation_db/keirin_profiles.jsonl

목적: 전개(페이스·라인) × 결정수(각질) 가설을 **나중에** 검증할 수 있도록 원본을 append-only 로 남긴다.
      추천 로직(_final_picks·EV 필터·_scenario_plan)은 **일절 건드리지 않는다** — 누적 전용.

⚠ 이 스크립트는 **읽기 전용 소비자**다. `data/analysis_log/` + `data/race_results/` + (있으면)
  `raw_profile` 만 읽어 JSONL 한 줄을 만든다. 운영 데이터는 수정하지 않는다.

⚠ 백필 한계(권대표 지시로 명시): 과거 로그에는 `raw_profile` 이 없어 **원본 복원이 불가능**하다.
  `corners` · `field_sizes` · `kimarite_n` · `lines` 는 **null** 로 남고 `backfilled: true` 로 표시한다.
  오늘(2026-07-29) 배선 이후 생성분부터 원본이 채워진다.

사용:
  python tools/build_keirin_profiles.py            # 전체 스캔(중복 자동 스킵)
  python tools/build_keirin_profiles.py --date 2026-07-29
  python tools/build_keirin_profiles.py --stats    # 누적 현황만 출력
"""
import json, glob, os, sys, argparse, collections

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(BASE, "data", "analysis_log")
RES = os.path.join(BASE, "data", "race_results")
OUT_DIR = os.path.join(BASE, "data", "simulation_db")
OUT = os.path.join(OUT_DIR, "keirin_profiles.jsonl")
SCHEMA_VERSION = 1
CYCLE_SPORTS = ("cycle", "boat", "bike")


def load(p):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return None


def _race_no_of(s):
    """'이토 6경주' / '2026_07_29_이토_6경주' → 6. 못 읽으면 None."""
    import re
    m = re.search(r"(\d{1,2})\s*경주", str(s or ""))
    if not m:
        m = re.search(r"_(\d{1,2})R?(?:\.json)?$", str(s or ""))
    try:
        return int(m.group(1)) if m else None
    except (TypeError, ValueError):
        return None


def _parse_line_pairs(pairs, riders_by_no):
    """`keirinLinePairs` 의 label 문자열을 구조화 — 파싱 없이 바로 쓰도록.
    예: {"combo":[1,3],"label":"라인 페어(1번 젖히기형 + 3번 마크)"} → lead/mark/lead_style 분해."""
    out = []
    for p in (pairs or []):
        cb = p.get("combo") or []
        if len(cb) != 2:
            continue
        lead, mark = cb[0], cb[1]          # 생성 규칙상 [선두, 2번수]
        out.append({"combo": [lead, mark], "lead": lead, "mark": mark,
                    "lead_style": (riders_by_no.get(lead) or {}).get("styleType"),
                    "mark_style": (riders_by_no.get(mark) or {}).get("styleType"),
                    "label": p.get("label")})
    return out


def build_row(log_path):
    d = load(log_path)
    if not d:
        return None
    sport = (d.get("sport") or "").lower()
    if sport not in CYCLE_SPORTS:
        return None
    fn = os.path.basename(log_path)
    cp = d.get("corePicks") or {}
    pa = cp.get("paceAnalysis") or {}
    raw = d.get("raw_profile") or {}
    entries = {e.get("no"): e for e in (raw.get("entries") or []) if e.get("no") is not None}

    rd = load(os.path.join(RES, fn)) or {}
    r = rd.get("result") or d.get("result") or {}
    try:
        top3 = [int(r[k]) for k in ("1st", "2nd", "3rd")]
    except (TypeError, ValueError, KeyError):
        top3 = None
    payouts = rd.get("payouts") or {}

    # 선수 프로파일 — raw_profile(원본) 우선, 없으면 로그의 가공값만
    riders = []
    for h in (d.get("horses") or []):
        no = h.get("no")
        if no is None:
            continue
        e = entries.get(no) or {}
        riders.append({
            "no": no, "name": h.get("name"),
            "style_type": e.get("styleType"),
            "kimarite_ratio": e.get("kimariteRatio"),
            "kimarite_n": e.get("kimarite"),          # ⚠ 백필분은 null(원본 미보존)
            "chaku": e.get("chaku"), "rentai": e.get("rentai"),
            "class_grade": e.get("classGrade"), "gear": e.get("gear"),
            "comp_score": h.get("record_score"), "grade": h.get("grade"),
            "odds": h.get("odds"),
        })
    riders_by_no = {x["no"]: {"styleType": x["style_type"]} for x in riders}

    gl = pa.get("gaitLists") or {}
    field_size = raw.get("fieldSize") or len(riders) or None
    lines = raw.get("line") or None                    # ⚠ 백필분은 null

    # 시장 기준선 — 엣지(실측÷시장암시) 계산용. 사후 조인 없이 같은 행에 둔다.
    market = {}
    qs = [q.get("odds") for q in (cp.get("finalQuinellas") or []) if q.get("odds")]
    if qs:
        market["rec_min_quinella"] = min(float(x) for x in qs)
    fq = (cp.get("finalQuinellas") or [])
    if fq and fq[0].get("combo"):
        market["rec_top_combo"] = fq[0]["combo"]

    return {
        "schema_version": SCHEMA_VERSION,
        "backfilled": not bool(raw),                   # raw_profile 없으면 소급 생성분
        "race_key": d.get("raceKey") or fn,
        "race_id": d.get("race_id") or os.path.splitext(fn)[0],
        "date": (d.get("date") or fn[:10]).replace("_", "-"),
        "venue": (fn.split("_")[3] if len(fn.split("_")) > 3 else None),
        "race_no": _race_no_of(d.get("race") or fn),      # "이토 6경주" → 6 (차원 축은 숫자여야 함)
        "sport": sport,
        "field_size": field_size,
        "pace": pa.get("pace"),
        "pace_counts": pa.get("counts"),
        "gait_lists": gl or None,
        "lead_count": (pa.get("counts") or {}).get("선행"),
        "lines": lines,
        "line_pairs": _parse_line_pairs(cp.get("keirinLinePairs"), riders_by_no),
        "venue_tendency": raw.get("tendency"),
        "riders": riders,
        "result": ({"top3": top3,
                    "payout_quinella": payouts.get("quinella"),
                    "payout_trio": payouts.get("trifecta")} if top3 else None),
        "market": market or None,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD 또는 YYYY_MM_DD (해당 날짜만)")
    ap.add_argument("--stats", action="store_true", help="누적 현황만 출력")
    a = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    existing = {}
    if os.path.exists(OUT):
        for ln in open(OUT, encoding="utf-8"):
            try:
                o = json.loads(ln)
                existing[o.get("race_id")] = o
            except Exception:
                continue

    if a.stats:
        print("누적 %d행 (%s)" % (len(existing), OUT))
        bf = sum(1 for o in existing.values() if o.get("backfilled"))
        print("  백필 %d · 전진누적 %d" % (bf, len(existing) - bf))
        print("  페이스 有 %d · 결과 有 %d · 라인원본 有 %d · 결정수원본 有 %d"
              % (sum(1 for o in existing.values() if o.get("pace")),
                 sum(1 for o in existing.values() if o.get("result")),
                 sum(1 for o in existing.values() if o.get("lines")),
                 sum(1 for o in existing.values()
                     if any(r.get("kimarite_n") for r in (o.get("riders") or [])))))
        c = collections.Counter((o.get("pace"), o.get("field_size")) for o in existing.values())
        ge30 = sum(1 for v in c.values() if v >= 30)
        print("  페이스×두수 셀 %d · 30건+ %d셀" % (len(c), ge30))
        return

    pat = "*.json"
    if a.date:
        pat = a.date.replace("-", "_") + "_*.json"
    added = skipped = 0
    with open(OUT, "a", encoding="utf-8") as f:
        for p in sorted(glob.glob(os.path.join(LOG, pat))):
            row = build_row(p)
            if not row:
                continue
            rid = row["race_id"]
            old = existing.get(rid)
            # 이미 있고, 새 행이 더 낫지 않으면(원본 없음) 스킵 — 백필→전진누적 승격은 허용
            if old and not (old.get("backfilled") and not row.get("backfilled")):
                skipped += 1
                continue
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            existing[rid] = row
            added += 1
    print("경륜 프로파일 누적: 추가 %d · 스킵(기존) %d · 총 %d행" % (added, skipped, len(existing)))
    print("→", OUT)


if __name__ == "__main__":
    main()
