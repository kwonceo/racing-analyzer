# -*- coding: utf-8 -*-
"""
전적표 예측(관측 계층) — keiba.go.jp 출마표(DebaTable) 원문을 발주 전에 읽고 Claude 가
축·상대·근거를 낸다. 2026-09-09 대표 승인(「너처럼 분석해야 해 · 승인해 지금 바로」).

🔴 추천 경로 무개입. app.py 가 import 하지 않는다(리로더와 무관 · 별도 프로세스).
🔴 기록만 한다 — logs/form_forecast/<YYYYMMDD>/<경기장>_<N>경주.json + <YYYYMMDD>.jsonl
🔴 판정선은 Phase A/B 와 같다: 100경주에서 시장 상위 3두(출마표 단승 순) 대조. 못 넘으면 종결.

사용:
  python tools/form_forecast.py --daemon                 # 60초 주기 · 발주 6~14분 전 경주를 예측
  python tools/form_forecast.py --once                   # 한 바퀴만
  python tools/form_forecast.py --race 27 10 [--date 2026/09/09]   # 특정 경주 즉시(babaCode · 경주번호)
  python tools/form_forecast.py --grade                  # 결과 채점 + 집계
  python tools/form_forecast.py --summary                # 집계만
환경: .env 의 ANTHROPIC_API_KEY · FORM_FORECAST_MODEL(기본 claude-opus-5) · FORM_FORECAST_LEAD(기본 12분)
"""
import os, sys, re, io, json, gzip, time, glob, html, datetime, argparse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
OUT_DIR = os.path.join(BASE, "logs", "form_forecast")
STAMP = os.path.join(OUT_DIR, "_daemon_last.txt")
KEIBA = "https://www.keiba.go.jp/KeibaWeb/TodayRaceInfo/"
HDR = {"User-Agent": "Mozilla/5.0", "Accept": "*/*", "Accept-Language": "ja,en"}
FETCH_GAP_SEC = 2.0          # keiba.go.jp 예의(서버 수집과 별개 프로세스)
LEAD_HI = 14                 # 발주 14분 전부터
LEAD_LO = 3                  # 3분 전까지(예측 1건 50~60초 · 급한 경주부터 처리)

# k_babaCode → 우리 저장 토큰(analysis_log 파일명과 같은 것 · app.py _JP_BABA_CODE 의 첫 한글 별칭)
BABA = {"36": "몬베츠", "10": "모리오카", "11": "미즈사와", "18": "우라와", "19": "후나바시", "20": "오오이",
        "21": "카와사키", "22": "카나자와", "23": "카사마츠", "24": "나고야", "27": "소노다", "28": "히메지",
        "31": "코치", "32": "사가", "65": "오비히로"}

SYSTEM = """당신은 일본 지방경마 출마표(전적표)만으로 복승·삼복승을 짚는 분석가다. 배당판 신호·우리 분석기 추천은 주어지지 않는다.
출마표 원문(일본어)을 읽고 아래 재료를 반드시 순서대로 검토한 뒤 답한다.

① 전개: 각 말의 최근 통과순위(예 1-1-1-1 = 선행, 8-8-3-1 = 추입)로 각질을 정한다. 선행형이 3두 이상이면 앞이 싸우는 난전이므로 추입·선입형이 유리하다. 선행형이 1두뿐이면 그 말이 유리하다.
② 등급 이동: 이번 경주 등급과 직전 등급을 비교한다(C2→C1 승급 첫 출주는 상승세 확인, B2→C1 강급은 실력 우위 가능).
③ 마장 적성: 오늘 馬場(良/稍重/重/不良)과 같은 마장에서의 착순.
④ 거리: 이번 거리 첫 출주(距 0-0-0-0)나 단거리(800~900m) 전문마의 1400m 이상 출주는 감점.
⑤ 휴양·복귀: 직전 경주와의 간격이 2개월 이상이면 복귀전, 복귀 2전째는 상승 여지.
⑥ 인기 대비 착순: 1·2인기로 연속 미입상이면 과열, 낮은 인기에서 입상이 반복되면 냉대.
⑦ 반복·꾸준함·회복: 직전 2전 같은 착순(3·3, 5·5), 최근 4전 모두 5착 이내, 대패 뒤 직전 회복, 기록이 계속 좋아지는 상승세.
⑧ 대전표: 출마표 안에서 같은 날 같은 경주를 뛴 말끼리의 우열.
⑨ 기수 감량(★☆▲◇)과 마체중 급변(±10kg).
⑩ 출走取消·除外 표시 말은 제외한다.

읽기 규칙(실전에서 틀렸던 것들 · 반드시 지킨다):
· 직전 경주를 선행(통과 1-1-x-x 또는 2-2-x-x)으로 입상(3착 이내)한 말은 나이·인기와 무관하게 상대에서 빼지 않는다.
· 같은 거리·같은 마장·같은 경기장에서 입상 실적이 있는 말을 직전 한 번의 대패로 버리지 않는다(휴양 뒤·게이트·마장 탓인지 본다).
· 상대 3~4두 중 최소 1두는 시장 5위 이하(単勝 순)의 냉대말로 넣고, 복승 조합 중 1개 이상은 그 냉대말과의 조합으로 한다. 근거 없이 넣지 말고 위 ①~⑨ 중 무엇에 해당하는지 reasons 에 적는다.
· 근거(reasons)에 적은 말은 조합에도 반영한다. 근거에는 쓰고 조합에서 빠뜨리는 것을 금지한다.
· 복승 3~4개 중 1개는 축을 빼고 상대 상위 2두끼리 묶는다(축이 3착 밖으로 무너질 때의 보험). 삼복승은 축+상대 2두로 한다.
· 3세 말이 고령마 조건에서 직전 입상했으면 상승 여지를 가산한다.

시장(単勝 오즈·人気)은 참고만 한다. 시장 1위를 축으로 삼는 것은 근거가 있을 때만 허용되고, 시장 순위를 그대로 베끼면 안 된다(상위 4두를 그대로 축·상대로 두는 답은 실패다). 목표는 시장이 놓친 말을 상대에 넣는 것이다. 배당이 낮은 자리라도 근거가 확실하면 산다.


출력 언어·깊이 규칙(대표 지시 2026-09-09 「한국말로 · 약해 보인다」):
· 모든 문장은 한국어로 쓴다. 일본어 용어는 반드시 번역한다 — 良=양호 · 稍重=약간 다습 · 重=다습 · 不良=불량 · 人気=인기 · 直前/前走=직전 · 距=거리 실적 · 場=경기장 실적 · 牝=암말 · 牡=수말 · セン=거세마 · 逃げ=도주 · 差し=차입 · 追込=추입 · 先行=선행 · 番手=2번수 · 上がり=상3F. 경기장·마명은 한글로(川崎=카와사키 · 園田=소노다 · 浦和=우라와 · 船橋=후나바시 · 大井=오이 · 門別=몬베츠 · 金沢=카나자와 · 笠松=카사마츠 · 名古屋=나고야 · 高知=고치 · 佐賀=사가 · 姫路=히메지 · 盛岡=모리오카 · 水沢=미즈사와). 마명은 가타카나를 한글 음역으로.
· 근거(reasons)는 말마다 2~3문장의 완결된 이야기로 쓴다: ① 직전에 무엇을 했나(착순·통과순위·마장·인기·상대) ② 그것이 오늘 왜 통하나(전개·마장·거리·등급) ③ 무엇이 되면 들어오나. 숫자 나열이 아니라 판단을 쓴다.
· "story": 이 경주가 어떻게 흘러갈지 3~4문장(누가 앞을 잡고, 누가 그 뒤에 붙고, 결승선에서 누가 뻗는가 · 마장 영향).
· "market_view": 시장(단승 순)과 내 판단이 갈리는 지점 2~3문장 — 시장 상위 중 내가 내린 말과 이유, 시장이 놓친 냉대말과 이유. 시장과 같으면 「시장과 같다」고 쓰고 그 이유를 쓴다.
· "risk": 이 그림이 깨지는 조건 1~2문장(축이 무너지는 경우와 그때 살아남는 조합).

반드시 아래 JSON 하나만 출력한다(설명문 금지):
{"axis": 축 마번(정수), "partners": [상대 마번 3~4개, 유력 순], "quinellas": [[a,b],...3~4개], "trios": [[a,b,c],...1~2개],
 "pace": "선행 N두 · 판단 한 줄", "reasons": {"마번": "한 줄 근거", ...}, "excluded": {"마번": "제외 이유", ...}, "story": "경주 시나리오 3~4문장", "market_view": "시장과 갈리는 점 2~3문장", "risk": "그림이 깨지는 조건 1~2문장", "confidence": 1~5}"""


def _env(key, default=None):
    v = os.environ.get(key)
    if v:
        return v
    try:
        for line in io.open(os.path.join(BASE, ".env"), encoding="utf-8"):
            line = line.strip()
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'") or default
    except Exception:
        pass
    return default


def _get(url):
    req = urllib.request.Request(url, headers=HDR)
    raw = urllib.request.urlopen(req, timeout=20).read()
    time.sleep(FETCH_GAP_SEC)
    return raw.decode("utf-8", "replace")


def _text(h):
    t = re.sub(r"<script.*?</script>|<style.*?</style>", "", h, flags=re.S)
    t = re.sub(r"<[^>]+>", " ", t)
    t = html.unescape(t)
    t = re.sub(r"[ \t　]+", " ", t)
    t = re.sub(r"\n\s*\n+", "\n", t)
    return t


def race_list(date_s, baba):
    """RaceList → [(rno, 'HH:MM', 경주명·조건·두수 한 줄)]. 개최 없으면 []."""
    try:
        t = _text(_get("%sRaceList?k_raceDate=%s&k_babaCode=%s" % (KEIBA, date_s, baba)))
    except Exception as e:
        print("[race_list] %s %s 실패: %s" % (date_s, baba, e))
        return []
    t = re.sub(r"\s+", " ", t)
    out = []
    for m in re.finditer(r"(\d{1,2})R (\d{1,2}:\d{2}) (.{0,80}?)(?= \d{1,2}R \d{1,2}:\d{2} |$)", t):
        rno = int(m.group(1))
        if 1 <= rno <= 12 and not any(r[0] == rno for r in out):
            out.append((rno, m.group(2), m.group(3)[:80]))
    return out


def deba_text(date_s, baba, rno):
    """DebaTable → (헤더 한 줄, 압축 본문). 착별성적 5줄(全/左/右/場/距)을 한 토큰으로 접는다."""
    h = _get("%sDebaTable?k_raceNo=%d&k_raceDate=%s&k_babaCode=%s" % (KEIBA, rno, date_s, baba))
    t = _text(h)
    i = t.find("第%d競走" % rno)
    head = re.sub(r"\s+", " ", t[max(0, i - 30): i + 260]).strip()
    s, e = t.find("5走前"), t.find("コースレコード")
    body = t[s:e] if s >= 0 else t
    lines = [l.strip() for l in body.split("\n") if l.strip()]
    out, buf = [], []
    for l in lines:
        if re.fullmatch(r"(全|左|右|場|距)", l) or re.fullmatch(r"\d+-", l) or (
                buf and re.fullmatch(r"\d+", l) and len(buf) >= 4 and buf[-1].endswith("-")):
            buf.append(l)
            if len(buf) == 5:
                out.append("".join(buf))
                buf = []
        else:
            if buf:
                out.extend(buf)
                buf = []
            out.append(l)
    return head, " | ".join(out)


def _market_order(body):
    """출마표의 単勝 오즈(人気)로 시장 순서 — 예측 시점 시장 대조군. [(마번, 오즈, 인기)]"""
    rows = []
    for m in re.finditer(r"\| (\d{1,2}) \| ([^|]{1,40}?) \| [^|]{1,30}?\| ([\d.]+) \| \((\d+)人気\)", body):
        rows.append((int(m.group(1)), float(m.group(3)), int(m.group(4))))
    rows.sort(key=lambda x: x[1])
    return rows


def ask_claude(head, body, model):
    import anthropic
    key = _env("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY 없음(.env)")
    client = anthropic.Anthropic(api_key=key)
    user = "【경주】 %s\n\n【출마표 원문(압축 · 말마다 '| 마번 | 마명 | 기수 | 単勝 | (人気) | 着別성적 全/左/右/場/距 | 최고타임 | 최근 5주(착순·날짜·馬場·두수·경기장·거리·게이트) | 性齢 | 부담중량·조건 | 등급명 | 種牡馬·조교사 | 마체중(증감) | 최근5주 人気·체중·기수 | 타임·통과순위·상3F | ...')】\n%s" % (head, body)
    msg = client.messages.create(model=model, max_tokens=12000, system=SYSTEM,   # 5계열은 기본 thinking 블록이 먼저 나온다 — 1200 이면 본문이 비었다(2026-09-09 실측)
                                 messages=[{"role": "user", "content": user}])
    txt = "".join(getattr(b, "text", "") for b in msg.content)
    m = re.search(r"\{.*\}", txt, flags=re.S)
    pred = json.loads(m.group(0)) if m else {"raw": txt}
    usage = {"in": getattr(msg.usage, "input_tokens", None), "out": getattr(msg.usage, "output_tokens", None)}
    return pred, usage


def _paths(date_s, baba, rno):
    ymd = date_s.replace("/", "")
    d = os.path.join(OUT_DIR, ymd)
    os.makedirs(d, exist_ok=True)
    return d, os.path.join(d, "%s_%d경주.json" % (BABA.get(baba, baba), rno)), os.path.join(OUT_DIR, ymd + ".jsonl")


def forecast_one(date_s, baba, rno, start_hm=None, model=None, force=False):
    d, path, jl = _paths(date_s, baba, rno)
    if os.path.exists(path) and not force:
        return None
    model = model or _env("FORM_FORECAST_MODEL", "claude-opus-5")
    head, body = deba_text(date_s, baba, rno)
    if len(body) < 500:
        print("[skip] %s %s %dR 출마표 본문 짧음(%d)" % (date_s, baba, rno, len(body)))
        return None
    mk = _market_order(body)
    t0 = time.time()
    pred, usage = ask_claude(head, body, model)
    rec = {"date": date_s, "baba": baba, "track": BABA.get(baba, baba), "rno": rno,
           "race": "%s %d경주" % (BABA.get(baba, baba), rno), "start": start_hm,
           "fetchedAt": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "model": model, "usage": usage, "latencySec": round(time.time() - t0, 1),
           "head": head, "bodyChars": len(body), "marketAtFetch": mk[:5],
           "prediction": pred}
    io.open(path, "w", encoding="utf-8").write(json.dumps(rec, ensure_ascii=False, indent=1))
    io.open(jl, "a", encoding="utf-8").write(json.dumps({k: v for k, v in rec.items() if k != "head"}, ensure_ascii=False) + "\n")
    p = pred if isinstance(pred, dict) else {}
    print("[예측] %s 축 %s 상대 %s 복승 %s 삼복승 %s conf %s (%s · %.0fs · in %s)" % (
        rec["race"], p.get("axis"), p.get("partners"), p.get("quinellas"), p.get("trios"), p.get("confidence"),
        model, rec["latencySec"], usage.get("in")))
    return rec


def _today():
    return datetime.date.today().strftime("%Y/%m/%d")


_LIST_CACHE = {}


def todays_races(date_s):
    if date_s in _LIST_CACHE:
        return _LIST_CACHE[date_s]
    allr = []
    for baba in BABA:
        for rno, hm, name in race_list(date_s, baba):
            allr.append((baba, rno, hm, name))
    _LIST_CACHE[date_s] = allr
    print("[개최] %s: %d경주 · 경기장 %s" % (date_s, len(allr), sorted({BABA[b] for b, _, _, _ in allr})))
    return allr


def run_once(date_s=None, model=None):
    date_s = date_s or _today()
    now = datetime.datetime.now()
    n = 0
    due = []
    for baba, rno, hm, name in todays_races(date_s):
        hh, mm = hm.split(":")
        st = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
        mb = (st - now).total_seconds() / 60.0
        if LEAD_LO <= mb <= LEAD_HI:
            due.append((mb, baba, rno, hm))
    for mb, baba, rno, hm in sorted(due):          # 발주가 가까운 경주부터(한 건 50~60초라 순서가 중요하다)
        try:
            if forecast_one(date_s, baba, rno, hm, model=model):
                n += 1
        except Exception as e:
            print("[오류] %s %dR: %s" % (BABA.get(baba), rno, e))
    try:
        io.open(STAMP, "w").write(now.strftime("%Y-%m-%d %H:%M:%S"))
    except Exception:
        pass
    return n


def _load(p):
    for path, gz in ((p, False), (p + ".gz", True)):
        try:
            return json.load(gzip.open(path, "rt", encoding="utf-8")) if gz else json.load(io.open(path, encoding="utf-8"))
        except Exception:
            continue
    return None


def _result_from_keiba(date_s, baba, rno):
    """keiba.go.jp RaceMarkTable(성적표) → {order, quinella, trifecta}. 미확정이면 None."""
    try:
        t = _text(_get("%sRaceMarkTable?k_raceDate=%s&k_raceNo=%d&k_babaCode=%s" % (KEIBA, date_s, rno, baba)))
    except Exception as e:
        print("[keiba 결과] %s %s %dR 실패: %s" % (date_s, baba, rno, e))
        return None
    t = re.sub(r"\s+", " ", t)
    i = t.find("単勝 オッズ")
    if i < 0:
        return None
    seg = t[i:i + 3000]
    order = {}
    for m in re.finditer(r"(?:^| )([1-3]) (\d{1,2}) (\d{1,2}) (?=[^\d ])", seg):
        pl = int(m.group(1))
        if pl not in order:
            order[pl] = int(m.group(3))
    if not (order.get(1) and order.get(2)):
        return None
    q = re.search(r"馬連複 (\d{1,2})-(\d{1,2}) ([\d,]+)円", t)
    tr = re.search(r"三連複 (\d{1,2})-(\d{1,2})-(\d{1,2}) ([\d,]+)円", t)
    qv = round(int(q.group(3).replace(",", "")) / 100.0, 1) if q else None
    tv = round(int(tr.group(4).replace(",", "")) / 100.0, 1) if tr else None
    if q and {int(q.group(1)), int(q.group(2))} != {order[1], order[2]}:
        print("[keiba 결과] %s %dR 착순↔馬連複 불일치 %s vs %s — 배당 버림" % (BABA.get(baba), rno, order, q.groups()))
        qv = None
    return {"order": [order[1], order[2], order.get(3) or 0], "quinella": qv, "trifecta": tv, "src": "keiba"}


def _result_of(rec):
    """운영 data/analysis_log/<Y_M_D>_<track>_<N>경주.json 의 result → 없으면 race_results → 없으면 keiba.go.jp 성적표(발주 20분 뒤부터)."""
    y, m, d = rec["date"].split("/")
    fn = "%s_%s_%s_%s_%d경주.json" % (y, m, d, rec["track"], rec["rno"])
    for sub in ("analysis_log", "race_results"):
        doc = _load(os.path.join(BASE, "data", sub, fn)) or {}
        res = doc.get("result") if sub == "analysis_log" else doc
        if isinstance(res, dict) and res.get("1st") and res.get("2nd"):
            try:
                pay = (res.get("payouts") or {})
                return {"order": [int(res["1st"]), int(res["2nd"]), int(res.get("3rd") or 0)],
                        "quinella": pay.get("quinella"), "trifecta": pay.get("trifecta") or pay.get("trio")}
            except Exception:
                return None
    try:
        st = datetime.datetime.strptime("%s %s" % (rec["date"], rec.get("start") or "00:00"), "%Y/%m/%d %H:%M")
        if (datetime.datetime.now() - st).total_seconds() < 20 * 60:
            return None
    except Exception:
        pass
    return _result_from_keiba(rec["date"], rec["baba"], rec["rno"])


def grade(date_s=None):
    files = sorted(glob.glob(os.path.join(OUT_DIR, "*", "*.json")))
    if date_s:
        files = [f for f in files if os.sep + date_s.replace("/", "") + os.sep in f]
    n_new = 0
    for f in files:
        rec = _load(f) or {}
        if rec.get("result") or not isinstance(rec.get("prediction"), dict):
            continue
        res = _result_of(rec)
        if not res:
            continue
        p = rec["prediction"]
        top2 = set(res["order"][:2])
        top3 = set(res["order"][:3])
        qs = [set(map(int, q)) for q in (p.get("quinellas") or []) if isinstance(q, (list, tuple)) and len(q) == 2]
        ts = [set(map(int, t)) for t in (p.get("trios") or []) if isinstance(t, (list, tuple)) and len(t) == 3]
        mk = [r[0] for r in (rec.get("marketAtFetch") or [])]
        g = {"q_hit": any(q == top2 for q in qs), "q_n": len(qs),
             "trio_hit": any(t == top3 for t in ts), "trio_n": len(ts),
             "axis_top2": p.get("axis") in top2,
             "market1_q_hit": len(mk) >= 2 and set(mk[:2]) == top2,
             "market3_q_hit": len(mk) >= 3 and top2 <= set(mk[:3]),
             "market3_n": 3 if len(mk) >= 3 else 0}
        rec["result"] = res
        rec["grade"] = g
        io.open(f, "w", encoding="utf-8").write(json.dumps(rec, ensure_ascii=False, indent=1))
        n_new += 1
    print("[채점] 새로 %d건" % n_new)
    summary()


def summary():
    files = sorted(glob.glob(os.path.join(OUT_DIR, "*", "*.json")))
    n = q = t = ax = m1 = m3 = 0
    inv_q = inv_m3 = 0
    ret_q = ret_m3 = 0.0
    pays = []
    for f in files:
        rec = _load(f) or {}
        g = rec.get("grade")
        if not g:
            continue
        n += 1
        q += g["q_hit"]; t += g["trio_hit"]; ax += g["axis_top2"]; m1 += g["market1_q_hit"]; m3 += g["market3_q_hit"]
        inv_q += g["q_n"]; inv_m3 += g["market3_n"]
        pay = (rec.get("result") or {}).get("quinella")
        if pay:
            if g["q_hit"]:
                ret_q += float(pay); pays.append(float(pay))
            if g["market3_q_hit"]:
                ret_m3 += float(pay)
    tot = len([f for f in files if (_load(f) or {}).get("prediction")])
    print("전적표 예측 집계 — 예측 %d · 채점 %d%s" % (tot, n, "  ⚠판정불가(적중<30)" if q < 30 else ""))
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
    ap.add_argument("--race", nargs=2, metavar=("BABA", "RNO"))
    ap.add_argument("--date", default=None, help="YYYY/MM/DD")
    ap.add_argument("--model", default=None)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--grade", action="store_true")
    ap.add_argument("--summary", action="store_true")
    a = ap.parse_args()
    global LEAD_HI
    LEAD_HI = int(_env("FORM_FORECAST_LEAD", str(LEAD_HI)))
    if a.race:
        rec = forecast_one(a.date or _today(), a.race[0], int(a.race[1]), model=a.model, force=a.force)
        if rec:
            print(json.dumps(rec["prediction"], ensure_ascii=False, indent=1))
        return
    if a.grade:
        grade(a.date); return
    if a.summary:
        summary(); return
    if a.once:
        run_once(a.date, a.model); return
    if a.daemon:
        print("[daemon] 시작 · 발주 %d~%d분 전 예측 · 모델 %s" % (LEAD_LO, LEAD_HI, _env("FORM_FORECAST_MODEL", "claude-opus-5")))
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
