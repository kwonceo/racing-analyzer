# -*- coding: utf-8 -*-
"""[개발 서버 분리 · A15 · 2026-08-20 승인] 운영(8011)을 건드리지 않고 dev/(8012)를 만든다.

왜: 그날 하루에만 서버가 세 번 죽었다. 전부 운영 app.py 를 고치다 리로더가 중간 상태를 읽어서다.
  ⇒ 코드를 dev/ 사본에서 먼저 돌려보고, 통과한 것만 운영에 옮긴다.

🔴 완전 격리 — dev/app.py 는 `os.path.dirname(__file__)` 로 경로를 만들므로
  자동으로 `dev/data/` 를 본다. **운영 data 를 한 글자도 건드리지 않는다.**
🔴 포트는 사본에서만 8012 로 바꾼다. `PORT` 환경변수는 쓰지 않는다 —
  그걸 쓰면 app.py 가 0.0.0.0 으로 바인딩해 외부에 열린다(운영과 같은 위험).
⚠ 데이터는 복사하지 않는다. 필요하면 `--with-data 3` 으로 최근 며칠만 가져온다.

되돌리기: dev/ 폴더를 지우면 끝. 운영에는 흔적이 없다.
"""
import argparse
import os
import re
import shutil
import time

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEV = os.path.join(SRC, "dev")
CODE_FILES = ("app.py", "admin_page.py", "review_engine.py", "gemini_reviewer.py",
              "gemini_forecast.py", "nar_guard.py", "netkeiba_guard.py")
CODE_DIRS = ("static", "tools", "tests", "chrome-extension")
DATA_DIRS = ("analysis_log", "odds_history", "race_results", "prerace", "korea_history",
             "dark_horse_log", "snapshots", "day_cards_cache", "ai_training",
             "race_report", "daily_summary", "simulation_db", "pace_analysis")
LOG_DIRS = ("gate_fire", "det_review", "t5_freeze", "miss_type", "input_check",
            "auto_finding", "product_split", "forecast", "collect_gaps")


def sync(with_data=0):
    os.makedirs(DEV, exist_ok=True)
    n = 0
    for f in CODE_FILES:
        s = os.path.join(SRC, f)
        if os.path.exists(s):
            shutil.copy2(s, os.path.join(DEV, f))
            n += 1
    for d in CODE_DIRS:
        s = os.path.join(SRC, d)
        if not os.path.isdir(s):
            continue
        t = os.path.join(DEV, d)
        if os.path.isdir(t):
            shutil.rmtree(t, ignore_errors=True)
        shutil.copytree(s, t)
        n += 1
    # 🔴 포트를 사본에서만 8012 로
    p = os.path.join(DEV, "app.py")
    s = open(p, encoding="utf-8").read()
    s2 = s.replace('_port = int(os.environ.get("PORT", 8011))',
                   '_port = int(os.environ.get("DEV_PORT", 8012))   # [dev] 개발 전용')
    s2 = s2.replace('_host = "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1"',
                    '_host = "127.0.0.1"   # [dev] 🔴 절대 외부 바인딩하지 않는다')
    s2 = s2.replace('print(f"서버 시작: http://{_host}:{_port}',
                    'print("🧪 [개발 서버] 운영(8011)과 분리된 사본입니다 · data 도 dev/data 를 씁니다")\n'
                    '    print(f"서버 시작: http://{_host}:{_port}')
    if s2 == s:
        print("⚠ 포트 치환 실패 — app.py 기동부가 바뀌었는지 확인할 것")
    open(p, "w", encoding="utf-8", newline="").write(s2)
    # 데이터 뼈대
    for d in DATA_DIRS:
        os.makedirs(os.path.join(DEV, "data", d), exist_ok=True)
    for d in LOG_DIRS:
        os.makedirs(os.path.join(DEV, "logs", d), exist_ok=True)
    os.makedirs(os.path.join(DEV, "backups"), exist_ok=True)
    copied = 0
    if with_data > 0:
        days = [time.strftime("%Y_%m_%d", time.localtime(time.time() - i * 86400))
                for i in range(with_data)]
        for d in ("analysis_log", "odds_history", "race_results"):
            sd = os.path.join(SRC, "data", d)
            td = os.path.join(DEV, "data", d)
            if not os.path.isdir(sd):
                continue
            for fn in os.listdir(sd):
                if any(fn.startswith(x) for x in days):
                    try:
                        shutil.copy2(os.path.join(sd, fn), os.path.join(td, fn))
                        copied += 1
                    except Exception:
                        pass
        for f in ("today_schedule.json", "ev_bands.json", "korea_session.json"):
            s1 = os.path.join(SRC, "data", f)
            if os.path.exists(s1):
                shutil.copy2(s1, os.path.join(DEV, "data", f))
    print("🟢 dev/ 동기화 완료 — 코드 %d개 · 데이터 %d파일(최근 %d일)" % (n, copied, with_data))
    print("   실행: python dev/app.py   → http://127.0.0.1:8012")
    print("   🔴 운영 8011 은 건드리지 않았습니다.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-data", type=int, default=0)
    a = ap.parse_args()
    sync(a.with_data)
