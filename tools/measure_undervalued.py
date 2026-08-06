# -*- coding: utf-8 -*-
"""저평가 발굴 — 배당을 입력에서 뺀 '조건 순위'와 시장 인기의 괴리를 측정한다.

🔴 대표 지시(2026-08-06): "저평가된 말을 찾는 게 절실하다. 매일 저배당 최저배당을 추천하고 있다."
🔴 원인은 확정돼 있다 — keyHorses 가 배당 복사본이다(7/31 실측 우리 54.9% ↔ 시장 상위3두 52.9%).

원칙(CLAUDE.md)
  · 🔴 **배당·인기를 조건 축 입력에 한 줄도 넣지 않는다.** 넣으면 같은 실패가 반복된다.
    인기는 오직 **대조군(시장 순위)** 으로만 쓴다.
  · 🔴 한 점수로 합치지 않는다. 축별 순위를 각각 두고 교차한다
    (record_score 0.0 이 566회 포화된 전례 — 합치면 하위권이 뭉개진다).
  · 🔴 `keyHorses` 를 바꾸지 않는다. 완전 독립·읽기 전용이고 추천·학습에 개입하지 않는다.
  · 분모를 항상 밝히고, n<30 은 판정 불가로 표시한다. 상위 1·3건 제외를 병기한다.

소스 = NAR 원문(keiba.go.jp DebaTable). 🔴 여기에만 距(당거리)·場(당경마장)·最高タイム 이 있다.
  ⚠ oddspark 출주표에는 全/場/他/重 뿐이고 **距 칸이 없다**(2026-08-06 실측).

사용
  python tools/measure_undervalued.py --days 20260803,20260804,20260805,20260806
  python tools/measure_undervalued.py --race 門別:4 --show      한 경주의 축별 순위를 눈으로 본다
"""
import os
import re
import io
import sys
import json
import time
import argparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _clean(html):
    """태그를 파이프로 바꾸고 공백을 정규화. 숫자·문자는 하나도 바꾸지 않는다."""
    t = html.replace("&nbsp;", " ")
    t = re.sub(r"<[^>]+>", "|", t)
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"[\| ]*\|[\| ]*", " | ", t)
    return t


_REC = r"%s\s*\|?\s*(\d+)\s*-\s*\|?\s*(\d+)\s*-\s*\|?\s*(\d+)\s*-\s*\|?\s*(\d+)"


def _rec(txt, lab):
    """'全 0- 3- 2- 18' → (1착,2착,3착,착외). 없으면 None."""
    m = re.search(_REC % lab, txt)
    return tuple(int(x) for x in m.groups()) if m else None


def parse_race(html):
    """NAR 출주표 원문 → {distance, horses:[{no,name,pop,winOdds,all,place,dist,best,weight}]}.
    ⚠ 배당·인기도 함께 뽑지만 **조건 축에는 쓰지 않는다**(대조군 전용)."""
    out = {"distance": None, "horses": []}
    head = _clean(html[:html.find('class="horseNum"')] if 'class="horseNum"' in html else html[:6000])
    md = re.search(r"(\d{3,4})\s*[ｍm]", head)
    if md:
        out["distance"] = int(md.group(1))
    pos = [m.start() for m in re.finditer(r'class="horseNum"', html)]
    if not pos:
        return out
    pos2 = pos + [len(html)]
    for i in range(len(pos)):
        blk = _clean(html[pos2[i]:pos2[i + 1]])
        mno = re.search(r"horseNum\"?>?\s*\|?\s*(\d+)", blk)
        if not mno:
            continue
        no = int(mno.group(1))
        mnm = re.search(r"\|\s*([ァ-ヶー]{2,})\s*\|", blk)
        mpop = re.search(r"\((\d+)\s*人気\)", blk)
        mod = re.search(r"\|\s*(\d+\.\d)\s*\|.{0,30}?人気", blk)
        mbest = re.search(r"(\d:\d{2}\.\d)", blk)
        mwt = re.search(r"\|\s*(\d{2}\.\d)\s+\d+-\d+-\d+-\d+", blk)
        out["horses"].append({
            "no": no,
            "name": mnm.group(1) if mnm else "",
            "pop": int(mpop.group(1)) if mpop else None,
            "winOdds": float(mod.group(1)) if mod else None,   # 대조군 전용
            "all": _rec(blk, "全"), "place": _rec(blk, "場"), "dist": _rec(blk, "距"),
            "best": mbest.group(1) if mbest else None,
            "weight": float(mwt.group(1)) if mwt else None,
        })
    return out


# ── 축(배당 미사용) ────────────────────────────────────────────────────────
#   🔴 각 축은 **값과 표본수를 함께** 낸다. 표본이 얇으면 순위에서 뺀다(추측으로 채우지 않는다).
def _rate(rec, kind):
    """rec=(1착,2착,3착,착외) → (값, 표본수).
    kind='ren' 연대율 · 'in3' 3착률 · 🔴 'win' **승수 절대값**(비율이 아니다).

    🔴 [2026-08-06] 축을 승수로도 두는 이유 — 대표 실증 4건이 전부 **승수·1착 경험**이었다.
      몬베츠 4R 9번: 당거리 63전 5승 → **승수 1위**인데 연대율은 (5+1)/63 = 9.5% 로 **하위**다.
      연대율만 보면 이 말을 못 잡는다. 대표가 보는 것은 '이 거리에서 이겨 본 적이 있는가'다.
    ⚠ 승수는 출주 경험이 많으면 자연히 는다 = '베테랑'을 뽑는 축이다. 시장은 최근 성적을 보므로
      과거 승리 경험을 저평가할 수 있다 — 그래서 괴리가 생길 자리다. 다만 그것이 실제로
      3착에 유리한지는 **측정으로만** 말한다."""
    if not rec:
        return None, 0
    tot = sum(rec)
    if tot < 1:
        return None, 0
    if kind == "win":
        return float(rec[0]), tot          # 승수 — 표본수 조건을 걸지 않는다(1승도 정보다)
    v = (rec[0] + rec[1]) if kind == "ren" else (rec[0] + rec[1] + rec[2])
    return v / float(tot), tot


AXES = [
    ("距승", "dist", "win", "당거리 승수"),
    ("距연", "dist", "ren", "당거리 연대율"),
    ("場승", "place", "win", "당경마장 승수"),
    ("場연", "place", "ren", "당경마장 연대율"),
    ("全연", "all", "ren", "통산 연대율"),
]
MIN_N = 5          # 🔴 비율 축은 그 조건 출주가 5전 미만이면 뺀다(비율이 요동친다). 승수 축은 예외.


def _time_sec(s):
    try:
        m, rest = s.split(":")
        return int(m) * 60 + float(rest)
    except Exception:
        return None


def _dense_rank(vals):
    """[(마번, 값, ...)] → {마번: 순위}. 🔴 **동점은 공동 순위**(공동 3위 다음은 5위).

    🔴 [2026-08-06 대표 지시] 동점 처리를 명세에 못 박는다.
      종전 코드는 `sort(key=(-값, -전수))` 로 **동점이면 전수 많은 쪽을 위로** 놓았다.
      대표가 손으로 낸 계산(+4)과 도구(+3)가 달랐던 유력 원인이 이것이다.
    ⇒ 공동 순위를 택한 이유: ⓐ동점을 인위적으로 가르지 않는다
      ⓑ순위가 앞당겨지지 않아 괴리가 **작게** 나온다(과대 지목 방지) ⓒ재현 가능하다.
    ⚠ 2차 기준 '전수 적은 쪽'은 표본이 얇다는 뜻이라 오히려 불리할 수 있어 쓰지 않는다."""
    out, prev, rank = {}, None, 0
    for i, item in enumerate(vals):
        no, v = item[0], item[1]
        if prev is None or v != prev:
            rank = i + 1                      # 건너뛰기 방식: 공동 3위가 둘이면 다음은 5위
            prev = v
        out[no] = rank
    return out


def axis_ranks(race):
    """축별 순위 → {축이름: {마번: 순위}}. 🔴 합치지 않는다. 판정 불가 말은 그 축에서 뺀다."""
    hs = race["horses"]
    out = {}
    for lab, key, kind, _t in AXES:
        vals = []
        for h in hs:
            r, n = _rate(h.get(key), kind)
            if r is None:
                continue
            if kind != "win" and n < MIN_N:
                continue                      # 비율 축만 표본 조건. 승수는 0승도 정보다
            if kind == "win" and r <= 0:
                continue                      # 🔴 그 조건에서 이겨 본 적 없는 말은 이 축에서 뺀다
            vals.append((h["no"], r, n))
        vals.sort(key=lambda x: -x[1])
        out[lab] = _dense_rank(vals)
    # 최고타임 — 빠를수록 상위. ⚠ 거리 조건이 다르면 비교 불가이나 원문 최고타임은
    #   그 말의 대표 기록이라 같은 경주 안 비교로만 쓴다(절대값을 인용하지 않는다).
    tv = [(h["no"], _time_sec(h["best"])) for h in hs if h.get("best")]
    tv = [(n, s) for n, s in tv if s]
    tv.sort(key=lambda x: x[1])
    out["타임"] = _dense_rank(tv)          # 동점 공동 순위(위 _dense_rank 주석 참조)
    # 부담중량 — 가벼울수록 상위(감량 기수 포함). 같은 값이면 동순위로 두지 않고 마번순.
    wv = [(h["no"], h["weight"]) for h in hs if h.get("weight")]
    wv.sort(key=lambda x: x[1])
    out["부담"] = _dense_rank(wv)
    return out


def market_rank(race):
    """시장 순위 = 인기. 🔴 이것은 대조군이고 조건 축에 절대 넣지 않는다."""
    hs = [h for h in race["horses"] if h.get("pop")]
    hs.sort(key=lambda h: h["pop"])
    return _dense_rank([(h["no"], h["pop"]) for h in hs])


def market_rank_from_odds(path):
    """배당 이력 → (시장 순위, 3착 집합). 인기가 원문에 없을 때 쓴다.

    🔴 [2026-08-06] 원문의 人気 칸은 **발주 전 수집분에서 비어 있다**(후나바시 06:00 수집분 전부).
      전수 측정에서 '인기없음 52경주'로 대부분이 빠져 분모가 0이 됐다.
      ⇒ 말 단위 시장 평가를 **그 말이 낀 최저 복승 배당**으로 대용한다.
        이 방식은 이미 검증된 선례다(`_repx` · pop_baseline 산출에서 같은 대용을 썼다).
    ⚠ 마감 후·오염 스냅샷은 건너뛴다. 마감 전 마지막 정상 틱을 쓴다."""
    try:
        d = json.load(io.open(path, encoding="utf-8"))
    except Exception:
        return {}, set()
    snap = None
    for s in reversed(d.get("snapshots") or []):
        if s.get("after_close") or s.get("odds_suspect") or s.get("baseline_reset"):
            continue
        if isinstance(s.get("quinella"), dict) and len(s["quinella"]) >= 6:
            snap = s
            break
    top3 = set()
    r = d.get("result") or {}
    for k in ("1st", "2nd", "3rd"):
        if r.get(k) is not None:
            try:
                top3.add(int(r[k]))
            except Exception:
                pass
    if not snap:
        return {}, top3
    best = {}
    for combo, od in snap["quinella"].items():
        try:
            a1, b1 = [int(x) for x in str(combo).split("+")]
            v = float(od)
        except Exception:
            continue
        if v <= 0:
            continue
        for no in (a1, b1):
            if no not in best or v < best[no]:
                best[no] = v
    order = sorted(best.items(), key=lambda x: x[1])
    return _dense_rank(order), top3


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", default="20260803,20260804,20260805,20260806")
    ap.add_argument("--race", default="")
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args()

    import build_form_brief as B
    import track_key as tk

    rows = []          # (축, 괴리, 3착여부, 두수, 인기)
    races = 0
    skipped = {"결과없음": 0, "인기없음": 0, "두수부족": 0}
    for day in [d for d in a.days.split(",") if d.strip()]:
        idx = B.index_raw(day)
        for key in sorted(idx):
            rec = idx[key]
            if rec["kind"] != "nar":
                continue
            if a.race:
                v, _, n = a.race.partition(":")
                if key != (v, int(n)):
                    continue
            race = parse_race(rec["html"])
            if len(race["horses"]) < 5:
                skipped["두수부족"] += 1
                continue
            std = tk.track_key(key[0]) or key[0]
            stem = "%s_%s_%s_%s_%s경주.json" % (day[:4], day[4:6], day[6:8], std, key[1])
            rp = os.path.join(ROOT, "data", "race_results", stem)
            op = os.path.join(ROOT, "data", "odds_history", stem)
            top3 = set()
            try:
                res = json.load(io.open(rp, encoding="utf-8")) if os.path.exists(rp) else {}
                for k in ("1st", "2nd", "3rd"):
                    if res.get(k) is not None:
                        top3.add(int(res[k]))
            except Exception:
                top3 = set()
            mr = market_rank(race)                 # ① 원문 人気 (있으면 가장 정확)
            if len(mr) < 5 and os.path.exists(op):  # ② 없으면 배당 이력으로 대용
                mr, t2 = market_rank_from_odds(op)
                if not top3:
                    top3 = t2
            if len(mr) < 5:
                skipped["인기없음"] += 1
                continue
            if len(top3) < 3 and not a.show:
                skipped["결과없음"] += 1      # ⚠ --show 는 진단용이라 결과가 없어도 보여준다
                continue
            races += 1
            ar = axis_ranks(race)
            nh = len(race["horses"])
            if a.show:
                print("\n=== %s %sR · %d두 · %sm ===" % (key[0], key[1], nh, race["distance"]))
                print("  결과 3착 이내:", sorted(top3))
                for lab in list(ar):
                    top = sorted(ar[lab].items(), key=lambda x: x[1])[:4]
                    print("  %-4s 상위: %s" % (lab, " ".join("%d위%d번" % (r, n) for n, r in top)))
                print("  시장 인기 :", " ".join("%d인기%d번" % (r, n)
                                              for n, r in sorted(mr.items(), key=lambda x: x[1])[:5]))
            for lab, ranks in ar.items():
                for no, r in ranks.items():
                    if no not in mr:
                        continue
                    rows.append((lab, mr[no] - r, no in top3, nh, mr[no]))

    if a.show:
        return 0

    print("=" * 74)
    print("저평가 발굴 — 조건 순위 ↔ 시장 인기 괴리 (NAR · %d경주)" % races)
    print("⚠ 분모: 축별로 '그 축을 판정할 수 있는 말'만. 축마다 분모가 다르다.")
    print("⚠ 제외:", skipped)
    print("🔴 조건 축에 배당·인기를 넣지 않았다. 인기는 대조군으로만 썼다.")
    print("=" * 74)

    # 기저선 — 이 표본의 인기별 3착률(자체 산출). 🔴 JRA pop_baseline 을 NAR 에 쓰지 않는다.
    base = {}
    for lab, _g, hit, _nh, pop in rows:
        if lab != "全연":
            continue
        b = base.setdefault(pop, [0, 0])
        b[0] += 1
        b[1] += 1 if hit else 0
    print("\n[인기별 기저선 · 이 표본 자체 산출]")
    for p in sorted(base):
        n, h = base[p]
        if n >= 20:
            print("  %2d인기  n=%3d  3착률 %5.1f%%" % (p, n, 100.0 * h / n))

    def expect(pop):
        b = base.get(pop)
        return (b[1] / float(b[0])) if (b and b[0] >= 20) else None

    print("\n[괴리 구간별 3착 진입률 · 기저선 대비 배수]")
    for lab in ["距승", "距연", "場승", "場연", "全연", "타임", "부담"]:
        sub = [r for r in rows if r[0] == lab]
        if not sub:
            continue
        print("\n  ── %s (판정 가능 %d두) ──" % (lab, len(sub)))
        for lo, hi, nm in [(4, 99, "괴리 +4 이상"), (2, 3, "괴리 +2~3"),
                           (-1, 1, "괴리 -1~+1"), (-99, -2, "괴리 -2 이하")]:
            g = [r for r in sub if lo <= r[1] <= hi]
            if not g:
                continue
            n = len(g)
            hit = sum(1 for r in g if r[2])
            exp = [expect(r[4]) for r in g]
            exp = [e for e in exp if e is not None]
            eb = (sum(exp) / len(exp)) if exp else None
            act = hit / float(n)
            mark = "⚠판정불가(n<30)" if n < 30 else ""
            if eb:
                print("    %-14s n=%3d  3착 %5.1f%%  기저 %5.1f%%  배수 %.2f  %s"
                      % (nm, n, 100 * act, 100 * eb, act / eb if eb else 0, mark))
            else:
                print("    %-14s n=%3d  3착 %5.1f%%  기저 —  %s" % (nm, n, 100 * act, mark))
    return 0


if __name__ == "__main__":
    sys.exit(main())
