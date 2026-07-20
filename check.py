# -*- coding: utf-8 -*-
# [확인용] 오늘 소노다 4·6·11·12경주 파일에 '우리 추천'이 실제로 들어있는지 + 서버가 어떻게 채점하는지 확인
import json, os, urllib.request

RACES = {"소노다 4경주":[7,6,5], "소노다 6경주":[9,6,10], "소노다 11경주":[7,9,2], "소노다 12경주":[1,4,9]}
FN = {"소노다 4경주":"소노다_4", "소노다 6경주":"소노다_6", "소노다 11경주":"소노다_11", "소노다 12경주":"소노다_12"}

print("="*60)
for rk, top3 in RACES.items():
    p = os.path.join("data","analysis_log","2026_07_17_%s경주.json" % FN[rk])
    print(f"[{rk}] 실제결과 {top3}")
    # 1) 파일에 추천이 있나?
    try:
        d = json.load(open(p, encoding="utf-8"))
        cp = d.get("corePicks") or {}
        fq = [q.get("combo") for q in (cp.get("finalQuinellas") or [])]
        print("   파일 추천(복승):", fq or "❌ 없음(비어있음)", "· 크기:", os.path.getsize(p), "bytes")
        print("   파일에 저장된 결과:", d.get("result"))
    except Exception as e:
        print("   파일 읽기 실패:", e)
    # 2) 서버가 채점하면?
    try:
        body = {"raceKey": rk, "result": {"1st":top3[0],"2nd":top3[1],"3rd":top3[2]}}
        req = urllib.request.Request("http://127.0.0.1:8011/api/history/record-result",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type":"application/json"}, method="POST")
        r = json.loads(urllib.request.urlopen(req, timeout=30).read().decode("utf-8","replace"))
        rec = r.get("record") or {}
        print("   서버 채점 → was_hit:", rec.get("was_hit"), "· 복승적중:", rec.get("quinella_hit"),
              "· 매칭된키:", r.get("matchedFrom") or "(그대로)")
    except Exception as e:
        print("   서버 요청 실패:", e)
    print("-"*60)
print("이 화면을 그대로 복사해서 보여주세요.")
