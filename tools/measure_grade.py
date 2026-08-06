# -*- coding: utf-8 -*-
"""등급(클래스) 축 측정 — 완전 읽기 전용. 운영 데이터 무수정.

🔴 원칙(CLAUDE.md)
  · 원문(logs/form_raw/*/oddspark_*.html.gz)만 읽는다. 네트워크·저장 없음.
  · 등급은 record_score 에 섞지 않는다 — 독립 축으로만 잰다.
  · n<30 은 판정 불가로 표시하고 결론에 쓰지 않는다.
  · 기저선 대비 배수로 낸다. 배수 1.0 이면 우위 없음.

왜 결과 조인이 필요 없나:
  oddspark 출주표 한 건에 말별 과거 5전의 (등급 · 착순 · 두수)가 함께 들어 있다.
  전이 t-1→t 의 등급 변화와 t 의 착순을 같은 원문에서 읽으므로 race_results 조인이 불필요하다.

측정 3종(대표 지시):
  ① 등급 낙차별 3착 진입률   ② 승급 첫 경기 3착 진입률   ③ 3착 전문(통산 3착 비율)의 3착 진입률

사용: python tools/measure_grade.py [--dir logs/form_raw]
"""
import os
import re
import sys
import gzip
import glob
import argparse
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ── 등급 표기 ──────────────────────────────────────────────────────────────
# 🔴 조(組) 한자를 범위로 쓰면 안 된다. 一二三四五六七八九十 은 유니코드에서 연속이 아니다
#    (一=U+4E00 · 三=U+4E09 · 七=U+4E03 · 九=U+4E5D 은 4E00~4E5D 안이지만
#     二=U+4E8C · 四=U+56DB · 五=U+4E94 · 六=U+516D · 八=U+516B 은 그 밖이다).
#    실제로 범위 정규식을 쓰니 'Ｃ３二' 가 'Ｃ３' 으로 잘렸다 — 명시 열거한다.
_KUMI = "一二三四五六七八九十"
_GRADE_RE = re.compile(r"([Ａ-Ｄ])([１-６])([%s])?" % _KUMI)   # Ａ~Ｄ, １~６, 조(선택)

_CLASS_ORDER = {"Ａ": 0, "Ｂ": 1, "Ｃ": 2, "Ｄ": 3}            # Ａ>Ｂ>Ｃ>Ｄ (0이 상위)
_KUMI_ORDER = {c: i for i, c in enumerate(_KUMI)}                              # 一>二>三 (0이 상위)


def parse_grade(text):
    """레이스명에서 등급 1개를 뽑는다. 반환 {'cls','num','kumi','raw','rank'} 또는 None.

    ⚠ oddspark 레이스명은 같은 등급을 두 번 반복한다('Ｃ３一Ｃ３一３歳以上').
      중복 제거 후 1종이면 그 등급, 2종 이상이면 혼합 조건(선발전)이라 None 을 준다.
      혼합을 억지로 하나로 고르면 승강급 판정이 조용히 틀린다."""
    if not text:
        return None
    seen = []
    for m in _GRADE_RE.finditer(text):
        g = (m.group(1), m.group(2), m.group(3))
        if g not in seen:
            seen.append(g)
    if len(seen) != 1:
        return None                       # 0건(등급 없는 오픈전) 또는 혼합(Ｃ２Ｃ３選抜)
    cls, num, kumi = seen[0]
    return {
        "cls": cls, "num": num, "kumi": kumi,
        "raw": cls + num + (kumi or ""),
        # rank: 작을수록 상위. 급(0~3)*100 + 급내번호*10 + 조(0~9). 조가 없으면 중간값 5.
        "rank": _CLASS_ORDER.get(cls, 9) * 100 + (int(num) - 1) * 10 + (_KUMI_ORDER.get(kumi, 5) if kumi else 5),
    }


# ── oddspark 출주표 파싱 ───────────────────────────────────────────────────
_BLOCK_RE = re.compile(r"HorseDetail")
_PAST_CELL_RE = re.compile(r'showElm\s+bg-(\d+)chaku', re.S)
_RACENAME_RE = re.compile(r'racename-small"\s+title="(.*?)"', re.S)
_ENT2_RE = re.compile(r'<table class="ent2">(.*?)</table>', re.S)
_TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_TR_RE = re.compile(r"<tr>(.*?)</tr>", re.S)


def parse_shutsuba(html):
    """oddspark 출주표 원문 → {'race': 이번경주등급, 'horses': [...]}.

    horses[]: {no, pastGrades[], pastPlacings[], career:{win,second,third,other}}
    ⚠ 착순(bg-Nchaku)과 레이스명(racename-small)의 개수가 다르면 그 말은 버린다.
      개수로 맞추면 조용히 밀린다(除外·계불 행 교훈)."""
    mt = re.search(r"<title>(.*?)</title>", html, re.S)
    race_grade = parse_grade(re.sub(r"<[^>]+>", "", mt.group(1))) if mt else None

    pos = [m.start() for m in _BLOCK_RE.finditer(html)]
    horses = []
    for i, p in enumerate(pos):
        blk = html[p:(pos[i + 1] if i + 1 < len(pos) else len(html))]
        placings = [int(x) for x in _PAST_CELL_RE.findall(blk)]
        names = _RACENAME_RE.findall(blk)
        if not placings or len(placings) != len(names):
            continue                                   # 🔴 짝이 안 맞으면 아무것도 붙이지 않는다
        career = None
        me = _ENT2_RE.search(blk)
        if me:
            rows = _TR_RE.findall(me.group(1))
            if rows:
                cells = [re.sub(r"<[^>]+>", "", c).strip() for c in _TD_RE.findall(rows[0])]
                nums = [c for c in cells if c.isdigit()]
                if len(nums) >= 4:
                    career = {"win": int(nums[0]), "second": int(nums[1]),
                              "third": int(nums[2]), "other": int(nums[3])}
        horses.append({
            "no": len(horses) + 1,
            "pastPlacings": placings,                  # 최근 → 과거 순서
            "pastGrades": [parse_grade(n) for n in names],
            "pastRaceNames": names,
            "career": career,
        })
    return {"race": race_grade, "horses": horses}


# ── 측정 ───────────────────────────────────────────────────────────────────
def collect(raw_dir):
    files = sorted(glob.glob(os.path.join(raw_dir, "*", "oddspark_*.html.gz")))
    races = []
    for f in files:
        try:
            html = gzip.open(f, "rt", encoding="utf-8", errors="ignore").read()
        except Exception:
            continue
        r = parse_shutsuba(html)
        r["file"] = os.path.basename(f)
        races.append(r)
    return races


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=os.path.join(ROOT, "logs", "form_raw"))
    a = ap.parse_args()

    races = collect(a.dir)
    print("[분모] oddspark 원문 %d건" % len(races))
    if not races:
        print("원문이 없다. 측정 불가.")
        return 1

    # 전이 표본: 말별 과거 5전에서 (t-1 등급 → t 등급, t 착순)
    # pastPlacings/pastGrades 는 최근이 앞이므로 t 의 직전은 인덱스 +1.
    trans = []          # {delta, in3, from, to}
    horse_n = 0
    for r in races:
        for h in r["horses"]:
            horse_n += 1
            gs, ps = h["pastGrades"], h["pastPlacings"]
            for t in range(len(gs) - 1):
                cur, prev = gs[t], gs[t + 1]
                if not cur or not prev:
                    continue
                trans.append({
                    "delta": cur["rank"] - prev["rank"],     # 양수 = 등급이 내려감(강급)
                    "in3": 1 if ps[t] <= 3 else 0,
                    "from": prev["raw"], "to": cur["raw"],
                    "clsdown": _CLASS_ORDER.get(cur["cls"], 9) - _CLASS_ORDER.get(prev["cls"], 9),
                })

    n = len(trans)
    base = sum(t["in3"] for t in trans) / n if n else 0
    print("[분모] 말 %d두 · 등급 전이 %d건" % (horse_n, n))
    print("[기저선] 전이 전체 3착 진입률 %.1f%%" % (base * 100))
    print("")

    def show(title, rows):
        print(title)
        for label, sel in rows:
            k = [t for t in trans if sel(t)]
            if not k:
                print("  %-22s n=0" % label)
                continue
            rate = sum(t["in3"] for t in k) / len(k)
            mult = (rate / base) if base else 0
            flag = "" if len(k) >= 30 else "  <-- n<30 판정불가"
            print("  %-22s n=%-4d 3착 %5.1f%%  배수 %.2f%s" % (label, len(k), rate * 100, mult, flag))
        print("")

    # ① 등급 낙차별
    show("[1] 등급 낙차별 (양수=강급·아래로)", [
        ("급 강급(Ｃ2->Ｃ3 등)", lambda t: t["clsdown"] > 0),
        ("급 승급(위로)", lambda t: t["clsdown"] < 0),
        ("같은 급·조 내림", lambda t: t["clsdown"] == 0 and t["delta"] > 0),
        ("같은 급·조 올림", lambda t: t["clsdown"] == 0 and t["delta"] < 0),
        ("완전 동일", lambda t: t["delta"] == 0),
    ])

    # ② 승급 첫 경기 = 직전보다 등급이 올라간(rank 감소) 전이
    show("[2] 승급 첫 경기(등급 상승 직후)", [
        ("승급(모든 폭)", lambda t: t["delta"] < 0),
        ("승급 폭 큼(급 단위)", lambda t: t["clsdown"] < 0),
        ("강급(대조군)", lambda t: t["delta"] > 0),
    ])

    # ③ 3착 전문 — 통산 3착 비율 상위. 말 단위로 재고 전이가 아니라 '직전 경주 3착 여부'로 본다.
    print("[3] 3착 전문(통산 3착 비율) — 말 단위")
    rows = []
    for r in races:
        for h in r["horses"]:
            c = h.get("career")
            if not c:
                continue
            tot = c["win"] + c["second"] + c["third"] + c["other"]
            if tot < 5:
                continue
            ps = h["pastPlacings"]
            if not ps:
                continue
            rows.append({"ratio": c["third"] / tot, "in3": 1 if ps[0] <= 3 else 0, "third": c["third"]})
    if rows:
        b3 = sum(x["in3"] for x in rows) / len(rows)
        print("  [기저선] 통산 5전+ 말의 최근 3착 진입률 %.1f%% (n=%d)" % (b3 * 100, len(rows)))
        for label, sel in [("3착비율 20%+", lambda x: x["ratio"] >= 0.20),
                           ("3착비율 15~20%", lambda x: 0.15 <= x["ratio"] < 0.20),
                           ("3착비율 10~15%", lambda x: 0.10 <= x["ratio"] < 0.15),
                           ("3착비율 10% 미만", lambda x: x["ratio"] < 0.10),
                           ("통산 3착 8회+", lambda x: x["third"] >= 8)]:
            k = [x for x in rows if sel(x)]
            if not k:
                print("  %-20s n=0" % label)
                continue
            rate = sum(x["in3"] for x in k) / len(k)
            flag = "" if len(k) >= 30 else "  <-- n<30 판정불가"
            print("  %-20s n=%-4d 3착 %5.1f%%  배수 %.2f%s"
                  % (label, len(k), rate * 100, (rate / b3) if b3 else 0, flag))
    else:
        print("  통산 성적 보유 말이 없다.")
    print("")
    print("[한계] 표본은 oddspark 원문 보유분(2026-08-05 이후)에 한정된다.")
    print("       기저선은 인기대 기저선이 아니라 이 표본의 전체 3착 진입률이다.")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.exit(main())
