# -*- coding: utf-8 -*-
"""회원용 **예상문**을 만든다 (완전 읽기 전용 · 추천·판정 무개입).

🔴 왜 필요한가 (2026-08-28 대표 지시)
  「1번말은 선행형으로 직전 경주 아쉽게도 3착을 했던 말입니다. 경험 많은 기수로 바뀌어
   우승을 노리는데 여러분 생각은 어떻습니까」 — **이런 예상문이 우리에겐 없다.**
  `race_summary`·`analysis` 는 **한국 PDF 경주 전용**이고 일본 경마·경륜은 전부 null 이다.
  지금 `summary` 는 「급락 1-3 ▼23.9% / 유력마 4·1·3」 같은 **기술 요약**이라 회원이 못 읽는다.

🔴 환각 금지 — 이 파일의 제1 규칙
  **저장된 값에서만** 문장을 만든다. 값이 없으면 그 문장을 **쓰지 않는다**(추측 금지).
  모든 문장에 `근거` 필드를 함께 남겨 사후에 원자료와 대조할 수 있게 한다.
  ⚠ LLM 을 쓰지 않는다 — 없는 사실을 지어낼 위험이 0 이어야 한다.

⚠ 추천 조합·판정에 일절 개입하지 않는다. 읽어서 글만 만든다.
"""
import os, io, re, sys, json, glob

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import measure_score_edge as E

_JP_ORD = {"１": 1, "２": 2, "３": 3, "４": 4, "５": 5, "６": 6, "７": 7, "８": 8, "９": 9}
_ROUND = [("決 勝", "결승"), ("決勝", "결승"), ("準決勝", "준결승"), ("予 選", "예선"),
          ("予選", "예선"), ("特 選", "특선"), ("特選", "특선"), ("一 般", "일반"), ("一般", "일반")]


def _prev_last(prev):
    """경륜 `prev1`(직전 개최 원문)에서 **마지막 경주의 라운드·착순**을 뽑는다.
    예: '西武園Ｆ１ 8/21 予 選 ３着 … 8/23 決 勝 ４着 11.4' → ('결승', 4, '西武園')
    🔴 못 읽으면 None — 지어내지 않는다."""
    if not isinstance(prev, str) or not prev.strip():
        return None
    ven = prev.split()[0] if prev.split() else ""
    ven = re.sub(r"[ＦＧ][１２Ｐ].*$", "", ven).strip() or None
    hits = []
    for jp, ko in _ROUND:
        st = 0
        while True:
            i = prev.find(jp, st)
            if i < 0:
                break
            m = re.search(r"([１-９])着", prev[i:i + 14])
            if m:
                hits.append((i, ko, _JP_ORD[m.group(1)]))
            st = i + 1
    if not hits:
        return None
    hits.sort()
    return (hits[-1][1], hits[-1][2], ven)


def _josa(w, a="이", b="가"):
    """받침 유무로 조사를 고른다(차입이 / 도주가)."""
    if not w:
        return b
    c = ord(w[-1])
    return a if (0xAC00 <= c <= 0xD7A3 and (c - 0xAC00) % 28) else b


_ORD = {1: "우승", 2: "2착", 3: "3착", 4: "4착", 5: "5착"}


def _mkt_rank(d, base):
    """마감 전 마지막 정상 틱의 시장순위(원칙 27)."""
    od = E._load(os.path.join(ROOT, "data", "odds_history", base + ".json"))
    tk = E._last_pre_close(od)
    return E._market_rank(E._qmap(tk or {})) or {}


def _corner_move(corners):
    """첫 코너 → 마지막 코너 상대위치 변화. +면 막판에 올라온다."""
    mv = []
    for c in (corners or [])[:5]:
        ns = [int(x) for x in str(c).replace("-", " ").split() if x.isdigit()]
        if len(ns) >= 2 and max(ns) > 0:
            f = max(ns + [8])
            mv.append(ns[0] / f - ns[-1] / f)
    return (sum(mv) / len(mv)) if len(mv) >= 2 else None


def horse_lines(h, ent, dist, mrank, nH, sport):
    """말 한 마리를 **두 문장**으로 서술한다. 반환 (문장, 근거목록).
    🔴 값이 없는 항목은 문장에서 통째로 뺀다 — 추측해서 채우지 않는다."""
    no, why = h.get("no"), []
    gait = h.get("gait") or (ent or {}).get("declaredStyleLabel") or ""
    gl = gait if gait.endswith(("형", "각)")) else (gait + "형" if gait else "")

    # ── 문장 1: 정체성 + 직전 성적 ──────────────────────────
    s1 = "%s번은 " % no
    if gl:
        s1 += "%s으로 " % gl; why.append("각질=%s" % gait)
    rp = [x for x in (h.get("recentPlacings") or h.get("pastPlacings") or []) if x]
    if rp:
        p0 = rp[0]; why.append("직전착순=%d" % p0)
        if p0 == 1:
            s1 += "직전 경주 우승마입니다."
        elif p0 in (2, 3):
            s1 += "직전 경주 아쉽게 %s에 그친 말입니다." % _ORD[p0]
        elif p0 >= 10:
            s1 += "직전 경주는 %d착으로 부진했습니다." % p0
        else:
            s1 += "직전 경주 %s이었습니다." % _ORD.get(p0, "%d착" % p0)
    else:
        pv = _prev_last((ent or {}).get("prev1"))
        if pv:
            rd, pl, ven = pv
            why.append("prev1 마지막=%s %d착" % (rd, pl))
            if pl == 1:
                s1 += "직전 개최 %s에서 우승한 기세입니다." % rd
            elif pl in (2, 3):
                s1 += "직전 개최 %s에서 아쉽게 %s에 그쳤습니다." % (rd, _ORD[pl])
            else:
                s1 += "직전 개최 %s은 %d착이었습니다." % (rd, pl)
        else:
            s1 += "이번 경주에 나섭니다."

    # ── 문장 2: 이번 경주의 조건 ────────────────────────────
    cl = []
    pd = [int(x) for x in (h.get("pastDistances") or []) if x]
    if dist and pd:
        if int(dist) not in pd:
            cl.append(("%dm는 이번이 첫 경험이지만" % int(dist), "%dm는 이번이 첫 경험입니다" % int(dist)))
            why.append("거리 첫경험(과거 %s)" % pd[:4])
        elif len(rp) >= 3 and rp[0] <= 3:
            cl.append(("%dm 경험이 있고" % int(dist), "%dm 경험이 있습니다" % int(dist))); why.append("거리 경험 있음")
    jk = (h.get("jockey") or "").strip()
    if jk and sport != "cycle":
        cl.append(("%s 기수와 호흡을 맞추며" % jk, "%s 기수와 호흡을 맞춥니다" % jk)); why.append("기수=%s" % jk)
    cm = _corner_move(h.get("corners"))
    if cm is not None:
        if cm >= 0.20:
            cl.append(("막판에 순위를 끌어올리는 힘이 뚜렷하고", "막판에 순위를 끌어올리는 힘이 뚜렷합니다"))
            why.append("코너 상대위치 +%.2f" % cm)
        elif cm <= -0.15:
            cl.append(("앞서 가다 막판에 처지는 흐름이라", "앞서 가다 막판에 처지는 흐름이라 스태미너가 관건입니다"))
            why.append("코너 상대위치 %.2f" % cm)
    kr = (ent or {}).get("kimariteRatio") or {}
    if kr:
        t = max(kr.items(), key=lambda kv: kv[1])
        if t[1] >= 50:
            cl.append(("승부수는 %s%s %.0f%%로 뚜렷하고" % (t[0], _josa(t[0]), t[1]), "승부수는 %s%s %.0f%%로 뚜렷합니다" % (t[0], _josa(t[0]), t[1])))
            why.append("결정수 %s %.0f%%" % t)
    rt = (ent or {}).get("rentai")
    if isinstance(rt, (int, float)) and rt >= 50:
        cl.append(("연대율 %.0f%%로 안정적이며" % rt, "연대율 %.0f%%로 안정적입니다" % rt)); why.append("연대율=%s" % rt)
    r = mrank.get(no)
    if r == 1:
        cl.append(("시장에서 가장 인기를 끌고 있어", "시장에서 가장 인기를 끌고 있습니다")); why.append("시장순위 1위")
    elif r == 2:
        cl.append(("인기 2번째로 지목되고 있고", "인기 2번째로 지목되고 있습니다")); why.append("시장순위 2위")
    elif r and nH and r >= max(6, nH - 2):
        cl.append(("인기는 낮은 편이지만", "인기는 낮은 편입니다")); why.append("시장순위 %d위" % r)

    s2 = ""
    if cl:
        # 마지막 조각만 종결형, 나머지는 연결형으로 잇는다
        body = " ".join(c[0] for c in cl[:-1] + []) if len(cl) > 1 else ""
        body = (body + " " if body else "") + cl[-1][1]
        s2 = " " + body
    if rp and len(rp) >= 3:
        s2 = s2.rstrip() + " (최근 %s)" % "-".join(str(x) for x in rp[:5])
        why.append("최근=%s" % rp[:5])
    return (s1 + s2, why)


def build(rk_path):
    d = E._load(rk_path)
    if not isinstance(d, dict):
        return None
    base = os.path.basename(rk_path)[:-5]
    sport = d.get("sport") or ""
    rp = d.get("raw_profile") or {}
    dist = rp.get("distance")
    ents = {}
    for e in (rp.get("entries") or []):
        try:
            ents[int(e.get("no"))] = e
        except Exception:
            pass
    hs = d.get("horses") or []
    nH = len(hs)
    mr = _mkt_rank(d, base)
    inv = sorted(mr.items(), key=lambda kv: kv[1])
    top = [n for n, _ in inv[:3]]
    pa = (d.get("corePicks") or {}).get("paceAnalysis") or {}

    L = []
    head = "%s · %d두" % (base.replace("_", " "), nH)
    if dist:
        head += " · %sm" % dist
    if pa.get("paceLabel") or pa.get("pace"):
        head += " · %s" % str(pa.get("paceLabel") or pa.get("pace")).replace("페이스", "").strip() + " 페이스"
    L.append(("[" + head + "]", "raw_profile / paceAnalysis"))

    for no in top:
        h = next((x for x in hs if x.get("no") == no), None)
        if not h:
            continue
        txt, why = horse_lines(h, ents.get(no), dist, mr, nH, sport)
        if not why:
            continue
        L.append((txt, " · ".join(why)))

    if len(top) >= 2:
        L.append(("%s번과 %s번의 우승 경합이 이 경주의 관전 포인트입니다."
                  % (top[0], top[1]), "시장순위 1·2위"))
    return {"raceKey": base, "sport": sport, "lines": L}


def main():
    pat = sys.argv[1] if len(sys.argv) > 1 else "2026_08_28_*"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 2
    fs = sorted(glob.glob(os.path.join(ROOT, "data", "analysis_log", pat + ".json")))[::-1]
    done = 0
    for f in fs:
        r = build(f)
        if not r or len(r["lines"]) < 3:
            continue
        print("\n" + "=" * 74)
        for txt, _ in r["lines"]:
            print("  " + txt)
        print("  " + "-" * 70)
        print("  [근거]")
        for txt, why in r["lines"][1:]:
            print("   · %s" % why[:110])
        done += 1
        if done >= n:
            break


if __name__ == "__main__":
    main()
