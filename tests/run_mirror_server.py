# -*- coding: utf-8 -*-
"""[미러 회귀] tools/mirror_server.py — 읽기 전용 미러의 안전장치가 실제로 도는가.

🔴 원칙 12 — 실제 모듈을 import 해 **실제 핸들러**에 HTTP 를 쏜다(픽스처가 아니다).
🔴 원칙 17 — 통과(토큰 일치)·차단(불일치·경로탈출·POST) 둘 다 확인한다.
🔴 원칙 19 — 모듈을 못 읽으면 **조용히 통과하지 않고** rc=1 로 죽는다.

실행: python tests/run_mirror_server.py
"""
import io
import os
import sys
import json
import time
import socket
import threading
import importlib.util
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(BASE, "tools", "mirror_server.py")

if not os.path.exists(SRC):
    # ⚠ 파일이 없는 것은 **정상 상태**다 — 미러를 안 쓰기로 했으면 지우는 것이 되돌리기다.
    #   그때까지 커밋을 막으면 안 되므로 여기서만 rc=0 으로 건너뛴다.
    #   🔴 파일이 **있는데** 구조가 바뀐 경우는 아래에서 rc=1 로 죽는다(원칙 19).
    print("[미러] tools/mirror_server.py 없음 — 미설치 상태로 보고 건너뛴다")
    sys.exit(0)

_spec = importlib.util.spec_from_file_location("mirror_server", SRC)
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)

for _fn in ("tailscale_ip", "read_json", "payload", "Handler", "TOKEN"):
    if not hasattr(M, _fn):
        print("[미러] %s 가 없다 — 모듈 구조가 바뀌었다" % _fn)
        sys.exit(1)

OK = []


def chk(name, cond, note=""):
    OK.append(bool(cond))
    print("  %s %-46s %s" % ("[OK] " if cond else "[FAIL]", name, note))


# ── ① 바인딩 정책 (fail closed) ────────────────────────────────
_real = socket.getaddrinfo


def _fake(ips):
    def f(*a, **k):
        return [(socket.AF_INET, 0, 0, "", (ip, 0)) for ip in ips]
    return f


os.environ.pop("MIRROR_HOST", None)
socket.getaddrinfo = _fake(["192.168.0.10", "10.0.0.5"])
chk("Tailscale IP 없으면 기동 거부(None)", M.tailscale_ip() is None)
socket.getaddrinfo = _fake(["192.168.0.10", "100.80.114.84"])
chk("Tailscale 대역이면 그 IP 선택", M.tailscale_ip() == "100.80.114.84")
socket.getaddrinfo = _fake(["100.200.0.1"])          # 100.x 지만 대역 밖(/10 아님)
chk("100.x 라도 대역 밖이면 거부", M.tailscale_ip() is None)
socket.getaddrinfo = _real

# ── ② 경로 탈출 차단 ────────────────────────────────
# 🔴 [원칙 8-D · 2026-08-30] 처음엔 `read_json(...) is None` 만 봤는데 **못 잡았다.**
#   가드를 통째로 빼도 `.env` 는 JSON 이 아니라 **파싱 실패로 None** 이 나온다 —
#   차단된 것이 아니라 우연히 None 이었다(자기검증 주입에서 rc=0 으로 드러났다).
#   ⇒ 화이트리스트 **밖에 진짜 JSON 을 심어** 그것을 못 읽는지로 판정한다.
#     가드가 빠지면 dict 가 돌아와 즉시 실패한다.
_PROBE = os.path.join(BASE, "data", "_mirror_escape_probe.json")
with io.open(_PROBE, "w", encoding="utf-8") as _f:
    json.dump({"secret": "이 값이 보이면 경로 탈출이다"}, _f)
try:
    chk("🔴 화이트리스트 밖 **유효 JSON** 을 못 읽는다",
        M.read_json("analysis_log", "../_mirror_escape_probe.json") is None)
    chk("🔴 역슬래시 탈출도 못 읽는다",
        M.read_json("analysis_log", "..\\_mirror_escape_probe.json") is None)
    for bad in ("../../.env", "..\\..\\.env", "/etc/passwd", "2026_08_30_x.json/../../.env",
                "app.py", "2026_08_30_x.txt", ""):
        chk("경로 차단: %r" % bad, M.read_json("analysis_log", bad) is None)
    chk("화이트리스트 밖 kind 차단", M.read_json("secrets", "2026_08_30_a.json") is None)
finally:
    try:
        os.remove(_PROBE)
    except Exception:
        pass

# ── ③ 요청 처리 중 쓰기 코드가 없는가 ────────────────────────────────
_src = io.open(SRC, encoding="utf-8").read()
_w = [ln.strip() for ln in _src.splitlines()
      if ('open(' in ln and ('"w"' in ln or "'w'" in ln or '"a"' in ln or "'a'" in ln))]
# 허용: 토큰 생성(기동 시 1회) · 접근 로그(append only)
_bad = [ln for ln in _w if ("TOKEN_FILE" not in ln and "ACCESS_LOG" not in ln)]
chk("요청 경로에 파일 쓰기 없음", not _bad, ("남은 것: %s" % _bad[:1]) if _bad else "")

# ── ④ 실제 핸들러에 HTTP ────────────────────────────────
srv = ThreadingHTTPServer(("127.0.0.1", 0), M.Handler)   # 테스트는 로컬 바인딩
threading.Thread(target=srv.serve_forever, daemon=True).start()
time.sleep(0.2)
PORT = srv.server_address[1]
BASEURL = "http://127.0.0.1:%d" % PORT


def req(path, method="GET"):
    r = urllib.request.Request(BASEURL + path, method=method)
    try:
        with urllib.request.urlopen(r, timeout=5) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, str(e)


T = M.TOKEN
c, b = req("/m?t=" + T)
chk("토큰 일치 → /m 200", c == 200 and "실시간 분석 미러" in b, "HTTP %s" % c)
c, b = req("/m/data?t=" + T)
_j = {}
try:
    _j = json.loads(b)
except Exception:
    pass
chk("토큰 일치 → /m/data 200 · JSON", c == 200 and "races" in _j, "HTTP %s" % c)
chk("응답에 토큰이 안 실린다", T not in b)

M._RATE.clear()
c, _ = req("/m?t=" + T[:-1] + ("A" if T[-1] != "A" else "B"))
chk("토큰 불일치 → 404(401 아님)", c == 404, "HTTP %s" % c)
M._RATE.clear()
c, _ = req("/m")
chk("토큰 없음 → 404", c == 404, "HTTP %s" % c)
for p in ("/", "/admin", "/api/health", "/static/js/app.js", "/.env"):
    M._RATE.clear()          # 레이트 리밋과 섞이면 429 가 나와 **다른 것을 재게 된다**(원칙 4)
    c, _ = req(p + "?t=" + T)
    chk("토큰 맞아도 다른 경로 404: %s" % p, c == 404, "HTTP %s" % c)

for mth in ("POST", "PUT", "DELETE", "OPTIONS"):
    M._RATE.clear()
    c, _ = req("/m?t=" + T, mth)
    chk("%s → 405" % mth, c == 405, "HTTP %s" % c)

# ── ⑤ 레이트 리밋 ────────────────────────────────
M._RATE.clear()
codes = [req("/m/data?t=" + T)[0] for _ in range(M.RATE_MAX + 3)]
chk("초당 %d회 초과 → 429" % M.RATE_MAX, 429 in codes, "코드 %s" % codes)

srv.shutdown()
print("\n  %d/%d" % (sum(OK), len(OK)))
sys.exit(0 if all(OK) else 1)
