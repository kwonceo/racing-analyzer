# -*- coding: utf-8 -*-
"""판정 명단(displayedCombos.quinellas) 조합을 속성별로 쪼개 회수·3제외·3분할 (읽기 전용)
   표본 analysis_log 2026-08-10~ · 결과+확정배당 복승 · 구좌=조합 1 · 정제 없음 · 명단=판정"""
import json,io,glob,os,re,collections
os.chdir(r"C:\Users\Administrator\Desktop\경마분석서버")
def tag(r):
    r=str(r or "")
    for k,v in [("히스테리시스","유지(히스테리시스)"),("다시 넣은","걸렀다 복원"),("자주 맞지는","고배당 짚은말끼리"),("교차","교차 짝"),("시장 최저복승","시장 최저복승"),("유력마 1·2위","유력마 1·2위"),("시장유력+급락","시장유력+급락"),("배당불일치","배당불일치"),("그물망","B라인 그물망"),("급락","급락 계열"),("시장","시장 계열"),("전적","전적 계열"),("라인","라인 계열")]:
        if k in r: return v
    return "기타:"+r[:10]
B=[]
for f in sorted(glob.glob("data/analysis_log/2026_0[89]_*.json")):
    day=os.path.basename(f)[:10]
    if day<"2026_08_10": continue
    try: d=json.load(io.open(f,encoding="utf-8"))
    except Exception: continue
    res=d.get("result") or {}
    try: win=tuple(sorted((int(res["1st"]),int(res["2nd"])))); pay=float((res.get("payouts") or {}).get("quinella") or 0)
    except Exception: continue
    if pay<=0: continue
    cp=d.get("corePicks") or {}; disp=[tuple(sorted(c)) for c in ((cp.get("displayedCombos") or {}).get("quinellas") or []) if len(c)==2]
    if not disp: continue
    fq={tuple(sorted(q["combo"])):q for q in (cp.get("finalQuinellas") or []) if isinstance(q,dict) and len(q.get("combo") or [])==2}
    sport=str(d.get("sport") or d.get("category") or ""); sp="경륜" if sport=="cycle" else ("한국" if "korea" in sport else ("중앙" if "central" in sport else "경마"))
    for i,c in enumerate(disp):
        q=fq.get(c) or {}; o=q.get("odds")
        ob="?" if not o else ("~3" if o<3 else "3~6" if o<6 else "6~12" if o<12 else "12~25" if o<25 else "25+")
        B.append(dict(sp=sp,day=day,pos=min(i+1,5),n=min(len(disp),6),ob=ob,tag=tag(q.get("reason")) if q else "명단에만(이유 없음)",p=pay if c==win else 0.0))
print("구좌",len(B),"· 기간",B[0]["day"],"~",B[-1]["day"])
days=sorted(set(b["day"] for b in B)); t1,t2=days[len(days)//3],days[2*len(days)//3]
def line(lab,xs):
    n=len(xs)
    if n<60: return
    h=sum(1 for x in xs if x["p"]>0); tot=sum(x["p"] for x in xs); top=sorted([x["p"] for x in xs],reverse=True)[:3]
    seg=[[x["p"] for x in xs if x["day"]<t1],[x["p"] for x in xs if t1<=x["day"]<t2],[x["p"] for x in xs if x["day"]>=t2]]
    print("  %-26s 구좌 %5d 적중 %4d(%4.1f%%) 회수 %5.1f 3제외 %5.1f · 3분할 %s%s"%(lab,n,h,100.0*h/n,100*tot/n,100*(tot-sum(top))/(n-3)," / ".join("%.0f"%(100*sum(s)/len(s)) if s else "-" for s in seg)," ⚠<30" if h<30 else ""))
for sp in ("경마","경륜"):
    X=[b for b in B if b["sp"]==sp]; print("■",sp); line("전체",X)
    for k,vals in (("pos",[1,2,3,4,5]),("n",[1,2,3,4,5,6]),("ob",["~3","3~6","6~12","12~25","25+","?"])):
        for v in vals: line({"pos":"명단 %s번째","n":"명단 크기 %s","ob":"배당 %s"}[k]%v,[x for x in X if x[k]==v])
    for t,_ in collections.Counter(x["tag"] for x in X).most_common(12): line("이유: "+t,[x for x in X if x["tag"]==t])
print("\n■■ 빼기 시뮬 (같은 경주 집합 · 구좌=조합)")
BAD={"경마":{"명단에만(이유 없음)","유지(히스테리시스)","기타:T-5 확정 후 삭","유력마 1·2위"},"경륜":{"명단에만(이유 없음)","유지(히스테리시스)","기타:회원에게 발송된 💎"}}
for sp in ("경마","경륜"):
    X=[b for b in B if b["sp"]==sp]; line(sp+" 현행",X); line(sp+" 나쁜 자리 제외",[x for x in X if x["tag"] not in BAD[sp]]); line(sp+" 빠진 구좌",[x for x in X if x["tag"] in BAD[sp]])
    for t in BAD[sp]:
        xs=[x for x in X if x["tag"]==t]; late=[x for x in xs if x["day"]>="2026_09_09"]
        print("     · %s: 9/09 이후 %d구좌 회수 %.0f"%(t,len(late),100*sum(x["p"] for x in late)/max(1,len(late))))
