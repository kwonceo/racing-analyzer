# -*- coding: utf-8 -*-
"""2번(경륜 「삼복승으로 짚은 세 말끼리」 자리가 상한에 잘린 것 되살리기) · 3번(경마 「유력마 1·2위」 자리 빼기) 리플레이 (읽기 전용)
   표본 analysis_log 8/10~ · 결과+확정배당 복승 · 구좌=조합 1 · 정제 없음 · 명단=판정(displayedCombos)"""
import json,io,glob,os,random
os.chdir(r"C:\Users\Administrator\Desktop\경마분석서버")
R=[]
for f in sorted(glob.glob("data/analysis_log/2026_0[89]_*.json")):
    day=os.path.basename(f)[:10]
    if day<"2026_08_10": continue
    try: d=json.load(io.open(f,encoding="utf-8"))
    except Exception: continue
    res=d.get("result") or {}
    try: win=tuple(sorted((int(res["1st"]),int(res["2nd"])))); pay=float((res.get("payouts") or {}).get("quinella") or 0)
    except Exception: continue
    if pay<=0: continue
    cp=d.get("corePicks") or {}
    cur=[tuple(sorted(c)) for c in ((cp.get("displayedCombos") or {}).get("quinellas") or []) if len(c)==2]
    if not cur: continue
    fq=[(tuple(sorted(q["combo"])),str(q.get("reason") or ""),q.get("odds")) for q in (cp.get("finalQuinellas") or []) if isinstance(q,dict) and len(q.get("combo") or [])==2]
    sport=str(d.get("sport") or d.get("category") or ""); sp="경륜" if sport=="cycle" else ("경마" if "korea" not in sport and "central" not in sport else "기타")
    R.append(dict(sp=sp,day=day,win=win,pay=pay,cur=cur,fq=fq))
days=sorted(set(r["day"] for r in R)); half=days[len(days)//2]
def st(b):
    n=len(b); h=sum(1 for p in b if p>0); top=sorted(b,reverse=True)[:3]
    return n,h,(100*sum(b)/n if n else 0),(100*(sum(b)-sum(top))/(n-3) if n>3 else 0)
P=lambda r,c:(r["pay"] if c==r["win"] else 0.0)
print("■■ 2번 · 경륜 「삼복승으로 짚은 세 말끼리」")
X=[r for r in R if r["sp"]=="경륜"]
for lab,sel in (("전체",lambda r:True),("전반",lambda r:r["day"]<half),("후반",lambda r:r["day"]>=half)):
    Y=[r for r in X if sel(r)]
    inn=[P(r,c) for r in Y for c,rs,o in r["fq"] if "삼복승으로 짚은" in rs and c in r["cur"]]
    cut=[P(r,c) for r in Y for c,rs,o in r["fq"] if "삼복승으로 짚은" in rs and c not in r["cur"]]
    cuto=[P(r,c) for r in Y for c,rs,o in r["fq"] if "삼복승으로 짚은" not in rs and c not in r["cur"]]
    other=[P(r,c) for r in Y for c,rs,o in r["fq"] if "삼복승으로 짚은" not in rs and c in r["cur"]]
    for l2,b in (("명단에 든 그 자리",inn),("상한에 잘린 그 자리(되살릴 대상)",cut),("상한에 잘린 다른 자리(대조)",cuto),("명단에 든 다른 자리",other)):
        n,h,rec,r3=st(b); print("  %s %-24s 구좌 %5d 적중 %4d(%4.1f%%) 회수 %5.1f 3제외 %5.1f"%(lab,l2,n,h,100.0*h/max(1,n),rec,r3))
# 같은 배당대 대조: 그 자리 조합과 같은 경주의 같은 배당대(±30%) 다른 명단 조합
m=[];c2=[]
for r in X:
    om={c:o for c,rs,o in r["fq"] if o}
    for c,rs,o in r["fq"]:
        if "삼복승으로 짚은" in rs and c in r["cur"] and o:
            m.append(P(r,c))
            for c3,rs3,o3 in r["fq"]:
                if c3!=c and "삼복승으로 짚은" not in rs3 and c3 in r["cur"] and o3 and 0.7<=o3/o<=1.3: c2.append(P(r,c3))
print("  같은 경주·같은 배당대(±30%%) 대조: 그 자리 %s ↔ 다른 자리 %s"%(st(m),st(c2)))
print("■■ 3번 · 경마 「유력마 1·2위 조합」 빼기")
X=[r for r in R if r["sp"]=="경마"]
for lab,sel in (("전체",lambda r:True),("전반",lambda r:r["day"]<half),("후반",lambda r:r["day"]>=half)):
    Y=[r for r in X if sel(r)]
    for r in Y: r["bad"]=[c for c,rs,o in r["fq"] if "유력마 1·2위" in rs and c in r["cur"]]
    cur=[P(r,c) for r in Y for c in r["cur"]]; rule=[P(r,c) for r in Y for c in r["cur"] if c not in r["bad"]]; dr=[P(r,c) for r in Y for c in r["bad"]]
    vals=[]
    for s in range(300):
        rnd=random.Random(s); b=[]
        for r in Y:
            k=set(rnd.sample(r["cur"],len(r["bad"]))) if r["bad"] else set()
            b+=[P(r,c) for c in r["cur"] if c not in k]
        vals.append(st(b)[3])
    vals.sort(); cf=st(rule)[3]
    print("  %s 현행 3제외 %.1f → 규칙 %.1f · 빠진 %s · 무작위 중앙 %.1f 95%% %.1f · 무작위≥규칙 %d/300"%(lab,st(cur)[3],cf,st(dr),vals[150],vals[285],sum(1 for v in vals if v>=cf)))
# 같은 배당대 대조
m=[];c2=[]
for r in X:
    for c,rs,o in r["fq"]:
        if "유력마 1·2위" in rs and c in r["cur"] and o:
            m.append(P(r,c))
            for c3,rs3,o3 in r["fq"]:
                if c3!=c and "유력마 1·2위" not in rs3 and c3 in r["cur"] and o3 and 0.7<=o3/o<=1.3: c2.append(P(r,c3))
print("  같은 경주·같은 배당대 대조: 그 자리 %s ↔ 다른 자리 %s"%(st(m),st(c2)))
