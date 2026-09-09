# -*- coding: utf-8 -*-
"""
경륜 전적표 예측(관측 계층) — 서버가 이미 모은 출주표(analysis_log.raw_profile: 並び·각질·決まり手·착별·연대율·
등급·기어·직전 개최 성적)를 발주 3~9분 전에 Claude 가 읽고 축·상대·근거를 낸다. 2026-09-09 대표 지시 「경륜도 만들어봐」.

🔴 추천 경로 무개입 · app.py 가 import 하지 않는다 · 별도 프로세스(tools/form_forecast.py 와 같은 구조)
🔴 금지 입력: 배당·인기 · 우리 점수(record_score·grade·paceBonus) · 우리 추천(keyHorses·finalQuinellas)
🔴 기록 logs/keirin_forecast/<YYYYMMDD>/<경기장>_<N>경주.json + <YYYYMMDD>.jsonl · 채점은 analysis_log.result(경륜 결과 보유 100%)
🔴 판정선: 경마판과 같다 — 채점 100경주(적중 30+)에서 시장 상위3두(T-fetch 최저복승 순) 대조 · 못 넘으면 종결

사용:
  python tools/keirin_forecast.py --daemon        # 60초 주기 · odds_history 최신 틱 mb 3~9 인 경륜 경주를 예측
  python tools/keirin_forecast.py --race "코치 4경주" [--date 2026-09-09] [--force]
  python tools/keirin_forecast.py --grade | --summary
환경: .env ANTHROPIC_API_KEY · KEIRIN_FORECAST_MODEL(없으면 FORM_FORECAST_MODEL · 기본 claude-opus-5)
"""
import os, sys, re, io, json, gzip, time, glob, datetime, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import form_forecast as FF          # _env · _load · 공통

OUT_DIR = os.path.join(BASE, "logs", "keirin_forecast")
STAMP = os.path.join(OUT_DIR, "_daemon_last.txt")
LEAD_HI = 9
PROMPT_VERSION = "k3-20260909-all"
LEAD_LO = 3

SYSTEM = """당신은 일본 경륜 출주표만으로 2차복(복승)·3연복(삼복승)을 짚는 분석가다. 배당·인기·다른 분석기의 추천은 주어지지 않는다.
출주표(並び=라인 순서 · 선수별 각질·決まり手 실적·착별성적·연대율·등급·기어·직전 두 개최 성적·최근 착순)를 읽고 아래 순서로 검토한 뒤 답한다.

① 라인: 並び 순서에서 라인을 나눈다(같은 지역·인접 선수가 한 라인 · 단기(単騎)는 혼자). 라인 길이(3명 > 2명 > 단기)와 선두(自力)의 힘을 본다. 라인 선두가 강하면 番手(2번째)가 가장 유리하다 — 선두는 바람을 맞고 番手는 그 뒤에서 차입한다.
② 자력형 수: 도주(逃)·젖히기(捲) 선언·실적이 있는 자력형이 몇 명인가. 자력형이 3명 이상이면 앞이 서로 잡아 난전이므로 뒤에서 차입·마크하는 추입형과 3번수가 산다. 자력형이 1명뿐이면 그 라인 선두·番手가 최유력이다.
③ 決まり手 실적: 도주·젖히기(자력) 비율이 높은 선수는 전개를 만들고, 차입·마크 비율이 높은 선수는 라인 선두가 강할 때만 산다.
④ 등급·기수: 등급(S1·S2·A1·A2·A3) 차이가 나면 상위 등급이 기본 우위. 期(기수)가 낮으면 젊은 신예로 성장 중일 수 있다. 나이 40세 이상은 자력이 약해 마크 의존.
⑤ 직전 두 개최: 予選→準決勝→決勝 진출 여부와 착순·타임(11.x 초는 상3F 개념). 최근 착순이 1·2착 반복이면 절정, 決勝 1着 뒤 첫 출주는 강하다.
⑥ 연대율·착별: 연대율 50% 이상이면 축 후보. 착외가 많은데 연대율이 높으면 기복형.
⑦ 기어: 3.92 이상 큰 기어는 젖히기·도주 지구형, 작은 기어는 순발형(마크·차입).
⑧ 7명 경주는 9명보다 라인 수가 적어 선두 라인 番手 유리, 9명은 난전 확률이 높다.

읽기 규칙(반드시 지킨다):
· 축은 「라인 선두가 강한 라인의 番手」 또는 「유일 자력형」 중에서 고른다. 강한 자력형 선두 자신을 축으로 삼는 것은 자력형이 2명 이하일 때만.
· 상대 3~4명 중 최소 1명은 다른 라인(축과 다른 라인)에서 넣는다 — 축 라인이 무너질 때의 보험.
· 복승 3~4개 중 1개는 축을 빼고 상대끼리 묶는다. 삼복승은 축+상대 2명(같은 라인 2명+다른 라인 1명 조합을 우선).
· 근거(reasons)에 적은 선수는 조합에도 반영한다. 근거에만 쓰고 조합에서 빠뜨리는 것을 금지한다.
· 출주표의 모든 선수를 reasons 또는 excluded 중 한 곳에 반드시 넣는다. 미언급 0명.
· 등급·연대율 상위만 나열하는 답(=시장 베끼기)은 실패다. 전개(누가 앞을 잡고 누가 그 뒤에 붙나)를 먼저 쓰고 거기서 조합을 만든다.


출력 언어·깊이 규칙(대표 지시 2026-09-09 「한국말로 · 약해 보인다」):
· 모든 문장은 한국어로 쓴다. 일본어 용어는 반드시 번역한다 — 良=양호 · 稍重=약간 다습 · 重=다습 · 不良=불량 · 人気=인기 · 直前/前走=직전 · 距=거리 실적 · 場=경기장 실적 · 牝=암말 · 牡=수말 · セン=거세마 · 逃げ=도주 · 差し=차입 · 追込=추입 · 先行=선행 · 番手=2번수 · 上がり=상3F. 경기장·마명은 한글로(川崎=카와사키 · 園田=소노다 · 浦和=우라와 · 船橋=후나바시 · 大井=오이 · 門別=몬베츠 · 金沢=카나자와 · 笠松=카사마츠 · 名古屋=나고야 · 高知=고치 · 佐賀=사가 · 姫路=히메지 · 盛岡=모리오카 · 水沢=미즈사와). 마명은 가타카나를 한글 음역으로.
· 근거(reasons)는 말마다 2~3문장의 완결된 이야기로 쓴다: ① 직전에 무엇을 했나(착순·통과순위·마장·인기·상대) ② 그것이 오늘 왜 통하나(전개·마장·거리·등급) ③ 무엇이 되면 들어오나. 숫자 나열이 아니라 판단을 쓴다.
· "story": 이 경주가 어떻게 흘러갈지 3~4문장(누가 앞을 잡고, 누가 그 뒤에 붙고, 결승선에서 누가 뻗는가 · 마장 영향).
· "market_view": 시장(단승 순)과 내 판단이 갈리는 지점 2~3문장 — 시장 상위 중 내가 내린 말과 이유, 시장이 놓친 냉대말과 이유. 시장과 같으면 「시장과 같다」고 쓰고 그 이유를 쓴다.
· "risk": 이 그림이 깨지는 조건 1~2문장(축이 무너지는 경우와 그때 살아남는 조합).

반드시 아래 JSON 하나만 출력한다(설명문 금지):
{"axis": 축 차번(정수), "partners": [상대 차번 3~4개, 유력 순], "quinellas": [[a,b],...3~4개], "trios": [[a,b,c],...1~2개],
 "pace": "라인 N개 · 자력형 N명 · 전개 판단 한 줄", "lines": [[라인1 차번들],[라인2],...], "reasons": {"차번": "한 줄 근거", ...}, "excluded": {"차번": "제외 이유", ...}, "story": "경주 시나리오 3~4문장", "market_view": "시장과 갈리는 점 2~3문장", "risk": "그림이 깨지는 조건 1~2문장", "confidence": 1~5}"""


def _ymd_token(date_s):
    return date_s.replace("-", "_")


def _card(doc):
    """analysis_log → (헤더, 출주표 텍스트). 금지 입력(배당·점수·추천)은 넣지 않는다."""
    rp = doc.get("raw_profile") or {}
    ents = {int(e.get("no")): e for e in (rp.get("entries") or []) if e.get("no")}
    hs = {int(h.get("no")): h for h in (doc.get("horses") or []) if h.get("no")}
    line = [int(x) for x in (rp.get("line") or []) if x]
    nos = sorted(set(list(ents.keys()) + list(hs.keys())))
    head = "%s · %s · %s명 · 거리 %s · 노면 %s" % (doc.get("raceKey"), doc.get("date"), rp.get("fieldSize") or len(nos),
                                              rp.get("distance") or "?", rp.get("trackCond") or "?")
    rows = ["並び(라인 순서): " + "-".join(str(n) for n in line) if line else "並び: (미수집)"]
    for n in nos:
        e = ents.get(n, {}); h = hs.get(n, {})
        kr = e.get("kimariteRatio") or {}
        ch = e.get("chaku") or []
        rows.append("[%d] %s %s세 %s %s期 등급 %s | 각질 선언 %s(%s) 유형 %s | 決まり手 도주 %s%% 젖히기 %s%% 차입 %s%% 마크 %s%% (실적 %s) | 착별 1-2-3-외 %s 연대율 %s%% | 기어 %s | 최근착순(직전이 앞) %s | 직전개최 %s | 그전개최 %s" % (
            n, h.get("name") or "", e.get("age") or "?", e.get("area") or "?", e.get("ki") or "?", e.get("classGrade") or "?",
            e.get("declaredStyle") or "?", e.get("declaredStyleLabel") or "", e.get("styleType") or h.get("gait") or "?",
            kr.get("도주", "?"), kr.get("젖히기", "?"), kr.get("차입", "?"), kr.get("마크", "?"), e.get("kimarite"),
            "-".join(str(x) for x in ch) if ch else "?", e.get("rentai", "?"), e.get("gear", "?"),
            h.get("keirinPlacings") or [], (e.get("prev1") or "").strip(), (e.get("prev2") or "").strip()))
    return head, "\n".join(rows)


def _latest_tick(fn):
    od = FF._load(os.path.join(BASE, "data", "odds_history", fn + ".json")) or {}
    sn = [s for s in (od.get("snapshots") or []) if isinstance(s, dict) and s.get("minutes_before") is not None and s.get("quinella")]
    if not sn:
        return None
    sn.sort(key=lambda s: s.get("t") or 0)
    return sn[-1]


def _market_from_tick(t):
    m = {}
    for k, v in (t.get("quinella") or {}).items():
        try:
            o = float(v.get("odds") if isinstance(v, dict) else v)
            a, b = [int(x) for x in re.split(r"[+\-]", k)]
        except Exception:
            continue
        m[a] = min(m.get(a, 9e9), o); m[b] = min(m.get(b, 9e9), o)
    return [(n, m[n]) for n in sorted(m, key=lambda x: m[x])]


def ask(head, body, model):
    import anthropic
    key = FF._env("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY 없음(.env)")
    client = anthropic.Anthropic(api_key=key)
    msg = client.messages.create(model=model, system=SYSTEM, **FF._gen_kwargs(),
                                 messages=[{"role": "user", "content": "【경주】 %s\n\n【출주표】\n%s" % (head, body)}])
    txt = "".join(getattr(b, "text", "") for b in msg.content)
    m = re.search(r"\{.*\}", txt, flags=re.S)
    pred = json.loads(m.group(0)) if m else {"raw": txt}
    return pred, {"in": getattr(msg.usage, "input_tokens", None), "out": getattr(msg.usage, "output_tokens", None)}


def _paths(date_s, rk):
    ymd = date_s.replace("-", "")
    d = os.path.join(OUT_DIR, ymd)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, rk.replace(" ", "_") + ".json"), os.path.join(OUT_DIR, ymd + ".jsonl")


def forecast_one(date_s, rk, model=None, force=False, tick=None):
    path, jl = _paths(date_s, rk)
    if os.path.exists(path) and not force:
        return None
    fn = "%s_%s" % (_ymd_token(date_s), rk.replace(" ", "_"))
    doc = FF._load(os.path.join(BASE, "data", "analysis_log", fn + ".json")) or {}
    if doc.get("sport") != "cycle" or not (doc.get("raw_profile") or {}).get("entries"):
        print("[skip] %s 출주표 없음(sport=%s)" % (rk, doc.get("sport")))
        return None
    model = model or FF._env("KEIRIN_FORECAST_MODEL") or FF._env("FORM_FORECAST_MODEL", "claude-opus-5")
    head, body = _card(doc)
    tick = tick or _latest_tick(fn)
    mk = _market_from_tick(tick) if tick else []
    t0 = time.time()
    pred, usage = ask(head, body, model)
    rec = {"date": date_s, "race": rk, "track": rk.rsplit(" ", 1)[0], "rno": rk.rsplit(" ", 1)[1],
           "fetchedAt": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "tickMb": tick.get("minutes_before") if tick else None, "tickTime": tick.get("time") if tick else None,
           "model": model, "usage": usage, "latencySec": round(time.time() - t0, 1), "prompt_version": PROMPT_VERSION,
           "head": head, "bodyChars": len(body), "marketAtFetch": mk[:5], "prediction": pred}
    io.open(path, "w", encoding="utf-8").write(json.dumps(rec, ensure_ascii=False, indent=1))
    io.open(jl, "a", encoding="utf-8").write(json.dumps({k: v for k, v in rec.items() if k != "head"}, ensure_ascii=False) + "\n")
    p = pred if isinstance(pred, dict) else {}
    print("[예측] %s 축 %s 상대 %s 복승 %s 삼복승 %s 라인 %s conf %s (%s · %.0fs · in %s)" % (
        rk, p.get("axis"), p.get("partners"), p.get("quinellas"), p.get("trios"), p.get("lines"), p.get("confidence"),
        model, rec["latencySec"], usage.get("in")))
    return rec


def run_once(date_s=None, model=None):
    date_s = date_s or datetime.date.today().strftime("%Y-%m-%d")
    tok = _ymd_token(date_s)
    due = []
    for p in glob.glob(os.path.join(BASE, "data", "odds_history", tok + "_*.json*")):
        fn = os.path.basename(p).split(".json")[0]
        rk = fn[len(tok) + 1:].replace("_", " ")
        path, _ = _paths(date_s, rk)
        if os.path.exists(path):
            continue
        t = _latest_tick(fn)
        if not t:
            continue
        mb = t.get("minutes_before")
        if mb is None or not (LEAD_LO <= float(mb) <= LEAD_HI):
            continue
        # 최신 틱이 오래됐으면(발주 지남) 건너뜀
        try:
            tt = datetime.datetime.strptime("%s %s" % (date_s, (t.get("time") or "")[:8]), "%Y-%m-%d %H:%M:%S")
            if (datetime.datetime.now() - tt).total_seconds() > 150:
                continue
        except Exception:
            pass
        due.append((float(mb), rk, t))
    n = 0
    for mb, rk, t in sorted(due):
        try:
            if forecast_one(date_s, rk, model=model, tick=t):
                n += 1
        except Exception as e:
            print("[오류] %s: %s" % (rk, e))
    try:
        io.open(STAMP, "w").write(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    except Exception:
        pass
    return n


def grade(date_s=None):
    files = sorted(glob.glob(os.path.join(OUT_DIR, "*", "*.json")))
    if date_s:
        files = [f for f in files if os.sep + date_s.replace("-", "") + os.sep in f]
    n_new = 0
    for f in files:
        rec = FF._load(f) or {}
        if rec.get("result") or not isinstance(rec.get("prediction"), dict):
            continue
        fn = "%s_%s" % (_ymd_token(rec["date"]), rec["race"].replace(" ", "_"))
        doc = FF._load(os.path.join(BASE, "data", "analysis_log", fn + ".json")) or {}
        res = doc.get("result") or {}
        if not (res.get("1st") and res.get("2nd")):
            continue
        try:
            order = [int(res["1st"]), int(res["2nd"]), int(res.get("3rd") or 0)]
        except Exception:
            continue
        pay = res.get("payouts") or {}
        p = rec["prediction"]
        top2, top3 = set(order[:2]), set(order[:3])
        qs = [set(map(int, q)) for q in (p.get("quinellas") or []) if isinstance(q, (list, tuple)) and len(q) == 2]
        ts = [set(map(int, t)) for t in (p.get("trios") or []) if isinstance(t, (list, tuple)) and len(t) == 3]
        mk = [r[0] for r in (rec.get("marketAtFetch") or [])]
        rec["result"] = {"order": order, "quinella": pay.get("quinella"), "trifecta": pay.get("trifecta")}
        rec["grade"] = {"q_hit": any(q == top2 for q in qs), "q_n": len(qs), "trio_hit": any(t == top3 for t in ts), "trio_n": len(ts),
                        "axis_top2": p.get("axis") in top2, "market1_q_hit": len(mk) >= 2 and set(mk[:2]) == top2,
                        "market3_q_hit": len(mk) >= 3 and top2 <= set(mk[:3]), "market3_n": 3 if len(mk) >= 3 else 0}
        io.open(f, "w", encoding="utf-8").write(json.dumps(rec, ensure_ascii=False, indent=1))
        n_new += 1
    print("[채점·경륜] 새로 %d건" % n_new)
    summary()


def summary():
    files = sorted(glob.glob(os.path.join(OUT_DIR, "*", "*.json")))
    n = q = t = ax = m1 = m3 = 0; inv_q = inv_m3 = 0; ret_q = ret_m3 = 0.0; pays = []
    for f in files:
        rec = FF._load(f) or {}; g = rec.get("grade")
        if not g:
            continue
        n += 1; q += g["q_hit"]; t += g["trio_hit"]; ax += g["axis_top2"]; m1 += g["market1_q_hit"]; m3 += g["market3_q_hit"]
        inv_q += g["q_n"]; inv_m3 += g["market3_n"]
        pay = (rec.get("result") or {}).get("quinella")
        if pay:
            if g["q_hit"]:
                ret_q += float(pay); pays.append(float(pay))
            if g["market3_q_hit"]:
                ret_m3 += float(pay)
    tot = len([f for f in files if (FF._load(f) or {}).get("prediction")])
    print("경륜 전적표 예측 집계 — 예측 %d · 채점 %d%s" % (tot, n, "  ⚠판정불가(적중<30)" if q < 30 else ""))
    if n:
        pays.sort(reverse=True)
        print("  복승 적중 %d/%d (%.1f%%) · 삼복승 %d · 축 1·2착 %d (%.1f%%)" % (q, n, 100.0 * q / n, t, ax, 100.0 * ax / n))
        print("  시장 대조 — 최저 1조합 %d (%.1f%%) · 상위3두 3조합 %d (%.1f%%)" % (m1, 100.0 * m1 / n, m3, 100.0 * m3 / n))
        print("  회수(구좌=조합 1 · 확정배당) — 예측 %.1f%% (%d구좌) · 3제외 %.1f%% ↔ 시장3두 %.1f%% (%d구좌)" % (
            100.0 * ret_q / max(inv_q, 1), inv_q, 100.0 * sum(pays[3:]) / max(inv_q, 1), 100.0 * ret_m3 / max(inv_m3, 1), inv_m3))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--daemon", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--race", default=None, help='예: "코치 4경주"')
    ap.add_argument("--date", default=None, help="YYYY-MM-DD")
    ap.add_argument("--model", default=None)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--grade", action="store_true")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--card", action="store_true", help="출주표 텍스트만 출력(모델 호출 없음)")
    a = ap.parse_args()
    date_s = a.date or datetime.date.today().strftime("%Y-%m-%d")
    if a.race and a.card:
        fn = "%s_%s" % (_ymd_token(date_s), a.race.replace(" ", "_"))
        doc = FF._load(os.path.join(BASE, "data", "analysis_log", fn + ".json")) or {}
        h, b = _card(doc); print(h); print(b); return
    if a.race:
        rec = forecast_one(date_s, a.race, model=a.model, force=a.force)
        if rec:
            print(json.dumps(rec["prediction"], ensure_ascii=False, indent=1))
        return
    if a.grade:
        grade(a.date); return
    if a.summary:
        summary(); return
    if a.once:
        run_once(date_s, a.model); return
    if a.daemon:
        print("[daemon·경륜] 시작 · 최신 틱 mb %d~%d 인 경륜 경주 예측 · 모델 %s" % (
            LEAD_LO, LEAD_HI, FF._env("KEIRIN_FORECAST_MODEL") or FF._env("FORM_FORECAST_MODEL", "claude-opus-5")))
        last_grade = 0
        while True:
            try:
                run_once(None, a.model)
                if time.time() - last_grade > 1800:
                    grade(); last_grade = time.time()
            except Exception as e:
                print("[daemon 오류]", e)
            time.sleep(60)
    ap.print_help()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
