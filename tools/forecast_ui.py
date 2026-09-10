# -*- coding: utf-8 -*-
"""
전적표 예측 — 필요할 때만 보는 화면 (2026-09-09 대표 「내가 필요할 때만 분석을 받고 싶다」)

http://127.0.0.1:8012  에서 오늘 경마(keiba.go.jp 출마표)·경륜(서버 출주표) 경주 목록을 보고 「분석」을 누르면
그 자리에서 tools/form_forecast.py · tools/keirin_forecast.py 를 호출해 결과를 보여준다(60~80초).
🔴 app.py 와 무관한 별도 프로세스 · 127.0.0.1 전용 · 데몬이 이미 만든 예측이 있으면 그것을 먼저 보여준다(다시 분석 버튼 있음).
실행: python -u tools/forecast_ui.py   (포트 변경 FORECAST_UI_PORT)
"""
import os, sys, io, json, glob, html, datetime, threading, urllib.parse, re
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import form_forecast as FF
import keirin_forecast as KF
import forecast_review as RV

PORT = int(os.environ.get("FORECAST_UI_PORT", "8012"))
LOCK = threading.Lock()

CSS = """<style>body{font-family:system-ui,'Malgun Gothic',sans-serif;background:#0f172a;color:#e2e8f0;margin:0;padding:16px;font-size:15px}
a{color:#7dd3fc}h1{font-size:20px;margin:0 0 12px}h2{font-size:17px;margin:18px 0 8px;color:#fbbf24}
table{border-collapse:collapse;width:100%;max-width:980px}td,th{border-bottom:1px solid #1e293b;padding:6px 8px;text-align:left;vertical-align:top}
.btn{display:inline-block;padding:4px 10px;border-radius:6px;background:#2563eb;color:#fff;text-decoration:none;font-weight:600}
.btn.gray{background:#334155}.ok{color:#4ade80;font-weight:700}.no{color:#f87171;font-weight:700}.mut{color:#94a3b8}
.box{background:#1e293b;border-radius:10px;padding:12px 14px;margin:10px 0;max-width:980px}.big{font-size:18px;font-weight:800}
pre{white-space:pre-wrap;background:#0b1220;padding:10px;border-radius:8px}</style>"""


def _today_h():
    return datetime.date.today().strftime("%Y/%m/%d")


def _today_k():
    return datetime.date.today().strftime("%Y-%m-%d")


def _horse_rows():
    rows = []
    try:
        races = FF.todays_races(_today_h())
    except Exception as e:
        return rows, "경마 목록 실패: %s" % e
    now = datetime.datetime.now()
    for baba, rno, hm, name in races:
        d, path, _ = FF._paths(_today_h(), baba, rno)
        rec = FF._load(path) if os.path.exists(path) else None
        hh, mm = hm.split(":")
        st = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
        rows.append({"kind": "horse", "key": "%s|%d" % (baba, rno), "race": "%s %d경주" % (FF.BABA.get(baba, baba), rno),
                     "start": hm, "mb": int((st - now).total_seconds() // 60), "name": name, "rec": rec})
    rows.sort(key=lambda r: r["start"])
    return rows, None


def _keirin_rows():
    rows = []
    tok = KF._ymd_token(_today_k())
    for p in sorted(glob.glob(os.path.join(BASE, "data", "analysis_log", tok + "_*.json*"))):
        fn = os.path.basename(p).split(".json")[0]
        rk = fn[len(tok) + 1:].replace("_", " ")
        doc = FF._load(os.path.join(BASE, "data", "analysis_log", fn + ".json")) or {}
        if doc.get("sport") != "cycle":
            continue
        has_card = bool((doc.get("raw_profile") or {}).get("entries"))
        t = KF._latest_tick(fn)
        mb = t.get("minutes_before") if t else None
        res = doc.get("result") or {}
        path, _ = KF._paths(_today_k(), rk)
        rec = FF._load(path) if os.path.exists(path) else None
        rows.append({"kind": "keirin", "key": rk, "race": rk, "start": (t.get("time") or "")[:5] if t else "", "mb": mb,
                     "name": "출주표 %s · 결과 %s" % ("있음" if has_card else "없음", "%s-%s-%s" % (res.get("1st"), res.get("2nd"), res.get("3rd")) if res.get("1st") else "-"),
                     "rec": rec, "card": has_card})
    rows.sort(key=lambda r: (r["mb"] is None, -(r["mb"] or 0)))
    return rows


def _pred_html(rec):
    p = (rec or {}).get("prediction") or {}
    if not isinstance(p, dict) or "axis" not in p:
        return "<div class='box'>예측 본문 없음<pre>%s</pre></div>" % html.escape(json.dumps(p, ensure_ascii=False)[:2000])
    g = rec.get("grade"); res = rec.get("result")
    out = ["<div class='box'>",
           "<div class='big'>%s</div>" % html.escape(rec.get("race", "")),
           "<div class='mut'>예측 %s · 모델 %s · %ss · 시장(예측 시점) %s</div>" % (
               html.escape(rec.get("fetchedAt", "")), html.escape(str(rec.get("model"))), rec.get("latencySec"),
               "→".join(str(m[0]) for m in (rec.get("marketAtFetch") or [])[:5])),
           "<p><b>축 %s</b> → 상대 %s</p>" % (p.get("axis"), "·".join(str(x) for x in p.get("partners") or [])),
           "<p>복승 %s<br>삼복승 %s</p>" % (" · ".join("+".join(str(x) for x in q) for q in p.get("quinellas") or []),
                                         " · ".join("-".join(str(x) for x in t) for t in p.get("trios") or [])),
           "<p><b>전개</b> %s</p>" % html.escape(str(p.get("pace", "")))]
    va = rec.get("validation") or {}
    if va:
        first = (va.get("history") or [{}])[0].get("violations") or []
        out.append("<div class='mut'>코드 검증 %s · 시도 %s%s</div>" % ("<span class='ok'>통과</span>" if va.get("passed") else "<span class='no'>미통과</span>", va.get("attempts"),
                   (" · 최초 위반: " + html.escape(" / ".join(first))) if first else ""))
    sh = rec.get("shadow") or {}
    if isinstance(sh.get("prediction"), dict):
        spp = sh["prediction"]; sg = sh.get("grade") or {}
        out.append("<div class='mut'>그림자(%s): 축 %s → %s · 복승 %s%s</div>" % (html.escape(str(sh.get("model"))), spp.get("axis"), "·".join(str(x) for x in spp.get("partners") or []),
                   " · ".join("+".join(str(x) for x in q) for q in spp.get("quinellas") or []), (" · <span class='%s'>%s</span>" % ("ok" if sg.get("q_hit") else "no", "적중" if sg.get("q_hit") else "미적중")) if sg else ""))
    for k, lab in (("story", "시나리오"), ("market_view", "시장과 갈리는 점"), ("risk", "깨지는 조건")):
        if p.get(k):
            out.append("<p><b>%s</b> %s</p>" % (lab, html.escape(str(p[k]))))
    if p.get("lines"):
        out.append("<p><b>라인</b> %s</p>" % html.escape(" / ".join("-".join(str(x) for x in l) for l in p["lines"])))
    out.append("<table><tr><th>번호</th><th>근거</th></tr>")
    for k, v in (p.get("reasons") or {}).items():
        out.append("<tr><td>%s</td><td>%s</td></tr>" % (html.escape(str(k)), html.escape(str(v))))
    out.append("</table>")
    if p.get("excluded"):
        out.append("<p class='mut'><b>제외</b> " + " · ".join("%s: %s" % (html.escape(str(k)), html.escape(str(v))) for k, v in p["excluded"].items()) + "</p>")
    if res:
        q = res.get("quinella")
        out.append("<p><b>결과</b> %s · 복승 %s배 — 복승 <span class='%s'>%s</span> · 삼복승 <span class='%s'>%s</span></p>" % (
            "-".join(str(x) for x in res.get("order") or []), q if q else "?",
            "ok" if (g or {}).get("q_hit") else "no", "적중" if (g or {}).get("q_hit") else "미적중",
            "ok" if (g or {}).get("trio_hit") else "no", "적중" if (g or {}).get("trio_hit") else "미적중"))
    rv = (rec.get("review") or {}).get("llm")
    if rv:
        mr = (rec.get("review") or {}).get("machine") or {}
        out.append("<div class='box' style='background:#172554'><div class='big'>복기 — %s</div>" % html.escape(str(rv.get("verdict"))))
        out.append("<p>축 결과 <b>%s</b> · 정답마 위치 %s</p>" % (html.escape(str(mr.get("axis_result"))), html.escape(json.dumps(mr.get("answer_where"), ensure_ascii=False))))
        for m in rv.get("missed") or []:
            out.append("<p><b>놓친 %s번</b>(%s) — %s<br><span class='mut'>출마표 단서: %s</span></p>" % (
                m.get("no"), html.escape(str(m.get("where"))), html.escape(str(m.get("why_missed"))), html.escape(str(m.get("clue") or ("있었음" if m.get("clue_in_card") else "없었음")))))
        pc = rv.get("pace_check") or {}
        if pc:
            out.append("<p><b>전개</b> 예측: %s<br>실제: %s → <span class='%s'>%s</span></p>" % (
                html.escape(str(pc.get("predicted"))), html.escape(str(pc.get("actual"))), "ok" if pc.get("correct") else "no", "맞음" if pc.get("correct") else "틀림"))
        rt = rv.get("rule_tags") or {}
        out.append("<p>도운 규칙 <span class='ok'>%s</span> · 해친 규칙 <span class='no'>%s</span></p>" % (
            html.escape(" · ".join(rt.get("helped") or []) or "-"), html.escape(" · ".join(rt.get("hurt") or []) or "-")))
        out.append("<p><b>교훈</b> %s<br><b>규칙 수정 제안</b> %s</p></div>" % (html.escape(str(rv.get("lesson"))), html.escape(str(rv.get("rule_change")))))
    elif res:
        out.append("<p><a class='btn' href='/review?kind=%s&key=%s'>복기 만들기</a></p>" % (
            "horse" if "baba" in rec else "keirin", urllib.parse.quote(("%s|%s" % (rec.get("baba"), rec.get("rno"))) if "baba" in rec else rec.get("race", ""))))
    out.append("</div>")
    return "".join(out)


def page_review(kind, key):
    try:
        if kind == "horse":
            baba, rno = key.split("|"); d, path, _ = FF._paths(_today_h(), baba, int(rno))
        else:
            path, _ = KF._paths(_today_k(), key)
        with LOCK:
            RV.review_one(path, kind)
        return page_view(kind, key)
    except Exception as e:
        return CSS + "<p class='no'>복기 오류: %s</p><p><a href='/'>목록</a></p>" % html.escape(str(e))


def page_index():
    hrows, herr = _horse_rows()
    krows = _keirin_rows()
    h = ["<title>전적표 분석</title>", CSS, "<h1>전적표 분석 — 필요할 때 누르세요</h1>",
         "<p class='mut'>분석 한 건에 40~80초 걸립니다. 버튼을 누르면 화면이 잠시 멈춘 뒤 결과가 뜹니다. 이미 만들어진 예측이 있으면 「보기」로 바로 봅니다.</p>",
         "<h2>경마(지방) — 출마표는 발주 전 언제든 읽을 수 있습니다</h2>"]
    if herr:
        h.append("<p class='no'>%s</p>" % html.escape(herr))
    h.append("<table><tr><th>발주</th><th>경주</th><th>조건</th><th>남은 시간</th><th></th></tr>")
    for r in hrows:
        st = "%d분" % r["mb"] if r["mb"] is not None and r["mb"] >= 0 else "발주 후"
        btn = ("<a class='btn gray' href='/view?kind=horse&key=%s'>보기</a> " % urllib.parse.quote(r["key"])) if r["rec"] else ""
        btn += "<a class='btn' href='/run?kind=horse&key=%s%s'>%s</a>" % (urllib.parse.quote(r["key"]), "&force=1" if r["rec"] else "", "다시 분석" if r["rec"] else "분석")
        h.append("<tr><td>%s</td><td>%s</td><td class='mut'>%s</td><td>%s</td><td>%s</td></tr>" % (
            r["start"], html.escape(r["race"]), html.escape(r["name"][:40]), st, btn))
    h.append("</table>")
    h.append("<h2>경륜 — 서버가 출주표를 모으는 발주 10분 전부터 가능합니다</h2>")
    h.append("<table><tr><th>최근 틱</th><th>경주</th><th></th><th>남은 시간</th><th></th></tr>")
    for r in krows:
        st = ("%s분" % r["mb"]) if r["mb"] is not None else "-"
        btn = ("<a class='btn gray' href='/view?kind=keirin&key=%s'>보기</a> " % urllib.parse.quote(r["key"])) if r["rec"] else ""
        if r["card"]:
            btn += "<a class='btn' href='/run?kind=keirin&key=%s%s'>%s</a>" % (urllib.parse.quote(r["key"]), "&force=1" if r["rec"] else "", "다시 분석" if r["rec"] else "분석")
        h.append("<tr><td>%s</td><td>%s</td><td class='mut'>%s</td><td>%s</td><td>%s</td></tr>" % (
            r["start"], html.escape(r["race"]), html.escape(r["name"]), st, btn))
    h.append("</table><p class='mut'>집계: <a href='/summary'>경마·경륜 채점 집계</a></p>")
    return "".join(h)


def page_view(kind, key, force=False):
    try:
        if kind == "horse":
            baba, rno = key.split("|")
            rno = int(rno)
            d, path, _ = FF._paths(_today_h(), baba, rno)
            rec = None if force else (FF._load(path) if os.path.exists(path) else None)
            if rec is None:
                with LOCK:
                    rec = FF.forecast_one(_today_h(), baba, rno, force=True)
                FF.grade(_today_h().replace("/", ""))
                rec = FF._load(path)
        else:
            path, _ = KF._paths(_today_k(), key)
            rec = None if force else (FF._load(path) if os.path.exists(path) else None)
            if rec is None:
                with LOCK:
                    rec = KF.forecast_one(_today_k(), key, force=True)
                KF.grade(_today_k().replace("-", ""))
                rec = FF._load(path)
        if not rec:
            return CSS + "<p class='no'>예측을 만들지 못했습니다(출마표 없음 또는 오류). <a href='/'>목록</a></p>"
        return "<title>%s</title>" % html.escape(rec.get("race", "")) + CSS + "<p><a href='/'>← 목록</a></p>" + _pred_html(rec)
    except Exception as e:
        return CSS + "<p class='no'>오류: %s</p><p><a href='/'>목록</a></p>" % html.escape(str(e))


TRACK_ALIAS = {"타케오": "다케오", "고치": "코치", "가와사키": "카와사키", "가나자와": "카나자와", "오이": "오오이", "몬베쓰": "몬베츠",
               "히코다테": "하코다테", "후쿠시마": "후쿠시마", "후크시마": "후쿠시마"}
HORSE_BABA = {}
for _b, _n in FF.BABA.items():
    HORSE_BABA[_n] = _b
HORSE_BABA.update({"오이": "20", "가와사키": "21", "가나자와": "22", "고치": "31", "몬베쓰": "36"})


def resolve_key(rk, sport):
    """배당판 raceKey('타케오 6경주') + 종목 → (kind, key). 경륜은 오늘 analysis_log 토큰으로, 경마는 babaCode 로."""
    m = re.match(r"^\s*([가-힣]{2,7})(?:\s*\[[^\]\s]{1,3}\])?\s*(\d{1,2})\s*(?:경주|R)?\s*$", str(rk or ""))
    if not m:
        return None, None, "경주명을 못 읽음: %r" % rk
    track, rno = m.group(1), int(m.group(2))
    cands = [track, TRACK_ALIAS.get(track, track)]
    tok = KF._ymd_token(_today_k())
    if sport in ("", None, "cycle", "keirin"):
        for t in cands:
            rk2 = "%s %d경주" % (t, rno)
            fn = os.path.join(BASE, "data", "analysis_log", "%s_%s_%d경주.json" % (tok, t, rno))
            doc = FF._load(fn) or {}
            if doc.get("sport") == "cycle":
                return "keirin", rk2, None
        if sport in ("cycle", "keirin"):
            return None, None, "경륜 출주표가 아직 없음(서버가 발주 10분 전부터 모읍니다): %s %d경주" % (track, rno)
    for t in cands:
        if t in HORSE_BABA:
            return "horse", "%s|%d" % (HORSE_BABA[t], rno), None
    return None, None, "지원하지 않는 경기장/종목: %s (%s)" % (track, sport)


def api_forecast(qs):
    rk = qs.get("rk", [""])[0]; sport = qs.get("sport", [""])[0]; force = qs.get("force", ["0"])[0] == "1"
    kind, key, err = resolve_key(rk, sport)
    if err:
        return {"ok": False, "error": err}
    try:
        if kind == "horse":
            baba, rno = key.split("|"); rno = int(rno)
            d, path, _ = FF._paths(_today_h(), baba, rno)
            cached = os.path.exists(path) and not force
            if not cached:
                with LOCK:
                    FF.forecast_one(_today_h(), baba, rno, force=True)
                try: FF.grade(_today_h().replace("/", ""))
                except Exception: pass
            rec = FF._load(path)
        else:
            path, _ = KF._paths(_today_k(), key)
            cached = os.path.exists(path) and not force
            if not cached:
                with LOCK:
                    KF.forecast_one(_today_k(), key, force=True)
                try: KF.grade(_today_k().replace("-", ""))
                except Exception: pass
            rec = FF._load(path)
        if not rec:
            return {"ok": False, "error": "예측을 만들지 못함(출마표 없음/오류)"}
        return {"ok": True, "kind": kind, "race": rec.get("race"), "cached": cached, "fetchedAt": rec.get("fetchedAt"), "model": rec.get("model"),
                "prediction": rec.get("prediction"), "validation": {"passed": (rec.get("validation") or {}).get("passed")},
                "result": rec.get("result"), "grade": rec.get("grade"), "marketAtFetch": rec.get("marketAtFetch")}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


def page_summary():
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        FF.grade(); KF.grade(); RV.stats()
    return "<title>채점 집계</title>" + CSS + "<p><a href='/'>← 목록</a></p><pre>%s</pre>" % html.escape(buf.getvalue())


class H(BaseHTTPRequestHandler):
    def log_message(self, fmt, *a):
        pass

    def do_GET(self):
        if self.client_address[0] not in ("127.0.0.1", "::1"):
            self.send_response(403); self.end_headers(); return
        u = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(u.query)
        if u.path == "/":
            body = page_index()
        elif u.path in ("/run", "/view"):
            body = page_view(qs.get("kind", ["horse"])[0], qs.get("key", [""])[0], force=(qs.get("force", ["0"])[0] == "1"))
        elif u.path == "/review":
            body = page_review(qs.get("kind", ["horse"])[0], qs.get("key", [""])[0])
        elif u.path == "/summary":
            body = page_summary()
        elif u.path == "/api/forecast":
            data = json.dumps(api_forecast(qs), ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers(); self.wfile.write(data); return
        else:
            self.send_response(404); self.end_headers(); return
        data = ("<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>" + body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("[forecast_ui] http://127.0.0.1:%d" % PORT)
    HTTPServer(("127.0.0.1", PORT), H).serve_forever()
