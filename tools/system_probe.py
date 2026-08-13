# -*- coding: utf-8 -*-
"""SYSTEM 계정에서 서버가 뜰 수 있는지 재부팅 없이 확인한다.

2026-08-14 신설.

🔴 왜 필요한가
  자동 기동을 SYSTEM 계정으로 등록하면 지금 서버가 도는 환경(Administrator)과
  환경변수·프로필 경로가 다르다. 그래서 .env 를 못 읽거나 인코딩이 깨질 수 있다.
  그런데 그 사실은 다음 재부팅 때에야 드러난다. 그때는 이미 경주를 놓친 뒤다.

  이 스크립트는 **서버를 띄우지 않고** 그 환경만 확인한다.
  현재 돌고 있는 서버에 손대지 않는다. 포트도 열지 않는다.

결과는 logs/system_probe.json 에 남는다.
"""
import json
import os
import sys
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE_DIR, "logs", "system_probe.json")

res = {
    "at": time.strftime("%Y-%m-%d %H:%M:%S"),
    "user": os.environ.get("USERNAME") or "?",
    "cwd": os.getcwd(),
    "python": sys.executable,
    "checks": {},
    "ok": False,
}


def chk(name, fn):
    try:
        res["checks"][name] = {"ok": True, "detail": fn()}
    except Exception as e:
        res["checks"][name] = {"ok": False, "detail": "%s: %s" % (type(e).__name__, str(e)[:200])}


# ① 작업 디렉터리가 프로젝트 루트인가
chk("cwd_is_project", lambda: os.path.exists(os.path.join(os.getcwd(), "app.py")))

# ② .env 를 읽을 수 있는가 (내용은 남기지 않는다 — 키가 들어 있다)
def _env():
    p = os.path.join(BASE_DIR, ".env")
    if not os.path.exists(p):
        return "없음"
    with open(p, "r", encoding="utf-8") as f:
        n = len([l for l in f if l.strip() and not l.strip().startswith("#")])
    return "읽기 성공 · 설정 %d줄" % n
chk("env_readable", _env)

# ③ 데이터·로그 폴더에 쓸 수 있는가
def _write():
    p = os.path.join(BASE_DIR, "logs", "_probe_write_test.tmp")
    with open(p, "w", encoding="utf-8") as f:
        f.write("한글 쓰기 확인 %s" % time.time())
    os.remove(p)
    return "쓰기·삭제 성공"
chk("logs_writable", _write)

def _write_data():
    p = os.path.join(BASE_DIR, "data", "_probe_write_test.tmp")
    with open(p, "w", encoding="utf-8") as f:
        f.write("ok")
    os.remove(p)
    return "쓰기·삭제 성공"
chk("data_writable", _write_data)

# ④ 한글 출력이 깨지지 않는가 (cp949 콘솔에서 UnicodeEncodeError 로 기동이 죽은 전례가 있다)
def _enc():
    enc = os.environ.get("PYTHONIOENCODING") or (sys.stdout.encoding or "?")
    "한글 인코딩 확인".encode(enc if enc and enc != "?" else "utf-8")
    return "PYTHONIOENCODING=%s · stdout=%s" % (
        os.environ.get("PYTHONIOENCODING") or "(없음)", sys.stdout.encoding)
chk("encoding", _enc)

# ⑤ 🔴 가장 중요 — app.py 를 import 할 수 있는가
#    NameError·ImportError 는 문법 검사로 안 잡힌다(2026-08-10 BASE_DIR 사고).
#    import 가 되면 이 계정에서 서버가 뜬다는 뜻이다.
def _imp():
    sys.path.insert(0, BASE_DIR)
    os.chdir(BASE_DIR)
    # 🔴 백그라운드 수집이 뜨지 않게 막는다.
    #    app.py 39704 는 SERVER_SOFTWARE 가 gunicorn 으로 시작할 때만
    #    모듈 로드 시점에 _boot_background() 를 부른다.
    #    로컬 import 에서는 안 뜨지만, 이중 수집은 과거 실제 사고(2026-07-30 · 41%)라
    #    명시적으로 지운다.
    os.environ.pop("SERVER_SOFTWARE", None)
    t0 = time.time()
    import app  # noqa: F401
    return "import 성공 · %.1f초" % (time.time() - t0)
chk("app_import", _imp)

res["ok"] = all(v.get("ok") for v in res["checks"].values())

try:
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
except Exception as e:
    print("결과 저장 실패:", str(e)[:150])

for k, v in res["checks"].items():
    print(("  OK   " if v["ok"] else "  FAIL ") + k + " : " + str(v["detail"])[:150])
print("전체:", "통과" if res["ok"] else "실패")
sys.exit(0 if res["ok"] else 1)
