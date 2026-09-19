# -*- coding: utf-8 -*-
"""1번 리플레이: 판정 명단(after=회원 수신분에 맞춘 것) ↔ 분석 자체 명단(judgeMatch.before · 실제 코드 산출) ↔ mb≥2 시점에 이미 빠진 것만 내림(R2) ↔ 무작위 제거 대조
   표본 analysis_log 8/10~ · 결과+확정배당 복승 · 구좌=조합 1 · 정제 없음"""
import json,io,glob,os,re,random
os.chdir(r"C:\Users\Administrator\Desktop\경마분석서버")
def combos(s):
    out=set()
    for x in (s or "").split("/"):
        p=[int(v) for v in re.findall(r"\d+",x)]
        if len(p)==2: out.add(tuple(sorted(p)))
    return out
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
    dc=((d.get("corePicks") or {}).get("displayedCombos") or {})
    cur=[tuple(sorted(c)) for c in (dc.get("quinellas") or []) if len(c)==2]
    if not cur: continue
    jm=dc.get("judgeMatch") or {}
    before=set(tuple(sorted(c)) for c in (jm.get("before") or []) if len(c)==2) if jm else set(cur)
    rfin=[c for c in cur if c not in before]
    H=[h for h in d.get("recommendation_history") or [] if isinstance(h.get("minutes_before"),(int,float)) and "|" in str(h.get("sig"))]
    l2=[h for h in H if 2<=h["minutes_before"]<=4]
    r2=[c for c in rfin if l2 and c not in combos(l2[-1]["sig"].split("|")[-1])]
    sport=str(d.get("sport") or d.get("category") or ""); sp="경륜" if sport=="cycle" else ("한국" if "korea" in sport else ("중앙" if "central" in sport else "경마"))
    R.append(dict(sp=sp,day=day,win=win,pay=pay,cur=cur,rfin=rfin,r2=r2))
days=sorted(set(r["day"] for r in R)); t1,t2=days[len(days)//3],days[2*len(days)//3]
def stat(bets):
    n=len(bets); h=sum(1 for p,_ in bets if p>0); tot=sum(p for p,_ in bets); top=sorted([p for p,_ in bets],reverse=True)[:3]
    seg=[[p for p,dd in bets if dd<t1],[p for p,dd in bets if t1<=dd<t2],[p for p,dd in bets if dd>=t2]]
    return n,h,100*tot/max(1,n),100*(tot-sum(top))/max(1,n-3)," / ".join("%.0f"%(100*sum(s)/max(1,len(s))) for s in seg)
def bets(rows,drop):
    return [(r["pay"] if c==r["win"] else 0.0,r["day"]) for r in rows for c in r["cur"] if c not in drop(r)]
for sp in ("경마","경륜"):
    X=[r for r in R if r["sp"]==sp]
    print("■ %s · 경주 %d · 잔류 있는 경주 %d(%.0f%%) · mb≥2 에 이미 빠진 잔류 있는 경주 %d"%(sp,len(X),sum(1 for r in X if r["rfin"]),100.0*sum(1 for r in X if r["rfin"])/len(X),sum(1 for r in X if r["r2"])))
    for lab,drop in (("현행(회원 수신 맞춤)",lambda r:()),("분석 자체 명단(잔류 전부 내림)",lambda r:r["rfin"]),("mb≥2 에 빠진 잔류만 내림",lambda r:r["r2"])):
        n,h,rec,r3,seg=stat(bets(X,drop)); print("  %-26s 구좌 %5d 적중 %4d 회수 %5.1f 3제외 %5.1f · 3분할 %s"%(lab,n,h,rec,r3,seg))
    for lab,key in (("빠진 구좌(전부)","rfin"),("빠진 구좌(mb≥2)","r2")):
        b=[(r["pay"] if c==r["win"] else 0.0,r["day"]) for r in X for c in r[key]]; n,h,rec,r3,seg=stat(b); print("  %-26s 구좌 %5d 적중 %4d 회수 %5.1f 3제외 %5.1f · 3분할 %s"%(lab,n,h,rec,r3,seg))
    # 무작위 제거 대조: 같은 경주에서 같은 개수를 무작위로 내림
    for key in ("rfin","r2"):
        cf=stat(bets(X,lambda r:r[key]))[3]; better=0; vals=[]
        for s in range(300):
            rnd=random.Random(s); b=[]
            for r in X:
                k=len(r[key]); drop=set(rnd.sample(r["cur"],k)) if k else set()
                b+=[(r["pay"] if c==r["win"] else 0.0,r["day"]) for c in r["cur"] if c not in drop]
            v=stat(b)[3]; vals.append(v); better+= v>=cf
        vals.sort(); print("  무작위 제거 대조(%s) 3제외 중앙 %.1f · 95%% %.1f ↔ 규칙 %.1f · 무작위가 규칙 이상 %d/300"%(key,vals[150],vals[285],cf,better))
