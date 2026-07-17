# -*- coding: utf-8 -*-
# [진단2] (1) 지금 돌아가는 서버가 새 코드인지 확인  (2) 현재 파일 코드로 직접 채점
import json, urllib.request, urllib.error

print("="*60)
print("[1] 지금 실행 중인 서버가 '새 코드'인지 확인")
for name, url in [("일본결과백필", "http://127.0.0.1:8011/api/jp/results/backfill"),
                  ("한국결과백필", "http://127.0.0.1:8011/api/kra/results/backfill")]:
    try:
        r = urllib.request.urlopen(url, timeout=10)
        print(f"   {name}: 있음(코드 {r.status}) → ✅ 새 코드 실행 중")
    except urllib.error.HTTPError as e:
        msg = "✅ 새 코드(주소는 있음)" if e.code in (400,405,500) else ("❌ 옛날 코드(주소 없음)" if e.code==404 else f"코드 {e.code}")
        print(f"   {name}: 응답 {e.code} → {msg}")
    except Exception as e:
        print(f"   {name}: 서버 연결 실패 - {e}")

print("="*60)
print("[2] 현재 저장된 app.py 코드로 직접 채점(서버 안 거치고)")
try:
    import app
    rec, _ = app._apply_result_learning("소노다 6경주", {"1st":9,"2nd":6,"3rd":10}, [9,6,10])
    print("   소노다6(정답 9-6-10) 직접채점 → was_hit:", rec.get("was_hit"),
          "· 복승적중:", rec.get("quinella_hit"), "· 삼복적중:", rec.get("trifecta_hit"))
except Exception as e:
    import traceback; traceback.print_exc()
print("="*60)
print("이 화면 그대로 복사해서 보여주세요.")
