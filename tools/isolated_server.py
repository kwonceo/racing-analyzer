# -*- coding: utf-8 -*-
"""격리 서버 — 실전을 건드리지 않고 게이트를 검증한다.

🔴 **왜 만들었나 (2026-08-04 실사고)**
  게이트 도달을 확인하려고 **라이브 서버에 가짜 raceKey 를 주입**했다. 생성물 5개와
  triple_store 2키가 실전 데이터에 섞였고 전부 되돌려야 했다. 운영 서버는 경주를 수집하고
  회원에게 카카오를 보내는 실전 서버다. ⇒ **게이트 검증을 라이브에서 하지 않는다.**

🔴 **격리가 성립하는 근거 (전수 확인함)**
  app.py 의 저장 경로는 전부 `os.path.dirname(__file__)` 기준이고 **절대경로 하드코딩이 0건**이다.
  ⇒ app.py 사본을 **다른 디렉터리**에 두면 data/·logs/·*.json 이 전부 그 안에 생긴다.
    환경변수를 새로 만들 필요가 없고, 실전 디렉터리를 **구조적으로 볼 수 없다.**

🔴 **사본에서 고치는 것 셋** (원본 app.py 는 한 줄도 안 건드린다)
  ① 포트 8011 → 8012              — 실전과 충돌 금지
  ② `PORT` 환경변수 분기 제거      — PORT 를 쓰면 `0.0.0.0` 바인딩이 돼 **외부에 열린다**
  ③ 백그라운드 기동 차단           — 안 막으면 격리 서버가 oddspark 를 긁고 **카카오를 보낸다**

사용법
  python tools/isolated_server.py --start          격리 서버 기동(임시 디렉터리 생성)
  python tools/isolated_server.py --probe          오염 payload 를 넣어 1층·3층 판정 확인
  python tools/isolated_server.py --stop           서버 종료 + 임시 디렉터리 통째 삭제
  python tools/isolated_server.py --check          실전과 충돌하지 않는지만 확인(기동 안 함)
"""
import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ISO_PORT = 8012
LIVE_PORT = 8011
MARK = os.path.join(tempfile.gettempdir(), "_iso_server_dir.txt")
# 사본에 반드시 있어야 하는 것 — 없으면 import 가 깨진다.
COPY_FILES = ("app.py", "review_engine.py", "gemini_reviewer.py", "gemini_forecast.py",
              "netkeiba_guard.py", "nar_guard.py", "admin_page.py")
COPY_DIRS = ("static", "tools")


def _port_busy(port):
    s = socket.socket()
    s.settimeout(0.6)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def check():
    """기동 전 안전 확인. 🔴 하나라도 걸리면 기동하지 않는다."""
    ok = True
    if _port_busy(ISO_PORT):
        print("🔴 포트 %d 가 이미 사용 중이다 — 기동하지 않는다." % ISO_PORT)
        ok = False
    else:
        print("🟢 포트 %d 비어 있음" % ISO_PORT)
    print("🟢 실전 포트 %d %s (건드리지 않는다)"
          % (LIVE_PORT, "가동 중" if _port_busy(LIVE_PORT) else "정지"))
    if "PORT" in os.environ:
        print("⚠ 환경변수 PORT 가 설정돼 있다 — 사본에서 제거하므로 영향 없음")
    for f in COPY_FILES:
        if f == "app.py" and not os.path.exists(os.path.join(ROOT, f)):
            print("🔴 app.py 가 없다"); ok = False
    return ok


def _patch(src):
    """사본 소스에 세 가지를 적용. 🔴 원본은 건드리지 않는다."""
    n = 0
    # ① + ② 포트 고정 · 외부바인드 금지
    src, c = re.subn(r'_port = int\(os\.environ\.get\("PORT", 8011\)\)',
                     "_port = %d  # [격리]" % ISO_PORT, src)
    n += c
    src, c = re.subn(r'_host = "0\.0\.0\.0" if os\.environ\.get\("PORT"\) else "127\.0\.0\.1"',
                     '_host = "127.0.0.1"  # [격리] 외부바인드 금지', src)
    n += c
    # ③ 백그라운드 전면 차단 — 수집·백업·카카오·선수집이 실제로 나가면 격리가 아니다.
    src, c = re.subn(r"^JRA_COLLECT_ENABLED = True", "JRA_COLLECT_ENABLED = False  # [격리]",
                     src, flags=re.M)
    n += c
    src, c = re.subn(r"^MIDCHECK_ENABLED = True", "MIDCHECK_ENABLED = False  # [격리]",
                     src, flags=re.M)
    n += c
    src, c = re.subn(r"^NAR_PRELOAD_ENABLED = True", "NAR_PRELOAD_ENABLED = False  # [격리]",
                     src, flags=re.M)
    n += c
    src, c = re.subn(r"^JRA_PRELOAD_ENABLED = True", "JRA_PRELOAD_ENABLED = False  # [격리]",
                     src, flags=re.M)
    n += c
    # 🔴 수집 루프·주기백업·카카오 스케줄러 기동 자체를 막는다(함수는 지우지 않는다).
    for fn in ("_start_multi_race_bg", "_start_periodic_backup", "_start_daily_learning_scheduler",
               "_start_health_kakao_scheduler", "_start_midcheck_scheduler",
               "_start_nar_preload_scheduler", "_start_jra_preload_scheduler",
               "_start_kra_backfill", "_start_result_backfill"):
        src, c = re.subn(r"^(\s*)%s\(\)" % re.escape(fn),
                         r"\1pass  # [격리] %s() 차단" % fn, src, flags=re.M)
        n += c
    # 🔴 카카오 발송 코어를 무력화 — 어떤 경로로도 실제 발송이 나가지 않게 한다.
    src, c = re.subn(r"^def _kakao_send_to_me\(",
                     "def _kakao_send_to_me(*_a, **_k):\n"
                     "    return {'ok': False, 'error': '[격리] 발송 차단'}\n\n\n"
                     "def _kakao_send_to_me_ORIG(", src, flags=re.M)
    n += c
    return src, n


def start():
    if not check():
        return 2
    d = tempfile.mkdtemp(prefix="iso_racing_")
    print("격리 디렉터리:", d)
    for f in COPY_FILES:
        p = os.path.join(ROOT, f)
        if os.path.exists(p):
            shutil.copy2(p, os.path.join(d, f))
    for sub in COPY_DIRS:
        p = os.path.join(ROOT, sub)
        if os.path.isdir(p):
            shutil.copytree(p, os.path.join(d, sub),
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    # 🔴 data/ 와 logs/ 는 **복사하지 않는다.** 실전 데이터가 섞이면 격리가 아니다.
    os.makedirs(os.path.join(d, "data"), exist_ok=True)
    os.makedirs(os.path.join(d, "logs"), exist_ok=True)
    ap = os.path.join(d, "app.py")
    with open(ap, encoding="utf-8") as f:
        src = f.read()
    src, n = _patch(src)
    with open(ap, "w", encoding="utf-8") as f:
        f.write(src)
    print("사본 패치 %d 곳(포트·바인드·백그라운드·카카오)" % n)
    import ast
    ast.parse(src)                      # 🔴 사본이 문법적으로 성립하는지 먼저 본다
    print("사본 문법 OK")
    env = dict(os.environ)
    env.pop("PORT", None)               # 🔴 있으면 외부바인드가 된다
    env["PYTHONIOENCODING"] = "utf-8"
    log = open(os.path.join(d, "logs", "iso.log"), "w", encoding="utf-8")
    subprocess.Popen([sys.executable, "app.py"], cwd=d, env=env, stdout=log,
                     stderr=subprocess.STDOUT)
    with open(MARK, "w", encoding="utf-8") as f:
        f.write(d)
    for _ in range(60):
        time.sleep(1)
        if _port_busy(ISO_PORT):
            print("🟢 격리 서버 기동 http://127.0.0.1:%d" % ISO_PORT)
            return 0
    print("🔴 기동 실패 — %s 확인" % os.path.join(d, "logs", "iso.log"))
    return 1


def _post(path, body):
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (ISO_PORT, path),
                                 data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"httpError": e.code, "body": e.read().decode("utf-8", "replace")[:300]}
    except Exception as e:
        return {"error": str(e)[:200]}


def probe():
    """오염 payload 를 넣어 1층이 실제로 막는지 확인. 🔴 격리 서버에만 넣는다."""
    if not _port_busy(ISO_PORT):
        print("🔴 격리 서버가 안 떠 있다 — 먼저 --start")
        return 2
    # 🔴 출마표 저장 엔드포인트(`/api/korea/form`)는 `_KRA_TRACK_RE` 를 통과해야 저장한다.
    #   ⇒ 한국 경마장 이름을 써야 하고, **오염 payload 의 raceKey 와 반드시 같아야 한다.**
    #   ⚠ 첫 판에 출마표는 `서울 1경주` · 오염은 `격리검증 1경주` 로 넣어 게이트가 명단을 못 찾았다
    #     (판정 불가 → 통과). **검사식 자체의 버그였다** — 격리 서버가 그것을 잡아 줬다.
    rk = "서울 1경주"
    # ① 정상: 6두 · 15조합 = C(6,2)
    ok_q = [{"combo": [a, b], "odds": 10.0}
            for a in range(1, 7) for b in range(a + 1, 7)]
    # ② 오염: 9두 · 36조합 = C(9,2) — 출마표(6두) 밖 마번 7·8·9 가 전부 8조합씩 등장
    bad_q = [{"combo": [a, b], "odds": 10.0}
             for a in range(1, 10) for b in range(a + 1, 10)]
    print("① 출마표 6두 저장 (%s)" % rk)
    print("  ", _post("/api/korea/form", {"raceKey": rk,
                                          "horses": [{"no": i} for i in range(1, 7)]}))
    print("② 정상 payload(15조합=C(6,2)) — 통과해야 한다")
    print("  ", _post("/api/odds/triple/ingest",
                      {"raceKey": rk, "quinella": ok_q, "source": "probe-private"}))
    print("③ 오염 payload(36조합=C(9,2)) — 1층이 막아야 한다")
    print("  ", _post("/api/odds/triple/ingest",
                      {"raceKey": rk, "quinella": bad_q, "source": "probe-private"}))
    d = open(MARK, encoding="utf-8").read().strip() if os.path.exists(MARK) else ""
    gf = os.path.join(d, "logs", "gate_fire")
    print("④ gate_fire 로그:", os.listdir(gf) if os.path.isdir(gf) else "없음")
    gh = os.path.join(d, "data", "_gate_hits.json")
    if os.path.exists(gh):
        print("⑤ 계수기:", open(gh, encoding="utf-8").read()[:300])
    return 0


def stop():
    d = open(MARK, encoding="utf-8").read().strip() if os.path.exists(MARK) else ""
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                    "Where-Object { $_.CommandLine -like '*iso_racing_*' } | "
                    "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"],
                   capture_output=True)
    time.sleep(2)
    if d and os.path.isdir(d) and "iso_racing_" in d:
        shutil.rmtree(d, ignore_errors=True)
        print("격리 디렉터리 삭제:", d)
    if os.path.exists(MARK):
        os.remove(MARK)
    print("🟢 정리 완료 · 실전 포트 %d %s"
          % (LIVE_PORT, "가동 중" if _port_busy(LIVE_PORT) else "정지"))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", action="store_true")
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--stop", action="store_true")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    if a.check:
        sys.exit(0 if check() else 1)
    if a.start:
        sys.exit(start())
    if a.probe:
        sys.exit(probe())
    if a.stop:
        sys.exit(stop())
    ap.print_help()
