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
LEAD_LO = 5                  # 5분 전까지(그 뒤는 마감이 가까워 건너뜀)

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

시장(単勝 오즈·人気)은 참고만 한다. 시장 1위를 축으로 삼는 것은 근거가 있을 때만 허용되고, 시장 순위를 그대로 베끼면 안 된다. 목표는 시장이 놓친 말을 상대에 넣는 것이다.

반드시 아래 JSON 하나만 출력한다(설명문 금지):
{"axis": 축 마번(정수), "partners": [상대 마번 3~4개, 유력 순], "quinellas": [[a,b],...3~4개], "trios": [[a,b,c],...1~2개],
 "pace": "선행 N두 · 판단 한 줄", "reasons": {"마번": "한 줄 근거", ...}, "excluded": {"마번": "제외 이유", ...}, "confidence": 1~5}"""


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
    for baba, rno, hm, name in todays_races(date_s):
        hh, mm = hm.split(":")
        st = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
        mb = (st - now).total_seconds() / 60.0
        if LEAD_LO <= mb <= LEAD_HI:
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


def _result_of(rec):
    """운영 data/analysis_log/<Y_M_D>_<track>_<N>경주.json 의 result (없으면 race_results)."""
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
    return None


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
