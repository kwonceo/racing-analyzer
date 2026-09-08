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
    # 🔴 각질이 안 갈리는 판(한국 = 「자유」뿐)에서는 페이스를 말하지 않는다 — _pace_usable 참조
    if (pa.get("paceLabel") or pa.get("pace")) and _pace_usable(hs):
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


def _pace_usable(hs):
    """[2026-08-30] 페이스 문장을 쓸 수 있는 판인가 — **각질이 실제로 갈리는가**.

    🔴 왜: 한국 경마는 각질 표기가 사실상 「자유」 하나다.
      8월 실측 1,749두 — 자유 1,233(71%) · 없음 469(27%) · **선행 5(0.3%)**.
      `_race_shape_label`·`paceAnalysis` 는 **선행 마릿수**로 판을 가르므로
      한국은 **37경주 중 36(97%)이 「느린 판」**으로 고정된다. 정보가 0 이다.
      실사고(부산 2경주): 「어떻게 봤나」가 「🐌 느린 페이스」라고 쓰는 동안
      같은 화면 아래 「경주 전개 예측」은 **「선행 3두 → 하이페이스」**였다.
      아래쪽은 `_simulate_race_flow_kra` 로 **KRA 구간기록**(첫 400m·코너 통과위치)을 쓴다 —
      🔴 **각질 표기가 아니라 실주행을 본다. 그쪽이 맞고 이쪽이 틀렸다.**
    ⇒ 선행/추입이 **하나도 안 잡히면** 페이스를 말하지 않는다(원칙: 근거 없으면 안 쓴다).
    ⚠ 일본 경륜은 추입 62 · 선행 40 으로 고르게 갈린다 — **거기서는 그대로 쓴다**(무영향).
    """
    # 🔴🔴 [2026-08-31] 호출부 둘이 **서로 다른 형식**을 넘긴다.
    #   248행은 리스트, **424행(`kakao_lines`)은 dict {마번: 말}** 이다(406행에서 그렇게 만든다).
    #   dict 를 그냥 순회하면 **키(int)** 가 나와 `h.get(...)` 이 터진다
    #   → `'int' object has no attribute 'get'` → **카톡 「어떻게 봤나」 블록이 통째로 빠졌다**(148건).
    #   ⚠ `except` 가 삼켜 stdout 에만 남았다 — 회원 카톡에서 조용히 사라지고 있었다.
    if isinstance(hs, dict):
        hs = list(hs.values())
    hs = hs or []
    if not hs:
        return False
    known = lead = 0
    for h in hs:
        if not isinstance(h, dict):
            continue
        g = str(h.get("gait") or h.get("styleType") or h.get("declaredStyleLabel") or "")
        if not g:
            continue
        known += 1
        if any(k in g for k in ("선행", "逃", "nige")):
            lead += 1
    # 🔴 페이스 판정의 **입력은 선행 마릿수**다(_race_shape_label · _apply_pace_analysis 둘 다).
    #   선행 0두는 「느리다」가 아니라 **「모른다」**이다 — 그걸 「느린 페이스」로 옮겨 적지 않는다.
    # ⚠ 라벨 보유가 절반 미만이면 역시 판정 불가로 본다(부분 수집).
    return lead >= 1 and known >= (len(hs) + 1) // 2


# ══════════ [전적표 태그 (2026-09-08 대표 「다 붙여」)] ══════════
#   대표가 전적표(착순 나열·인기)만 보고 고른 말이 같은 날 두 번 시장·분석기를 이겼다(카나자와 10R 2번 · 미즈사와 11R 1번).
#   그 읽는 법 넷을 **표시 전용** 태그로 옮긴다. 추천·판정·점수에는 넣지 않는다(7주 측정: 전적은 시장 상위 층에서는 값이 없다).
#   🔴 넷 다 「시장이 차가울 때만 값이 있다」 — 시장 순위를 함께 붙인다. 문턱은 아래에 고정(스윕 금지).
#   실측(경마지방 1,326경주 · 시장 단승 순위 층 고정 · 2026-09-08):
#     꾸준함(최근 4전 모두 5착 이내)          세 층 모두 +3.6~+6.0pp(냉대층 202두·입상 25)
#     회복형(3착내 이력→8착↓ 대패→직전 개선)  시장 3~5층 +3.7pp(209두·입상 54) · 냉대층 +1.8 · 상위층 −8.7
#     반복착순(직전=2전전 · 2~6착)            냉대층 +3.1pp(251두·입상 24) · 상위층 −6.8
#     상승세(직전이 5전 최고·3착↓ + 인기이김 3+) 냉대층 −3.3(n<30 판정 불가) — 표시만 · 재측정 대상
#   ⚠ 태그는 「저장된 값」으로만 만든다(recentPlacings · pastPops). 없으면 안 붙인다(환각 금지).
FORM_TAGS_ENABLED = True
FORM_TAG_MAX_EXTRA = 3          # 상위 3두 밖에서 태그 붙은 말을 몇 두까지 한 줄로 보여줄까


def _placings_of(h, ent=None):
    rp = (h or {}).get("recentPlacings") or (h or {}).get("pastPlacings") or (ent or {}).get("pastPlacings") or []
    out = []
    for x in rp if isinstance(rp, (list, tuple)) else []:
        try:
            v = int(x)
        except (TypeError, ValueError):
            continue
        if v >= 1:
            out.append(v)
    return out


def _pops_of(h, ent=None):
    for src in ((h or {}).get("pastPops"), (ent or {}).get("pastPops"), (ent or {}).get("debaPastPops"), (h or {}).get("debaPastPops")):
        if isinstance(src, (list, tuple)) and any(x is not None for x in src):
            return src
    return None


def form_tags(h, ent=None):
    """전적표 태그 목록(문자열) — 최신이 맨 앞인 착순 배열만 쓴다. 없으면 []."""
    if not FORM_TAGS_ENABLED:
        return []
    rp = _placings_of(h, ent)
    tags = []
    if len(rp) >= 4 and all(x <= 5 for x in rp[:4]):
        tags.append("꾸준함")
    if len(rp) >= 3 and rp[0] < rp[1] and rp[1] >= 8 and min(rp[2:5]) <= 3:
        tags.append("회복형")
    if len(rp) >= 2 and rp[0] == rp[1] and 2 <= rp[0] <= 6:
        tags.append("반복%d-%d" % (rp[0], rp[0]))
    if rp and rp[0] <= 3 and rp[0] <= min(rp[:5]):
        pops = _pops_of(h, ent)
        beat = 0
        if pops:
            pl_raw = (h or {}).get("pastPlacings") or (ent or {}).get("pastPlacings") or rp
            for a, b in zip(pl_raw, pops):
                try:
                    if a is not None and b is not None and int(a) < int(b):
                        beat += 1
                except (TypeError, ValueError):
                    pass
        if (pops and beat >= 3) or (not pops and len(rp) >= 2 and rp[0] < rp[1]):
            tags.append("상승세")
    return tags


def form_tag_line(hs, ents, mrank, exclude=(), maxn=FORM_TAG_MAX_EXTRA):
    """「🧭 전적표: 6번 꾸준함(시장 6위) · 1번 회복형(5위)」 — 상위 3두 밖 태그 말. 없으면 None."""
    items = []
    for no in sorted(hs):
        if no in exclude:
            continue
        t = form_tags(hs.get(no) or {}, ents.get(no))
        if not t:
            continue
        r = mrank.get(no)
        items.append((r if r else 99, no, t))
    items.sort()
    if not items:
        return None
    parts = []
    for r, no, t in items[:maxn]:
        parts.append("%d번 %s%s" % (no, "·".join(t), (" (시장 %d위)" % r) if r and r < 99 else ""))
    return " 🧭 전적표: " + " · ".join(parts)


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
        # [전적표 태그] 상위 3두에도 붙인다(있을 때만) — 「직전 2착·꾸준함」 식
        try:
            _ft = form_tags(hs.get(no) or {}, ents.get(no))
        except Exception:
            _ft = []
        if _ft:
            f = f + "·" + "·".join(_ft)
        out.append(" %d번 %s%s" % (no, f, tag))
        used.append(no)
    if not out:
        return []
    # [전적표 태그] 상위 3두 밖의 태그 말 — 대표가 보는 자리(시장 냉대)는 여기에 나온다
    try:
        _tl = form_tag_line(hs, ents, mrank, exclude=set(used))
        if _tl:
            out.append(_tl)
    except Exception:
        pass
    tail = ""
    if pace and _pace_usable(hs):
        tail += str(pace).replace("페이스", "").strip() + " 페이스"
    if len(used) >= 2:
        tail += (" · " if tail else "") + "%d번과 %d번의 우승 경합이 관전 포인트" % (used[0], used[1])
    if tail:
        out.append(" " + tail)
    return out


if __name__ == "__main__":
    main()
