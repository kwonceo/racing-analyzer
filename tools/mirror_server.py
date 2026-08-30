#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""[읽기 전용 미러] 오버레이 내용을 **다른 PC 에서** 보기 위한 최소 서버 (2026-08-30 대표 승인).

🔴 설계 원칙 — 이 셋이 나머지를 다 정한다
  ① **app.py 를 한 줄도 안 건드린다.** 본 서버는 127.0.0.1:8011 그대로다.
     ⇒ `/admin`·`.env`·쓰기 API 는 이 프로세스에 **존재하지 않는다**. 뚫을 것이 없다.
  ② **Tailscale IP 에만 바인딩한다**(0.0.0.0 + 방화벽이 아니다).
     OS 가 다른 인터페이스에 아예 listen 하지 않으므로 방화벽 규칙보다 강한 보장이다.
     🔴 그 IP 를 못 찾으면 **기동을 거부한다**(fail closed) — 실수로 전체 노출될 수 없다.
  ③ **API 를 부르지 않고 파일만 읽는다.**
     `/api/odds/triple/analyze` 는 **재분석·저장 부작용**이 있다(CLAUDE.md 2026-07-30
     「마감 후 폴링 저장」 — 하루 종일 재분석이 돌아 Gemini 가 670회 호출된 그 경로).
     미러가 그것을 부르면 **조회가 데이터를 바꾼다.** 그래서 안 부른다.

⚠ 신선도: 본 서버가 analysis_log 를 저장하는 주기만큼 늦다(실측 수 초~수십 초).
  🔴 **마감 임박 판단에 쓰지 말 것. 관찰용이다.** 베팅은 배당판에서 한다.

⚠ 남는 위험(숨기지 않는다)
  🔴 Tailscale 계정에 연결된 **모든 기기**가 이 IP 에 닿는다. 토큰이 유일한 구분이다.
  🔴 토큰이 URL 에 있으면 브라우저 히스토리·북마크에 남는다.

실행:  python tools/mirror_server.py
되돌리기: 프로세스 종료 + 이 파일 삭제. app.py 무변경이라 흔적이 0 이다.
"""
import os
import io
import re
import json
import gzip
import time
import hmac
import socket
import secrets
import threading
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, "data")
LOGS = os.path.join(BASE, "logs")
TOKEN_FILE = os.path.join(DATA, "_mirror_token.txt")
ACCESS_LOG = os.path.join(LOGS, "mirror_access.log")
PORT = int(os.environ.get("MIRROR_PORT", "8014"))

# 🔴 화이트리스트 — 이 두 디렉터리 밖은 어떤 경로로도 못 읽는다
_DIRS = {"analysis_log": os.path.join(DATA, "analysis_log"),
         "odds_history": os.path.join(DATA, "odds_history")}
# 🔴 [2026-08-31] 보고 문서 — **정확한 파일명만** 담는다(패턴·디렉터리 순회 없음).
#   대표가 다른 PC 에서 3단 보고를 읽기 위한 것이다. 그 외 파일은 어떤 이름으로도 못 읽는다.
#   ⚠ CLAUDE.md 는 넣지 않는다 — 1MB+ 이고 내부 판단 기록이다.
_DOCS = {"report": os.path.join(BASE, "docs", "REPORT.md"),
         "next": os.path.join(BASE, "docs", "NEXT.md")}
DOC_MAX = 400 * 1024        # 그 이상은 앞부분만(최신이 위에 온다)
# 파일명 규격: 날짜_이름.json · 경로 구분자·상위 이동(..) 원천 차단
_NAME_RE = re.compile(r"^\d{4}_\d{2}_\d{2}_[^\\/:*?\"<>|]{1,80}\.json$")

_RATE = {}                      # {ip: deque[ts]} — IP 당 초당 5회
_RATE_LOCK = threading.Lock()
RATE_MAX, RATE_WIN = 5, 1.0


# ────────────────────────── 바인딩 (fail closed) ──────────────────────────
def tailscale_ip():
    """100.64.0.0/10 대역의 로컬 IPv4 를 찾는다. 없으면 None(→ 기동 거부)."""
    env = (os.environ.get("MIRROR_HOST") or "").strip()
    cand = [env] if env else []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            cand.append(info[4][0])
    except Exception:
        pass
    for ip in cand:
        p = str(ip).split(".")
        if len(p) == 4 and p[0] == "100":
            try:
                if 64 <= int(p[1]) <= 127:
                    return ip
            except ValueError:
                continue
    return None


# ────────────────────────── 토큰 ──────────────────────────
def load_token():
    """토큰을 읽는다. 없으면 **기동 시 1회만** 생성한다(요청 처리 중 쓰기 없음)."""
    try:
        t = io.open(TOKEN_FILE, encoding="utf-8").read().strip()
        if len(t) >= 20:
            return t, False
    except Exception:
        pass
    t = secrets.token_urlsafe(32)
    with io.open(TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(t)
    try:
        os.chmod(TOKEN_FILE, 0o600)
    except Exception:
        pass
    return t, True


TOKEN, _NEW = load_token()


def token_ok(q):
    got = (q.get("t") or [""])[0]
    return bool(got) and hmac.compare_digest(str(got), TOKEN)


# ────────────────────────── 읽기 (요청 중 쓰기 코드 없음) ──────────────────────────
def read_json(kind, name):
    d = _DIRS.get(kind)
    if not d or not _NAME_RE.match(name or ""):
        return None
    p = os.path.normpath(os.path.join(d, name))
    if os.path.dirname(p) != os.path.normpath(d):      # 정규화 후에도 그 폴더인가
        return None
    try:
        with io.open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        pass
    try:
        with gzip.open(p + ".gz", "rt", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def read_doc(kind):
    """보고 문서를 읽는다. 🔴 이름이 화이트리스트에 **정확히** 있을 때만."""
    p = _DOCS.get(kind)
    if not p or not os.path.exists(p):
        return None
    try:
        with io.open(p, encoding="utf-8") as f:
            return f.read(DOC_MAX)
    except Exception:
        return None


def md_lite(t):
    """마크다운을 아주 가볍게 HTML 로. 🔴 **먼저 이스케이프**한 뒤에만 태그를 만든다."""
    t = (t or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    out, code = [], False
    for ln in t.split("\n"):
        if ln.startswith("```"):
            out.append("</pre>" if code else "<pre>")
            code = not code
            continue
        if code:
            out.append(ln)
            continue
        s2 = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", ln)
        s2 = re.sub(r"`([^`]+)`", r"<code>\1</code>", s2)
        m = re.match(r"^(#{1,4})\s+(.*)$", s2)
        if m:
            lv = min(4, len(m.group(1))) + 1
            out.append("<h%d>%s</h%d>" % (lv, m.group(2), lv))
        elif not s2.strip():
            out.append("<div class='sp'></div>")
        else:
            out.append("<div>%s</div>" % s2)
    if code:
        out.append("</pre>")
    return "\n".join(out)


def today_files():
    d = _DIRS["analysis_log"]
    pre = time.strftime("%Y_%m_%d") + "_"
    try:
        names = [x for x in os.listdir(d) if x.startswith(pre) and x.endswith(".json")]
    except Exception:
        return []
    names.sort(key=lambda n: os.path.getmtime(os.path.join(d, n)), reverse=True)
    return names


def _combo(c):
    try:
        return "+".join(str(int(x)) for x in (c or []))
    except Exception:
        return ""


def race_view(name):
    d = read_json("analysis_log", name)
    if not isinstance(d, dict):
        return None
    cp = d.get("corePicks") or {}
    dc = cp.get("displayedCombos") or {}
    # 배당은 finalQuinellas/bmedSpecial 에서 찾아 붙인다(판정 명단은 마번만 담는다)
    pool = {}
    for src in ("finalQuinellas", "bmedSpecial", "quinellaRef"):
        for q in (cp.get(src) or []):
            if isinstance(q, dict) and q.get("combo"):
                pool.setdefault(_combo(q["combo"]), q)
    quin = []
    for c in (dc.get("quinellas") or []):
        k = _combo(c)
        q = pool.get(k) or {}
        quin.append({"combo": k, "odds": q.get("odds"), "stars": q.get("stars")})
    dia = [{"combo": _combo(s.get("combo")), "odds": s.get("odds")}
           for s in (cp.get("bmedSpecial") or [])[:1] if s.get("combo")]
    tri = [{"combo": _combo(c)} for c in (dc.get("trifectas") or [])]
    ss = d.get("strong_signals") or {}
    sigs = [{"label": str(x.get("label") or ""), "detail": str(x.get("detail") or "")[:60],
             "level": str(x.get("level") or "")} for x in (ss.get("signals") or [])][:6]
    drops = []
    for x in (d.get("drops_raw") or [])[:6]:
        if isinstance(x, dict):
            try:
                pct = round(float(x.get("pct") or x.get("dropPct") or 0), 1)
            except (TypeError, ValueError):
                pct = 0
            drops.append("%s %s%%" % (x.get("combo") or x.get("no") or "", pct))
    # 수집 상태 — odds_history 마지막 스냅샷
    oh = read_json("odds_history", name) or {}
    sn = [s for s in (oh.get("snapshots") or []) if isinstance(s, dict) and s.get("quinella")]
    last = sn[-1] if sn else {}
    rg = cp.get("raceGrade") or {}
    try:
        file_at = time.strftime("%H:%M:%S", time.localtime(
            os.path.getmtime(os.path.join(_DIRS["analysis_log"], name))))
    except Exception:
        file_at = "—"
    return {
        "raceKey": d.get("raceKey") or name,
        "sport": d.get("sport"), "category": d.get("category"),
        "grade": rg.get("label"), "gradeBasis": rg.get("basis"),
        "confidence": rg.get("confidence"), "confTop1": cp.get("confTop1"),
        "horseCount": cp.get("raceHorseCount"), "summary": str(d.get("summary") or "")[:140],
        "keyHorses": cp.get("keyHorses") or [],
        "quinellas": quin, "dia": dia, "trifectas": tri,
        "signals": sigs, "drops": drops,
        "ticks": len(sn), "src": last.get("src"), "tickTime": last.get("time"),
        "minutesBefore": last.get("minutes_before"),
        "afterClose": bool(last.get("after_close")),
        "fileAt": file_at,
    }


def payload(sel=None):
    names = today_files()
    cur = sel if (sel and sel in names) else (names[0] if names else None)
    return {"now": time.strftime("%H:%M:%S"), "date": time.strftime("%Y-%m-%d"),
            "races": [{"file": n, "label": n[11:-5].replace("_", " ")} for n in names[:24]],
            "current": (race_view(cur) if cur else None), "currentFile": cur}


# ────────────────────────── HTML (인라인 · 정적 파일 서빙 없음) ──────────────────────────
HTML = u"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>실시간 분석 미러</title><style>
:root{color-scheme:dark}
body{margin:0;background:#0b1020;color:#e6edf7;font:14px/1.55 -apple-system,"Malgun Gothic",sans-serif}
.wrap{max-width:720px;margin:0 auto;padding:14px}
h1{font-size:16px;margin:0 0 2px}.sub{color:#8b9bb4;font-size:12px}
.card{background:#141b30;border:1px solid #222d4a;border-radius:10px;padding:12px;margin:10px 0}
.row{display:flex;justify-content:space-between;gap:8px;padding:5px 0;border-bottom:1px solid #1d2740}
.row:last-child{border-bottom:0}
.k{color:#8b9bb4}.v{font-weight:600}
.big{font-size:17px;font-weight:700;margin-bottom:4px}
.tag{display:inline-block;padding:1px 7px;border-radius:99px;background:#1d2740;color:#a9bcd8;font-size:11px;margin-left:6px}
.red{color:#ff6b6b}.grn{color:#5ee0a0}.amb{color:#ffc46b}
select{background:#141b30;color:#e6edf7;border:1px solid #222d4a;border-radius:7px;padding:7px;width:100%}
.warn{background:#2a1a12;border-color:#5a3a20;color:#ffc46b;font-size:12px}
</style></head><body><div class="wrap">
<h1>실시간 분석 미러</h1>
<div class="sub" id="hd">불러오는 중…</div>
<div class="sub" style="padding:6px 0"><a id="rp" href="#" style="color:#7dd3fc;text-decoration:none">📄 보고 읽기 →</a></div>
<div class="card warn">⚠ 서버 저장분을 읽습니다 — 배당판보다 <b>수 초~수십 초 늦습니다</b>.
<b>관찰용</b>이며 마감 임박 판단에 쓰지 마십시오.</div>
<select id="sel"></select>
<div id="body"></div>
</div><script>
var T=new URLSearchParams(location.search).get('t')||'';
function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})}
function row(k,v,cls){return '<div class="row"><span class="k">'+esc(k)+'</span><span class="v '+(cls||'')+'">'+v+'</span></div>'}
function stars(n){var s='';for(var i=0;i<(n||0);i++)s+='★';return s}
function draw(d){
  document.getElementById('hd').textContent=d.date+' · 갱신 '+d.now+' · 10초마다 자동';
  var s=document.getElementById('sel');
  if(s.options.length!==d.races.length){s.innerHTML=d.races.map(function(r){return '<option value="'+esc(r.file)+'">'+esc(r.label)+'</option>'}).join('')}
  if(d.currentFile&&!s.dataset.locked) s.value=d.currentFile;
  var c=d.current,h='';
  if(!c){document.getElementById('body').innerHTML='<div class="card">오늘 분석된 경주가 없습니다.</div>';return}
  var mb=c.minutesBefore;
  h+='<div class="card"><div class="big">'+esc(c.raceKey)+'<span class="tag">'+esc(c.grade||'')+'</span></div>';
  h+=row('마감까지', c.afterClose?'<span class="red">마감 후</span>':(mb==null?'—':'<b>'+esc(mb)+'분</b>'));
  h+=row('수집', esc(c.ticks)+'틱 · '+esc(c.src||'—')+' · '+esc(c.tickTime||'—'));
  h+=row('저장 시각', esc(c.fileAt));
  h+=row('두수 / 확신도', esc(c.horseCount==null?'—':c.horseCount)+' / '+esc(c.confidence==null?'—':c.confidence));
  h+=row('유력마', esc((c.keyHorses||[]).join(' · ')||'—'))+'</div>';
  if(c.quinellas.length){h+='<div class="card"><div class="k" style="margin-bottom:6px">복승 (판정 명단)</div>';
    c.quinellas.forEach(function(q){h+=row(q.combo,(q.odds!=null?esc(q.odds)+'배':'—')+(q.stars?' <span class="tag">'+stars(q.stars)+'</span>':''),'grn')});h+='</div>'}
  if(c.dia.length||c.trifectas.length){h+='<div class="card"><div class="k" style="margin-bottom:6px">한방</div>';
    c.dia.forEach(function(q){h+=row('💎 '+q.combo,(q.odds!=null?esc(q.odds)+'배':'—'),'amb')});
    c.trifectas.forEach(function(q){h+=row('삼복승 '+q.combo,'','amb')});h+='</div>'}
  if(c.signals.length||c.drops.length){h+='<div class="card"><div class="k" style="margin-bottom:6px">신호</div>';
    c.signals.forEach(function(x){h+=row(x.label,esc(x.detail),x.level==='red'?'red':'amb')});
    if(c.drops.length)h+=row('급락',esc(c.drops.join(' · ')));h+='</div>'}
  h+='<div class="card"><div class="k">요약</div><div>'+esc(c.summary||'—')+'</div></div>';
  document.getElementById('body').innerHTML=h;
}
function tick(){var f=document.getElementById('sel').value;
  fetch('/m/data?t='+encodeURIComponent(T)+(f?'&r='+encodeURIComponent(f):''))
   .then(function(r){if(!r.ok)throw 0;return r.json()}).then(draw)
   .catch(function(){document.getElementById('hd').textContent='연결 실패 — 토큰/네트워크 확인'})}
document.getElementById('sel').addEventListener('change',function(){this.dataset.locked='1';tick()});
document.getElementById('rp').href='/m/report?d=report&t='+encodeURIComponent(T);
tick();setInterval(tick,10000);
</script></body></html>"""


REPORT_HEAD = u"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>보고</title><style>
:root{color-scheme:dark}
body{margin:0;background:#0b1020;color:#e6edf7;font:14px/1.65 -apple-system,"Malgun Gothic",sans-serif}
.wrap{max-width:760px;margin:0 auto;padding:14px}
.nav{padding:8px 0 12px;border-bottom:1px solid #222d4a;margin-bottom:10px}
.nav a{color:#7dd3fc;text-decoration:none;margin-right:4px}
h2{font-size:17px;margin:18px 0 6px;color:#fff;border-top:1px solid #222d4a;padding-top:14px}
h3{font-size:15px;margin:14px 0 4px;color:#cbd5e1}
h4,h5{font-size:14px;margin:10px 0 4px;color:#a9bcd8}
pre{background:#141b30;border:1px solid #222d4a;border-radius:8px;padding:10px;
    overflow-x:auto;font-size:12px;line-height:1.5;white-space:pre;margin:8px 0}
code{background:#1d2740;padding:1px 5px;border-radius:4px;font-size:12.5px}
b{color:#fff}.sp{height:7px}
.card{background:#141b30;border:1px solid #222d4a;border-radius:10px;padding:12px}
</style></head><body><div class="wrap">"""


# ────────────────────────── 핸들러 ──────────────────────────
def _rate_ok(ip):
    now = time.time()
    with _RATE_LOCK:
        dq = _RATE.setdefault(ip, deque())
        while dq and now - dq[0] > RATE_WIN:
            dq.popleft()
        if len(dq) >= RATE_MAX:
            return False
        dq.append(now)
        return True


def _access(ip, path, code):
    try:
        os.makedirs(LOGS, exist_ok=True)
        with io.open(ACCESS_LOG, "a", encoding="utf-8") as f:      # append only
            f.write("%s\t%s\t%s\t%d\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), ip, path, code))
    except Exception:
        pass


class Handler(BaseHTTPRequestHandler):
    server_version = "mirror"
    sys_version = ""

    def log_message(self, *a):        # 기본 stderr 로그는 끈다(접근 로그로 대체)
        pass

    def _send(self, code, body, ctype):
        b = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        try:
            self.wfile.write(b)
        except Exception:
            pass

    def do_GET(self):
        ip = self.client_address[0]
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if not _rate_ok(ip):
            _access(ip, u.path, 429)
            return self._send(429, "Too Many Requests", "text/plain; charset=utf-8")
        # 🔴 토큰 불일치는 404 — 401 이 아니다(존재 자체를 알리지 않는다)
        if u.path not in ("/m", "/m/data", "/m/report") or not token_ok(q):
            _access(ip, u.path, 404)
            return self._send(404, "Not Found", "text/plain; charset=utf-8")
        _access(ip, u.path, 200)
        if u.path == "/m":
            return self._send(200, HTML, "text/html; charset=utf-8")
        if u.path == "/m/report":
            # 🔴 [2026-08-31] 3단 보고를 다른 PC 에서 읽는다. 화이트리스트에 있는 문서만.
            kind = (q.get("d") or ["report"])[0]
            if kind not in _DOCS:
                _access(ip, u.path, 404)
                return self._send(404, "Not Found", "text/plain; charset=utf-8")
            body = read_doc(kind)
            tok = (q.get("t") or [""])[0]
            nav = ("<a href='/m?t=%s'>← 실시간</a> · <a href='/m/report?d=report&t=%s'>보고</a>"
                   " · <a href='/m/report?d=next&t=%s'>명령</a>") % (tok, tok, tok)
            html = (REPORT_HEAD + "<div class='nav'>" + nav + "</div>"
                    + (md_lite(body) if body else "<div class='card'>문서가 아직 없습니다.</div>")
                    + "</div></body></html>")
            return self._send(200, html, "text/html; charset=utf-8")
        sel = (q.get("r") or [None])[0]
        return self._send(200, json.dumps(payload(sel), ensure_ascii=False),
                          "application/json; charset=utf-8")

    def _405(self):
        _access(self.client_address[0], self.path, 405)
        self._send(405, "Method Not Allowed", "text/plain; charset=utf-8")

    do_POST = _405
    do_PUT = _405
    do_DELETE = _405
    do_PATCH = _405
    do_HEAD = _405
    do_OPTIONS = _405


def main():
    host = tailscale_ip()
    if not host:
        print("[미러] 기동 거부 - Tailscale IP(100.64.0.0/10)를 못 찾았다.")
        print("       설계상 다른 인터페이스에는 절대 바인딩하지 않는다(fail closed).")
        print("       Tailscale 을 켜거나 MIRROR_HOST 로 그 대역 IP 를 지정하십시오.")
        raise SystemExit(1)
    if _NEW:
        print("[미러] 토큰을 새로 만들었다 -> %s" % TOKEN_FILE)
    print("[미러] 읽기 전용 미러 시작")
    print("       주소 : http://%s:%d/m?t=%s" % (host, PORT, TOKEN))
    print("       주의 : 이 주소를 아는 사람만 볼 수 있다. 토큰은 URL 에 남는다.")
    print("       주의 : 배당판보다 수 초~수십 초 늦다 - 관찰용이다.")
    ThreadingHTTPServer((host, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
