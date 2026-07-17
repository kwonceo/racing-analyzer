# -*- coding: utf-8 -*-
# [진단3] 소노다6에 대해 서버 코드가 '추천 조합'을 실제로 뽑아내는지 단계별로 확인
import app, json, os

rk = "소노다 6경주"
print("="*60)
ck = app._canonical_log_key(rk)
print("1) canonical key:", repr(ck))
path, date, race = app._analysis_log_path(ck)
print("2) 읽는 파일:", path)
print("   존재:", os.path.exists(path), "· 크기:", os.path.getsize(path) if os.path.exists(path) else "-")
if os.path.exists(path):
    d = json.load(open(path, encoding="utf-8"))
    cp = d.get("corePicks") or {}
    print("3) 파일 finalQuinellas:", [q.get("combo") for q in (cp.get("finalQuinellas") or [])])
    print("   파일 confQuinellas :", [q.get("combo") for q in (cp.get("confQuinellas") or [])])
combos = app._rec_combos_from_analysis_log(rk)
print("4) _rec_combos_from_analysis_log 반환:", combos)
print("   → 복승 조합만:", [c["combo"] for c in combos if c.get("kind")=="복승"])
print("5) 실제결과 9-6-10 의 1·2착(정렬):", sorted([9,6]))
print("="*60)
print("이 화면 그대로 보여주세요.")
