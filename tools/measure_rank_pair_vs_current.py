# -*- coding: utf-8 -*-
"""현행 판정 명단 ↔ 「시장 1위+4~5위」 회수율 비교 (읽기 전용).

🔴 원칙 27 — **그 시점에 존재한 값만** 쓴다.
  처음에 마감 직전(T-0) 틱으로 재니 91.0% 가 나왔다. 그런데 현행 명단은 **T-5 에 동결**되므로
  그 비교는 **5분치 정보를 더 준 불공평한 비교**였다(「마감 후 유력마가 잘 들어온다」와 같은 함정).
  T-5 로 맞춰 다시 재니 **84.1%** 로 내려갔다 — 정보 우위가 실재했다. 그래도 현행보다 높다.

⚠ 원칙 15 확정배당 · 원칙 2 대박3뺀 병기 · 원칙 26 표본/정제/구좌 병기.
⚠ 이 도구는 **측정 전용**이다. 추천 경로를 바꾸지 않는다.
"""
# -*- coding: utf-8 -*-
import sys, os, io, json, glob
sys.path.insert(0,'tools')
import measure_score_edge as E, measure_rank_pairs as R
_BAD=E._BAD

def tick_at(doc, mb_min):
    """🔴 원칙 27 — **그 시점에 존재한 값만** 쓴다. mb >= mb_min 인 마지막 정상 틱."""
    best=None
    for t in E._ticks(doc):
        if not isinstance(t,dict) or any(t.get(k) for k in _BAD): continue
        mb=t.get('minutes_before')
        if mb is None or mb < mb_min: continue
        q=t.get('quinella')
        if not q: continue
        if best is None or float(t.get('t') or 0) >= float(best[0]): best=(float(t.get('t') or 0),q,mb)
    return best

def _cb(x):
    if isinstance(x,dict): x=x.get('combo')
    if isinstance(x,(list,tuple)) and len(x)>=2:
        try: return (min(int(x[0]),int(x[1])),max(int(x[0]),int(x[1])))
        except Exception: return None
    return None

def build(mb_min):
    rows=[]; miss=0
    for f in sorted(glob.glob('data/race_results/2026_08_*.json')):
        d=E._load(f)
        if not isinstance(d,dict) or d.get('payouts_approx') or d.get('payouts_suspect'): continue
        pay=(d.get('payouts') or {}).get('quinella')
        if pay is None: continue
        base=os.path.basename(f)[:-5]
        al=E._load(os.path.join('data','analysis_log',base+'.json')) or {}
        res=al.get('result') or {}
        try: top2={int(res.get('1st')),int(res.get('2nd'))}
        except Exception: continue
        if len(top2)<2: continue
        od=E._load(os.path.join('data','odds_history',base+'.json'))
        cl=E._last_pre_close(od)
        if not cl: continue
        qc=E._qmap(cl); key=(min(top2),max(top2)); mc=qc.get(key)
        if not mc: continue
        ra=float(pay)/mc
        if not (R.CLEAN_LO<=ra<=R.CLEAN_HI): continue
        tk=tick_at(od, mb_min)
        if not tk: miss+=1; continue
        mr=E._market_rank(E._qmap(tk[1]))
        if not mr: miss+=1; continue
        dc=((al.get('corePicks') or {}).get('displayedCombos') or {}).get('quinellas') or []
        cur=[c for c in (_cb(x) for x in dc) if c]
        inv=sorted(mr.items(), key=lambda kv: kv[1])
        r1=[n for n,r in inv if r==1]; r45=[n for n,r in inv if 4<=r<=5]
        rows.append({'rk':base,'sport':E._sport(al),'top2':key,'pay':float(pay),
                     'cur':cur,'prop':[(min(a,b),max(a,b)) for a in r1 for b in r45],'mb':tk[2]})
    return rows, miss

def per(r):
    d=r['rk'][:10]
    return 0 if d<='2026_08_09' else (1 if d<='2026_08_19' else 2)

def ev(rows,fld,sp=None,pi=None):
    g=[r for r in rows if (sp is None or r['sport']==sp) and (pi is None or per(r)==pi)]
    seats=sum(len(r[fld]) for r in g)
    pays=[r['pay'] for r in g if r['top2'] in r[fld]]
    if seats==0: return None
    return (seats,len(pays),100.0*sum(pays)/seats,100.0*R._ex(pays,3)/seats,seats/float(max(1,len(g))))

for mb in (5,):
    rows,miss=build(mb)
    mbs=sorted(r['mb'] for r in rows)
    print('  === T-%d 기준(원칙 27) ===  경주 %d · 틱없음 %d · 실제 mb 중앙 %.1f분'%(mb,len(rows),miss,mbs[len(mbs)//2]))
    print('  %-8s %-16s %8s %6s %8s %8s %8s'%('종목','안','구좌','적중','회수율','대박3뺀','경주당'))
    for sp in (None,'경륜','경마'):
        for fld,lab in (('cur','현행 판정 명단'),('prop','시장 1위+4~5위')):
            v=ev(rows,fld,sp)
            if v: print('  %-8s %-16s %8d %6d %7.1f%% %7.1f%% %8.2f'%((sp or '전체'),lab,v[0],v[1],v[2],v[3],v[4]))
        print()
    print('  --- 기간 3분할(전체) ---')
    for fld,lab in (('cur','현행'),('prop','1위+4~5위')):
        s=[]
        for pi,pl in ((0,'8/01~09'),(1,'8/10~19'),(2,'8/20~')):
            v=ev(rows,fld,None,pi)
            if v: s.append('%s %.1f%%(대박뺀 %.1f%%)'%(pl,v[2],v[3]))
        print('   %-10s %s'%(lab,' · '.join(s)))

print()
rows,_=build(5)
ov=sum(len(set(r['cur'])&set(r['prop'])) for r in rows)
tp=sum(len(r['prop']) for r in rows)
print('  === 겹침 ===  1위+4~5위 %d구좌 중 현행에도 있는 것 %d (%.1f%%)'%(tp,ov,100.0*ov/max(1,tp)))
print()
print('  === 안별 (⚠ 대표 기준 「경주당 3개 이하」) ===')
print('  %-24s %8s %6s %8s %8s %8s'%('안','구좌','적중','회수율','대박3뺀','경주당'))
def mk(r,mode):
    cur=list(r['cur']); pr=[p for p in r['prop'] if p not in cur]
    if mode=='cur': return cur
    if mode=='prop': return list(r['prop'])
    if mode=='union': return cur+pr
    if mode=='add1': return cur+pr[:1]
    if mode=='cur2add1': return cur[:2]+pr[:1]
    return cur
for mode,lab in (('cur','① 현행'),('prop','② 시장 1위+4~5위 대체'),
                 ('add1','③ 현행 + 1개 추가'),('cur2add1','④ 현행 상위2 + 1개'),
                 ('union','⑤ 합집합')):
    for r in rows: r['_x']=mk(r,mode)
    v=ev(rows,'_x')
    s=[]
    for pi,pl in ((0,'8/01~09'),(1,'8/10~19'),(2,'8/20~')):
        vv=ev(rows,'_x',None,pi)
        if vv: s.append('%.1f'%vv[3])
    mark='' if v[4]<=3.0 else '  🔴 3개 초과'
    print('  %-24s %8d %6d %7.1f%% %7.1f%% %8.2f%s   대박뺀 3분할 %s'%(lab,v[0],v[1],v[2],v[3],v[4],mark,' / '.join(s)))
