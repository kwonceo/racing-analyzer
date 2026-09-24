# -*- coding: utf-8 -*-
"""대표 복기 입력 화면 (3층) — 「내가 봤으면 어느 말 · 왜 · 이 경주는 터질 것 같은가」를 발주 전에, 「왜 맞았나·놓쳤나」를 발주 후에 남긴다.

왜 (2026-09-25 대표 「a 마무리하면서 3층 만들어 · 매번 경기를 보니까 남길 수 있다 · 나중에 중요한 자료가 되도록 정밀하게」)
  두 달간 규칙 30여 개가 시장을 못 이겼다(전적 축 20종 엣지 1.00). 시장에 없는 정보원은 둘뿐이다 —
  서사 읽기(form_forecast)와 **대표의 눈**(9/08 대조: 냉대 자리 대표 2/2 · 분석기 0/2 · 나 0/2).
  그 판단이 카톡 한 마디로만 남아 데이터가 되지 않았다. 이 화면이 그것을 **측정 가능한 형태로** 쌓는다.

정밀하게 = 나중에 잴 수 있게
  ① 🔴 원칙 27 시각 — 저장 시점이 발주 전이면 pre(예측) · 후면 post(복기). 결과가 보였는지도 적는다. **성적은 pre 만으로 잰다.**
  ② 말마다 따로 태그 — 「6번 꾸준함 · 3번 상승세」가 구분돼야 태그별 입상률이 나온다. 정의는 9/08 form_tags 와 같다.
  ③ 경주 단위 판단 — 터짐 예상(0~3)·이유·예상 전개·자기 복승 픽. 「터지는 경주를 미리 아는가」를 처음으로 잰다.
  ④ 저장 시점 시장 스냅샷 — 최저 복승 상위 3조합·말별 시장 순위를 같이 박는다. 「시장을 이겼나」는 그때 시장이 있어야 잰다(원칙 8).
  ⑤ post 는 다른 질문 — 왜 맞았나·놓쳤나. 정답이 내 후보/축/명단에 있었나는 자동 계산해 저장한다.
  ⑥ append 전용 jsonl · schema_version · 같은 경주 재저장은 새 줄(최신 유효 · 지우지 않는다)

실행  python -u tools/review_ui.py      (포트 REVIEW_UI_PORT · 기본 8013 · 127.0.0.1 전용)
저장  logs/review_owner/<YYYYMMDD>.jsonl        조회 GET /list?date=YYYYMMDD
"""
import os, sys, io, json, glob, html, time, datetime, urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = int(os.environ.get("REVIEW_UI_PORT", "8013"))
OUT_DIR = os.environ.get("REVIEW_DIR") or os.path.join(BASE, "logs", "review_owner")
SCHEMA = 2
HTAGS = ["꾸준함", "회복형", "상승세", "반복착순", "라인", "급락", "전입", "기수교체", "냉대", "기타"]          # 말 단위
BURST = ["시장흩어짐", "난전(선행多)", "인기마 불안", "신마·정보없음", "거리·등급 변화", "마장·날씨", "기타"]       # 터짐 이유
FLOW = ["선행 유리", "추입 유리", "난전", "한 말 독주", "모름"]                                             # 예상 전개
POST = ["예상대로", "전개가 달랐다", "막판 돈 몰림", "정보가 없었다", "사고·낙마", "시장이 맞았다", "내가 틀렸다"]    # 복기


def _load(p):
    try:
        return json.load(io.open(p, encoding="utf-8"))
    except Exception:
        return None


def _today():
    return datetime.date.today().strftime("%Y_%m_%d")


def _market(fn):
    """odds_history 동명 파일 → (deadline_epoch, 최저복승 상위3, 말별 시장순위) — 저장 시점 스냅샷용."""
    d = _load(os.path.join(BASE, "data", "odds_history", fn)) or {}
    dl = d.get("deadline_epoch")
    try:
        dl = float(dl) if dl else None
    except (TypeError, ValueError):
        dl = None
    q = None
    for s in (d.get("snapshots") or []):
        if s.get("quinella"):
            q = s["quinella"]
    pairs, w = [], {}
    items = q.items() if isinstance(q, dict) else [("+".join(map(str, x.get("combo") or [])), x.get("odds")) for x in (q or [])]
    for k, o in items:
        try:
            a, b = sorted(int(y) for y in str(k).split("+")); o = float(o)
        except Exception:
            continue
        if o <= 1.0:
            continue
        pairs.append((o, a, b)); w[a] = w.get(a, 0) + 1 / o; w[b] = w.get(b, 0) + 1 / o
    pairs.sort()
    rank = {str(n): i + 1 for i, (n, _) in enumerate(sorted(w.items(), key=lambda kv: -kv[1]))}
    return dl, [[a, b, o] for o, a, b in pairs[:3]], rank


def _races(tok):
    rows = []
    for p in sorted(glob.glob(os.path.join(BASE, "data", "analysis_log", tok + "_*.json"))):
        fn = os.path.basename(p)
        d = _load(p) or {}
        cp = d.get("corePicks") or {}
        r = d.get("result") or {}
        dl, top3, rank = _market(fn)
        rows.append({
            "file": fn, "rk": d.get("raceKey") or fn[11:-5].replace("_", " "),
            "sport": d.get("sport") or "", "cat": d.get("category") or "",
            "n": len(cp.get("rosterNos") or d.get("horses") or []),
            "kh": cp.get("keyHorses") or [],
            "fq": [q.get("combo") for q in (cp.get("finalQuinellas") or []) if isinstance(q, dict) and q.get("combo")],
            "res": [r.get("1st"), r.get("2nd"), r.get("3rd")] if r.get("1st") else None,
            "deadline": dl, "top3": top3, "rank": rank,
        })
    rows.sort(key=lambda x: (x["deadline"] or 9e12, x["file"]))
    return rows


def _saved(tok):
    p = os.path.join(OUT_DIR, tok.replace("_", "") + ".jsonl")
    out = {}
    if os.path.exists(p):
        for ln in io.open(p, encoding="utf-8"):
            try:
                e = json.loads(ln); out.setdefault(e["file"], {})[e["phase"]] = e
            except Exception:
                pass
    return out


def _save(e):
    os.makedirs(OUT_DIR, exist_ok=True)
    with io.open(os.path.join(OUT_DIR, e["date"].replace("_", "") + ".jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")


CSS = """<style>
body{font-family:-apple-system,'Malgun Gothic',sans-serif;background:#0f1320;color:#e8ecf5;margin:0;padding:12px;font-size:16px}
a{color:#8fb8ff;text-decoration:none} h2{margin:6px 0 10px;font-size:19px}
.row{display:flex;gap:8px;align-items:center;padding:10px;border-bottom:1px solid #222a3d}.row .rk{flex:1;font-weight:600}
.b{display:inline-block;padding:2px 7px;border-radius:6px;font-size:12px;margin-left:4px}.pre{background:#2b4a7a}.post{background:#6a3d1f}.done{background:#1f6a3d}.res{color:#9fb;font-size:13px}
.grid{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0}
.h{width:54px;height:54px;border-radius:12px;border:2px solid #3a4666;background:#182038;color:#fff;font-size:21px;font-weight:700;position:relative}
.h.on{background:#3f7bff;border-color:#8fb8ff}.h.kh{box-shadow:inset 0 0 0 3px #ffb347}.h.ax{background:#ffb347;color:#000}
.h small{position:absolute;bottom:2px;right:4px;font-size:10px;color:#cfd6ea}.h.on small,.h.ax small{color:#000}
.t{padding:9px 13px;border-radius:20px;border:2px solid #3a4666;background:#182038;color:#fff;font-size:14px}.t.on{background:#ff7a45;border-color:#ffb9a0}
.t.lv.on{background:#c23b3b}.sec{margin:14px 0 4px;color:#9aa6c4;font-size:13px}
.hrow{background:#141a2c;border-radius:10px;padding:8px;margin:6px 0}.hrow b{font-size:17px;margin-right:6px}
textarea,input[type=text]{width:100%;box-sizing:border-box;background:#182038;color:#fff;border:2px solid #3a4666;border-radius:10px;padding:10px;font-size:16px}
textarea{min-height:60px}.save{width:100%;padding:16px;font-size:18px;font-weight:700;border:0;border-radius:12px;background:#3f7bff;color:#fff;margin-top:12px}
.our{background:#141a2c;border-radius:10px;padding:10px;font-size:14px;line-height:1.7}.hint{font-size:12px;color:#7f8bab}
</style>"""


def page_index(tok):
    saved = _saved(tok); now = time.time(); rs = _races(tok)
    prev = (datetime.datetime.strptime(tok, "%Y_%m_%d") - datetime.timedelta(days=1)).strftime("%Y_%m_%d")
    L = ["<html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>복기 %s</title>%s</head><body>" % (tok, CSS),
         "<h2>🧭 대표 복기 · %s <span class='hint'>%d경주 · 예측 %d · 복기 %d</span></h2>" % (
             tok.replace("_", "-"), len(rs), sum(1 for v in saved.values() if "pre" in v), sum(1 for v in saved.values() if "post" in v)),
         "<div class='hint' style='margin-bottom:8px'><a href='/?d=%s'>어제</a> · <a href='/'>오늘</a> · <a href='/list?date=%s'>JSON</a> · 주황 테두리=분석기 유력마 · 작은 숫자=시장순위</div>" % (prev, tok.replace("_", ""))]
    for r in rs:
        e = saved.get(r["file"], {})
        phase = "pre" if (r["deadline"] and now < r["deadline"]) else "post"
        bd = ("<span class='b done'>예측✓</span>" if "pre" in e else "") + ("<span class='b done'>복기✓</span>" if "post" in e else "")
        if not e:
            bd = "<span class='b pre'>발주 전</span>" if phase == "pre" else "<span class='b post'>복기 가능</span>"
        res = ("<span class='res'>%s</span>" % "-".join(str(x) for x in r["res"] if x)) if r["res"] else ""
        L.append("<a class='row' href='/r?f=%s'><span class='rk'>%s <small class='hint'>%s</small></span>%s %s</a>"
                 % (urllib.parse.quote(r["file"]), html.escape(r["rk"]), "경륜" if r["sport"] == "cycle" else "경마", res, bd))
    return "".join(L) + "</body></html>"


def _chips(cls, names, on, attr):
    return "".join("<button type='button' class='%s%s' data-%s='%s' onclick='this.classList.toggle(\"on\")'>%s</button>"
                   % (cls, " on" if n in on else "", attr, html.escape(n), html.escape(n)) for n in names)


def page_race(fn):
    tok = fn[:10]
    r = {x["file"]: x for x in _races(tok)}.get(fn)
    if not r:
        return "<p>경주 없음</p>"
    sv = _saved(tok).get(fn, {})
    now = time.time()
    phase = "pre" if (r["deadline"] and now < r["deadline"]) else "post"
    e = sv.get(phase) or sv.get("pre") or {}
    pre = sv.get("pre")
    picks = [int(x) for x in (e.get("picks") or [])]
    tags = e.get("horse_tags") or {}
    kh = set(int(x) for x in r["kh"])
    left = ("발주까지 %d분" % ((r["deadline"] - now) / 60)) if (r["deadline"] and now < r["deadline"]) else ""
    L = ["<html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>%s</title>%s</head><body>" % (html.escape(r["rk"]), CSS),
         "<div><a href='/?d=%s'>← 목록</a></div><h2>%s <span class='b %s'>%s</span> <span class='hint'>%s</span></h2>" % (
             tok, html.escape(r["rk"]), phase, "발주 전 · 예측" if phase == "pre" else "발주 후 · 복기", left),
         "<div class='our'>분석기 유력마 <b>%s</b> · 명단 %s<br>시장 최저 %s<br>결과 <b>%s</b>%s</div>" % (
             "·".join(str(x) for x in r["kh"]) or "-",
             " ".join("%s+%s" % tuple(c[:2]) for c in r["fq"] if isinstance(c, list) and len(c) >= 2) or "-",
             " ".join("%d+%d(%.1f)" % (a, b, o) for a, b, o in r["top3"]) or "-",
             "-".join(str(x) for x in r["res"] if x) if r["res"] else "(아직)",
             ("<br><span class='hint'>내 예측: %s · 축 %s · 터짐 %s</span>" % ("·".join(map(str, pre.get("picks") or [])), pre.get("axis") or "-", pre.get("burst_level"))) if (phase == "post" and pre) else ""),
         "<form method='post' action='/save'><input type='hidden' name='file' value='%s'>" % html.escape(fn)]
    # ── 말 고르기
    L.append("<div class='sec'>내가 봤으면 — 마번 (한 번 = 후보 · 두 번 = 축)</div><div class='grid'>")
    for no in range(1, max(r["n"], 1) + 1):
        cls = "h" + (" ax" if e.get("axis") == no else (" on" if no in picks else "")) + (" kh" if no in kh else "")
        L.append("<button type='button' class='%s' data-no='%d' onclick='cyc(this)'>%d<small>%s</small></button>" % (cls, no, no, r["rank"].get(str(no), "")))
    L.append("</div><div id='hrows'></div>")
    # ── 경주 단위 (pre 전용이지만 post 에서도 보이게 · 참고)
    lv = e.get("burst_level")
    L.append("<div class='sec'>이 경주 터질 것 같은가 (0 없음 · 1 약간 · 2 높음 · 3 확신)</div><div class='grid'>"
             + "".join("<button type='button' class='t lv%s' data-lv='%d' onclick='lvl(this)'>%d</button>" % (" on" if lv == i else "", i, i) for i in range(4)) + "</div>")
    L.append("<div class='sec'>터질 이유</div><div class='grid'>%s</div>" % _chips("t", BURST, set(e.get("burst_why") or []), "bw"))
    L.append("<div class='sec'>예상 전개</div><div class='grid'>%s</div>" % _chips("t", FLOW, set([e.get("flow")] if e.get("flow") else []), "fl"))
    L.append("<div class='sec'>내 복승 픽 (예: 1+5, 3+7 · 비우면 후보끼리 전조합으로 저장)</div><input type='text' name='my_pairs' value='%s' placeholder='1+5, 3+7'>" % html.escape(e.get("my_pairs_raw") or ""))
    if phase == "post":
        L.append("<div class='sec'>복기 — 왜 그렇게 됐나</div><div class='grid'>%s</div>" % _chips("t", POST, set(e.get("post_why") or []), "pw"))
    L.append("<div class='sec'>메모</div><textarea name='note' placeholder='예: 6번 전적 1위인데 시장 9위 · 선행 셋이라 추입 유리'>%s</textarea>" % html.escape(e.get("note") or ""))
    for k in ("picks", "axis", "htags", "burst_level", "burst_why", "flow", "post_why"):
        L.append("<input type='hidden' name='%s' id='%s'>" % (k, k))
    L.append("<button class='save' onclick='return fin()'>저장 (%s)</button></form>" % ("예측" if phase == "pre" else "복기"))
    L.append("<script>var HT=%s,TG=%s;" % (json.dumps(HTAGS, ensure_ascii=False), json.dumps({str(k): v for k, v in tags.items()}, ensure_ascii=False)))
    L.append("""
function cyc(b){if(b.classList.contains('ax')){b.classList.remove('ax','on')}else if(b.classList.contains('on')){document.querySelectorAll('.h.ax').forEach(x=>{x.classList.remove('ax');x.classList.add('on')});b.classList.remove('on');b.classList.add('ax')}else{b.classList.add('on')}rows()}
function rows(){var box=document.getElementById('hrows');box.innerHTML='';[...document.querySelectorAll('.h.on,.h.ax')].forEach(b=>{var no=b.dataset.no,cur=TG[no]||[];
 var d=document.createElement('div');d.className='hrow';d.innerHTML='<b>'+no+'번'+(b.classList.contains('ax')?' 축':'')+'</b>'+HT.map(t=>'<button type="button" class="t'+(cur.includes(t)?' on':'')+'" data-h="'+no+'" data-t="'+t+'" onclick="this.classList.toggle(\\'on\\')">'+t+'</button>').join(' ');box.appendChild(d)})}
function lvl(b){document.querySelectorAll('.t.lv').forEach(x=>x.classList.remove('on'));b.classList.add('on')}
function fin(){var ax=document.querySelector('.h.ax');document.getElementById('axis').value=ax?ax.dataset.no:'';
 document.getElementById('picks').value=[...document.querySelectorAll('.h.on,.h.ax')].map(b=>b.dataset.no).join(',');
 var ht={};document.querySelectorAll('#hrows .t.on').forEach(b=>{(ht[b.dataset.h]=ht[b.dataset.h]||[]).push(b.dataset.t)});document.getElementById('htags').value=JSON.stringify(ht);
 var lv=document.querySelector('.t.lv.on');document.getElementById('burst_level').value=lv?lv.dataset.lv:'';
 document.getElementById('burst_why').value=[...document.querySelectorAll('.t[data-bw].on')].map(b=>b.dataset.bw).join(',');
 var fl=document.querySelector('.t[data-fl].on');document.getElementById('flow').value=fl?fl.dataset.fl:'';
 document.getElementById('post_why').value=[...document.querySelectorAll('.t[data-pw].on')].map(b=>b.dataset.pw).join(',');return true}
rows();</script></body></html>""")
    return "".join(L)


def _pairs(raw, picks):
    out = []
    for tokn in raw.replace(" ", "").split(","):
        if "+" in tokn:
            try:
                a, b = sorted(int(x) for x in tokn.split("+")[:2])
                if a != b:
                    out.append([a, b])
            except ValueError:
                pass
    if not out and len(picks) >= 2:
        ps = sorted(set(picks))
        out = [[ps[i], ps[j]] for i in range(len(ps)) for j in range(i + 1, len(ps))]
    return out


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body, ctype="text/html; charset=utf-8", code=200):
        b = body.encode("utf-8")
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path); qs = urllib.parse.parse_qs(u.query)
        if u.path == "/":
            return self._send(page_index(qs.get("d", [_today()])[0]))
        if u.path == "/r":
            return self._send(page_race(urllib.parse.unquote(qs.get("f", [""])[0])))
        if u.path == "/list":
            p = os.path.join(OUT_DIR, qs.get("date", [_today().replace("_", "")])[0] + ".jsonl")
            rows = [json.loads(l) for l in io.open(p, encoding="utf-8")] if os.path.exists(p) else []
            return self._send(json.dumps(rows, ensure_ascii=False, indent=1), "application/json; charset=utf-8")
        return self._send("not found", code=404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        f = urllib.parse.parse_qs(self.rfile.read(n).decode("utf-8"))
        g = lambda k: (f.get(k) or [""])[0]
        fn = g("file"); tok = fn[:10]
        r = {x["file"]: x for x in _races(tok)}.get(fn) or {}
        now = time.time(); dl = r.get("deadline")
        phase = "pre" if (dl and now < dl) else "post"          # 🔴 원칙 27 — 폼이 아니라 저장 시점으로 판정
        picks = [int(x) for x in g("picks").split(",") if x.strip().isdigit()]
        try:
            htags = json.loads(g("htags") or "{}")
        except Exception:
            htags = {}
        res = r.get("res")
        axis = int(g("axis")) if g("axis").isdigit() else None
        my_pairs = _pairs(g("my_pairs"), picks)
        e = {
            "schema": SCHEMA, "t": now, "at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
            "date": tok, "file": fn, "rk": r.get("rk"), "sport": r.get("sport"), "cat": r.get("cat"), "n": r.get("n"),
            "phase": phase, "deadline_epoch": dl, "sec_to_post": (dl - now) if dl else None, "result_visible": bool(res),
            # 말 단위
            "picks": picks, "axis": axis, "horse_tags": {str(k): v for k, v in htags.items() if v},
            # 경주 단위
            "burst_level": int(g("burst_level")) if g("burst_level").isdigit() else None,
            "burst_why": [x for x in g("burst_why").split(",") if x], "flow": g("flow") or None,
            "my_pairs_raw": g("my_pairs").strip(), "my_pairs": my_pairs,
            # 복기
            "post_why": [x for x in g("post_why").split(",") if x], "note": g("note").strip(),
            # 그 시점 스냅샷 (원칙 8·27)
            "market_top3": r.get("top3"), "market_rank": r.get("rank"),
            "our": {"kh": r.get("kh"), "fq": r.get("fq")}, "result": res,
        }
        if res and res[0] and res[1]:                                # 자동 채점(결과가 보일 때만)
            w = {int(res[0]), int(res[1])}
            e["score"] = {
                "winner_in_picks": len(w & set(picks)), "axis_in_top2": (axis in w) if axis else None,
                "my_pair_hit": any(set(p) == w for p in my_pairs),
                "our_pair_hit": any(set(int(x) for x in c[:2]) == w for c in (r.get("fq") or []) if isinstance(c, list) and len(c) >= 2),
                "winner_market_rank": [r.get("rank", {}).get(str(x)) for x in sorted(w)],
            }
        _save(e)
        self.send_response(303); self.send_header("Location", "/?d=" + tok); self.end_headers()


if __name__ == "__main__":
    print("[복기 화면] http://127.0.0.1:%d  저장→%s" % (PORT, OUT_DIR))
    HTTPServer(("127.0.0.1", PORT), H).serve_forever()
