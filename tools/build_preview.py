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


def horse_lines(h, ent, dist, mrank, nH, sport, l3rank=None):
    """말 한 마리를 **두 문장**으로 서술한다. 반환 (문장, 근거목록).
    🔴 값이 없는 항목은 문장에서 통째로 뺀다 — 추측해서 채우지 않는다."""
    no, why = h.get("no"), []
    gait = h.get("gait") or (ent or {}).get("declaredStyleLabel") or ""
    gl = gait if gait.endswith(("형", "각)")) else (gait + "형" if gait else "")

    # ── 문장 1: 정체성 + 직전 성적 ──────────────────────────
    s1 = "%s번은 " % no
    if gl:
        s1 += "%s으로 " % gl; why.append("각질=%s" % gait)
    # ⚠ 경륜은 착순이 문자열로 들어오는 경우가 있다 — 정수로만 받는다(실측 TypeError)
    # 🔴 [2026-08-28] **리스트가 아니면 통째로 버린다.**
    #   실사고: 경륜 `recentPlacings` 가 `"8/27 "`(날짜 문자열)이라 문자 단위로 순회돼
    #   첫 글자 '8' 을 착순으로 읽었다 → 전원 「직전 8착」. 문자열은 착순 배열이 아니다.
    _rpsrc = h.get("recentPlacings")
    if not isinstance(_rpsrc, (list, tuple)):
        _rpsrc = h.get("pastPlacings")
    if not isinstance(_rpsrc, (list, tuple)):
        _rpsrc = []
    rp = []
    for x in _rpsrc:
        try:
            v = int(str(x).strip())
        except (TypeError, ValueError):
            continue
        if v > 0:
            rp.append(v)
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

    # 🔴 [2026-08-28] 직전 **인기 대비 착순** — 「기대를 받았는데 못했다 / 평가를 뒤집었다」
    #   ⚠ pastPops 는 경마 raw_profile.entries 에만 있다(보유 79%). 없으면 이 문장을 쓰지 않는다.
    pops = [x for x in ((ent or {}).get("pastPops") or []) if x]
    if pops and rp:
        p0, q0 = rp[0], pops[0]
        if q0 <= 3 and p0 >= 6:
            s1 = s1.rstrip(".") + "만, %d인기의 기대를 받고도 %d착에 그친 것이 아쉽습니다." % (q0, p0)
            why.append("직전 %d인기 → %d착" % (q0, p0))
        elif q0 >= 6 and p0 <= 3:
            s1 = s1.rstrip(".") + ". %d인기 평가를 뒤집은 결과였습니다." % q0
            why.append("직전 %d인기 → %d착(평가 상회)" % (q0, p0))
        beat = sum(1 for a, b in zip(rp, pops) if a < b)
        if len(pops) >= 4 and beat >= len(pops) - 1:
            s1 += " 최근 경주마다 인기 이상으로 달리고 있습니다."
            why.append("인기 상회 %d/%d전" % (beat, len(pops)))

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
    if l3rank and no in l3rank:
        r3, n3 = l3rank[no]
        if r3 == 1:
            cl.append(("막판 스피드는 이 경주에서 가장 빠르고", "막판 스피드는 이 경주에서 가장 빠릅니다"))
            why.append("상3F 경주 내 1위")
        elif n3 >= 6 and r3 <= max(2, n3 // 3):
            cl.append(("막판 스피드가 상위권이며", "막판 스피드가 상위권입니다"))
            why.append("상3F 경주 내 %d/%d위" % (r3, n3))
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

    # ⚠ 조각이 많으면 문장이 장황해진다 — **3개까지만** 쓴다(뒤쪽은 버린다).
    #   순서상 뒤가 시장 인기이므로, 넘칠 때는 가운데를 버려 「조건 + 인기」를 남긴다.
    if len(cl) > 3:
        cl = cl[:2] + cl[-1:]
    s2 = ""
    if cl:
        # 마지막 조각만 종결형, 나머지는 연결형으로 잇는다
        body = " ".join(c[0] for c in cl[:-1] + []) if len(cl) > 1 else ""
        body = (body + " " if body else "") + cl[-1][1]
        s2 = " " + body
    if rp and len(rp) >= 3:
        s2 = (s2.rstrip() if s2.rstrip().endswith(".") else s2.rstrip() + ".") + " (최근 %s)" % "-".join(str(x) for x in rp[:5])
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
    # 상3F(막판 스피드) 경주 내 순위 — 최근 3경주 평균(작을수록 빠르다)
    l3 = []
    for x in hs:
        v = [float(y) for y in (x.get("last3fList") or [])[:3]
             if isinstance(y, (int, float)) and y > 0]
        if v:
            l3.append((sum(v) / len(v), x.get("no")))
    l3rank = {}
    if len(l3) >= 4:
        l3.sort()
        l3rank = {no: (i + 1, len(l3)) for i, (_, no) in enumerate(l3)}
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
        txt, why = horse_lines(h, ents.get(no), dist, mr, nH, sport, l3rank)
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


def fact_short(h, ent=None, dist=None):
    """카톡 한 줄용 **짧은 근거**. 저장된 값에서만 만든다.
    예: '선행형·직전 3착·도주 62%' · 만들 게 없으면 None(빈 말 금지).
    🔴 app.py `_why_line` 이 이걸 부른다 — 규칙을 두 곳에 두지 않기 위해서다."""
    h = h or {}
    bits = []
    g = (h.get("gait") or (ent or {}).get("declaredStyleLabel") or "").strip()
    if g:
        bits.append(g if g.endswith(("형", "각)")) else g + "형")
    # 🔴 [2026-08-28] **리스트가 아니면 통째로 버린다.**
    #   실사고: 경륜 `recentPlacings` 가 `"8/27 "`(날짜 문자열)이라 문자 단위로 순회돼
    #   첫 글자 '8' 을 착순으로 읽었다 → 전원 「직전 8착」. 문자열은 착순 배열이 아니다.
    _rpsrc = h.get("recentPlacings")
    if not isinstance(_rpsrc, (list, tuple)):
        _rpsrc = h.get("pastPlacings")
    if not isinstance(_rpsrc, (list, tuple)):
        _rpsrc = []
    rp = []
    for x in _rpsrc:
        try:
            v = int(str(x).strip())
        except (TypeError, ValueError):
            continue
        if v > 0:
            rp.append(v)
    if rp:
        bits.append("직전 %d착" % rp[0] if rp[0] != 1 else "직전 우승")
    else:
        pv = _prev_last((ent or {}).get("prev1"))
        if pv:
            bits.append("직전 %s %s" % (pv[0], "우승" if pv[1] == 1 else "%d착" % pv[1]))
    # 🔴🔴 [2026-08-28 대표 지적] **기수 성적** — 「기수 능력에 따라 배당이 판이하게 바뀐다」
    #   일본은 h["jockeyRate"](복승률·오늘 로그 배선분) · 한국은 ent["jockeyStat"] 을 쓴다.
    #   ⚠ 값이 없으면 이 조각을 안 쓴다(환각 금지).
    _jr = h.get("jockeyRate")
    _js = (ent or {}).get("jockeyStat") or {}
    if _jr is None and _js:
        _jr = _js.get("placeRate")
    try:
        _jr = float(_jr) if _jr is not None else None
    except (TypeError, ValueError):
        _jr = None
    if _jr is not None and len(bits) < 3:
        _rides = _js.get("rides") if _js else None
        _rt = ("기수 복승률 %.0f%%" % _jr) + (" %d기승" % _rides if _rides and _rides >= 500 else "")
        if _jr >= 30:
            bits.append(_rt + " 강세")
        elif _jr <= 12:
            bits.append(_rt)
        elif _rides and _rides >= 3000:
            bits.append("경험 많은 기수(%d기승)" % _rides)
    # 🟢 [2026-08-28] 기수 변경 — 대표 예시의 「경험 많은 기수로 변경되어」
    #   ⚠ pastJockeys 는 2026-08-28 배선분이라 **그날 이후 경주에만** 있다(소급 없음).
    _jk = (h.get("jockey") or "").strip()
    _pj = [str(x).strip() for x in ((ent or {}).get("pastJockeys") or []) if x]
    if _jk and _pj and _pj[0] and _pj[0] != _jk:
        bits.append("기수 %s→%s 교체" % (_pj[0], _jk))
    # 🟢 마체중 증감 — 직전 대비
    try:
        _bw = float(h.get("bodyWeight"))
        _pb = [float(x) for x in ((ent or {}).get("pastBodyWeights") or []) if x]
        if _bw and _pb and abs(_bw - _pb[0]) >= 6:
            bits.append("마체중 %+dkg" % int(round(_bw - _pb[0])))
    except (TypeError, ValueError):
        pass
    kr = (ent or {}).get("kimariteRatio") or {}
    if kr:
        t = max(kr.items(), key=lambda kv: kv[1])
        if t[1] >= 50:
            bits.append("%s %.0f%%" % (t[0], t[1]))
    rt = (ent or {}).get("rentai")
    if isinstance(rt, (int, float)) and rt >= 50 and len(bits) < 3:
        bits.append("연대율 %.0f%%" % rt)
    pd = [int(x) for x in (h.get("pastDistances") or []) if x]
    if dist and pd and int(dist) not in pd and len(bits) < 3:
        bits.append("%dm 첫 경험" % int(dist))
    cm = _corner_move(h.get("corners"))
    if cm is not None and cm >= 0.20 and len(bits) < 3:
        bits.append("막판 추입 강함")
    return "·".join(bits[:3]) if bits else None


def kakao_lines(hs, ents=None, dist=None, mrank=None, pace=None, topn=3):
    """카톡용 **짧은 예상문** — 「어떻게 봤나」 블록.
    주목마 topn 두를 한 줄씩 + 관전 포인트 한 줄. 만들 게 없으면 빈 리스트.
    🔴 fact_short 를 그대로 쓴다 — 규칙을 두 곳에 두지 않는다.
    ⚠ 카톡은 길면 안 읽힌다. 한 줄 40자 안쪽을 목표로 한다."""
    hs = {int(k): v for k, v in (hs or {}).items()}
    ents = {int(k): v for k, v in (ents or {}).items()}
    mrank = {int(k): v for k, v in (mrank or {}).items()}
    order = [n for n, _ in sorted(mrank.items(), key=lambda kv: kv[1])] if mrank else sorted(hs)
    out, used = [], []
    for no in order:
        if len(out) >= topn:
            break
        f = fact_short(hs.get(no) or {}, ents.get(no), dist)
        if not f:
            continue
        r = mrank.get(no)
        tag = " (시장 %d위)" % r if r and r <= 3 else ""
        out.append(" %d번 %s%s" % (no, f, tag))
        used.append(no)
    if not out:
        return []
    tail = ""
    if pace:
        tail += str(pace).replace("페이스", "").strip() + " 페이스"
    if len(used) >= 2:
        tail += (" · " if tail else "") + "%d번과 %d번의 우승 경합이 관전 포인트" % (used[0], used[1])
    if tail:
        out.append(" " + tail)
    return out


if __name__ == "__main__":
    main()
